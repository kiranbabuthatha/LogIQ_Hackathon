# LogIQ — first-run guide

Ask questions about system logs in plain language. Three roles: **Engineer**, **Team lead**, **Manager**.
The database searches and counts; the AI explains the evidence. Every number cites the SQL behind it.

**Prerequisite:** Docker. *(Everything else is automatic.)*

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

---

Full documentation: [README.md](README.md) · tool reference: [FUNCTIONS.md](FUNCTIONS.md) ·
worked example: [worked_investigation/submission_report_illustrated.md](worked_investigation/submission_report_illustrated.md)
