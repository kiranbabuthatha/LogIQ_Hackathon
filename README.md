# SereneDB Logs Hackathon

Small toolkit for analysing system logs stored in [SereneDB](https://serenedb.com) (Postgres-compatible).
It gives you query helpers an LLM agent can call, plus a command-line viewer that prints readable tables.

Datasets (parsed logs from [Loghub](https://github.com/logpai/loghub)):

| Table       | Source                          | Levels                                  |
|-------------|---------------------------------|-----------------------------------------|
| `bgl`       | Blue Gene/L supercomputer logs  | INFO, FATAL, ERROR, WARNING, SEVERE     |
| `openstack` | OpenStack cloud logs            | INFO, WARNING                           |

Columns used: `LineId`, `Level`, `Component`, `EventTemplate`, `Content`.

## Setup

Requires Python 3.9+ and access to a SereneDB instance that already has the `bgl` and `openstack` tables loaded.

```bash
git clone <your-repo-url>
cd serenedb-logs-hackathon
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env     # then edit .env with your connection string
```

`.env`:

```
SERENEDB_DSN=postgresql://user:password@host:port/dbname
```

`.env` is git-ignored. Never commit credentials.

## Command-line usage

```bash
python agent_tools.py levels bgl                          # which log levels exist, with counts
python agent_tools.py errors bgl                          # top message templates at the most severe level
python agent_tools.py errors bgl --level FATAL            # ...or at a specific level
python agent_tools.py context openstack 500 --window 5    # lines around LineId 500
python agent_tools.py search bgl "Link has been severed" --limit 10
```

Typical workflow: `levels` → `errors` → `search` to find a LineId → `context` to read the surrounding lines.
Long cells are truncated at 100 characters (`width` in `show()`).

## Using the functions from Python (for an agent)

```python
from agent_tools import list_levels, error_summary, get_context, search_logs

list_levels("bgl")                 # [(level, count), ...]
error_summary("bgl")               # [(component, template, count), ...]; falls back to the most severe level present
get_context("bgl", 1257, 5)        # [(LineId, Level, Component, Content), ...]
search_logs("bgl", "severed", 10)  # case-insensitive match on Content
```

Dataset names are whitelisted (`TABLES`) because table names cannot be SQL parameters. Values are always passed as parameters.

## Exploring with SQL (VS Code SQLTools)

1. Install the **SQLTools** and **SQLTools PostgreSQL/Cockroach Driver** extensions.
2. Add a PostgreSQL connection using the values from your DSN.
3. Run queries. Column names are case-sensitive, so quote them:

```sql
SELECT "Level", count(*) FROM bgl GROUP BY 1 ORDER BY 2 DESC;

SELECT "Component", "EventTemplate", count(*) AS n
FROM bgl WHERE "Level" IN ('FATAL','ERROR','SEVERE')
GROUP BY 1, 2 ORDER BY n DESC LIMIT 20;
```

## Project layout

```
agent_tools.py     query helpers + CLI
requirements.txt   dependencies
.env.example       template for your connection string
```
