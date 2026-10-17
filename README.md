# LogIQ: the right log insights for the right people

LogIQ turns system logs into answers for three people who read the same logs for different reasons:

| Role | Asks | Gets |
|---|---|---|
| **Engineer** | "What failed, and why?" | The failing log lines, what happened just before and after, a possible cause (labelled as a hypothesis) and the next check to run |
| **Team lead** | "What keeps coming back?" | Recurring problems with counts, trends and owning teams, and what to investigate first |
| **Manager** | "What should we prioritise?" | At most five ranked risks, each with an owner and an action, without invented business impact |

The logs (BGL supercomputer and OpenStack cloud, from Loghub) are far too large to paste into an AI prompt. So
**SereneDB does the searching and counting**, and an **AI agent chooses which queries to run and explains the results**.
Every number in an answer links back to the SQL query that produced it.

---

## How it works

LogIQ has two stages: data is **loaded once**, then **every question** goes through the same agent loop.

### Stage 1: Load and index the logs (once)

```
 Loghub structured CSVs          sql/schema.sql (run by load.py)                          ready to query
┌──────────────────────┐        ┌───────────────────────────────────────────────┐        ┌──────────────────┐
│ BGL.log_structured   │──read──▶ bgl, openstack      raw tables                  │        │ events_v         │
│ OpenStack…structured │  _csv  │        │                                      │        │  every line +    │
└──────────────────────┘        │        ▼  unify columns, add severity 0-3     │        │  team +          │
                                │ events (dataset, ts, source, component,       │───────▶│  is_problem      │
 team-ownership table           │         level, severity, is_alert,            │        │                  │
┌──────────────────────┐        │         template_id, content)                 │        │ events_idx       │
│ ownership            │────────▶ component_team  (longest prefix → team)      │        │  full-text index │
│ (component → team)   │        │ events_idx      (inverted index on content)   │        │  (BM25 ranking)  │
└──────────────────────┘        └───────────────────────────────────────────────┘        └──────────────────┘
```

1. **Read** the raw CSVs straight into SereneDB with `read_csv` (from a URL or a server path).
2. **Unify** both datasets into one `events` table with shared columns, and map each level to a **severity** (3 = FATAL/SEVERE, 2 = ERROR, 1 = WARNING, 0 = INFO).
3. **Attach owners.** The `component_team` view gives every component an owning team from the `ownership` table, by longest matching prefix (for example `nova.compute.manager` → `compute-core`).
4. **Mark problems.** The `events_v` view adds `team` and `is_problem` (severity ≥ WARNING, or a BGL expert alert label).
5. **Index the text.** `events_idx` is an inverted full-text index over the log message, lower-cased, normalised and stemmed, so searches use `@@` and rank results by BM25.

### Stage 2: Answer a question (every time)

```
   ① question + role
          │
          ▼
 ┌──────────────────┐   ② picks a tool    ┌───────────────────┐   ③ parameterised SQL   ┌─────────────┐
 │    AI agent      │────────────────────▶│  tools.py         │────────────────────────▶│  SereneDB   │
 │  (role prompt +  │                     │  11 tools, e.g.   │                         │  search &   │
 │   evidence rules)│◀────────────────────│  search_logs,     │◀────────────────────────│  aggregate  │
 └──────────────────┘   ④ evidence        │  risk_ranking     │   limited rows + counts └─────────────┘
          │               envelope [Q1]   └───────────────────┘
          │  ⑤ repeat ②–④ until it has enough evidence
          ▼
   ⑥ answer with [Q#] citations ──▶ web UI: answer on the left, every query on the right
```

1. **Question and role.** You pick a role and ask a question in plain language. The role selects a prompt that sets which questions matter, what evidence to collect and what kind of action to recommend.
2. **The agent picks a tool.** It reads the 11 tool descriptions and calls one with arguments, for example `search_logs(text="Link has been severed", dataset="bgl", start="2005-09-01")`. It never writes SQL.
3. **SereneDB does the work.** The tool runs fixed, parameterised SQL: full-text search through the index, or `count(*)` / `GROUP BY` aggregations over the whole corpus. Only a limited number of rows come back, but totals are always computed in full.
4. **An evidence envelope comes back** and gets an id (`Q1`, `Q2`, …). It contains the scope, the filters, every SQL statement with its parameters, rows returned, a `truncated` flag, the results and caveat notes.
5. **The agent repeats** steps 2–4, usually 2–6 times. For example: search → investigate one line → look at what preceded it → check the template's history.
6. **The answer** follows a role-specific structure. Every number cites its evidence (`[Q2]`), hypotheses are labelled, and unknowns are stated. Counts are log lines, not incidents, and nothing about downtime or cost is invented.

### A real run, traced

The engineer question *"What is behind the 'Link has been severed' errors in BGL after September 2005?"* produced this trace:

| Step | Tool call | What the database returned |
|---|---|---|
| Q1 | `corpus_overview()` | Dataset sizes, date ranges and team names |
| Q2 | `search_logs("Link has been severed", dataset=bgl, start=2005-09-01)` | **16** matching lines (exact count), 2005-09-11 → 2005-12-14 |
| Q3 | `investigate_event(bgl, line 1399)` | The failing line (FATAL, APP, team job-runtime), its neighbours, same-source problems |
| Q4 | `preceding_patterns(bgl, 1399, 30 min)` | No log lines in the 30 minutes before it |
| Q5 | `template_history(bgl, E29)` | The same message on **9** nodes over **8** days |

**Answer:** every failure points at one peer address, `172.16.96.116`. The hypothesis is a service-side or network fault rather than many failing nodes. The next check is that node's logs and the network path to it.

The same pipeline answers a team lead with `recurring_problems` + `problem_trend`, and a manager with `risk_ranking`.

---

## How to use it

### 1. Install

Requirements: Python 3.10+, the connection string of a SereneDB instance, and credentials for one model provider: an
Anthropic API key, an NVIDIA API key, or a local OpenAI-compatible server (see [Choosing the model provider](#choosing-the-model-provider)).

```bash
git clone <repo-url> LogIQ && cd LogIQ
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure `.env`

```bash
cp .env.example .env               # Windows: copy .env.example .env
```

Edit `.env`. You always need the database, plus **one** model provider:

```
SERENEDB_DSN=postgresql://user:password@host:port/dbname

# Option A: Claude (default)
ANTHROPIC_API_KEY=sk-ant-...

# Option B: NVIDIA
LOGIQ_PROVIDER=nvidia
NVIDIA_API_KEY=nvapi-...
```

`.env` is git-ignored. Never commit it.

### 3. Load the data

```bash
python load.py
```

This builds everything from Stage 1. It's safe to re-run, because it drops and rebuilds. By default it loads the Loghub **2,000-line
samples** from GitHub. To load the full datasets, point it at the structured CSVs; each path must be a URL or a path the SereneDB
*server* can read:

```bash
BGL_SOURCE=/data/BGL.log_structured.csv OPENSTACK_SOURCE=/data/OpenStack.log_structured.csv python load.py
```

The ownership rows in [sql/schema.sql](sql/schema.sql) are **sample data**. Replace them with the organisers' team-ownership
table, then run `python load.py` again.

### 4. Start the web app

```bash
uvicorn app:app --port 8000
```

Open **http://127.0.0.1:8000**. The header shows the loaded data (lines, problem lines, date range per dataset) and the
agent status. **Agent ready · <model>** means it's configured. If you see **Agent offline**, the banner says which setting is missing.

### 5. Ask a question (tab: *Ask the agent*)

1. **Pick a role card:** Engineer, Team lead or Manager. Each card shows what that role gets.
2. **Ask:** click one of the example questions, or type your own and press **Ask** (or Ctrl/⌘ + Enter).
3. **Watch it work.** The status line shows progress, and each tool call appears in the **Evidence trail** on the right as it runs.
4. **Read the answer** on the left. It's structured for the role, for example *What failed → Evidence → Possible cause → Next check → Unknowns* for an engineer.
5. **Check any number.** Click a **Q2** chip in the answer to jump to that tool call. Open **SQL 1, SQL 2…** to see the exact query and parameters. A **truncated** badge means only the first rows are shown; the totals are still exact. Yellow notes are caveats the answer must respect.
6. **Follow up.** Ask another question in the same conversation, for example *"Now check the 60 minutes before line 1399"*. Press **New** to start fresh, or switch role.

Example questions:

| Role | Try asking |
|---|---|
| Engineer | *What is behind the "Link has been severed" errors in BGL after September 2005?*<br>*Investigate the data TLB error interrupts on BGL: what failed and what should I check?*<br>*Why does OpenStack log "Unknown base file" warnings, and what happened around them?* |
| Team lead | *Which problems recur most in BGL over the whole period, and which teams own them?*<br>*Which teams had the most problem lines between 2005-10-01 and 2006-01-01, and is it getting worse?*<br>*Compare June–August with September–December 2005 by team.* |
| Manager | *What should we prioritise across BGL and OpenStack, and who should own each item?*<br>*Give me the top 3 risks in BGL for Q4 2005 and the action for each.*<br>*Is anything in OpenStack worth escalating?* |

Tips for good answers:
- **Name a period** (`after 2005-09-01`, `between 2005-10-01 and 2006-01-01`); without one, the whole corpus is used and the answer says so.
- **Name the dataset** (BGL or OpenStack) when you mean one.
- For engineers, **quote the error text** you saw; it goes straight into full-text search.

### 6. Run tools yourself (tab: *Explore tools*)

1. Choose a tool from the list; its description explains what it does.
2. Fill in the form. Fields marked `*` are required, and the rest are optional filters.
3. Press **Run**. You get the same evidence card the agent sees: scope, SQL, parameters, row counts and results.

This tab needs no model or API key. It's useful for checking an agent's claim, or exploring the data directly.

### 7. See the whole corpus (tab: *Corpus & teams*)

Whole-corpus counts calculated in SereneDB: lines, problem lines, alerts and time span per dataset, problem lines per team,
and lines per level.

### Other ways to use it

**From Claude Code or Claude Desktop (MCP).** The same 11 tools, with Claude as the agent:

```bash
claude mcp add logiq -- "$PWD/.venv/bin/python" "$PWD/mcp_server.py"
```

For Claude Desktop, add this to `claude_desktop_config.json`:

```json
{"mcpServers": {"logiq": {"command": "/abs/path/LogIQ/.venv/bin/python", "args": ["/abs/path/LogIQ/mcp_server.py"]}}}
```

Then ask, for example: *"Using logiq, which recurring problems in BGL should the job-runtime team look at first?"*

**From the terminal.** One question, with the tool calls and answer printed:

```bash
python agent.py engineer 'What is behind the "Link has been severed" errors in BGL?'
python agent.py team_lead "Which problems recur most in BGL and who owns them?"
python agent.py manager "What should we prioritise across BGL and OpenStack?"
```

**Generate the worked investigation.** One question per role through the agent, saved with every tool call, SQL statement and answer:

```bash
python worked_investigation.py      # writes WORKED_INVESTIGATION.md
```

### Choosing the model provider

`LOGIQ_PROVIDER` in `.env` picks the model provider, and `LOGIQ_MODEL` overrides its default model. Restart the server after
changing `.env`. Only `agent.py` depends on the provider: tools, UI and evidence trail are identical.

| `LOGIQ_PROVIDER` | Credentials | Default model | Notes |
|---|---|---|---|
| `anthropic` (default) | **One** of `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` | `claude-opus-5-5` | Adaptive thinking, `LOGIQ_EFFORT` (default `medium`), prompt caching, server-side refusal fallback. An auth token also gets the `oauth-2025-04-20` beta header, and `ANTHROPIC_BASE_URL` can point at a gateway. Setting both credentials makes the API reject requests. |
| `nvidia` | `NVIDIA_API_KEY` (from build.nvidia.com) | `nvidia/nemotron-3-super-120b-a12b` | NVIDIA's OpenAI-compatible endpoint. The model must support tool calling. Alternatives: `moonshotai/kimi-k2.6`, `z-ai/glm-5.3`, `deepseek-ai/deepseek-v4.1-flash`, `openai/gpt-oss-20b`. Current list: `curl -s https://integrate.api.nvidia.com/v1/models`. |
| `openai` | `LOGIQ_BASE_URL`, plus `LOGIQ_API_KEY` if the server needs one | none: set `LOGIQ_MODEL` | Any OpenAI-compatible server, e.g. Ollama (`http://localhost:11434/v1`), LM Studio or vLLM. |

You can leave unused keys in `.env`; only the selected provider's credentials are read. Open models are usually slower and less
consistent at tool selection and citation than Claude, so check their answers against the evidence trail.

---

## Reference

### Tools

There are 11 parameterised tools. The agent chooses them from their descriptions. Full inputs and outputs are in [FUNCTIONS.md](FUNCTIONS.md).

| Tool | Kind | Purpose |
|---|---|---|
| `corpus_overview` | aggregation | Whole-corpus totals per dataset, problems per team, lines per level |
| `search_logs` | full-text search | BM25-ranked search with time, dataset, team, component and level filters; exact total counts |
| `get_nearby_events` | retrieval | Lines just before and after an event, by log order or a ±minutes window (optionally same source) |
| `investigate_event` | retrieval + aggregation | Failing line, owner, context, recurrence, same-source problems, candidate cause, next checks |
| `preceding_patterns` | aggregation | Templates over-represented before an event (lift against the dataset baseline) |
| `template_history` | aggregation | Whole-dataset history of one template |
| `recurring_problems` | aggregation | Templates that recur in a period: lines, active days/hours, sources, team |
| `count_problems` | aggregation | Counts grouped by team, component, template, level, alert type, source or dataset |
| `problem_trend` | aggregation | Problem lines per hour, day, week or month, split by team, component or dataset |
| `compare_periods` | aggregation | Two periods side by side: new, gone, rising, falling |
| `risk_ranking` | aggregation | Ranked risks by pattern or by team, with share, spread and trend |

### How the three roles differ

All three roles use the same agent, tools and data. The role prompt changes the questions asked, the evidence gathered and the action recommended:

| | Engineer | Team lead | Manager |
|---|---|---|---|
| Question | What failed, where, what happened around it, why? | What recurs, how often, who owns it, is it changing? | What deserves attention first, and who acts? |
| Evidence | Full-text hits with exact counts, surrounding lines, preceding patterns, template history | Recurring patterns, counts by team, weekly trend, period comparison | Risk ranking by pattern and team, whole-corpus totals |
| Output | Key log lines verbatim, labelled hypothesis, a concrete diagnostic check | Table of patterns with owner, lines, active days, trend; what to investigate first and why | At most five ranked risks, each with evidence, owner and action; what the data does not show |

Grounding rules in every role's prompt:
- Every number cites a `[Q#]` evidence id.
- Totals come from `count(*)` queries, never from a truncated sample.
- Counts are reported as log lines, not incidents.
- Hypotheses are labelled, and unknowns are stated.
- Answers never mention downtime, cost or customer impact, because the logs don't contain them.

### Data model

| Object | What it is |
|---|---|
| `bgl`, `openstack` | Raw Loghub structured CSVs |
| `ownership` | Team-ownership table: `(dataset, component_prefix, team)` |
| `events` | Both datasets in one table: `ts`, `source`, `component`, `level`, `severity` 0-3, `is_alert`, `alert_type`, `template_id`, `template`, `content` |
| `component_team` | Each component's owning team (longest prefix match, `unassigned` if none) |
| `events_v` | `events` + `team` + `is_problem` |
| `events_idx` | Inverted full-text index over `content`, queried with `@@`, ranked with BM25 |

### Project layout

```
app.py                  web server: /api/ask streams agent events, /api/tool/<name> runs one tool
static/index.html       web UI (no build step)
agent.py                agent loop (Claude or OpenAI-compatible) and the three role prompts
tools.py                the 11 SereneDB tools and their descriptions
mcp_server.py           MCP server exposing the same tools
load.py, sql/schema.sql loader and schema (events, ownership, full-text index)
worked_investigation.py writes WORKED_INVESTIGATION.md from real agent runs
FUNCTIONS.md            tool reference
agent_tools.py          small CLI for raw-table inspection (not used by the agent)
```

### Known limitations

- **Sample data by default.** `load.py` loads the 2k-line samples unless you set `BGL_SOURCE` / `OPENSTACK_SOURCE`, so counts and trends describe the sample. Every result states its scope.
- **Sample ownership table.** Team names come from the sample rows in `sql/schema.sql` until the organisers' table replaces them.
- **Heuristics, not root causes.** `candidate_cause` (the nearest earlier problem on the same source, or any source within 1 hour), `lift`, trend labels (a 1.5× change between the halves of the period; "single burst" when every line falls within 5% of it) and `priority_score` are simple, documented rules. They point to where to look; they don't prove a cause.
- **Lines are not incidents.** No tool groups lines into incidents. `active_hours` and `active_days` are rough proxies for separate episodes.
- **Timestamps** are as recorded in each log, with no timezone conversion. BGL and OpenStack cover different years, so trends are computed per dataset.
- **Web UI** has no login, and keeps conversations in memory, so a restart clears them. Run it locally or behind your own access control.
- **Not implemented:** on-demand indexing, the OpenTelemetry Parquet data, and row-level security per role.

### Data and citation

Loghub (Zhu et al., *Loghub: A Large Collection of System Log Datasets for AI-driven Log Analytics*, ISSRE 2023). For BGL, also
Oliner & Stearley, *What Supercomputers Say: A Study of Five System Logs*, DSN 2007.
