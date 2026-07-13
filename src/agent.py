#!/usr/bin/env python3
"""Actual web research agent for the Composio API buildability scan.

This file is deliberately separate from ``research_agent.py``. The older script
renders the verified case study. This one does the agentic first pass:

1. Read app seeds.
2. Search for docs/auth pages.
3. Fetch and clean page text.
4. Extract structured auth/access/API/MCP/buildability fields.
5. Run a critic pass over missing or weak evidence.
6. Optionally use OpenAI and/or Composio tool execution when keys are present.

The script is runnable without paid keys by using direct hints + DuckDuckGo HTML
search + deterministic extraction. With keys, set:

    OPENAI_API_KEY=... python3 src/agent.py --limit 5 --use-llm
    COMPOSIO_API_KEY=... python3 src/agent.py --limit 5 --use-composio

The final CSV in this repo is the repaired/human-verified layer. The output from
this agent lives in ``data/agent_runs/`` so reviewers can inspect the raw first
pass separately.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import textwrap
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote_plus, unquote, urlencode, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / "data" / "app_seeds.csv"
RUN_DIR = ROOT / "data" / "agent_runs"

USER_AGENT = "composio-research-agent/2.0 (+https://github.com/itzrahuldas/composio-research-agent)"

DOC_PRIORS = {
    "Salesforce": [
        "https://developer.salesforce.com/docs/apis",
        "https://help.salesforce.com/s/articleView?id=sf.remoteaccess_oauth_flows.htm"
    ],
    "HubSpot": [
        "https://developers.hubspot.com/docs/api",
        "https://developers.hubspot.com/docs/api/oauth-quickstart"
    ],
    "Pipedrive": ["https://developers.pipedrive.com/docs/api/v1"],
    "Attio": ["https://docs.attio.com/rest-api/overview"],
    "Twenty": ["https://twenty.com/developers"],
    "Zendesk": ["https://developer.zendesk.com/api-reference/"],
    "Intercom": ["https://developers.intercom.com/"],
    "Freshdesk": ["https://developers.freshdesk.com/api/"],
    "Front": ["https://dev.frontapp.com/"],
    "Slack": ["https://api.slack.com/apis"],
    "Twilio": ["https://www.twilio.com/docs/usage/api"],
    "Shopify": ["https://shopify.dev/docs/api"],
    "GitHub": ["https://docs.github.com/en/rest"],
    "Notion": ["https://developers.notion.com/"],
    "Stripe": ["https://docs.stripe.com/api"],
}


@dataclass
class AppSeed:
    id: int
    category: str
    app: str
    hint: str


@dataclass
class PageHit:
    url: str
    title: str
    status: int | None
    ok: bool
    text: str
    error: str | None = None


@dataclass
class ResearchResult:
    id: int
    category: str
    app: str
    hint: str
    what_it_does: str
    auth_methods: str
    access: str
    surface: str
    mcp: str
    verdict: str
    blocker: str
    confidence: str
    evidence: list[str]
    source_mode: str
    pages_fetched: int
    evidence_terms: dict[str, list[str]]
    critic_flags: list[str]


class TextExtractor(HTMLParser):
    """Small HTML-to-text parser to avoid making BeautifulSoup mandatory."""

    def __init__(self) -> None:
        super().__init__()
        self.in_skip = False
        self.skip_depth = 0
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.in_skip = True
            self.skip_depth += 1
        if tag == "title":
            self.in_title = True
        if tag in {"p", "li", "br", "h1", "h2", "h3", "h4", "tr", "section", "article"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1
            self.in_skip = self.skip_depth > 0
        if tag == "title":
            self.in_title = False
        if tag in {"p", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.in_skip:
            return
        cleaned = re.sub(r"\s+", " ", data).strip()
        if not cleaned:
            return
        if self.in_title:
            self.title_parts.append(cleaned)
        self.parts.append(cleaned)

    def text(self) -> str:
        text = unescape(" ".join(self.parts))
        return re.sub(r"\s+", " ", text).strip()

    def title(self) -> str:
        return " ".join(self.title_parts).strip()


def read_seeds(path: Path = SEED_PATH) -> list[AppSeed]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = csv.DictReader(f, delimiter="|")
        return [
            AppSeed(
                id=int(row["id"]),
                category=row["category"],
                app=row["app"],
                hint=row["hint"],
            )
            for row in rows
        ]


def normalize_hint(hint: str) -> str | None:
    hint = hint.strip()
    if not hint or " " in hint:
        return None
    if hint.startswith("http://") or hint.startswith("https://"):
        return hint
    return f"https://{hint}"


def docs_candidates(seed: AppSeed) -> list[str]:
    candidates: list[str] = []
    candidates.extend(DOC_PRIORS.get(seed.app, []))
    base = normalize_hint(seed.hint)
    if base:
        parsed = urlparse(base)
        root = f"{parsed.scheme}://{parsed.netloc}"
        for path in ["/docs", "/developer", "/developers", "/api", "/api-docs", "/reference", "/docs/api"]:
            candidates.append(root + path)
        candidates.append(base)
        host = parsed.netloc.replace("www.", "")
        pieces = host.split(".")
        if len(pieces) >= 2:
            domain = ".".join(pieces[-2:])
            candidates.extend(
                [
                    f"https://developer.{domain}",
                    f"https://developers.{domain}",
                    f"https://docs.{domain}",
                    f"https://api.{domain}",
                ]
            )

    docs_query = f"{seed.app} API documentation authentication"
    candidates.extend(search_web(docs_query, limit=5))
    candidates.extend(search_web(f"{seed.app} developer API OAuth API key", limit=3))
    return sorted(dedupe_urls(candidates), key=lambda url: candidate_score(seed, url), reverse=True)


def candidate_score(seed: AppSeed, url: str) -> int:
    lower = url.lower()
    score = 0
    for token in ["developer", "developers", "docs", "api", "reference", "auth", "oauth", "mcp"]:
        if token in lower:
            score += 3
    if seed.app.lower().split()[0] in lower:
        score += 2
    path = urlparse(url).path.strip("/")
    if not path:
        score -= 8
    if any(word in lower for word in ["pricing", "blog", "careers", "contact", "login"]):
        score -= 5
    return score


def dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if not url or not url.startswith(("http://", "https://")):
            continue
        clean = url.split("#", 1)[0].rstrip("/")
        if clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out


def search_web(query: str, limit: int = 5) -> list[str]:
    """Search web without an API key.

    DuckDuckGo's HTML endpoint is not guaranteed; this is a pragmatic fallback
    for a take-home repo. With Composio/Tavily configured, ``composio_search``
    can replace this path.
    """

    url = "https://duckduckgo.com/html/?" + urlencode({"q": query})
    try:
        body = fetch_raw(url, timeout=12)
    except Exception:
        return []

    urls: list[str] = []
    for href in re.findall(r'href="([^"]+)"', body):
        href = unescape(href)
        if "duckduckgo.com/l/?" in href:
            parsed = urlparse(href)
            qs = parse_qs(parsed.query)
            if "uddg" in qs:
                urls.append(unquote(qs["uddg"][0]))
        elif href.startswith("http"):
            urls.append(href)
    preferred = [u for u in urls if any(word in u.lower() for word in ["developer", "docs", "api", "reference"])]
    return dedupe_urls(preferred or urls)[:limit]


def composio_search(query: str, user_id: str = "composio-research-agent") -> list[str]:
    """Optional Composio search hook.

    This uses the current Composio Python SDK shape from official docs:
    ``Composio(...).tools.execute(tool_name, arguments=..., user_id=...)``.
    Tool names can vary by configured workspace, so the function tries a small
    set of likely Tavily/Exa search tool IDs and degrades cleanly.
    """

    api_key = os.getenv("COMPOSIO_API_KEY")
    if not api_key:
        return []
    try:
        from composio import Composio  # type: ignore
    except Exception:
        return []

    composio = Composio(api_key=api_key)
    tool_names = ["TAVILY_SEARCH", "TAVILY_TAVILY_SEARCH", "EXA_SEARCH", "SERPAPI_SEARCH"]
    for tool_name in tool_names:
        try:
            result = composio.tools.execute(tool_name, arguments={"query": query, "max_results": 5}, user_id=user_id)
            return extract_urls_from_any(result)
        except Exception:
            continue
    return []


def extract_urls_from_any(value: Any) -> list[str]:
    text = json.dumps(value, default=str)
    return dedupe_urls(re.findall(r"https?://[^\s\"'<>]+", text))


def fetch_raw(url: str, timeout: int = 16) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(1_500_000)
        charset = response.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


def fetch_page(url: str) -> PageHit:
    try:
        body = fetch_raw(url)
        parser = TextExtractor()
        parser.feed(body)
        text = parser.text()
        return PageHit(
            url=url,
            title=parser.title(),
            status=200,
            ok=bool(text),
            text=text[:25_000],
        )
    except HTTPError as exc:
        return PageHit(url=url, title="", status=exc.code, ok=False, text="", error=f"HTTP {exc.code}")
    except URLError as exc:
        return PageHit(url=url, title="", status=None, ok=False, text="", error=str(exc.reason))
    except Exception as exc:
        return PageHit(url=url, title="", status=None, ok=False, text="", error=repr(exc))


def term_in_text(text: str, term: str) -> bool:
    lower_term = term.lower()
    if len(lower_term) <= 4 and lower_term.isalnum():
        return re.search(rf"(?<![a-z0-9]){re.escape(lower_term)}(?![a-z0-9])", text) is not None
    return lower_term in text


def find_terms(text: str, terms: dict[str, list[str]]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    lower = text.lower()
    for label, needles in terms.items():
        hits = []
        for needle in needles:
            if term_in_text(lower, needle):
                hits.append(needle)
        if hits:
            found[label] = hits
    return found


AUTH_TERMS = {
    "OAuth2": ["oauth 2", "oauth2", "authorization code", "client credentials", "refresh token"],
    "API key": ["api key", "apikey", "x-api-key", "private key", "access key"],
    "Bearer token": ["bearer", "access token", "personal access token", "pat"],
    "Basic auth": ["basic auth", "basic authentication", "username:password"],
    "JWT": ["jwt", "json web token"],
    "Signed request": ["hmac", "hmac signature", "sigv4", "aws signature", "signed request"],
    "No auth/local": ["no authentication", "local cli", "command line"],
}

ACCESS_TERMS = {
    "Self-serve": ["sign up", "free trial", "developer account", "create an app", "create api key", "generate api key", "sandbox"],
    "Review/admin gate": ["app review", "approval", "admin approval", "business verification", "developer token", "verify your business"],
    "Paid/customer gate": ["paid plan", "enterprise plan", "contact sales", "request access", "account manager", "customer account", "partner portal", "partner account"],
    "No public docs": ["not available", "no public api", "coming soon"],
}

SURFACE_TERMS = {
    "REST": ["rest api", "endpoint", "http api", "api reference"],
    "GraphQL": ["graphql"],
    "Webhooks": ["webhook", "webhooks"],
    "SDK": ["sdk", "client library"],
    "Bulk/async": ["bulk", "batch", "job", "async"],
    "CLI": ["cli", "command line"],
}

MCP_TERMS = {
    "MCP": ["model context protocol", "mcp server", "mcp"],
    "Agent": ["agent", "ai toolkit", "llms.txt", "server-card"],
}


def summarize_product(seed: AppSeed, combined_text: str) -> str:
    category_nouns = {
        "CRM and Sales": "CRM/sales platform",
        "Support and Helpdesk": "support/helpdesk platform",
        "Communications and Messaging": "communications platform",
        "Marketing, Ads, Email and Social": "marketing or ads platform",
        "Ecommerce": "commerce platform",
        "Data, SEO and Scraping": "data, SEO or scraping platform",
        "Developer, Infra and Data platforms": "developer/infrastructure platform",
        "Productivity and Project Management": "productivity/work management platform",
        "Finance and Fintech": "finance or fintech platform",
        "AI, Research and Media-native": "AI, research or media-native tool",
    }
    if "open-source" in combined_text.lower() or "github.com" in seed.hint:
        return f"{seed.app} is an open-source or developer-facing {category_nouns.get(seed.category, 'application')}."
    return f"{seed.app} is a {category_nouns.get(seed.category, 'application')}."


def extract_heuristic(seed: AppSeed, pages: list[PageHit], source_mode: str) -> ResearchResult:
    ok_pages = [page for page in pages if page.ok and page.text]
    combined = "\n\n".join(page.text for page in ok_pages)
    auth_found = find_terms(combined, AUTH_TERMS)
    access_found = find_terms(combined, ACCESS_TERMS)
    surface_found = find_terms(combined, SURFACE_TERMS)
    mcp_found = find_terms(combined, MCP_TERMS)

    auth_methods = "; ".join(auth_found.keys()) or "Unknown / not verified"

    access = classify_access(access_found, ok_pages)
    surface = classify_surface(surface_found, ok_pages)
    mcp = classify_mcp(mcp_found)
    verdict, blocker = classify_verdict(seed, ok_pages, auth_found, access_found, surface_found)
    confidence = confidence_score(ok_pages, auth_found, surface_found, access_found)
    evidence = [page.url for page in ok_pages[:4]]
    if not evidence:
        evidence = [page.url for page in pages[:2]]

    result = ResearchResult(
        id=seed.id,
        category=seed.category,
        app=seed.app,
        hint=seed.hint,
        what_it_does=summarize_product(seed, combined),
        auth_methods=auth_methods,
        access=access,
        surface=surface,
        mcp=mcp,
        verdict=verdict,
        blocker=blocker,
        confidence=confidence,
        evidence=evidence,
        source_mode=source_mode,
        pages_fetched=len(pages),
        evidence_terms={**auth_found, **access_found, **surface_found, **mcp_found},
        critic_flags=[],
    )
    result.critic_flags = critic_pass(result)
    return result


def classify_access(access_found: dict[str, list[str]], pages: list[PageHit]) -> str:
    if not pages:
        return "No reachable public docs found."
    labels = set(access_found)
    if "No public docs" in labels:
        return "No public/self-serve path verified."
    pieces = []
    if "Self-serve" in labels:
        pieces.append("Self-serve signup/developer path indicated")
    if "Review/admin gate" in labels:
        pieces.append("review, business, developer-token or admin approval gate indicated")
    if "Paid/customer gate" in labels:
        pieces.append("paid/customer/partner access gate indicated")
    return "; ".join(pieces) if pieces else "Access path unclear from fetched docs."


def classify_surface(surface_found: dict[str, list[str]], pages: list[PageHit]) -> str:
    if not pages:
        return "No public API surface verified."
    labels = list(surface_found)
    if not labels:
        return "Docs found, but API style/breadth unclear from fetched text."
    breadth = "broad" if len(labels) >= 3 else "moderate/focused"
    return f"{breadth} documented surface with {', '.join(labels)} signals."


def classify_mcp(mcp_found: dict[str, list[str]]) -> str:
    if "MCP" in mcp_found:
        return "MCP/server-card signal found in fetched docs."
    if "Agent" in mcp_found:
        return "Agent/AI-tooling signal found, but MCP not proven."
    return "No MCP signal found in fetched docs."


def classify_verdict(
    seed: AppSeed,
    pages: list[PageHit],
    auth_found: dict[str, list[str]],
    access_found: dict[str, list[str]],
    surface_found: dict[str, list[str]],
) -> tuple[str, str]:
    if not pages:
        return "Not buildable yet", "No reachable public docs were found by the agent."
    if not auth_found and not surface_found:
        return "Investigate further", "Docs were reachable, but auth/API indicators were weak."
    labels = set(access_found)
    if "No public docs" in labels:
        return "Not buildable yet", "Fetched text indicates no public API path."
    if "Paid/customer gate" in labels and "Self-serve" not in labels:
        return "Outreach needed", "Paid/customer/partner access appears to be the main blocker."
    if "Review/admin gate" in labels or "Paid/customer gate" in labels:
        return "Ready with gate", "Buildable after review/admin/plan/compliance gate is cleared."
    return "Ready", "Public docs and auth/API indicators are present."


def confidence_score(
    pages: list[PageHit],
    auth_found: dict[str, list[str]],
    surface_found: dict[str, list[str]],
    access_found: dict[str, list[str]],
) -> str:
    score = 0
    score += min(len(pages), 3)
    score += 2 if auth_found else 0
    score += 2 if surface_found else 0
    score += 1 if access_found else 0
    if score >= 6:
        return "High"
    if score >= 3:
        return "Medium"
    return "Low"


def critic_pass(result: ResearchResult) -> list[str]:
    flags: list[str] = []
    if result.auth_methods.startswith("Unknown"):
        flags.append("auth_missing")
    if "unclear" in result.access.lower() or "not verified" in result.access.lower():
        flags.append("access_unclear")
    if "unclear" in result.surface.lower() or "No public API surface" in result.surface:
        flags.append("surface_unclear")
    if result.confidence == "Low":
        flags.append("low_confidence")
    if not result.evidence:
        flags.append("no_evidence_url")
    return flags


def maybe_llm_rewrite(seed: AppSeed, pages: list[PageHit], heuristic: ResearchResult) -> ResearchResult:
    """Optional LLM extraction pass.

    It is disabled unless ``--use-llm`` and ``OPENAI_API_KEY`` are set. The
    prompt asks for JSON only, then falls back to heuristic output if parsing
    fails. This keeps the repo reproducible without secrets.
    """

    if not os.getenv("OPENAI_API_KEY"):
        return heuristic
    try:
        from openai import OpenAI  # type: ignore
    except Exception:
        return heuristic

    context = "\n\n".join(
        f"URL: {page.url}\nTITLE: {page.title}\nTEXT:\n{textwrap.shorten(page.text, width=6000, placeholder='...')}"
        for page in pages
        if page.ok
    )
    if not context:
        return heuristic

    schema = {
        "what_it_does": "one sentence",
        "auth_methods": "OAuth2/API key/Basic/token/other",
        "access": "self-serve vs gated with nuance",
        "surface": "REST/GraphQL/breadth/MCP",
        "mcp": "official/community/no MCP signal",
        "verdict": "Ready/Ready with gate/Outreach needed/Not buildable yet/Investigate further",
        "blocker": "main blocker",
        "confidence": "High/Medium/Low",
        "evidence": ["urls used"],
    }
    prompt = f"""
You are researching whether Composio can build an agent toolkit for an app.
Return JSON only matching this schema: {json.dumps(schema)}.
Be conservative. If docs or auth are not proven, say so.

App: {seed.app}
Category: {seed.category}
Hint: {seed.hint}

Fetched docs:
{context}
"""
    try:
        client = OpenAI()
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        content = response.choices[0].message.content or "{}"
        parsed = json.loads(re.sub(r"^```json|```$", "", content.strip(), flags=re.I | re.M))
    except Exception:
        return heuristic

    for field in [
        "what_it_does",
        "auth_methods",
        "access",
        "surface",
        "mcp",
        "verdict",
        "blocker",
        "confidence",
    ]:
        value = parsed.get(field)
        if isinstance(value, str) and value.strip():
            setattr(heuristic, field, value.strip())
    if isinstance(parsed.get("evidence"), list):
        heuristic.evidence = [str(url) for url in parsed["evidence"] if str(url).startswith("http")] or heuristic.evidence
    heuristic.source_mode += "+openai"
    heuristic.critic_flags = critic_pass(heuristic)
    return heuristic


def research_one(seed: AppSeed, max_pages: int, use_composio: bool, use_llm: bool) -> ResearchResult:
    source_mode = "direct+duckduckgo"
    candidates = docs_candidates(seed)
    if use_composio:
        comp_urls = composio_search(f"{seed.app} API documentation authentication")
        if comp_urls:
            source_mode += "+composio"
            candidates = dedupe_urls(comp_urls + candidates)
        else:
            source_mode += "+composio_unavailable"

    pages: list[PageHit] = []
    for url in candidates[: max_pages * 3]:
        page = fetch_page(url)
        pages.append(page)
        if page.ok and is_relevant(seed, page):
            if len([p for p in pages if p.ok]) >= max_pages:
                break

    relevant_pages = [page for page in pages if page.ok and is_relevant(seed, page)]
    if not relevant_pages:
        relevant_pages = [page for page in pages if page.ok][:max_pages]

    result = extract_heuristic(seed, relevant_pages or pages[:max_pages], source_mode)
    if use_llm:
        result = maybe_llm_rewrite(seed, relevant_pages or pages[:max_pages], result)
    return result


def is_relevant(seed: AppSeed, page: PageHit) -> bool:
    text = f"{page.url} {page.title} {page.text[:5000]}".lower()
    app_tokens = [token for token in re.split(r"[^a-z0-9]+", seed.app.lower()) if len(token) > 2]
    category_hit = any(word in text for word in ["api", "developer", "docs", "oauth", "authentication", "webhook", "mcp"])
    app_hit = any(token in text for token in app_tokens[:2])
    return app_hit and category_hit


def write_results(results: list[ResearchResult], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(results),
        "summary": {
            "confidence": Counter(result.confidence for result in results),
            "verdict": Counter(result.verdict for result in results),
            "critic_flags": Counter(flag for result in results for flag in result.critic_flags),
        },
        "results": [asdict(result) for result in results],
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_critic_report(results: list[ResearchResult], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": "Deterministic critic flags rows where auth/access/surface/evidence confidence is weak. Human verification then repairs the final CSV.",
        "flag_counts": Counter(flag for result in results for flag in result.critic_flags),
        "rows": [
            {
                "app": result.app,
                "confidence": result.confidence,
                "verdict": result.verdict,
                "flags": result.critic_flags,
                "next_action": next_action(result),
                "evidence": result.evidence,
            }
            for result in results
        ],
    }
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def next_action(result: ResearchResult) -> str:
    if "auth_missing" in result.critic_flags:
        return "Find and verify an auth-specific docs page."
    if "access_unclear" in result.critic_flags:
        return "Check signup, marketplace review, pricing or admin docs."
    if "surface_unclear" in result.critic_flags:
        return "Fetch API reference or OpenAPI/GraphQL schema docs."
    if result.confidence == "Low":
        return "Human review before using this row."
    return "No immediate repair required."


def write_csv(results: list[ResearchResult], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "id",
        "category",
        "app",
        "what_it_does",
        "auth_methods",
        "access",
        "surface",
        "mcp",
        "verdict",
        "blocker",
        "confidence",
        "evidence",
    ]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="|")
        writer.writeheader()
        for result in results:
            row = asdict(result)
            row["evidence"] = "; ".join(result.evidence)
            writer.writerow({field: row[field] for field in fields})


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a first-pass docs research agent for app API buildability.")
    parser.add_argument("--seeds", type=Path, default=SEED_PATH)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--use-composio", action="store_true", help="Use Composio tool execution if COMPOSIO_API_KEY is configured.")
    parser.add_argument("--use-llm", action="store_true", help="Use OpenAI structured extraction if OPENAI_API_KEY is configured.")
    parser.add_argument("--output", type=Path, default=RUN_DIR / "agent_results.json")
    parser.add_argument("--critic-output", type=Path, default=None)
    parser.add_argument("--csv-output", type=Path, default=None)
    args = parser.parse_args()

    seeds = read_seeds(args.seeds)
    selected = seeds[args.offset : args.offset + args.limit]
    results: list[ResearchResult] = []
    for index, seed in enumerate(selected, start=1):
        print(f"[{index}/{len(selected)}] researching {seed.app} ({seed.category})")
        result = research_one(
            seed=seed,
            max_pages=args.max_pages,
            use_composio=args.use_composio,
            use_llm=args.use_llm,
        )
        print(f"  -> {result.verdict} | {result.confidence} | {result.auth_methods} | flags={','.join(result.critic_flags) or 'none'}")
        results.append(result)

    write_results(results, args.output)
    critic_output = args.critic_output or args.output.with_name(args.output.stem + "_critic.json")
    write_critic_report(results, critic_output)
    if args.csv_output:
        write_csv(results, args.csv_output)
    print(f"Wrote {args.output.relative_to(ROOT) if args.output.is_relative_to(ROOT) else args.output}")
    print(f"Wrote {critic_output.relative_to(ROOT) if critic_output.is_relative_to(ROOT) else critic_output}")
    if args.csv_output:
        print(f"Wrote {args.csv_output.relative_to(ROOT) if args.csv_output.is_relative_to(ROOT) else args.csv_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
