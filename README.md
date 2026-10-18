# LogIQ: the right log insights for the right people

LogIQ turns system logs into answers for three people who read the same logs for different reasons:

| Role | Asks | Gets |
|---|---|---|
| **Engineer** | "What failed, and why?" | The failing log lines, what happened just before and after, a possible cause (labelled as a hypothesis) and the next check to run |
| **Team lead** | "What keeps coming back?" | Recurring problems with counts, trends and owning teams, and what to investigate first |
| **Manager** | "What should we prioritise?" | At most five ranked risks, each with an owner and an action, without invented business impact |

The data is the full Loghub **BGL** supercomputer log (4,747,963 lines over 215 days) and **OpenStack** cloud log (207,820
lines over 3 days), about 5 million lines, far too many to paste into an AI prompt. So **SereneDB does the searching and
counting**, and an **AI model chooses which queries to run and explains the results**. Every number in an answer can be
traced back to the SQL query that produced it.

You can use LogIQ in three ways: a **web app**, any **MCP client** (Claude Code, Claude Desktop, Codex, Cursor, …), or the **terminal**.

## Contents

- [Quick start](#quick-start)
- [1. Set up (everyone)](#1-set-up-everyone)
- [2. Choose how to use it](#2-choose-how-to-use-it)
- [3. Use the web app](#3-use-the-web-app)
- [4. Use LogIQ over MCP](#4-use-logiq-over-mcp)
- [5. Use it from the terminal](#5-use-it-from-the-terminal)
- [6. How it works](#6-how-it-works)
- [7. Reference](#7-reference)

---

## Quick start

On macOS or Linux, with Python 3.10+ and Docker installed. Each command is explained in [section 1](#1-set-up-everyone).

```bash
git clone https://github.com/kiranbabuthatha/LogIQ_Hackathon.git && cd LogIQ_Hackathon
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt

docker run -d --name serenedb -p 7890:7890 -e POSTGRES_PASSWORD=change-me \
  -v serenedb-data:/var/lib/serenedb serenedb/serenedb:26.09.2

cp .env.example .env                 # contains SERENEDB_DSN for the container above; add a model key for the web app
python prepare_data.py && python load.py
```

Then either:
- **Web app:** `uvicorn app:app --port 8000` and open http://127.0.0.1:8000. This needs a model key in `.env`; see [3.1](#31-pick-the-model).
- **MCP:** `claude mcp add logiq -- "$PWD/.venv/bin/python" "$PWD/mcp_server.py"`, then ask Claude Code. No model key is needed; see [section 4](#4-use-logiq-over-mcp).

---

## 1. Set up (everyone)

These steps are the same however you use LogIQ.

### 1.1 Install

Requirements: **Python 3.10+** and **Docker** (to run SereneDB).

```bash
git clone https://github.com/kiranbabuthatha/LogIQ_Hackathon.git && cd LogIQ_Hackathon
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

With the virtual environment active, `python` below means the project's Python.

### 1.2 Start SereneDB

SereneDB runs locally; the organisers don't host an instance. It speaks the PostgreSQL protocol, so any PostgreSQL client or
driver works, including `psycopg`, which LogIQ uses.

```bash
docker run -d --name serenedb -p 7890:7890 \
  -e POSTGRES_PASSWORD=change-me \
  -v serenedb-data:/var/lib/serenedb \
  serenedb/serenedb:26.09.2
```

- `-p 7890:7890` exposes the database on `localhost:7890`.
- `-v serenedb-data:/var/lib/serenedb` keeps the data in a Docker volume, so it survives restarts.
- `-e POSTGRES_PASSWORD=…` sets the password of the `postgres` user when the volume is first created. Pick your own.
- `serenedb/serenedb:26.09.2` is the version LogIQ was tested with. Plain `serenedb/serenedb` gives the latest release.

**Why a password is needed.** A fresh SereneDB server has one user, `postgres`, with no password. That user is trusted only for
connections made *inside* the server's own machine. To SereneDB, connections from your computer into the Docker container
are network connections, and a user without a password can't log in over the network. So the organisers' example
`psql -h localhost -p 7890` works inside the container, but LogIQ needs a password. We tested this with `26.09.2`.
If your container already runs without one, set it from inside the container:

```bash
docker exec -it serenedb psql -h 127.0.0.1 -p 7890 -U postgres -c "ALTER ROLE postgres PASSWORD 'change-me'"
```

**Day to day:**

```bash
docker start serenedb     # after a reboot: the container does not start on its own
docker stop serenedb      # stop it (data stays in the volume)
docker ps                 # is it running?
```

**Test the connection** from your computer. Either command should print `PostgreSQL 18.3 (SereneDB 26.09.2)` or similar:

```bash
psql "postgresql://postgres:change-me@localhost:7890/postgres" -c "SELECT version()"
python -c "import psycopg; print(psycopg.connect('postgresql://postgres:change-me@localhost:7890/postgres').execute('SELECT version()').fetchone())"
```

A GUI client works too. For VS Code SQLTools, install the *SQLTools PostgreSQL/Cockroach Driver*, then connect to host
`localhost`, port `7890`, database `postgres`, user `postgres`, with your password.

<details><summary>Connection troubleshooting</summary>

| Error | Cause | Fix |
|---|---|---|
| `connection refused` | Docker or the container isn't running | Start Docker, then `docker start serenedb` |
| `fe_sendauth: no password supplied` | The connection string has no password | Add it to `SERENEDB_DSN` |
| `password authentication failed` | Wrong password, or the user has none yet | Use the right one, or set it with the `ALTER ROLE` command above |
| `port is already allocated` on `docker run` | Something else uses port 7890 | Stop it, or use `-p 7891:7890` and port 7891 in the connection string |
| `No files found that match the pattern` (in your own SQL) | The server can't see files on your computer | See [Local files and Docker](#local-files-and-docker) |

</details>

### 1.3 Configure `.env`

```bash
cp .env.example .env               # Windows: copy .env.example .env
```

**Only `SERENEDB_DSN` is required.** `.env.example` already contains the value for the container above. Change the
password if you used a different one:

```
SERENEDB_DSN=postgresql://postgres:change-me@localhost:7890/postgres
```

Either PostgreSQL connection format works:

| Format | Example |
|---|---|
| URL | `postgresql://postgres:change-me@localhost:7890/postgres` (percent-encode special characters in the password, e.g. `@` becomes `%40`) |
| Key/value | `host=localhost port=7890 dbname=postgres user=postgres password=change-me` |

A **model key** is needed only for the web app and the terminal; MCP uses your client's model. See [3.1 Pick the model](#31-pick-the-model).
`.env` is git-ignored. Never commit it.

### 1.4 Get and load the data

```bash
python prepare_data.py     # download, extract and parse the full datasets into data/   (~25 s after the download)
python load.py             # load into SereneDB and build the full-text index           (~12 s)
```

- `prepare_data.py` downloads the two Zenodo archives into `data/`: about 63 MB compressed, 1.9 GB extracted and parsed. It skips the download if the archives are already there, and writes `data/BGL.log_structured.csv` and `data/OpenStack.log_structured.csv`. `data/` is git-ignored. The downloads are the same as:
  ```bash
  curl -L -o data/BGL.zip "https://zenodo.org/records/8196385/files/BGL.zip?download=1"
  curl -L -o data/OpenStack.tar.gz "https://zenodo.org/records/8196385/files/OpenStack.tar.gz?download=1"
  ```
  Keep `?download=1`, or Zenodo returns a preview page; `-L` follows Zenodo's redirect.
- `load.py` uses those CSVs automatically. It's safe to re-run, because it drops and rebuilds everything.

| You want | Run |
|---|---|
| The full data (the default once `prepare_data.py` has run) | `python load.py` |
| The small Loghub 2k samples (quick demo, no download) | Move `data/*.csv` away, then `python load.py` |
| Your own CSVs with the same columns | `BGL_SOURCE=path/or/url.csv OPENSTACK_SOURCE=path/or/url.csv python load.py` |

### 1.5 Check that it worked

```bash
python -c "import tools; print([(d['dataset'], d['lines']) for d in tools.corpus_overview()['data']['datasets']])"
# [('bgl', 4747963), ('openstack', 207820)]
```

You're set up. Now [choose how to use it](#2-choose-how-to-use-it).

---

## 1.9 One-command Docker setup (any OS)

If you have Docker, the whole stack — SereneDB, data download/parse/load, web UI and the MCP server — starts with:

```bash
cp .env.example .env        # add your model key (the only manual step)
docker compose up --build
```

Then:
- Web UI: http://localhost:8000
- First run takes ~10 minutes (770 MB download + parse + load) — watch progress with `docker compose logs -f init`;
  later runs reuse the `serenedb-data` volume and the parsed CSVs in `./data`.
- MCP over HTTP (works from any machine, no clone needed): add a **remote** MCP server with URL
  `http://<this-machine>:8001/mcp`, e.g. on the same machine:
  `claude mcp add --transport http logiq http://localhost:8001/mcp`
- Stop: `docker compose down` (keeps data). Fresh start: `docker compose down -v`.

## 2. Choose how to use it

All three ways use the same 19 tools and the same data:

| | [Web app](#3-use-the-web-app) | [MCP](#4-use-logiq-over-mcp) | [Terminal](#5-use-it-from-the-terminal) |
|---|---|---|---|
| Best for | Demos, and checking every number visually | Working inside the AI assistant you already use | Scripts and the worked investigation |
| Which model answers | Set in `.env`: Claude, NVIDIA or a local model | Your MCP client's model | Set in `.env` |
| Model key in `.env` | Needed | **Not needed** | Needed |
| Role behaviour | Pick a role card | Use a role prompt such as `/mcp__logiq__engineer` | `python agent.py <role> "…"` |
| Where you see the evidence | Evidence panel with every SQL query | In the tool results; ask the model to list them | Printed |

---

## 3. Use the web app

### 3.1 Pick the model

Add **one** provider to `.env`, then start or restart the server:

| `LOGIQ_PROVIDER` | Put in `.env` | Default model | Notes |
|---|---|---|---|
| `anthropic` (default) | `ANTHROPIC_API_KEY=…` **or** `ANTHROPIC_AUTH_TOKEN=…`, not both | `claude-opus-5-5` | Adaptive thinking; `LOGIQ_EFFORT` = `low`/`medium` (default)/`high`/`xhigh`/`max`; prompt caching; server-side refusal fallback. An auth token also gets the `oauth-2025-04-20` beta header, and `ANTHROPIC_BASE_URL` can point at a gateway. |
| `nvidia` | `LOGIQ_PROVIDER=nvidia` and `NVIDIA_API_KEY=nvapi-…` (from build.nvidia.com) | `nvidia/nemotron-3-super-120b-a12b` | The model must support tool calling. Alternatives: `moonshotai/kimi-k2.6`, `z-ai/glm-5.3`, `deepseek-ai/deepseek-v4.1-flash`, `openai/gpt-oss-20b`. To list them: `curl -s https://integrate.api.nvidia.com/v1/models` |
| `openai` | `LOGIQ_PROVIDER=openai`, `LOGIQ_BASE_URL=…`, `LOGIQ_MODEL=…`, and `LOGIQ_API_KEY=…` if the server needs one | none | Any OpenAI-compatible server, e.g. Ollama (`http://localhost:11434/v1`), LM Studio or vLLM |

`LOGIQ_MODEL` overrides the default model for any provider. Keys for providers you aren't using can stay in the file.
Open models are usually slower and less consistent than Claude at choosing tools and citing evidence, so check their answers against the evidence trail.

### 3.2 Start it

```bash
uvicorn app:app --port 8000
```

Open **http://127.0.0.1:8000**. The header shows the loaded data and the agent status. **Agent ready · <model>** means it's
configured. If you see **Agent offline**, the banner says which setting is missing.

### 3.3 Ask a question (tab: *Ask the agent*)

1. **Pick a role card:** Engineer, Team lead or Manager.
2. **Ask:** click an example question, or type your own and press **Ask** (or Ctrl/⌘ + Enter).
3. **Watch it work.** Each tool call appears in the **Evidence trail** on the right as it runs.
4. **Read the answer** on the left. It's tiered: a **Level 0 summary** (1–2 sentences) and a **Level 1 summary** first, then
   the role structure — for an engineer *What failed → Key log lines → Possible cause → Next check → Unknowns* — and a
   **Show your work** appendix at the end.
5. **Check any number.** Click a **Q2** chip to jump to that tool call, and open **SQL 1, SQL 2…** to see the exact query and parameters. A **truncated** badge means only the first rows are shown; totals are still exact. Yellow notes are caveats the answer must respect.
6. **Follow up** in the same conversation, for example *"Now check the 2 hours before line 3326688"*. Press **New** to start over.

| Role | Try asking |
|---|---|
| Engineer | *What is behind the "Link has been severed" errors in BGL after September 2005?*<br>*Investigate the data TLB error interrupts on BGL: what failed and what should I check?*<br>*What are the ERROR lines in OpenStack, which component logs them, and what happened just before?* |
| Team lead | *Which problems recur most in BGL over the whole period, and which teams own them?*<br>*Which teams had the most problem lines between 2005-10-01 and 2006-01-01, and is it getting worse?*<br>*Compare June–August with September–December 2005 by team.* |
| Manager | *What should we prioritise across BGL and OpenStack, and who should own each item?*<br>*Give me the top 3 risks in BGL for Q4 2005 and the action for each.*<br>*Is anything in OpenStack worth escalating?* |

Tips: **name a period** (`after 2005-09-01`), otherwise the whole corpus is used; **name the dataset** (BGL or OpenStack);
and as an engineer, **quote the error text**, which goes straight into full-text search.

### 3.4 The other tabs

- **Explore tools:** run any tool yourself from a form and get the same evidence card the agent sees. No model or API key needed. Useful for checking a claim.
- **Corpus & teams:** whole-corpus counts per dataset and per team, calculated in SereneDB.

---

## 4. Use LogIQ over MCP

### 4.1 What happens

```
 you ──question──▶ your MCP client (Claude Code, Claude Desktop, Codex, Cursor, …)
                        │  its own LLM decides which LogIQ tools to call
                        ▼
                   mcp_server.py ── started by the client on your computer (stdio: no port, no URL)
                        │  parameterised SQL
                        ▼
                   SereneDB (Docker, localhost:7890) ──▶ results with their SQL, filters and row counts
```

- **Your client's model does the reasoning.** LogIQ provides the tools and data, so `.env` needs only `SERENEDB_DSN`.
- **The server offers 19 tools and 3 role prompts** (`engineer`, `team_lead`, `manager`). The role prompts give your client's model the same instructions and evidence rules as the web app.
- **Before connecting,** complete [section 1](#1-set-up-everyone): SereneDB running, data loaded, `.env` in place.

### 4.2 Connect your client

Clients start the server themselves, so they need **absolute paths**. Get yours from the project folder:

```bash
echo "$PWD/.venv/bin/python"   # the command      (Windows: C:\path\to\LogIQ_Hackathon\.venv\Scripts\python.exe)
echo "$PWD/mcp_server.py"      # its argument     (Windows: C:\path\to\LogIQ_Hackathon\mcp_server.py)
```

In the configs below, replace `/abs/path/LogIQ_Hackathon` with your folder. In JSON on Windows, write `\` as `\\` or use `/`.

**Claude Code** (✔ tested). Run in the project folder:

```bash
claude mcp add logiq -- "$PWD/.venv/bin/python" "$PWD/mcp_server.py"              # this project only
claude mcp add --scope user logiq -- "$PWD/.venv/bin/python" "$PWD/mcp_server.py"  # or: every folder
```

<details><summary><b>Claude Desktop</b> (config validated)</summary>

Open Settings → Developer → Edit Config, or edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)
or `%APPDATA%\Claude\claude_desktop_config.json` (Windows). Add `logiq` inside `mcpServers`, keeping any servers already
there, then quit and reopen Claude Desktop:

```json
{
  "mcpServers": {
    "logiq": {
      "command": "/abs/path/LogIQ_Hackathon/.venv/bin/python",
      "args": ["/abs/path/LogIQ_Hackathon/mcp_server.py"]
    }
  }
}
```
</details>

<details><summary><b>Codex CLI</b> (untested)</summary>

```bash
codex mcp add logiq -- /abs/path/LogIQ_Hackathon/.venv/bin/python /abs/path/LogIQ_Hackathon/mcp_server.py
```

or add this to `~/.codex/config.toml`:

```toml
[mcp_servers.logiq]
command = "/abs/path/LogIQ_Hackathon/.venv/bin/python"
args = ["/abs/path/LogIQ_Hackathon/mcp_server.py"]
```
</details>

<details><summary><b>Cursor</b> (untested)</summary>

Add to `~/.cursor/mcp.json` (all projects) or `.cursor/mcp.json` (one project), then enable `logiq` under Settings → MCP:

```json
{ "mcpServers": { "logiq": { "command": "/abs/path/LogIQ_Hackathon/.venv/bin/python", "args": ["/abs/path/LogIQ_Hackathon/mcp_server.py"] } } }
```
</details>

<details><summary><b>VS Code with GitHub Copilot, agent mode</b> (untested)</summary>

Add to `.vscode/mcp.json` in your workspace. Note the key is `servers`:

```json
{ "servers": { "logiq": { "type": "stdio", "command": "/abs/path/LogIQ_Hackathon/.venv/bin/python", "args": ["/abs/path/LogIQ_Hackathon/mcp_server.py"] } } }
```
</details>

<details><summary><b>Gemini CLI</b> (untested)</summary>

Add to `~/.gemini/settings.json`:

```json
{ "mcpServers": { "logiq": { "command": "/abs/path/LogIQ_Hackathon/.venv/bin/python", "args": ["/abs/path/LogIQ_Hackathon/mcp_server.py"] } } }
```
</details>

<details><summary><b>Any other MCP client</b></summary>

Add a **stdio** server named `logiq`. Set the command to `/abs/path/LogIQ_Hackathon/.venv/bin/python` and the argument to
`/abs/path/LogIQ_Hackathon/mcp_server.py`. It needs no environment variables, because it reads `.env` itself.
</details>

"Untested" means we couldn't try that client here. Its config format comes from the client's documentation at the time of
writing; check that documentation if something doesn't match.

### 4.3 Check the connection

| Client | How to check | Expected |
|---|---|---|
| Claude Code | `claude mcp list`, or `/mcp` inside Claude Code | `logiq … ✔ Connected`, with 19 tools and 3 prompts |
| Any client | Ask: *"Using logiq, give me an overview of the data."* | It calls `corpus_overview` and reports **4,747,963** BGL and **207,820** OpenStack lines |
| No LLM at all | `npx -y @modelcontextprotocol/inspector --cli /abs/path/LogIQ_Hackathon/.venv/bin/python /abs/path/LogIQ_Hackathon/mcp_server.py --method tools/list` | The 19 tools (✔ tested; needs Node.js). Use `--method prompts/list` for the 3 prompts, or drop `--cli` for the web interface. |

### 4.4 Ask as a role (recommended)

The easiest way to get role-appropriate, evidence-based answers is to **start with a role prompt**. It loads the same
instructions the web app uses: which evidence to gather, the answer structure, and the evidence rules.

**In Claude Code** (✔ tested), the prompts are slash commands. Put the question in quotes:

```text
/mcp__logiq__engineer "What is behind the Link has been severed errors in BGL after September 2005?"
/mcp__logiq__team_lead "Which problems recur most in BGL, which teams own them, and what should we investigate first?"
/mcp__logiq__manager "What are the top 3 risks across BGL and OpenStack, and who should own each?"
```

For a non-interactive run, add the permission flag: `claude -p '/mcp__logiq__manager "…"' --allowedTools "mcp__logiq__*"`.

**In other clients**, MCP prompts usually appear as slash commands or in an attachment / "+" menu under *LogIQ: Engineer*,
*LogIQ: Team lead* and *LogIQ: Manager*. Where they appear depends on the client, and some don't support MCP prompts at all.
In that case, start your message with *"Using logiq, as an engineer / a team lead / a manager: …"* and add the rules from
[4.6](#46-get-grounded-answers).

Each role prompt asks the model to number its tool calls (Q1, Q2, …), cite them, and list them at the end. That gives you the
evidence trail in the chat.

### The shape of an answer

Every LogIQ answer — in the web app, an MCP client or the CLI — follows the same tiered structure:

1. **`## Level 0 summary`** — one or two sentences that compress the whole result. No plumbing; only the one or two
   numbers that matter. Useful if you read nothing else.
2. **`## Level 1 summary`** — a short paragraph: key findings with the most important measured numbers, the scope they
   cover, and the main recommendation or next step.
3. **The role sections** — the detailed answer for your role (engineer: *What failed → Key log lines → Around it →
   Possible cause (hypothesis) → Next check → Unknowns*; team lead: *Period and scope → Recurring problems → …*;
   manager: *Bottom line → Priorities → What the data does not tell us*).
4. **`## Show your work`** — the appendix where all the plumbing lives: for every query behind a claim, one line with
   the tool used, the inputs given, the SQL that ran, the rows returned and whether the result was truncated, each
   tagged with its evidence id `[Q#]`.

Two rules keep the answer readable and honest: the narrative **interprets results only** (tool names, inputs and SQL
never appear outside the appendix), and facts stay separated from interpretation — hypotheses and limitations are
labelled where they occur.

### 4.5 Your first session: three walk-throughs

You can also work step by step and steer the model yourself. These sequences were run on the full data; the numbers are what you should see.

**Engineer: investigate an error**

| Step | You ask | The model should call | What you get |
|---|---|---|---|
| 1 | *Using logiq, find the "Link has been severed" errors in BGL after September 2005.* | `search_logs` | **41,837** matching lines (exact count), first on 2005-09-02, with sample lines |
| 2 | *Investigate the first matching line: what failed and what happened around it?* | `investigate_event` | The line (FATAL, APP, team job-runtime); the lines before and after it; the same template's 15,273 lines on 1,024 nodes |
| 3 | *What was unusually common in the 2 hours before it?* | `preceding_patterns` | Over-represented messages, such as MidplaneSwitchController and EDRAM errors. This is co-occurrence, not cause. |
| 4 | *Do all of those errors talk to the same peer address?* | `search_logs` (the phrase plus `172.16.96.116`) | The same 41,837 lines, so they all name one peer and a shared cause is likely |
| 5 | *So what's the next check? Show the SQL behind each number.* | (none) | A diagnostic step, and the queries from the tool results |

**Team lead: find recurring problems**

| Step | You ask | The model should call | What you get |
|---|---|---|---|
| 1 | *Using logiq, which problems recur most in BGL over the whole period, and who owns them?* | `recurring_problems` | A table such as: `data TLB error interrupt`, compute-kernel, 152,734 lines over 11 days |
| 2 | *How does the top one trend week by week?* | `problem_trend` | Weekly counts: rising, falling, or a single burst |
| 3 | *Compare June–August with September–December 2005 by team.* | `compare_periods` | Each team's change: new, gone, rising or falling |
| 4 | *Which one should we investigate first, and why?* | (none) | A recommendation based on frequency, spread and trend |

**Manager: decide priorities**

| Step | You ask | The model should call | What you get |
|---|---|---|---|
| 1 | *Using logiq, as a manager: what are the top risks across BGL and OpenStack?* | `risk_ranking` (by pattern and by team) | Ranked risks with share, spread and trend. For example, job-runtime's 228,536 problem lines are rising. |
| 2 | *Who should own each, and what's the first action?* | (none) | One owner team and one action per risk |
| 3 | *What does the data not tell us?* | (none) | No downtime, cost or incident counts in the logs; causes unconfirmed |

### 4.6 Get grounded answers

- **Say "Using logiq"** at the start, so the model reaches for the tools instead of answering from memory.
- **Name a period and a dataset** (*BGL after 2005-09-01*). Without them, the whole corpus is used.
- **Ask for the evidence:** *"Show the tool calls and SQL behind each number."* Every tool result contains its SQL, parameters, row counts and a `truncated` flag.
- **Counts are log lines, not incidents.** One fault can write thousands of lines. Ask *"over how many days and sources?"* for spread.
- **Hypotheses aren't facts.** Ask the model to label possible causes, and to say what would confirm them.
- **Follow up in the same chat.** The model keeps line ids and template ids from earlier answers.

To steer the model to a specific tool, name it or describe the question type:

| Question type | Tool |
|---|---|
| Find specific log lines by text, with a time window | `search_logs` |
| What happened around one line | `investigate_event`, `get_nearby_events` (`minutes=…` for a time window) |
| What was unusually common before an event | `preceding_patterns` |
| The full history of one message type | `template_history` |
| What keeps recurring, and who owns it | `recurring_problems` |
| Counts by team, component, level, source… | `count_problems` |
| How something changes over time | `problem_trend` |
| Period A vs period B | `compare_periods` |
| What to prioritise | `risk_ranking` (`by="pattern"` or `by="team"`) |
| Dataset sizes, dates and team names | `corpus_overview` |

**Limits to expect:** search returns at most 100 lines per call, but totals are always exact. Tool calls take under 1 s;
the model's thinking takes longer (a full answer in Claude Code took 13–40 s in our tests). Cost depends on your client and model.

### 4.7 Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Client shows `logiq` as failed / not connected | Wrong path, or packages missing in `.venv` | Run the command and argument from [4.2](#42-connect-your-client) by hand. It should wait silently; stop it with Ctrl+C. If it prints an error, fix that first (e.g. `pip install -r requirements.txt`). |
| Every call fails with `database error … Check that SereneDB is running` | SereneDB is stopped, or `SERENEDB_DSN` is wrong | `docker start serenedb`; check `.env` ([1.3](#13-configure-env)) |
| Calls fail with `database error (UndefinedTable): Table with name events_v does not exist!` | Data not loaded | `python load.py` |
| `invalid arguments: …` | The model sent a bad argument | Nothing to fix: the message goes back to the model, which can retry |
| The model answers without calling tools | It didn't realise the tools apply | Start with *"Using logiq, …"* or use a role prompt |
| No tools or prompts after editing a config | The client hasn't reloaded | Restart the client completely |
| A role prompt only gets the first word of the question | The client split the argument on spaces | Put the question in quotes |

---

## 5. Use it from the terminal

Uses the model from `.env`, like the web app ([3.1](#31-pick-the-model)):

```bash
python agent.py engineer 'What is behind the "Link has been severed" errors in BGL?'
python agent.py team_lead "Which problems recur most in BGL and who owns them?"
python agent.py manager "What should we prioritise across BGL and OpenStack?"
```

**Worked investigation.** [WORKED_INVESTIGATION.md](WORKED_INVESTIGATION.md) shows one question per role, run through the agent,
with every tool call, SQL statement, row count and answer. To regenerate it: `python worked_investigation.py`.

---

## 6. How it works

LogIQ has two stages: the data is **prepared and loaded once**, then **every question** goes through the same loop.

### Stage 1: Prepare, load and index (once)

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

1. **Prepare:** download and extract the archives, parse every raw line into Loghub's columns, and derive a message template per line. Details are in [Data preparation](#data-preparation).
2. **Load:** stream the CSVs into SereneDB, then merge both datasets into one `events` table with a shared **severity** (3 = FATAL/SEVERE/FAILURE/CRITICAL, 2 = ERROR, 1 = WARNING, 0 = INFO).
3. **Attach owners:** map each component to a team from the `ownership` table by longest matching prefix (for example `nova.compute.manager` → `compute-core`).
4. **Mark problems:** a problem line has severity ≥ WARNING, or a BGL expert alert label.
5. **Index:** build an inverted full-text index over the message text (lower-cased, normalised, stemmed), searched with `@@` and ranked with BM25.

On a laptop, preparing takes about 25 s and loading plus indexing all 4.96M lines about 12 s. Every tool answers in under 1 s.

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
 │  AI model        │────────────────────▶│  tools.py         │────────────────────────▶│  SereneDB   │
 │  (role prompt +  │                     │  19 tools, e.g.   │                         │  search &   │
 │   evidence rules)│◀────────────────────│  search_logs,     │◀────────────────────────│  aggregate  │
 └──────────────────┘   ④ evidence        │  risk_ranking     │   limited rows + counts └─────────────┘
          │               envelope [Q1]   └───────────────────┘
          │  ⑤ repeat ②–④ until it has enough evidence
          ▼
   ⑥ answer with [Q#] citations
```

1. **The role** sets which questions matter, what evidence to gather, and what kind of action to recommend.
2. **The model picks a tool** from the 19 tool descriptions and fills in its arguments. It never writes SQL.
3. **SereneDB does the work:** full-text search through the index, or `count(*)` / `GROUP BY` over the whole corpus. Only limited rows come back, but totals are computed in full.
4. **An evidence envelope comes back** (`Q1`, `Q2`, …) with the scope, filters, each SQL statement and its parameters, rows returned, a `truncated` flag, the results and caveat notes.
5. **The model repeats**, usually 2–6 times, for example search → investigate one line → look at what preceded it → check the template's history.
6. **The answer** follows the role's structure. Every number cites its evidence, hypotheses are labelled, unknowns are stated, counts are log lines (not incidents), and nothing about downtime or cost is invented.

### A real run, traced

The engineer question *"What is behind the 'Link has been severed' errors in BGL after September 2005?"* produced this trace.
Every number was re-checked against the database:

| Step | Tool call | What the database returned |
|---|---|---|
| Q1 | `corpus_overview()` | Dataset sizes, date ranges and team names |
| Q2 | `search_logs('"Link has been severed"', dataset=bgl, start=2005-09-01)` | **41,837** matching lines (exact `count(*)`), 2005-09-02 → 2006-01-03; 20 returned (truncated) |
| Q3 | `investigate_event(bgl, line 3326688)` | The failing line: FATAL, APP, team **job-runtime**, alert type APPSEV, peer `172.16.96.116`. The same template has 15,273 lines on 1,024 nodes over 27 days. The 10 lines after it come from 10 different nodes, half in the same second. |
| Q4 | `preceding_patterns(bgl, 3326688, 30 min)` | Templates over-represented in the 30 minutes before, compared with the whole dataset |

**Answer, in short:** the same FATAL error hit many nodes at once. A separate check confirmed that all 41,837 lines name the
same peer, `172.16.96.116`. That points to a shared cause (the service node or the network path to it) rather than many
independent node faults. The next check is the hardware and network logs for that peer at the times of the first errors.

Checking against the evidence trail matters. In this run the model (NVIDIA Nemotron) wrongly said the lines before the
failure came from the *same* node, and it over-read lift values that each rested on a single line. The trail showed both
mistakes. Since then, `preceding_patterns` ignores templates seen fewer than 2 times by default, and the engineer prompt
warns about both.

---

## 7. Reference

### Tools

There are 19 parameterised tools, layered from fundamental single-query primitives to decision-level rankings (the
agent picks them from their descriptions). Full inputs and outputs are in [FUNCTIONS.md](FUNCTIONS.md).

| Tool | Kind | Purpose |
|---|---|---|
| `corpus_overview` | aggregation | Whole-corpus totals per dataset, problems per team, lines per level |
| `search_logs` | full-text search | BM25-ranked search with time, dataset, team, component and level filters; exact total counts |
| `get_nearby_events` | retrieval | Lines just before and after an event, by log order or a ±minutes window (optionally same source) |
| `investigate_event` | retrieval + aggregation | Failing line, owner, context, recurrence, same-source problems, candidate cause, next checks |
| `preceding_patterns` | aggregation | Templates over-represented before an event (lift against the dataset baseline) |
| `template_history` | aggregation | Whole-dataset history of one template |
| `get_traceback` | retrieval | One complete multi-line exception: the `Traceback` anchor plus all continuation lines stitched into a single block |
| `trace_instance` | retrieval | Ordered lifecycle of one entity id (instance/request/tenant): every line mentioning it, in time order, with per-template summary |
| `extract_field` | structured extraction | Pull a value buried in message text (latency, HTTP status, request/tenant/instance ids) and summarise it: stats for numbers, top values for text |
| `filter_numeric` | structured extraction | Filter lines by an extracted numeric field, e.g. `latency > 1` (slow requests) or `status >= 500` |
| `recurring_problems` | aggregation | Templates that recur in a period: lines, active days/hours, sources, team |
| `count_problems` | aggregation | Counts grouped by team, component, template, level, alert type, source, severity or dataset |
| `problem_trend` | aggregation | Problem lines per hour, day, week or month, split by team, component, severity or dataset; `template_id` accepts a comma-separated list |
| `compare_periods` | aggregation | Two periods side by side: new, gone, rising, falling |
| `count_incidents` | aggregation | Collapses line bursts into episodes (one incident = one source + time bucket), per the lines-vs-incidents rule |
| `correlate_templates` | analytics | Which templates fire together: Pearson correlation of daily per-template counts, with teams and co-active days |
| `correlation_chains` | analytics | Strict ±1 correlations grouped into chains: confirmed only when the same bundle repeats on ≥ `min_repeats` days; timestamp-based `order_proposal` (a sequencing hint, not causation); negative pairs listed as regime indicators |
| `chain_profile` | analytics | One confirmed chain as a single repeated event: episodes, trend, proposed order and a ±context window around an episode |
| `risk_ranking` | aggregation | Ranked risks by pattern or by team, with share, spread and trend |

The MCP server also offers three **prompts**, `engineer`, `team_lead` and `manager`, each taking one `question` argument. They use the same text as the web app's role prompts.

### How the three roles differ

All roles use the same tools and data. Every answer starts with the Level 0 / Level 1 summaries and ends with the Show-your-work appendix; the role prompt changes the detailed sections in between - the questions asked, the evidence gathered and the action recommended:

| | Engineer | Team lead | Manager |
|---|---|---|---|
| Question | What failed, where, what happened around it, why? | What recurs, how often, who owns it, is it changing? | What deserves attention first, and who acts? |
| Evidence | Full-text hits with exact counts, surrounding lines, preceding patterns, template history | Recurring patterns, counts by team, weekly trend, period comparison | Risk ranking by pattern and team, whole-corpus totals |
| Output | Key log lines verbatim, a labelled hypothesis, a concrete diagnostic check | Table of patterns with owner, lines, active days and trend; what to investigate first and why | At most five ranked risks, each with evidence, owner and action; what the data does not show |

Evidence rules in every role prompt:
- Every number cites a `Q#` evidence id.
- Totals come from `count(*)`, never from a truncated sample.
- Counts are log lines, not incidents.
- Hypotheses are labelled, and unknowns are stated.
- No downtime, cost or customer impact, because the logs don't contain it.

### Data preparation

- **Parsing.**
  - BGL lines are `Label Timestamp Date Node Time NodeRepeat Type Component Level Content`.
  - OpenStack lines are `Logrecord Date Time Pid Level Component [ADDR] Content`. The three OpenStack files are merged in time order.
  - Malformed lines are repaired, not dropped: a missing NodeRepeat field (306 FATAL "Kill job" lines), garbled text, binary bytes, and OpenStack ERROR lines with no message. All 4,747,963 + 207,820 lines are kept.
- **Templates.**
  - Variable tokens are masked as `<*>`: any token containing a digit, paths, and UUIDs. Field names are kept, so `generating core.254` becomes `generating <*>`, and `lr:00004ed0 cr:…` becomes `lr:<*> cr:<*>`.
  - Lines with nothing constant left, such as BGL kernel register dumps, become `<numeric-only message>`.
  - Ids `E1, E2, …` are assigned in order of first appearance.
- **Team ownership.** The `ownership` rows in [sql/schema.sql](sql/schema.sql) map component prefixes to the teams of a fictional organisation (`KERNEL` → `compute-kernel`, `nova.compute` → `compute-core`, …). Edit them and run `python load.py` to change owners. Two OpenStack components have no owner and show as **unassigned**: `oslo_service.periodic_task` (192 ERROR lines) and `keystonemiddleware.auth_token` (6 lines).

### Local files and Docker

The SereneDB server reads files from *its own* filesystem, so inside Docker it can't see files on your computer. `load.py`
avoids the problem by streaming local CSVs with `COPY … FROM STDIN`. For your own SQL with `read_csv('/data/…')`, mount a
folder by adding `-v "$PWD/data":/data` to `docker run`. Remote files over HTTP(S) or S3 need no mount.

### All `.env` settings

| Setting | Needed for | What it does |
|---|---|---|
| `SERENEDB_DSN` | **everything** | How to connect to SereneDB ([1.3](#13-configure-env)) |
| `LOGIQ_PROVIDER` | web app, terminal | `anthropic` (default), `nvidia` or `openai` |
| `ANTHROPIC_API_KEY` *or* `ANTHROPIC_AUTH_TOKEN` | provider `anthropic` | Claude credentials; set only one of the two |
| `NVIDIA_API_KEY` | provider `nvidia` | Key from build.nvidia.com |
| `LOGIQ_BASE_URL`, `LOGIQ_API_KEY` | provider `openai` | The server's address, and its key if it needs one |
| `LOGIQ_MODEL` | optional | Model to use; each provider has a default |
| `LOGIQ_EFFORT` | optional, Claude only | `low` / `medium` (default) / `high` / `xhigh` / `max` |
| `BGL_SOURCE`, `OPENSTACK_SOURCE` | optional | Load data from somewhere other than `data/` ([1.4](#14-get-and-load-the-data)) |

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
mcp_server.py           MCP server: the same tools plus the three role prompts
prepare_data.py         downloads the Zenodo archives, parses raw logs into data/*.log_structured.csv
load.py, sql/schema.sql loader (COPY for local files) and schema (events, ownership, full-text index)
worked_investigation.py writes WORKED_INVESTIGATION.md from real agent runs
WORKED_INVESTIGATION.md one worked investigation per role, with every query
FUNCTIONS.md            tool reference
agent_tools.py          small CLI for raw-table inspection (not used by the agent)
```

### Known limitations

- **Our own templates.** The Zenodo archives contain raw logs only, so templates come from masking variable tokens, not from Loghub's Drain parser. Ids (`E1`…) therefore differ from Loghub's 2k samples, and masking can merge or split some messages.
- **OpenStack anomaly labels are not used.** `anomaly_labels.txt` names 4 VM instances with injected anomalies, but those labels are per instance, not per line. To look at one, search for its instance id, e.g. `search_logs("544fd51c-4edc-4780-baae-ba1d80a0acfc")`.
- **OpenStack line order.** The three files are merged by timestamp, so neighbouring `line_id`s can come from different files. Use `get_nearby_events(minutes=…, same_source=true)` to stay within one file.
- **Fictional ownership.** Teams come from the `ownership` table, which describes a fictional organisation; two OpenStack components are unassigned.
- **Heuristics, not root causes.** These are simple, documented rules: `candidate_cause` (the nearest earlier problem on the same source, or on any source within 1 hour), `lift`, the trend labels (a 1.5× change between halves; "single burst" when every line falls within 5% of the period) and `priority_score`. They point to where to look; they don't prove a cause.
- **Correlations and chains are heuristics too.** `correlate_templates`/`correlation_chains` use Pearson on log1p daily counts; r close to ±1 can mean 'always written together' (a report structure) as easily as interacting faults. Chain `order_proposal` ranks templates by average first-line time inside episodes — a sequencing hint, explicitly not causation. Strict (±0.999) chains need the bundle to repeat on ≥2 days; single-day coincidences stay candidates.
- **Lines are not incidents.** `count_incidents` collapses bursts into episodes (one incident = one source + time bucket), but the bucket is an approximation: a fault logging intermittently for an hour in one place still counts as one episode, and the same fault on many nodes counts as many. `active_hours`/`active_days` remain rough proxies.
- **Structured extraction is OpenStack-shaped.** The `extract_field`/`filter_numeric` registry (latency, status, request/tenant/instance ids) matches OpenStack message formats; on other datasets these return zero coverage until the registry is extended. All other tools are dataset-agnostic.
- **Timestamps** are as recorded in each log, with no timezone conversion. BGL and OpenStack cover different years, so trends are computed per dataset.
- **Web UI** has no login, and keeps conversations in memory, so a restart clears them. Run it locally.
- **Not implemented:** on-demand indexing, the OpenTelemetry Parquet data, and row-level security per role. These are optional extras, judged separately from the main score.
  - **Reading the OpenTelemetry data in place works:** `SELECT * FROM read_parquet('https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_000.parquet') LIMIT 10`.
  - **Measured here, for one file (1,000,000,000 rows):** schema 1.5 s, row count 0.2 s, sample rows 0.8 s, but a full aggregation by service and severity **132 s**, which is too slow for every agent question.
  - **The path forward:** an inverted index on a view of a chosen time window (refreshed with `REINDEX INDEX`), or a summary table built once.

### Data and citation

Loghub (Zhu et al., *Loghub: A Large Collection of System Log Datasets for AI-driven Log Analytics*, ISSRE 2023). For BGL, also
Oliner & Stearley, *What Supercomputers Say: A Study of Five System Logs*, DSN 2007.
