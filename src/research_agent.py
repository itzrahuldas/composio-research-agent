#!/usr/bin/env python3
"""Research/verification pipeline for the Composio take-home.

This is intentionally dependency-light so the reviewer can run it with only
Python 3. It turns the researched app matrix into:

- index.html: a two-minute case-study page
- data/toolkit_queue.json: machine-readable build priority recommendations
- data/evidence_status.json: optional URL reachability report
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "apps_research.csv"
AUDIT_PATH = ROOT / "data" / "manual_verification.csv"
HTML_PATH = ROOT / "index.html"
QUEUE_PATH = ROOT / "data" / "toolkit_queue.json"
EVIDENCE_STATUS_PATH = ROOT / "data" / "evidence_status.json"


def read_pipe_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return [dict(row) for row in csv.DictReader(f, delimiter="|")]


def split_evidence(row: dict[str, str]) -> list[str]:
    return [url.strip() for url in row["evidence"].split(";") if url.strip()]


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def slug(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def verdict_bucket(row: dict[str, str]) -> str:
    verdict = row["verdict"].lower()
    if verdict == "ready":
        return "Ready today"
    if "ready with" in verdict:
        return "Ready after gate"
    if "outreach" in verdict:
        return "Outreach needed"
    if "not buildable" in verdict:
        return "No public build path"
    return "Needs investigation"


def access_bucket(row: dict[str, str]) -> str:
    text = f"{row['access']} {row['verdict']} {row['blocker']}".lower()
    if any(term in text for term in ["no verified", "no public", "not buildable", "outreach needed", "gated:"]):
        return "Gated / unknown"
    if any(
        term in text
        for term in [
            "review",
            "approval",
            "paid",
            "plan",
            "customer",
            "admin",
            "compliance",
            "business verification",
            "account manager",
        ]
    ):
        return "Review, plan or admin gate"
    return "Self-serve"


def auth_families(apps: Iterable[dict[str, str]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in apps:
        auth = row["auth_methods"].lower()
        if "oauth2" in auth or "oauth" in auth:
            counts["OAuth2"] += 1
        if "api key" in auth or "access token" in auth or "bearer" in auth or "pat" in auth or "secret" in auth:
            counts["API key / bearer token"] += 1
        if "basic" in auth:
            counts["Basic auth"] += 1
        if "jwt" in auth:
            counts["JWT"] += 1
        if "hmac" in auth or "sigv4" in auth:
            counts["Signed request"] += 1
        if "unknown" in auth or "not verified" in auth:
            counts["Unknown"] += 1
    return counts


def is_official_mcp(row: dict[str, str]) -> bool:
    text = row["mcp"].lower()
    return "official" in text and "no official" not in text


def priority(row: dict[str, str]) -> str:
    bucket = verdict_bucket(row)
    confidence = row["confidence"].lower()
    if bucket == "Ready today" and confidence == "high":
        return "P0 easy win"
    if bucket in {"Ready today", "Ready after gate"}:
        return "P1 build after credential check"
    if bucket in {"Outreach needed", "No public build path"}:
        return "P3 outreach / park"
    return "P2 investigate"


def compute_stats(apps: list[dict[str, str]], audit: list[dict[str, str]]) -> dict[str, object]:
    verdict_counts = Counter(verdict_bucket(row) for row in apps)
    access_counts = Counter(access_bucket(row) for row in apps)
    confidence_counts = Counter(row["confidence"] for row in apps)
    category_stats: dict[str, Counter[str]] = defaultdict(Counter)
    for row in apps:
        category_stats[row["category"]][verdict_bucket(row)] += 1
        category_stats[row["category"]]["Total"] += 1

    first_total = sum(int(row["first_pass_supported"]) for row in audit)
    final_total = sum(int(row["final_supported"]) for row in audit)
    checked_total = sum(int(row["fields_checked"]) for row in audit)

    return {
        "total": len(apps),
        "verdict_counts": verdict_counts,
        "access_counts": access_counts,
        "confidence_counts": confidence_counts,
        "auth_counts": auth_families(apps),
        "category_stats": category_stats,
        "official_mcp_count": sum(1 for row in apps if is_official_mcp(row)),
        "high_confidence_count": sum(1 for row in apps if row["confidence"] == "High"),
        "audit_sample": len(audit),
        "audit_checked_total": checked_total,
        "audit_first_total": first_total,
        "audit_final_total": final_total,
        "audit_first_pct": round(first_total / checked_total * 100, 1),
        "audit_final_pct": round(final_total / checked_total * 100, 1),
    }


def write_toolkit_queue(apps: list[dict[str, str]]) -> None:
    queue = []
    for row in apps:
        queue.append(
            {
                "id": int(row["id"]),
                "app": row["app"],
                "category": row["category"],
                "priority": priority(row),
                "verdict": row["verdict"],
                "auth_methods": row["auth_methods"],
                "main_blocker": row["blocker"],
                "evidence": split_evidence(row),
            }
        )
    QUEUE_PATH.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def check_url(url: str, timeout: int = 12) -> dict[str, object]:
    headers = {"User-Agent": "composio-research-agent/1.0 (+take-home verification)"}
    request = Request(url, method="GET", headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            final_url = response.geturl()
            status = getattr(response, "status", 200)
            content_type = response.headers.get("content-type", "")
            return {"url": url, "ok": 200 <= status < 400, "status": status, "final_url": final_url, "content_type": content_type}
    except HTTPError as exc:
        return {"url": url, "ok": 200 <= exc.code < 400, "status": exc.code, "final_url": exc.url, "error": str(exc)}
    except URLError as exc:
        return {"url": url, "ok": False, "status": None, "error": str(exc.reason)}
    except Exception as exc:  # pragma: no cover - defensive for live network variance
        return {"url": url, "ok": False, "status": None, "error": repr(exc)}


def check_evidence_links(apps: list[dict[str, str]], limit: int | None = None) -> dict[str, object]:
    urls: list[str] = []
    for row in apps:
        urls.extend(split_evidence(row))
    unique_urls = list(dict.fromkeys(urls))
    if limit:
        unique_urls = unique_urls[:limit]
    results = [check_url(url) for url in unique_urls]
    report = {
        "checked_at": date.today().isoformat(),
        "checked": len(results),
        "ok": sum(1 for item in results if item["ok"]),
        "failed": [item for item in results if not item["ok"]],
        "results": results,
    }
    EVIDENCE_STATUS_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def evidence_links(row: dict[str, str]) -> str:
    links = []
    for index, url in enumerate(split_evidence(row), start=1):
        links.append(f'<a href="{esc(url)}" target="_blank" rel="noopener">proof {index}</a>')
    return " ".join(links)


def build_category_matrix(stats: dict[str, object]) -> str:
    category_stats: dict[str, Counter[str]] = stats["category_stats"]  # type: ignore[assignment]
    rows = []
    order = ["Ready today", "Ready after gate", "Outreach needed", "No public build path", "Needs investigation"]
    for category, counts in category_stats.items():
        cells = "".join(f"<td>{counts.get(name, 0)}</td>" for name in order)
        rows.append(f"<tr><th>{esc(category)}</th><td>{counts['Total']}</td>{cells}</tr>")
    return "\n".join(rows)


def build_app_rows(apps: list[dict[str, str]]) -> str:
    rows = []
    for row in apps:
        bucket = verdict_bucket(row)
        confidence = row["confidence"].lower()
        mcp_badge = "Official MCP" if is_official_mcp(row) else ("MCP/agent path" if "mcp" in row["mcp"].lower() and "no official" not in row["mcp"].lower() else "No official MCP")
        rows.append(
            f"""
            <tr data-category="{esc(row['category'])}" data-verdict="{esc(bucket)}" data-search="{esc((row['app'] + ' ' + row['category'] + ' ' + row['auth_methods'] + ' ' + row['verdict']).lower())}">
              <td class="num">{esc(row['id'])}</td>
              <td><strong>{esc(row['app'])}</strong><span>{esc(row['what_it_does'])}</span></td>
              <td>{esc(row['category'])}</td>
              <td>{esc(row['auth_methods'])}</td>
              <td>{esc(access_bucket(row))}<span>{esc(row['access'])}</span></td>
              <td>{esc(row['surface'])}<span class="muted">{esc(mcp_badge)}</span></td>
              <td><b class="badge {slug(bucket)}">{esc(bucket)}</b><span>{esc(row['blocker'])}</span></td>
              <td><b class="confidence {confidence}">{esc(row['confidence'])}</b>{evidence_links(row)}</td>
            </tr>
            """
        )
    return "\n".join(rows)


def build_audit_rows(audit: list[dict[str, str]]) -> str:
    rows = []
    for row in audit:
        fields = int(row["fields_checked"])
        first = int(row["first_pass_supported"])
        final = int(row["final_supported"])
        fixed = final - first
        rows.append(
            f"""
            <tr>
              <td><strong>{esc(row['app'])}</strong><span>{esc(row['category'])}</span></td>
              <td>{first}/{fields}</td>
              <td>{final}/{fields}</td>
              <td>{fixed:+d}</td>
              <td>{esc(row['miss_or_risk_found'])}</td>
              <td>{esc(row['human_repair'])}</td>
            </tr>
            """
        )
    return "\n".join(rows)


def pct(count: int, total: int) -> str:
    return f"{round(count / total * 100)}%"


def build_html(apps: list[dict[str, str]], audit: list[dict[str, str]], stats: dict[str, object]) -> None:
    total = int(stats["total"])
    verdict_counts: Counter[str] = stats["verdict_counts"]  # type: ignore[assignment]
    access_counts: Counter[str] = stats["access_counts"]  # type: ignore[assignment]
    auth_counts: Counter[str] = stats["auth_counts"]  # type: ignore[assignment]
    top_auth = auth_counts.most_common(4)
    p0_count = sum(1 for row in apps if priority(row) == "P0 easy win")
    gated_count = verdict_counts["Outreach needed"] + verdict_counts["No public build path"] + verdict_counts["Needs investigation"]
    ready_total = verdict_counts["Ready today"] + verdict_counts["Ready after gate"]

    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Composio Research Agent: 100-App API Buildability Scan</title>
  <style>
    :root {{
      --ink: #18212f;
      --muted: #5c6878;
      --line: #d9dee6;
      --panel: #f7f8fb;
      --blue: #2764d8;
      --teal: #087f7a;
      --green: #26734d;
      --amber: #a96800;
      --red: #b43434;
      --violet: #6456c8;
      --white: #ffffff;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      background: #ffffff;
      font: 14px/1.45 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    a {{ color: var(--blue); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    header {{
      padding: 42px 40px 28px;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(180deg, #f9fbff 0%, #ffffff 100%);
    }}
    main {{ padding: 26px 40px 56px; }}
    .eyebrow {{
      color: var(--teal);
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 0;
      text-transform: uppercase;
      margin-bottom: 10px;
    }}
    h1 {{
      margin: 0 0 14px;
      max-width: 1180px;
      font-size: clamp(31px, 4vw, 56px);
      line-height: 1.02;
      letter-spacing: 0;
    }}
    h2 {{
      margin: 34px 0 14px;
      font-size: 22px;
      letter-spacing: 0;
    }}
    h3 {{
      margin: 0 0 8px;
      font-size: 15px;
      letter-spacing: 0;
    }}
    p {{ max-width: 920px; color: var(--muted); margin: 0 0 12px; }}
    .lead {{ max-width: 1040px; font-size: 18px; color: #303949; }}
    .grid {{
      display: grid;
      gap: 12px;
    }}
    .kpis {{
      grid-template-columns: repeat(4, minmax(0, 1fr));
      margin-top: 24px;
    }}
    .kpi {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      background: var(--white);
    }}
    .kpi b {{
      display: block;
      font-size: 30px;
      line-height: 1;
      margin-bottom: 8px;
    }}
    .kpi span {{ color: var(--muted); }}
    .insights {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
      margin-top: 22px;
    }}
    .insight {{
      border-left: 4px solid var(--teal);
      padding: 12px 14px;
      background: var(--panel);
      border-radius: 6px;
    }}
    .insight:nth-child(2) {{ border-color: var(--amber); }}
    .insight:nth-child(3) {{ border-color: var(--blue); }}
    .insight:nth-child(4) {{ border-color: var(--red); }}
    .strip {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin: 14px 0 18px;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      min-height: 30px;
      padding: 5px 10px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--white);
      color: #303949;
      font-weight: 650;
    }}
    .panel {{
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: var(--white);
    }}
    .panel-head {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
      padding: 14px 16px;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
    }}
    .controls {{
      display: grid;
      grid-template-columns: minmax(180px, 1fr) 220px 220px;
      gap: 10px;
      margin: 12px 0;
    }}
    input, select {{
      width: 100%;
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      color: var(--ink);
      background: var(--white);
      font: inherit;
    }}
    .table-wrap {{
      overflow: auto;
      max-height: 760px;
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 1120px;
      background: var(--white);
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 10px 11px;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      position: sticky;
      top: 0;
      z-index: 1;
      background: #eef2f7;
      color: #303949;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0;
    }}
    td span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-top: 4px;
    }}
    .num {{ color: var(--muted); width: 46px; }}
    .badge, .confidence {{
      display: inline-block;
      border-radius: 6px;
      padding: 3px 7px;
      font-size: 12px;
      line-height: 1.3;
      color: #fff;
      white-space: nowrap;
    }}
    .ready-today {{ background: var(--green); }}
    .ready-after-gate {{ background: var(--amber); }}
    .outreach-needed {{ background: var(--red); }}
    .no-public-build-path {{ background: #842f74; }}
    .needs-investigation {{ background: var(--violet); }}
    .confidence.high {{ background: var(--green); }}
    .confidence.medium {{ background: var(--amber); }}
    .confidence.low {{ background: var(--red); }}
    .muted {{ color: var(--muted); }}
    .flow {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 10px;
      margin-top: 12px;
    }}
    .step {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 13px;
      background: var(--white);
    }}
    .step b {{ color: var(--teal); }}
    .two-col {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 16px;
    }}
    code {{
      background: #eef2f7;
      border: 1px solid var(--line);
      border-radius: 5px;
      padding: 2px 5px;
      color: #273142;
    }}
    footer {{
      margin-top: 30px;
      padding-top: 18px;
      border-top: 1px solid var(--line);
      color: var(--muted);
    }}
    @media (max-width: 980px) {{
      header, main {{ padding-left: 18px; padding-right: 18px; }}
      .kpis, .insights, .two-col, .flow {{ grid-template-columns: 1fr; }}
      .controls {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 34px; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="eyebrow">Composio take-home · AI Product Ops research agent</div>
    <h1>100-app API scan: the easy wins are self-serve SaaS; the real blockers are review gates, paid data licenses, and missing public docs.</h1>
    <p class="lead">I built a small research pipeline that classifies auth, access, API breadth, MCP readiness and buildability for 100 requested apps, then uses a verification loop to repair the rows most likely to hallucinate.</p>
    <div class="grid kpis">
      <div class="kpi"><b>{total}</b><span>apps researched across 10 categories</span></div>
      <div class="kpi"><b>{ready_total}</b><span>{pct(ready_total, total)} are buildable now or after ordinary account/review gates</span></div>
      <div class="kpi"><b>{p0_count}</b><span>P0 easy wins: high-confidence, self-serve, agent-callable APIs</span></div>
      <div class="kpi"><b>{gated_count}</b><span>{pct(gated_count, total)} need outreach, paid access, or had no public API path</span></div>
    </div>
    <div class="grid insights">
      <div class="insight"><h3>OAuth2 is the default for user-owned SaaS.</h3><p>{auth_counts['OAuth2']} apps support OAuth/OAuth2. It dominates CRM, support, productivity, commerce marketplaces, ads and accounting.</p></div>
      <div class="insight"><h3>API keys still win for machine/data products.</h3><p>{auth_counts['API key / bearer token']} apps expose API-key or bearer-token paths, especially scraping, email, observability, fintech and internal-tool APIs.</p></div>
      <div class="insight"><h3>Developer and productivity apps are the fastest Composio wins.</h3><p>GitHub, Vercel, Cloudflare, Supabase, Linear, Notion, Airtable and similar tools have clear docs, scopes and predictable API objects.</p></div>
      <div class="insight"><h3>Do not fake the hard cases.</h3><p>Pumble, FanBasis, Waterfall.io, Paygent Connect, PitchBook and NotebookLM are useful precisely because they reveal outreach/no-public-API patterns.</p></div>
    </div>
  </header>

  <main>
    <section>
      <h2>Pattern Matrix</h2>
      <p>The raw 100-row table is below, but the operating answer is this matrix: build self-serve SaaS first, then queue review-gated categories, then send outreach for hidden enterprise/data products.</p>
      <div class="strip">
        <span class="pill">Self-serve: {access_counts['Self-serve']}</span>
        <span class="pill">Review/admin/plan gate: {access_counts['Review, plan or admin gate']}</span>
        <span class="pill">Gated or unknown: {access_counts['Gated / unknown']}</span>
        <span class="pill">Official MCP/agent path found: {stats['official_mcp_count']}</span>
        <span class="pill">High-confidence rows: {stats['high_confidence_count']}</span>
      </div>
      <div class="table-wrap" style="max-height: 420px;">
        <table>
          <thead>
            <tr><th>Category</th><th>Total</th><th>Ready today</th><th>Ready after gate</th><th>Outreach</th><th>No public path</th><th>Investigate</th></tr>
          </thead>
          <tbody>
            {build_category_matrix(stats)}
          </tbody>
        </table>
      </div>
    </section>

    <section class="two-col">
      <div>
        <h2>Agent Workflow</h2>
        <p>The pipeline uses loop prompting as an operating pattern even when the final run is deterministic: collect evidence, classify fields, criticize likely hallucinations, then repair with human-verifiable evidence.</p>
        <div class="flow">
          <div class="step"><b>1. Seed</b><span>Start with app names, category, and official-doc hints.</span></div>
          <div class="step"><b>2. Fetch</b><span>Resolve docs/search evidence and keep source URLs per row.</span></div>
          <div class="step"><b>3. Extract</b><span>Classify auth, access gate, API breadth, MCP and buildability.</span></div>
          <div class="step"><b>4. Critique</b><span>Flag ambiguous rows: hidden APIs, ads review, finance compliance.</span></div>
          <div class="step"><b>5. Repair</b><span>Human audit updates rows and records misses honestly.</span></div>
        </div>
      </div>
      <div>
        <h2>Runnable Proof</h2>
        <p>Run the research artifact locally:</p>
        <p><code>python3 src/research_agent.py --build</code></p>
        <p>Run URL verification loop:</p>
        <p><code>python3 src/research_agent.py --check-links --limit 40</code></p>
        <p>Composio-oriented output: <code>data/toolkit_queue.json</code> ranks P0 easy wins, P1 gated builds, and P3 outreach targets for toolkit planning.</p>
      </div>
    </section>

    <section>
      <h2>Verification</h2>
      <p>Manual sample: {stats['audit_sample']} apps, {stats['audit_checked_total']} field checks. First pass supported {stats['audit_first_total']}/{stats['audit_checked_total']} fields ({stats['audit_first_pct']}%). After critique + repair, supported fields rose to {stats['audit_final_total']}/{stats['audit_checked_total']} ({stats['audit_final_pct']}%). Remaining uncertainty is labeled Low/Medium instead of hidden.</p>
      <div class="table-wrap" style="max-height: 520px;">
        <table>
          <thead>
            <tr><th>Sampled app</th><th>First pass</th><th>Final</th><th>Delta</th><th>Miss or risk found</th><th>Repair</th></tr>
          </thead>
          <tbody>{build_audit_rows(audit)}</tbody>
        </table>
      </div>
    </section>

    <section>
      <h2>Full 100-App Matrix</h2>
      <p>Every row includes a verdict, the main blocker, confidence and evidence links. Use the filters to skim by category or buildability.</p>
      <div class="controls">
        <input id="search" type="search" placeholder="Search app, auth, category, verdict">
        <select id="category"><option value="">All categories</option></select>
        <select id="verdict"><option value="">All verdicts</option></select>
      </div>
      <div class="table-wrap">
        <table id="apps-table">
          <thead>
            <tr>
              <th>#</th>
              <th>App / one-line category</th>
              <th>Category</th>
              <th>Auth</th>
              <th>Access</th>
              <th>API surface + MCP</th>
              <th>Buildability</th>
              <th>Confidence + evidence</th>
            </tr>
          </thead>
          <tbody>{build_app_rows(apps)}</tbody>
        </table>
      </div>
    </section>

    <footer>
      Built from <code>data/apps_research.csv</code> and <code>data/manual_verification.csv</code>. Last generated {date.today().isoformat()}. The table intentionally includes failed/gated apps because those are product-ops findings, not mistakes.
    </footer>
  </main>

  <script>
    const rows = Array.from(document.querySelectorAll("#apps-table tbody tr"));
    const category = document.querySelector("#category");
    const verdict = document.querySelector("#verdict");
    const search = document.querySelector("#search");
    const categories = [...new Set(rows.map(row => row.dataset.category))].sort();
    const verdicts = [...new Set(rows.map(row => row.dataset.verdict))].sort();
    for (const value of categories) {{
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      category.appendChild(option);
    }}
    for (const value of verdicts) {{
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      verdict.appendChild(option);
    }}
    function filterRows() {{
      const q = search.value.trim().toLowerCase();
      for (const row of rows) {{
        const matchesCategory = !category.value || row.dataset.category === category.value;
        const matchesVerdict = !verdict.value || row.dataset.verdict === verdict.value;
        const matchesSearch = !q || row.dataset.search.includes(q);
        row.style.display = matchesCategory && matchesVerdict && matchesSearch ? "" : "none";
      }}
    }}
    category.addEventListener("change", filterRows);
    verdict.addEventListener("change", filterRows);
    search.addEventListener("input", filterRows);
  </script>
</body>
</html>
"""
    HTML_PATH.write_text(html_doc, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and verify the Composio research-agent case study.")
    parser.add_argument("--build", action="store_true", help="Generate index.html and toolkit queue.")
    parser.add_argument("--check-links", action="store_true", help="Fetch evidence URLs and write data/evidence_status.json.")
    parser.add_argument("--limit", type=int, default=None, help="Limit URL checks for fast smoke runs.")
    args = parser.parse_args(argv)

    apps = read_pipe_csv(DATA_PATH)
    audit = read_pipe_csv(AUDIT_PATH)

    if len(apps) != 100:
        print(f"Expected 100 apps; found {len(apps)}", file=sys.stderr)
        return 1

    stats = compute_stats(apps, audit)

    if args.build or not args.check_links:
        write_toolkit_queue(apps)
        build_html(apps, audit, stats)
        print(f"Generated {HTML_PATH.relative_to(ROOT)}")
        print(f"Generated {QUEUE_PATH.relative_to(ROOT)}")
        print(
            "Summary: "
            f"{stats['verdict_counts']['Ready today']} ready today, "
            f"{stats['verdict_counts']['Ready after gate']} ready after gate, "
            f"{stats['verdict_counts']['Outreach needed']} outreach, "
            f"{stats['verdict_counts']['No public build path']} no public path, "
            f"{stats['verdict_counts']['Needs investigation']} investigate."
        )

    if args.check_links:
        report = check_evidence_links(apps, limit=args.limit)
        print(
            f"Checked {report['checked']} evidence URLs: "
            f"{report['ok']} reachable, {len(report['failed'])} failed or blocked."
        )
        if report["failed"]:
            print(f"Wrote details to {EVIDENCE_STATUS_PATH.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
