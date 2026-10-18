# LogIQ: the right log insights for the right people

LogIQ turns system logs into answers for three people who read the same logs for different reasons:

| Role | Asks | Gets |
|---|---|---|
| **Engineer** | "What failed, and why?" | The failing log lines, what happened just before and after, a possible cause (labelled as a hypothesis) and the next check to run |
| **Team lead** | "What keeps coming back?" | Recurring problems with counts, trends and owning teams, and what to investigate first |
| **Manager** | "What should we prioritise?" | At most five ranked risks, each with an owner and an action, without invented business impact |

The logs are the full Loghub **BGL** supercomputer log (4,747,963 lines over 215 days) and **OpenStack** cloud log (207,820
lines over 3 days), about 5 million lines in total, far too many to paste into an AI prompt. So **SereneDB does the searching
and counting**, and an **AI agent chooses which queries to run and explains the results**. Every number in an answer links
back to the SQL query that produced it.

---

## How it works

LogIQ has two stages: data is **prepared and loaded once**, then **every question** goes through the same agent loop.

### Stage 1: Prepare, load and index the logs (once)

```
 Zenodo archives            prepare_data.py              load.py + sql/schema.sql                       ready to query
┌────────────────┐        ┌─────────────────────┐      ┌──────────────────────────────────────────┐   ┌────────────────┐
│ BGL.zip        │download│ raw logs ─▶ parse   │ CSV  │ bgl, openstack   raw tables (COPY)       │   │ events_v       │
│  BGL.log       │──────▶ │ fields, mask values │─────▶│      │  unify columns, severity 0-3      │   │  every line +  │
│ OpenStack.tar  │extract │ ─▶ template + id    │      │      ▼                                   │──▶│  team +        │
│  3 raw logs    │        │ data/*_structured   │      │ events  (5.0M lines, both datasets)      │   │  is_problem    │
└────────────────┘        └─────────────────────┘      │ component_team  ownership → team         │   │ events_idx     │
 ownership table ─────────────────────────────────────▶│ events_idx      full-text index (BM25)   │   │  full-text     │
                                                       └──────────────────────────────────────────┘   └────────────────┘
```

1. **Download** `BGL.zip` and `OpenStack.tar.gz` from Zenodo, then extract them safely (absolute paths and `..` entries are rejected).
2. **Parse** the raw logs into the Loghub column layout. BGL lines are `Label Timestamp Date Node Time NodeRepeat Type Component Level Content`. OpenStack lines are `Logrecord Date Time Pid Level Component [ADDR] Content`; the three OpenStack files are merged in time order. Malformed lines are repaired rather than dropped: a missing NodeRepeat field, garbled text and binary bytes. All 4,747,963 + 207,820 lines are kept.
3. **Derive a template** for every line by masking variable parts (any token with a digit, paths, UUIDs) as `<*>`, keeping field names. For example, `generating core.254` becomes `generating <*>`, and `lr:00004ed0 cr:28244842` becomes `lr:<*> cr:<*>`. Lines with nothing constant left, such as BGL's kernel register dumps (`0:000008ce 1:00086000 …`), become `<numeric-only message>`. Each template gets an id (`E1`, `E2`, … in order of first appearance). That gives 695 BGL templates and 73 OpenStack templates.
4. **Load** the CSVs into SereneDB. `load.py` streams local files with `COPY … FROM STDIN`, so this works even when SereneDB runs in Docker or on another host.
5. **Unify** both datasets into one `events` table with shared columns, and map each level to a **severity** (3 = FATAL/SEVERE/FAILURE/CRITICAL, 2 = ERROR, 1 = WARNING, 0 = INFO).
6. **Attach owners.** The `component_team` view gives every component an owning team from the `ownership` table, by longest matching prefix (for example `nova.compute.manager` → `compute-core`).
7. **Mark problems.** The `events_v` view adds `team` and `is_problem` (severity ≥ WARNING, or a BGL expert alert label).
8. **Index the text.** `events_idx` is an inverted full-text index over the log message, lower-cased, normalised and stemmed, so searches use `@@` and rank results by BM25.

Timings on a laptop: preparing takes about 25 s, and loading and indexing all 4.96M lines takes about 12 s. Every tool then answers in under 1 s on the full corpus.

| After loading | BGL | OpenStack |
|---|---|---|
| Lines | 4,747,963 | 207,820 |
| Problem lines | 1,012,140 | 3,314 |
| Time span | 2005-06-03 → 2006-01-04 | 2017-05-14 → 2017-05-17 |
| Templates | 695 | 73 |
| Levels | INFO, FATAL, ERROR, WARNING, SEVERE, FAILURE | INFO, WARNING, ERROR, CRITICAL |

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

The engineer question *"What is behind the 'Link has been severed' errors in BGL after September 2005?"*, on the full data,
produced this trace. Every number below was re-checked against the database:

| Step | Tool call | What the database returned |
|---|---|---|
| Q1 | `corpus_overview()` | Dataset sizes, date ranges and team names |
| Q2 | `search_logs('"Link has been severed"', dataset=bgl, start=2005-09-01)` | **41,837** matching lines (exact `count(*)`), 2005-09-02 → 2006-01-03; 20 returned (truncated) |
| Q3 | `investigate_event(bgl, line 3326688)` | The failing line: FATAL, APP, team **job-runtime**, alert type APPSEV, peer `172.16.96.116`. The same template has 15,273 lines on 1,024 nodes over 27 days. The 10 lines after it come from 10 different nodes, half in the same second. |
| Q4 | `preceding_patterns(bgl, 3326688, 30 min)` | Templates over-represented in the 30 minutes before, compared with the whole dataset |

**Answer, in short:** a burst of the same FATAL error across many nodes at once. A separate check confirmed that all 41,837
lines name the same peer, `172.16.96.116`. That points to a shared cause (the service node or the network path to it) rather
than many independent node faults. The next check is the
hardware and network logs for that peer and its link at the times of the first errors.

Checking the answer against the evidence trail matters. In this run the model (NVIDIA Nemotron) wrongly said the lines before
the failure came from the *same* node, and it over-read lift values that rested on a single line each. The trail showed both
mistakes. Since then, `preceding_patterns` ignores templates seen fewer than 2 times by default, and the engineer prompt warns
about both.

The same pipeline answers a team lead with `recurring_problems` + `problem_trend`, and a manager with `risk_ranking`.

---

## How to use it

### 1. Install

Requirements:
- Python 3.10+
- Docker, to run SereneDB locally
- Credentials for one model provider: an Anthropic API key, an NVIDIA API key, or a local OpenAI-compatible server (see [Choosing the model provider](#choosing-the-model-provider))

```bash
git clone <repo-url> LogIQ && cd LogIQ
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Start SereneDB and connect to it

SereneDB runs locally; the organisers don't host an instance. It speaks the PostgreSQL protocol, so any PostgreSQL client or
driver works, including `psycopg`, which LogIQ uses.

**Start it with Docker, setting a password:**

```bash
docker run -d --name serenedb -p 7890:7890 \
  -e POSTGRES_PASSWORD=change-me \
  -v serenedb-data:/var/lib/serenedb \
  serenedb/serenedb:26.09.2
```

- `-p 7890:7890` exposes the database port on `localhost:7890`.
- `-v serenedb-data:/var/lib/serenedb` keeps the data in a Docker volume, so it survives restarts. Leave it off for a throwaway container.
- `-e POSTGRES_PASSWORD=…` sets the password of the `postgres` superuser when the data volume is first created.
- `serenedb/serenedb:26.09.2` is the version LogIQ was tested with. Plain `serenedb/serenedb` gives the latest release.

**Why the password matters:** a fresh SereneDB server has one user, `postgres`, with no password, and that user is trusted
only for connections made *inside* the server's own machine. To SereneDB, connections from your computer into the Docker
container are network connections, and a user without a password can never log in over the network. So the
organisers' example `psql -h localhost -p 7890` works inside the container, but LogIQ needs a password. (We tested this
with `serenedb/serenedb:26.09.2`.)

If your container already runs without a password, set one from inside it. You don't need to recreate it:

```bash
docker exec -it serenedb psql -h 127.0.0.1 -p 7890 -U postgres -c "ALTER ROLE postgres PASSWORD 'change-me'"
```

**Manage the container:**

```bash
docker stop serenedb      # stop (data stays in the volume)
docker start serenedb     # start again, e.g. after a reboot: the container does not start on its own
docker ps                 # check that it is running
```

**Test the connection** from your computer:

```bash
psql "postgresql://postgres:change-me@localhost:7890/postgres" -c "SELECT version()"
# or, without psql:
.venv/bin/python -c "import psycopg; print(psycopg.connect('postgresql://postgres:change-me@localhost:7890/postgres').execute('SELECT version()').fetchone())"
```

You should see `PostgreSQL 18.3 (SereneDB 26.09.2)` or similar. A GUI client works too. For VS Code SQLTools, install the
*SQLTools PostgreSQL/Cockroach Driver* and connect to host `localhost`, port `7890`, database `postgres`, user `postgres`
and your password.

**About local files:** the SereneDB server reads files from *its own* filesystem, so inside Docker it cannot see files on
your computer. LogIQ handles this for you: `load.py` streams local CSVs to the server with `COPY … FROM STDIN`, so no mount
is needed. For your own SQL with `read_csv('/data/…')`, mount a folder by adding `-v "$PWD/data":/data` to `docker run`.
Remote files over HTTP(S) or S3 need no mount.

<details><summary>Troubleshooting the connection</summary>

| Error | Cause | Fix |
|---|---|---|
| `connection refused` | Docker or the container isn't running | Start Docker, then `docker start serenedb` |
| `fe_sendauth: no password supplied` | The DSN has no password | Add the password to `SERENEDB_DSN` |
| `password authentication failed` | Wrong password, or the user has none yet | Use the right password, or set one with the `ALTER ROLE` command above |
| `No files found that match the pattern` (in your own SQL) | The server can't see a local path | Mount the folder (`-v "$PWD/data":/data`) or use `load.py`, which streams files |
| `port is already allocated` on `docker run` | Something else uses port 7890 | Stop it, or map another port (`-p 7891:7890`) and use that port in the DSN |

</details>

### 3. Configure `.env`

```bash
cp .env.example .env               # Windows: copy .env.example .env
```

Edit `.env`. You always need the database connection, plus **one** model provider. A working example for the Docker setup
above, using NVIDIA:

```
SERENEDB_DSN=postgresql://postgres:change-me@localhost:7890/postgres
LOGIQ_PROVIDER=nvidia
NVIDIA_API_KEY=nvapi-...
```

`SERENEDB_DSN` accepts either PostgreSQL connection format:

| Format | Example |
|---|---|
| URL | `postgresql://postgres:change-me@localhost:7890/postgres` |
| Key/value | `host=localhost port=7890 dbname=postgres user=postgres password=change-me` |

In the URL format, special characters in the password must be percent-encoded, for example `@` becomes `%40`. The key/value format needs no encoding.

All settings:

| Setting | Required | What it does |
|---|---|---|
| `SERENEDB_DSN` | **yes** | How to connect to SereneDB (see above) |
| `LOGIQ_PROVIDER` | no | `anthropic` (default), `nvidia`, or `openai` for any OpenAI-compatible server |
| `ANTHROPIC_API_KEY` *or* `ANTHROPIC_AUTH_TOKEN` | with `anthropic` | Claude credentials; set only one of the two |
| `NVIDIA_API_KEY` | with `nvidia` | Key from build.nvidia.com |
| `LOGIQ_BASE_URL`, `LOGIQ_API_KEY` | with `openai` | Server address, e.g. `http://localhost:11434/v1` for Ollama, and its key if it needs one |
| `LOGIQ_MODEL` | no | Model to use; each provider has a default |
| `LOGIQ_EFFORT` | no | Claude only: `low` / `medium` (default) / `high` / `xhigh` / `max` |
| `BGL_SOURCE`, `OPENSTACK_SOURCE` | no | Load data from somewhere other than `data/` (see step 4) |

Credentials for providers you aren't using can stay in the file. `.env` is git-ignored; never commit it.

### 4. Get and load the data

```bash
python prepare_data.py     # download + extract + parse the full datasets into data/  (~25 s after download)
python load.py             # load into SereneDB and build the full-text index        (~12 s)
```

`prepare_data.py` downloads the two archives from Zenodo into `data/`, about 63 MB compressed and 1.9 GB once extracted
and parsed. It skips the download if the archives are already there. These are the same files as:

```bash
curl -L -o data/BGL.zip "https://zenodo.org/records/8196385/files/BGL.zip?download=1"
curl -L -o data/OpenStack.tar.gz "https://zenodo.org/records/8196385/files/OpenStack.tar.gz?download=1"
```

Keep `?download=1` (otherwise Zenodo returns a preview page) and `-L` (Zenodo redirects). It writes
`data/BGL.log_structured.csv` and `data/OpenStack.log_structured.csv`. `data/` is git-ignored.

`load.py` uses those CSVs automatically when they exist. It's safe to re-run, because it drops and rebuilds everything. Other sources:

| You want | Run |
|---|---|
| The full data (default once `prepare_data.py` has run) | `python load.py` |
| The small Loghub 2k samples (quick demo, no download) | Delete or move `data/*.csv`, then `python load.py` (reads the samples from GitHub) |
| Your own CSVs in the same columns | `BGL_SOURCE=path/or/url.csv OPENSTACK_SOURCE=path/or/url.csv python load.py` |

Local files are streamed with `COPY`; URLs are read by SereneDB itself.

**Team ownership.** The team-ownership table is part of this repo. It's the `ownership` rows in [sql/schema.sql](sql/schema.sql),
which map component prefixes to the teams of a fictional platform organisation (for example `KERNEL` → `compute-kernel`,
`nova.compute` → `compute-core`). To change owners, edit those rows and run `python load.py` again. Two OpenStack
components currently have no owner and are reported as **unassigned**: `oslo_service.periodic_task` (192 ERROR lines) and
`keystonemiddleware.auth_token` (6 lines).

### 5. Start the web app

```bash
uvicorn app:app --port 8000
```

Open **http://127.0.0.1:8000**. The header shows the loaded data (lines, problem lines, date range per dataset) and the
agent status. **Agent ready · <model>** means it's configured. If you see **Agent offline**, the banner says which setting is missing.

### 6. Ask a question (tab: *Ask the agent*)

1. **Pick a role card:** Engineer, Team lead or Manager. Each card shows what that role gets.
2. **Ask:** click one of the example questions, or type your own and press **Ask** (or Ctrl/⌘ + Enter).
3. **Watch it work.** The status line shows progress, and each tool call appears in the **Evidence trail** on the right as it runs.
4. **Read the answer** on the left. It's structured for the role, for example *What failed → Evidence → Possible cause → Next check → Unknowns* for an engineer.
5. **Check any number.** Click a **Q2** chip in the answer to jump to that tool call. Open **SQL 1, SQL 2…** to see the exact query and parameters. A **truncated** badge means only the first rows are shown; the totals are still exact. Yellow notes are caveats the answer must respect.
6. **Follow up.** Ask another question in the same conversation, for example *"Now check the 60 minutes before line 1399"*. Press **New** to start fresh, or switch role.

Example questions:

| Role | Try asking |
|---|---|
| Engineer | *What is behind the "Link has been severed" errors in BGL after September 2005?*<br>*Investigate the data TLB error interrupts on BGL: what failed and what should I check?*<br>*What are the ERROR lines in OpenStack, which component logs them, and what happened just before?* |
| Team lead | *Which problems recur most in BGL over the whole period, and which teams own them?*<br>*Which teams had the most problem lines between 2005-10-01 and 2006-01-01, and is it getting worse?*<br>*Compare June–August with September–December 2005 by team.* |
| Manager | *What should we prioritise across BGL and OpenStack, and who should own each item?*<br>*Give me the top 3 risks in BGL for Q4 2005 and the action for each.*<br>*Is anything in OpenStack worth escalating?* |

Tips for good answers:
- **Name a period** (`after 2005-09-01`, `between 2005-10-01 and 2006-01-01`); without one, the whole corpus is used and the answer says so.
- **Name the dataset** (BGL or OpenStack) when you mean one.
- For engineers, **quote the error text** you saw; it goes straight into full-text search.

### 7. Run tools yourself (tab: *Explore tools*)

1. Choose a tool from the list; its description explains what it does.
2. Fill in the form. Fields marked `*` are required, and the rest are optional filters.
3. Press **Run**. You get the same evidence card the agent sees: scope, SQL, parameters, row counts and results.

This tab needs no model or API key. It's useful for checking an agent's claim, or exploring the data directly.

### 8. See the whole corpus (tab: *Corpus & teams*)

Whole-corpus counts calculated in SereneDB: lines, problem lines, alerts and time span per dataset, problem lines per team,
and lines per level.

### Other ways to use it

**From your own AI assistant (MCP).** The same 11 tools in Claude Code, Claude Desktop, Codex, Cursor, VS Code or any other
MCP client, with that client's model as the agent. See [Use LogIQ from any MCP client](#use-logiq-from-any-mcp-client-any-llm) below.

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

### Use LogIQ from any MCP client (any LLM)

[`mcp_server.py`](mcp_server.py) exposes LogIQ's 11 tools over the Model Context Protocol (MCP). Your MCP client starts it as
a local program (stdio transport: there is no port or URL), lists the tools, and **your client's own model** decides which
tools to call. You can use Claude, GPT, Gemini, or a local model, whatever your client runs.

Things to know first:
- **No model key in `.env` is needed for MCP.** The model belongs to your client; LogIQ only needs `SERENEDB_DSN`.
- **The web app's role prompts don't apply here.** Your client's model sees the tool descriptions and short server instructions, not the Engineer / Team lead / Manager prompts. Say the role and what you want in your question; see the examples below.
- **Use absolute paths in client configs.** Clients start the server from their own folder.

#### Before you start

1. SereneDB is running: `docker start serenedb` (see step 2).
2. The data is loaded: `python load.py` (see step 4).
3. `.env` in the project folder has a working `SERENEDB_DSN`. The server finds `.env` itself, wherever the client starts it.
4. You know your two absolute paths. Run this in the project folder:

   ```bash
   echo "$PWD/.venv/bin/python"   # the command
   echo "$PWD/mcp_server.py"      # its argument
   ```

   On Windows they are `C:\path\to\LogIQ\.venv\Scripts\python.exe` and `C:\path\to\LogIQ\mcp_server.py`. In JSON, write
   backslashes as `\\` or use forward slashes.

Below, replace `/abs/path/LogIQ` with your project folder.

#### Set up your client

**Claude Code** (✔ tested). Run in the project folder:

```bash
claude mcp add logiq -- "$PWD/.venv/bin/python" "$PWD/mcp_server.py"              # this project only
claude mcp add --scope user logiq -- "$PWD/.venv/bin/python" "$PWD/mcp_server.py"  # or: every folder
claude mcp list                                                                    # expect: logiq … ✔ Connected
```

Inside Claude Code, `/mcp` shows the server and its tools. Claude Code asks permission the first time each tool runs. For
non-interactive runs, allow the tools explicitly:
`claude -p "…" --allowedTools "mcp__logiq__*"`.

**Claude Desktop** (config validated). Open Settings → Developer → Edit Config, or edit the file directly:
macOS `~/Library/Application Support/Claude/claude_desktop_config.json`, Windows `%APPDATA%\Claude\claude_desktop_config.json`.
Add `logiq` inside `mcpServers`, keeping any servers already there, then fully quit and reopen Claude Desktop:

```json
{
  "mcpServers": {
    "logiq": {
      "command": "/abs/path/LogIQ/.venv/bin/python",
      "args": ["/abs/path/LogIQ/mcp_server.py"]
    }
  }
}
```

**Codex CLI** (untested). Either run

```bash
codex mcp add logiq -- /abs/path/LogIQ/.venv/bin/python /abs/path/LogIQ/mcp_server.py
```

or add this to `~/.codex/config.toml`:

```toml
[mcp_servers.logiq]
command = "/abs/path/LogIQ/.venv/bin/python"
args = ["/abs/path/LogIQ/mcp_server.py"]
```

**Cursor** (untested). Add to `~/.cursor/mcp.json` (all projects) or `.cursor/mcp.json` (this project), then enable `logiq` under Settings → MCP:

```json
{ "mcpServers": { "logiq": { "command": "/abs/path/LogIQ/.venv/bin/python", "args": ["/abs/path/LogIQ/mcp_server.py"] } } }
```

**VS Code with GitHub Copilot, agent mode** (untested). Add to `.vscode/mcp.json` in your workspace. Note the key is `servers`:

```json
{ "servers": { "logiq": { "type": "stdio", "command": "/abs/path/LogIQ/.venv/bin/python", "args": ["/abs/path/LogIQ/mcp_server.py"] } } }
```

**Gemini CLI** (untested). Add to `~/.gemini/settings.json`:

```json
{ "mcpServers": { "logiq": { "command": "/abs/path/LogIQ/.venv/bin/python", "args": ["/abs/path/LogIQ/mcp_server.py"] } } }
```

**Any other MCP client.** Add a **stdio** server with these settings:

| Setting | Value |
|---|---|
| Name | `logiq` |
| Command | `/abs/path/LogIQ/.venv/bin/python` |
| Arguments | `/abs/path/LogIQ/mcp_server.py` |
| Environment | none needed (it reads `.env`) |

"Untested" means we couldn't try that client here. Its config format comes from the client's documentation at the time of
writing; check that documentation if a step doesn't match.

#### Check the server without any LLM

The official [MCP Inspector](https://github.com/modelcontextprotocol/inspector) talks to the server directly (✔ tested; needs Node.js):

```bash
npx -y @modelcontextprotocol/inspector --cli /abs/path/LogIQ/.venv/bin/python /abs/path/LogIQ/mcp_server.py --method tools/list
npx -y @modelcontextprotocol/inspector --cli /abs/path/LogIQ/.venv/bin/python /abs/path/LogIQ/mcp_server.py \
    --method tools/call --tool-name corpus_overview
```

The first command should list the 11 tools; the second should return the line counts per dataset. Drop `--cli` to open the
Inspector's web interface and call tools by hand.

#### Ask role-specific questions

Mention LogIQ so the model uses the tools, and say who you are, since the role prompts aren't applied over MCP:

| Role | Example question |
|---|---|
| Engineer | *Using logiq, I'm an engineer: what is behind the "Link has been severed" errors in BGL after September 2005? Show the failing lines, what happened just before, a possible cause labelled as a hypothesis, and the next check.* |
| Team lead | *Using logiq, as a team lead: which problems recur most in BGL over the whole period, which teams own them, and what should we investigate first? Give counts and trends.* |
| Manager | *Using logiq, as a manager: what are the top 3 risks across BGL and OpenStack, who should own each, and what does the data not tell us? Don't speculate about downtime or cost.* |

Each tool result carries its SQL, filters and row counts. Ask the model to *"show the queries behind each number"* if you want the evidence.

#### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Client shows `logiq` as failed / not connected | Wrong path, or Python packages not installed in `.venv` | Run the command and argument from *Before you start* by hand. It should wait silently; stop it with Ctrl+C. If it prints an error, fix that first (e.g. `pip install -r requirements.txt`). |
| Tools appear, but every call fails with `database error … Check that SereneDB is running` | SereneDB is stopped, or `SERENEDB_DSN` is wrong | `docker start serenedb`; check the DSN (step 3) |
| Calls fail with `database error (UndefinedTable): Table with name events_v does not exist!` | Data not loaded | `python load.py` |
| `invalid arguments: …` | The model sent a bad argument | Nothing to fix: the message goes back to the model, which can retry |
| The model answers without calling tools | It didn't recognise that the tools apply | Start the question with *"Using logiq, …"* |
| No tools listed after editing a config file | The client hasn't reloaded | Restart the client completely |

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
| `bgl`, `openstack` | Parsed logs in Loghub's structured columns (from `prepare_data.py`, or the Loghub 2k samples) |
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
prepare_data.py         downloads the Zenodo archives, parses raw logs into data/*.log_structured.csv
load.py, sql/schema.sql loader (COPY for local files) and schema (events, ownership, full-text index)
worked_investigation.py writes WORKED_INVESTIGATION.md from real agent runs
FUNCTIONS.md            tool reference
agent_tools.py          small CLI for raw-table inspection (not used by the agent)
```

### Known limitations

- **Our own templates.** The Zenodo archives contain raw logs only, so `prepare_data.py` derives templates by masking variable tokens, not with Loghub's Drain parser. Template ids (`E1`…) therefore differ from the ids in Loghub's 2k samples. Masking can also merge messages that differ only in variable words, or split messages whose wording varies.
- **OpenStack anomaly labels are not used.** `anomaly_labels.txt` names 4 VM instances with injected anomalies, but the labels are per instance, not per line, so they aren't mapped to `is_alert`. To look at them, search for an instance id, e.g. `search_logs("544fd51c-4edc-4780-baae-ba1d80a0acfc")`.
- **OpenStack line order.** The three OpenStack files are merged and numbered in timestamp order, so neighbouring `line_id`s can come from different log files. Use `get_nearby_events(minutes=…, same_source=true)` to stay within one file.
- **Fictional ownership.** Teams come from the `ownership` table in `sql/schema.sql`, which describes a fictional organisation. Two OpenStack components are unassigned (see step 4).
- **Heuristics, not root causes.** `candidate_cause` (the nearest earlier problem on the same source, or any source within 1 hour), `lift`, trend labels (a 1.5× change between the halves of the period; "single burst" when every line falls within 5% of it) and `priority_score` are simple, documented rules. They point to where to look; they don't prove a cause.
- **Lines are not incidents.** No tool groups lines into incidents. `active_hours` and `active_days` are rough proxies for separate episodes.
- **Timestamps** are as recorded in each log, with no timezone conversion. BGL and OpenStack cover different years, so trends are computed per dataset.
- **Web UI** has no login, and keeps conversations in memory, so a restart clears them. Run it locally or behind your own access control.
- **Not implemented:** on-demand indexing, the OpenTelemetry Parquet data, and row-level security per role. These are optional extras, judged separately from the main score.
  - **Reading the OpenTelemetry data in place works.** SereneDB queries the public Parquet files over HTTPS with no download: `SELECT * FROM read_parquet('https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_000.parquet') LIMIT 10`.
  - **Measured on this setup, for one file (1,000,000,000 rows):** the schema took 1.5 s, the row count 0.2 s, and sample rows 0.8 s. A full aggregation (lines per service and severity) took **132 s**, which is too slow to run on every agent question.
  - **The path forward:** a view over the files plus an inverted index on a chosen time window (`CREATE INDEX … USING inverted` on the view, refreshed with `REINDEX INDEX`), or a summary table built once.

### Data and citation

Loghub (Zhu et al., *Loghub: A Large Collection of System Log Datasets for AI-driven Log Analytics*, ISSRE 2023). For BGL, also
Oliner & Stearley, *What Supercomputers Say: A Study of Five System Logs*, DSN 2007.
