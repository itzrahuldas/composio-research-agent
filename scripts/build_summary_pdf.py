#!/usr/bin/env python3
"""Build the 5-page PDF summary without third-party dependencies.

The repo's main deliverable is the live HTML case study. This script creates a
short submission PDF for reviewers who want a compact offline artifact.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "composio_research_agent_summary.pdf"

PAGE_W = 612
PAGE_H = 792
MARGIN = 54


def pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


@dataclass
class Page:
    commands: list[str]

    def __init__(self) -> None:
        self.commands = []

    def text(self, x: float, y: float, value: str, size: int = 11, font: str = "F1", color: str = "0 0 0") -> None:
        self.commands.append(f"{color} rg BT /{font} {size} Tf {x:.1f} {y:.1f} Td ({pdf_escape(value)}) Tj ET")

    def line(self, x1: float, y1: float, x2: float, y2: float, color: str = "0.80 0.84 0.90", width: float = 1) -> None:
        self.commands.append(f"{color} RG {width:.1f} w {x1:.1f} {y1:.1f} m {x2:.1f} {y2:.1f} l S")

    def rect(self, x: float, y: float, w: float, h: float, fill: str = "0.96 0.97 0.99", stroke: str = "0.80 0.84 0.90") -> None:
        self.commands.append(f"{fill} rg {stroke} RG {x:.1f} {y:.1f} {w:.1f} {h:.1f} re B")

    def fill_rect(self, x: float, y: float, w: float, h: float, fill: str) -> None:
        self.commands.append(f"{fill} rg {x:.1f} {y:.1f} {w:.1f} {h:.1f} re f")


class PDF:
    def __init__(self) -> None:
        self.pages: list[Page] = []

    def new_page(self) -> Page:
        page = Page()
        self.pages.append(page)
        return page

    def write(self, path: Path) -> None:
        objects: list[str] = []
        catalog_id = 1
        pages_id = 2
        font_regular_id = 3
        font_bold_id = 4
        next_id = 5
        page_ids = []
        content_ids = []

        for page in self.pages:
            page_id = next_id
            content_id = next_id + 1
            next_id += 2
            page_ids.append(page_id)
            content_ids.append(content_id)
            content = "\n".join(page.commands)
            objects.append(f"{page_id} 0 obj\n<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] /Resources << /Font << /F1 {font_regular_id} 0 R /F2 {font_bold_id} 0 R >> >> /Contents {content_id} 0 R >>\nendobj\n")
            objects.append(f"{content_id} 0 obj\n<< /Length {len(content.encode('latin-1', errors='replace'))} >>\nstream\n{content}\nendstream\nendobj\n")

        kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
        base_objects = [
            f"{catalog_id} 0 obj\n<< /Type /Catalog /Pages {pages_id} 0 R >>\nendobj\n",
            f"{pages_id} 0 obj\n<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>\nendobj\n",
            f"{font_regular_id} 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
            f"{font_bold_id} 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>\nendobj\n",
        ]
        all_objects = base_objects + objects

        out = ["%PDF-1.4\n%\xE2\xE3\xCF\xD3\n"]
        offsets = [0]
        for obj in all_objects:
            offsets.append(sum(len(chunk.encode("latin-1", errors="replace")) for chunk in out))
            out.append(obj)
        xref = sum(len(chunk.encode("latin-1", errors="replace")) for chunk in out)
        out.append(f"xref\n0 {len(all_objects) + 1}\n0000000000 65535 f \n")
        for offset in offsets[1:]:
            out.append(f"{offset:010d} 00000 n \n")
        out.append(f"trailer\n<< /Size {len(all_objects) + 1} /Root {catalog_id} 0 R >>\nstartxref\n{xref}\n%%EOF\n")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes("".join(out).encode("latin-1", errors="replace"))


def wrap(text: str, width: int = 82) -> list[str]:
    return textwrap.wrap(text, width=width, break_long_words=False)


def header(page: Page, title: str, number: int) -> None:
    page.text(MARGIN, 748, title, 16, "F2", "0.08 0.13 0.20")
    page.text(520, 748, f"{number}/5", 9, "F1", "0.42 0.47 0.55")
    page.line(MARGIN, 735, PAGE_W - MARGIN, 735)


def bullet(page: Page, x: float, y: float, text: str, size: int = 10, color: str = "0.18 0.22 0.28") -> float:
    lines = wrap(text, 76)
    page.text(x, y, "-", size, "F2", color)
    page.text(x + 14, y, lines[0], size, "F1", color)
    y -= 14
    for line in lines[1:]:
        page.text(x + 14, y, line, size, "F1", color)
        y -= 14
    return y - 4


def label_value(page: Page, x: float, y: float, label: str, value: str) -> None:
    page.text(x, y + 28, value, 25, "F2", "0.14 0.39 0.82")
    for idx, line in enumerate(wrap(label, 22)):
        page.text(x, y + 10 - idx * 12, line, 9, "F1", "0.35 0.40 0.48")


def page1(pdf: PDF) -> None:
    p = pdf.new_page()
    p.fill_rect(0, 0, PAGE_W, PAGE_H, "0.98 0.99 1.00")
    p.text(MARGIN, 706, "Composio Research Agent", 31, "F2", "0.08 0.13 0.20")
    p.text(MARGIN, 672, "App Integration Buildability Study", 23, "F2", "0.14 0.39 0.82")
    p.text(MARGIN, 636, "Rahul Das  |  github.com/itzrahuldas/composio-research-agent", 11, "F1", "0.25 0.30 0.38")
    p.text(MARGIN, 616, "Live page: https://itzrahuldas.github.io/composio-research-agent/", 11, "F1", "0.25 0.30 0.38")
    p.line(MARGIN, 590, PAGE_W - MARGIN, 590, "0.68 0.75 0.86", 1.5)
    summary = "A 2-layer agentic research pipeline that analyzed 100 apps across 10 categories to identify buildability status for Composio tool integrations."
    y = 556
    for line in wrap(summary, 76):
        p.text(MARGIN, y, line, 13, "F1", "0.16 0.20 0.27")
        y -= 18

    cards = [
        ("100", "apps researched"),
        ("66", "ready-to-build today"),
        ("93.3%", "field verification accuracy after critique + repair"),
        ("20", "app live agent run with critic flags"),
    ]
    x_positions = [MARGIN, 185, 316, 455]
    for x, (value, label) in zip(x_positions, cards):
        p.rect(x, 392, 112, 94)
        label_value(p, x + 13, 414, label, value)
    p.text(MARGIN, 336, "Primary outcome", 13, "F2", "0.08 0.13 0.20")
    y = 312
    for item in [
        "Build self-serve SaaS and developer platforms first.",
        "Queue review-gated ads, fintech and enterprise commerce next.",
        "Route hidden/private data products to outreach instead of pretending they are buildable.",
    ]:
        y = bullet(p, MARGIN, y, item, 11)


def page2(pdf: PDF) -> None:
    p = pdf.new_page()
    header(p, "Agent Architecture", 2)
    y = 694
    p.text(MARGIN, y, "2-layer system", 13, "F2", "0.08 0.13 0.20")
    y -= 26
    boxes = [
        (70, 610, 150, 42, "app_seeds.csv"),
        (258, 610, 150, 42, "agent.py"),
        (445, 610, 110, 42, "agent_runs/"),
        (70, 486, 150, 42, "apps_research.csv"),
        (258, 486, 150, 42, "research_agent.py"),
        (445, 486, 110, 42, "index.html"),
    ]
    for x, yy, w, h, text in boxes:
        p.rect(x, yy, w, h)
        p.text(x + 12, yy + 17, text, 11, "F2", "0.08 0.13 0.20")
    for x1, y1, x2, y2 in [(220, 631, 258, 631), (408, 631, 445, 631), (145, 610, 145, 528), (220, 507, 258, 507), (408, 507, 445, 507)]:
        p.line(x1, y1, x2, y2, "0.14 0.39 0.82", 1.5)
    p.text(265, 662, "Search -> Fetch -> Extract -> Critic", 9, "F1", "0.35 0.40 0.48")
    p.text(248, 458, "Chart generation + HTML rendering", 9, "F1", "0.35 0.40 0.48")

    y = 410
    p.text(MARGIN, y, "Key components", 13, "F2", "0.08 0.13 0.20")
    y -= 28
    for item in [
        "src/agent.py: web search, HTML parsing, heuristic extraction and critic flags.",
        "src/research_agent.py: computes patterns, renders charts, verification tables and the live HTML page.",
        "Composio SDK hook: optional --use-composio path for tool-mediated search when COMPOSIO_API_KEY is set.",
        "OpenAI LLM path: optional --use-llm path for structured extraction when OPENAI_API_KEY is set.",
        "Final matrix: human-repaired, evidence-backed rows in data/apps_research.csv.",
    ]:
        y = bullet(p, MARGIN, y, item)


def page3(pdf: PDF) -> None:
    p = pdf.new_page()
    header(p, "Key Findings / Insights", 3)
    y = 700
    p.text(MARGIN, y, "Verdict breakdown", 13, "F2", "0.08 0.13 0.20")
    y -= 28
    rows = [
        ("Ready today", "66"),
        ("Ready after gate", "20"),
        ("Outreach needed", "9"),
        ("No public build path", "3"),
        ("Investigate further", "2"),
    ]
    p.rect(MARGIN, y - 130, 250, 148, "0.98 0.99 1.00")
    p.text(MARGIN + 16, y - 2, "Status", 10, "F2", "0.08 0.13 0.20")
    p.text(MARGIN + 190, y - 2, "Count", 10, "F2", "0.08 0.13 0.20")
    yy = y - 26
    for status, count in rows:
        p.text(MARGIN + 16, yy, status, 10, "F1", "0.18 0.22 0.28")
        p.text(MARGIN + 198, yy, count, 10, "F2", "0.14 0.39 0.82")
        yy -= 23

    y = 514
    p.text(MARGIN, y, "Top strategic insights", 13, "F2", "0.08 0.13 0.20")
    y -= 30
    insights = [
        "Where to build first: Productivity (10/10), Dev/Infra (9/10), CRM (9/10) have clear docs and reusable OAuth/token patterns.",
        "Where outreach matters: Fintech and Ecommerce have the densest blocked/gated rows; this is licensing/access friction, not pure engineering difficulty.",
        "MCP is a wedge: 19 apps show official MCP or agent-native signals, so Composio can wrap existing MCP surfaces first.",
        "Auth x buildability: OAuth rows are about 92% buildable now/after normal gates; API-key rows are about 93%.",
    ]
    for item in insights:
        y = bullet(p, MARGIN, y, item, 10)


def page4(pdf: PDF) -> None:
    p = pdf.new_page()
    header(p, "Verification & Methodology", 4)
    y = 700
    p.text(MARGIN, y, "Human-in-the-loop process", 13, "F2", "0.08 0.13 0.20")
    y -= 30
    for item in [
        "First pass: live agent ran on 5-20 app samples per batch.",
        "27 apps manually sampled, stratified across all 10 categories.",
        "135 field checks performed across auth, access, surface, verdict and evidence.",
        "First pass: 90/135 fields supported (66.7%).",
        "After critique + repair: 126/135 fields supported (93.3%).",
        "Misses are documented in verification_field_audit.csv with evidence URLs.",
    ]:
        y = bullet(p, MARGIN, y, item, 10)

    p.rect(MARGIN, 310, PAGE_W - 2 * MARGIN, 142, "0.98 0.99 1.00")
    p.text(MARGIN + 18, 420, "Honest limitation", 13, "F2", "0.08 0.13 0.20")
    note = "DuckDuckGo rate-limits and many enterprise doc pages are JS-rendered, making raw HTML scraping noisy beyond roughly 20 apps. The final matrix combines agent extraction with human verification, which is a realistic production pattern for high-accuracy API research."
    yy = 392
    for line in wrap(note, 72):
        p.text(MARGIN + 18, yy, line, 10, "F1", "0.18 0.22 0.28")
        yy -= 14


def page5(pdf: PDF) -> None:
    p = pdf.new_page()
    header(p, "How to Run It", 5)
    commands = [
        "# Step 1: Install",
        "pip install -r requirements.txt",
        "",
        "# Step 2: Run live agent",
        "python src/agent.py --limit 20 --max-pages 3",
        "",
        "# Step 3: Build output",
        "python src/research_agent.py --build",
        "",
        "# Optional: With LLM extraction",
        "OPENAI_API_KEY=... python src/agent.py --use-llm --limit 5",
        "",
        "# Optional: With Composio",
        "COMPOSIO_API_KEY=... python src/agent.py --use-composio --limit 5",
        "",
        "# Tests",
        "python -m unittest tests/test_agent.py",
    ]
    p.rect(MARGIN, 330, PAGE_W - 2 * MARGIN, 360, "0.06 0.08 0.11", "0.06 0.08 0.11")
    y = 664
    for line in commands:
        color = "0.70 0.78 0.88" if line.startswith("#") else "0.92 0.95 0.98"
        p.text(MARGIN + 18, y, line, 9, "F1", color)
        y -= 18
    p.text(MARGIN, 270, "Links", 13, "F2", "0.08 0.13 0.20")
    p.text(MARGIN, 242, "GitHub: https://github.com/itzrahuldas/composio-research-agent", 10, "F1", "0.14 0.39 0.82")
    p.text(MARGIN, 222, "Live page: https://itzrahuldas.github.io/composio-research-agent/", 10, "F1", "0.14 0.39 0.82")


def main() -> None:
    pdf = PDF()
    page1(pdf)
    page2(pdf)
    page3(pdf)
    page4(pdf)
    page5(pdf)
    pdf.write(OUT)
    print(f"Wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
