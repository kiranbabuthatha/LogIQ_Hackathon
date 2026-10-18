# LogIQ — first-run guide

Ask questions about system logs in plain language. Three roles: **Engineer**, **Team lead**, **Manager**.
The database searches and counts; the AI explains the evidence. Every number cites the SQL behind it.

**Prerequisite:** Docker. *(Everything else is automatic.)*

**How it works:** your question → the agent picks from 19 tools → parameterised SQL runs in
SereneDB over the BGL + OpenStack corpus (≈5M lines) → limited evidence returns → the agent
answers in your role's language, citing the exact query behind every number.

## Run it (3 commands)

```bash
git clone -b feature_docker https://github.com/kiranbabuthatha/LogIQ_Hackathon.git && cd LogIQ_Hackathon
cp .env.example .env          # add ONE model key (NVIDIA_API_KEY / ANTHROPIC_API_KEY / LOGIQ_...)
docker compose up --build
```

First run ≈ 10 min (downloads 770 MB of logs, parses, loads). Watch progress: `docker compose logs -f init`

## Use it

| What | Where |
|---|---|
| **Web UI** | http://localhost:8000 — pick a role, ask a question |
| **MCP** (Claude Code) | `claude mcp add --transport http logiq http://localhost:8001/mcp` |
| **MCP** (OpenCode) | `opencode mcp add logiq --global --url http://localhost:8001/mcp` |
| **MCP** (Codex) | `codex mcp add logiq --url http://localhost:8001/mcp` |
| **MCP** (Cursor) | `mcp.json`: `{"mcpServers": {"logiq": {"url": "http://localhost:8001/mcp"}}}` |

19 tools are served at the MCP URL — search, counts, trends, incident counting, correlations, chains.

## Example questions

- *Engineer:* "Investigate the InvalidDiskInfo errors in OpenStack — what failed and what should I check?"
- *Team lead:* "Which problems recur most in BGL and which team owns them?"
- *Manager:* "What should we prioritise across both datasets?"

## Every answer

Is structured for its role, shows a Level-0 summary first, and ends with a **SQL evidence** section —
open any `[Q#]` to see the exact query behind a number.

## Stop / reset

```bash
docker compose down        # stop (data kept)
docker compose down -v     # stop + wipe (next run reloads automatically)
```

## If a port is busy (7890 / 8000 / 8001)

| OS | Kill the process holding it (example: 8001) |
|---|---|
| Linux / WSL | `fuser -k 8001/tcp` |
| macOS | `lsof -ti:8001 \| xargs kill -9` |
| Windows (PowerShell) | `Get-NetTCPConnection -LocalPort 8001 -State Listen \| % { Stop-Process -Id $_.OwningProcess -Force }` |

## Known limitations

- Log-line counts are **not** incident counts (the incident-counting tool approximates by time buckets).
- The bundled datasets cover different eras and spans (BGL: 214 days in 2005; OpenStack: 3 days in 2017) —
  cross-dataset comparisons are limited, and short windows weaken correlation analysis.
- Template IDs are assigned by this project's parser (first-appearance order), not Loghub's official catalog.
- The logs contain **no downtime, cost or user-impact data** — priorities are severity × frequency × trend only.
- The web UI keeps conversations in memory; a restart clears them.

---

Full documentation: [README.md](README.md) · tool reference: [FUNCTIONS.md](FUNCTIONS.md) ·
worked example: [worked_investigation/submission_report_illustrated.md](worked_investigation/submission_report_illustrated.md)
