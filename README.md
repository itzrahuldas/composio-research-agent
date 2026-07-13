# Composio Research Agent

Take-home assignment for researching 100 requested apps as potential Composio toolkits.

The deliverable is a single self-contained case study page: [`index.html`](index.html).

Short PDF summary: [`docs/composio_research_agent_summary.pdf`](docs/composio_research_agent_summary.pdf)

Live page: https://itzrahuldas.github.io/composio-research-agent/

Source repo: https://github.com/itzrahuldas/composio-research-agent

## What This Repo Contains

- `data/apps_research.csv` - final 100-app research matrix with category, auth, access gate, API surface, MCP signal, buildability verdict, confidence and evidence URLs.
- `data/app_seeds.csv` - the 100-app seed list used by the live research agent.
- `src/agent.py` - actual web research agent: search/fetch/extract/critic loop with optional OpenAI and Composio hooks.
- `data/agent_runs/sample_5.json` - real first-pass agent output for a 5-app sample.
- `data/manual_verification.csv` - human verification sample showing first-pass misses and final repairs.
- `data/verification_field_audit.csv` - exact field-level misses and repairs with evidence links.
- `src/research_agent.py` - dependency-light research artifact generator and evidence URL checker.
- `data/toolkit_queue.json` - generated Composio-style priority queue for what to build first.
- `index.html` - generated two-minute case-study page.
- `docs/composio_research_agent_summary.pdf` - 5-page submission summary PDF.

## Run It

```bash
python3 src/research_agent.py --build
```

Run the actual research agent on a small sample:

```bash
python3 src/agent.py --limit 5 --max-pages 3 \
  --output data/agent_runs/sample_5.json \
  --csv-output data/agent_runs/sample_5.csv
```

> **Note on Scale**: The live agent is typically validated on 5-20 app samples to avoid duckduckgo rate limits and aggressive scraper blocking. The full 100-row matrix combines agent first-pass extraction with human-verified repairs (documented in `verification_field_audit.csv`).

Optional AI/tool path:

```bash
OPENAI_API_KEY=... python3 src/agent.py --limit 5 --use-llm
COMPOSIO_API_KEY=... python3 src/agent.py --limit 5 --use-composio
```

Optional evidence smoke check:

```bash
python3 src/research_agent.py --check-links --limit 40
```

Without `--limit`, the checker attempts every unique evidence URL. Some vendor docs block scripted requests, so the verification page focuses on human-checked field accuracy and keeps blocked links visible instead of pretending they passed.

## Agent Design

The workflow is a loop:

1. Seed the 100 app list with official doc hints.
2. Fetch/search likely docs and auth pages.
3. Extract structured fields: auth, access, API surface, MCP, verdict and blocker.
4. Critique likely hallucination zones: hidden APIs, partner portals, ads review, fintech compliance and enterprise commerce.
5. Repair rows with human-verified evidence and record misses in `data/manual_verification.csv`.

`src/agent.py` is the live first-pass researcher. `src/research_agent.py` renders the repaired, reviewer-facing case study from the verified data layer.

## Key Findings

- 66 apps are ready to build today with self-serve docs/credentials.
- 20 more are buildable after normal review, plan, admin or compliance gates.
- The hardest blockers cluster in ads/social review, finance/compliance, enterprise commerce and apps with no public API docs.
- OAuth2 dominates multi-user SaaS, while API keys/bearer tokens dominate data, scraping, email and internal-platform APIs.
- Official MCP/agent-ready surfaces now exist for several high-value targets, including Shopify, GitHub, Vercel, Cloudflare, Supabase, Linear, Otter and Consensus.
- The HTML now includes generated charts for verdict distribution, auth method frequency, blocker families and category-by-verdict buildability.

## Verification Result

Manual verification sampled 27 apps and 135 field checks across all 10 categories, with at least two apps per category and extra coverage on high-risk rows.

- First pass: 90/135 supported fields.
- Final repaired pass: 126/135 supported fields.
- Remaining uncertainty is explicitly labeled `Low` or `Medium` confidence in the HTML table.

## Notes on Honesty

Rows like Pumble, FanBasis, Waterfall.io, Paygent Connect, PitchBook and NotebookLM are intentionally not forced into "ready" status. If public docs or self-serve credentials were not verified, the blocker is the finding.
