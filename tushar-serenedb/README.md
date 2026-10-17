# SereneDB Log Insights

### LogIQ Hackathon — Contribution by Tushar Rao

## 1. Project Overview

This module is part of the **LogIQ Hackathon** project.

**Contributor:** Tushar Rao  

**Module:** SereneDB Log Investigation & MCP Integration  

**Status:** Working prototype — further development in progress

### The Problem

Modern software systems generate thousands or millions of log messages.

These messages contain useful information about system errors, failures, warnings, and operational behavior. However, manually analyzing large volumes of logs is difficult and time-consuming.

Different stakeholders need different insights:

| User Role | Primary Question |

|---|---|

| Engineer | What failed, where, and what should I investigate? |

| Team Lead | Which errors occur most frequently? |

| Manager | Which problems should we prioritize? |

### Our Solution

We are building an AI-powered log investigation system that connects an AI agent to SereneDB through MCP (Model Context Protocol).

The system allows users to ask natural-language questions about logs.

The AI agent selects appropriate tools, retrieves evidence from SereneDB, and explains the findings.

**Core principle:**

> SereneDB retrieves and calculates evidence. The AI agent interprets the evidence and recommends next steps.

The AI should not invent error counts, root causes, incidents, or business impact.

---

## 2. System Architecture

```text

                  USER

                   |

                   |

         Natural-language question

                   |

                   v

                AI AGENT

                 (Codex)

                   |

                   |

         Selects appropriate tool

                   |

                   v

                MCP SERVER

                   |

                   |

           Calls Python function

                   |

                   v

               PYTHON TOOLS

                   |

                   |

            Executes SQL query

                   |

                   v

                SERENEDB

                   |

                   |

           Returns actual evidence

                   |

                   v

               PYTHON TOOLS

                   |

                   v

                AI AGENT

                   |

                   |

         Explains findings and

         recommends next checks

                   |

                   v

                  USER

```

### Component Responsibilities

| Component | Responsibility |

|---|---|

| User | Asks questions in natural language |

| AI Agent | Understands questions, selects tools, interprets results |

| MCP Server | Exposes Python tools to the AI agent |

| Python Tools | Execute predefined database operations |

| SereneDB | Stores, searches, filters, and aggregates log records |

| BGL Dataset | Supplies real-world log messages |

### Why This Architecture?

Sending millions of log messages directly to an LLM is inefficient and unreliable.

Instead:

1. The AI identifies what information is needed.

2. An appropriate MCP tool is selected.

3. SereneDB performs the search or calculation.

4. The database returns relevant evidence.

5. The AI explains the evidence.

This keeps database calculations separate from AI-generated explanations.

---

## 3. Technologies Used

| Technology | Purpose |

|---|---|

| Python 3.13 | Backend programming |

| SereneDB 26.04.4 | Database for storing and analyzing logs |

| Docker | Running SereneDB locally |

| Psycopg 3 | Connecting Python to SereneDB |

| MCP Python SDK 1.x | Exposing tools through MCP |

| Codex | AI agent used for development and testing |

| SQL | Searching, filtering, and aggregating logs |

| Git/GitHub | Version control and team collaboration |

### Important Dependency Note

The MCP server currently uses the MCP Python SDK v1 API:

```python

from mcp.server.fastmcp import FastMCP

```

MCP SDK v2 introduced breaking API changes.

Install the compatible version:

```bash

python -m pip install "mcp>=1,<2"

```

The implementation was successfully tested with MCP SDK 1.30.0.

---

## 4. Dataset

### BGL — Blue Gene/L

We use the public BGL log dataset from Loghub.

Source repository:

https://github.com/logpai/loghub

BGL contains operational log messages collected from a supercomputer.

### Current Data Scope

| Property | Value |

|---|---|

| Dataset | BGL |

| Imported records | 2,000 |

| Database | SereneDB |

| Database table | `bgl_logs_clean` |

| Total FATAL messages | 347 |

| Unique log IDs | 2,000 |

**Important:** Current findings apply only to the imported 2,000-record sample, not the complete BGL dataset.

### Structured CSV Fields

The original BGL structured CSV contains:

```text

LineId

Label

Timestamp

Date

Node

Time

NodeRepeat

Type

Component

Level

Content

EventId

EventTemplate

```

These fields provide information about:

- Log identity

- Timestamp

- Affected node

- Component

- Severity

- Message content

- Event pattern

### Database Schema

The current database table was created with:

```sql

CREATE TABLE bgl_logs_clean (

    line_id INTEGER,

    label TEXT,

    timestamp_raw BIGINT,

    date_raw TEXT,

    node TEXT,

    time_raw TEXT,

    node_repeat TEXT,

    type TEXT,

    component TEXT,

    level TEXT,

    content TEXT,

    event_id TEXT,

    event_template TEXT

);

```

### Why We Created a Clean Table

The initial CSV import using SereneDB's SQL COPY command appeared successful but incorrectly handled some messages containing commas.

For example:

```text

CE sym 2, at 0x0b85eee0, mask 0x05

```

This caused incorrect field parsing.

We therefore implemented a Python ingestion script using:

- `csv.DictReader`

- Psycopg

- PostgreSQL-compatible COPY FROM STDIN

This preserved CSV field boundaries correctly.

The clean table was verified to contain:

- 2,000 records

- 2,000 unique log IDs

- No invalid event ID formats under the validation query

A previously corrupted example, log ID 8, was also verified.

**Note:** This does not constitute a complete field-by-field validation of all records.

---

## 5. Project Structure

```text

tushar-serenedb/

|

|-- README.md

|

|-- scripts/

|   |-- load_bgl_sample.py

|

|-- src/

|   |-- serene_log_insights/

|       |-- __init__.py

|       |

|       |-- mcp_server.py

|       |

|       |-- tools/

|           |-- __init__.py

|           |-- search_logs.py

|           |-- nearby_events.py

|           |-- count_events.py

|           |-- error_trends.py

|

|-- tests/

    |-- test_error_trends.py

```

### File Responsibilities

| File | Purpose |

|---|---|

| `load_bgl_sample.py` | Imports structured BGL records into SereneDB |

| `search_logs.py` | Searches logs by keyword and optional filters |

| `nearby_events.py` | Retrieves messages around a selected log |

| `count_events.py` | Counts messages by component and severity |

| `error_trends.py` | Calculates daily message counts |

| `mcp_server.py` | Makes Python functions available as MCP tools |

| `test_error_trends.py` | Integration test for daily trend analysis |

---

## 6. Implemented Python Tools

Four tools are currently implemented.

### Tool 1 — Search Log Messages

**Python function:**

```python

search_logs(

    keyword: str,

    component: str | None = None,

    level: str | None = None,

    limit: int = 20

)

```

**Purpose:**

Find log messages containing a specific keyword.

**Example:**

```python

search_logs(

    keyword="TLB error",

    component="KERNEL",

    level="FATAL",

    limit=10

)

```

**Returns:**

Matching log records with fields including:

- Log ID

- Timestamp

- Node

- Component

- Severity

- Message content

- Event ID

**Current implementation:**

Uses SQL `ILIKE` for case-insensitive substring matching.

**Limitation:**

This is not yet a dedicated full-text search implementation.

The default result limit is 20, with a maximum allowed limit of 100.

Returned results are matching examples, not the total number of matches.

---

### Tool 2 — Investigate Nearby Events

**Python function:**

```python

get_nearby_events(

    log_id: int,

    window_minutes: int = 60

)

```

**Purpose:**

Investigate messages occurring on the same node around a selected log.

**Example:**

```python

get_nearby_events(

    log_id=104,

    window_minutes=60

)

```

**How It Works:**

1. Finds the selected log.

2. Retrieves its node and timestamp.

3. Calculates the requested time window.

4. Searches for messages from the same node.

5. Returns matching records chronologically.

**Constraints:**

- Window: 1–1,440 minutes

- Maximum returned records: 100

- Uses the BGL timestamp stored in seconds

**Important:**

This tool retrieves nearby messages from the same node. It does not automatically establish causal relationships.

---

### Tool 3 — Count Log Events

**Python function:**

```python

count_events(

    level: str | None = None,

    component: str | None = None

)

```

**Purpose:**

Count log messages grouped by component and severity.

**Example:**

```python

count_events(level="FATAL")

```

**Verified Result:**

| Component | FATAL Messages |

|---|---:|

| KERNEL | 240 |

| APP | 107 |

**Interpretation:**

KERNEL produced more FATAL log messages than APP in the imported sample.

This makes KERNEL a reasonable starting point for further investigation.

**Important:**

Message counts do not necessarily represent separate incidents.

---

### Tool 4 — Error Trends

**Python function:**

```python

get_error_trends(

    level: str | None = None,

    component: str | None = None

)

```

**Purpose:**

Calculate daily log-message counts.

**Example:**

```python

get_error_trends(level="FATAL")

```

**Expected Output Structure:**

```python

[

    {

        "date_raw": "2005.06.11",

        "log_count": 60

    },

    {

        "date_raw": "2005.06.14",

        "log_count": 128

    }

]

```

The example above shows selected dates, not the complete result.

**Implementation Details:**

- Groups records by `date_raw`

- Supports optional severity filtering

- Supports optional component filtering

- Uses parameterized SQL

- Returns dates in chronological order

- Does not limit the number of returned dates

**Status:**

Implemented and registered with MCP.

Local structural checks passed.

The integration test against the running database is still pending.

---

## 7. MCP Server

The MCP server is located at:

```text

src/serene_log_insights/mcp_server.py

```

It uses the MCP Python SDK.

### Exposed MCP Tools

| MCP Tool | Python Implementation |

|---|---|

| `search_log_messages` | `search_logs()` |

| `investigate_log` | `get_nearby_events()` |

| `count_log_events` | `count_events()` |

| `get_error_trends` | `get_error_trends()` |

### Why MCP?

MCP provides a standardized interface through which an AI agent can discover and call external tools.

Without MCP, we would need to create custom integration code for each AI client.

With MCP, compatible clients can access the same tools.

### MCP Transport

The current server uses:

```python

mcp.run(transport="stdio")

```

This means the AI client launches the MCP server as a local process and communicates through standard input/output.

The MCP server does not need its own HTTP endpoint in this configuration.

---

## 8. Verified AI Agent Demonstrations

We connected the MCP server to Codex and successfully tested two workflows.

### Demo 1 — Team Lead Investigation

**Question:**

> Which components have the most FATAL log messages?

**Tool used:**

```python

count_log_events(level="FATAL")

```

**Database evidence:**

| Component | FATAL Messages |

|---|---:|

| KERNEL | 240 |

| APP | 107 |

**AI interpretation:**

KERNEL generated more than twice as many FATAL messages as APP.

Investigating KERNEL first may be useful because it has the highest observed message volume.

**Caution:**

These counts represent messages, not confirmed incidents.

---

### Demo 2 — Engineer Investigation

**Question:**

> What happened around the repeated "data TLB error interrupt" messages?

**Tools used:**

```python

search_log_messages(

    keyword="data TLB error interrupt",

    component="KERNEL",

    level="FATAL"

)

```

Then:

```python

investigate_log(

    log_id=104,

    window_minutes=60

)

```

**Observed Evidence:**

| Field | Finding |

|---|---|

| Component | KERNEL |

| Severity | FATAL |

| Node | R30-M0-N9-C:J16-U01 |

| Event ID | E55 |

| Message | data TLB error interrupt |

| Nearby messages | 13 |

| Log IDs | 104–116 |

| Date | June 11, 2005 |

| First returned timestamp | 17:32:07 |

| Last returned timestamp | 18:31:31 |

The investigation returned 13 repeated FATAL messages from the same node.

The initial search also found a later matching message at log ID 123.

**Possible Explanations:**

- A persistent condition may have triggered repeated errors.

- Repeated operations may have encountered the same underlying condition.

- Multiple log messages may describe one underlying failure.

None of these explanations has been confirmed.

**Recommended Next Checks:**

1. Extend the investigation timeline.

2. Correlate logs with job failures and monitoring records.

3. Inspect hardware and kernel diagnostics.

4. Compare behavior across neighboring nodes.

**Why This Demo Matters:**

The AI selected multiple tools and combined their results into an evidence-based investigation.

---

## 9. Additional SQL Analysis

### Daily FATAL Message Counts

A SQL query was executed to identify the dates with the most FATAL messages.

```sql

SELECT

    date_raw,

    COUNT(*) AS fatal_messages

FROM bgl_logs_clean

WHERE level = 'FATAL'

GROUP BY date_raw

ORDER BY fatal_messages DESC

LIMIT 10;

```

**Results:**

| Date | FATAL Messages |

|---|---:|

| 2005.06.14 | 128 |

| 2005.06.11 | 60 |

| 2005.08.12 | 15 |

| 2005.06.13 | 15 |

| 2005.09.20 | 13 |

| 2005.12.09 | 8 |

| 2005.11.21 | 7 |

| 2005.09.02 | 4 |

| 2005.07.11 | 3 |

| 2005.10.12 | 3 |

These are the 10 dates with the highest FATAL message counts, not a complete chronological trend.

### June 14 Investigation

On June 14, 2005:

- 128 FATAL messages were recorded.

- All 128 came from KERNEL.

- 38 distinct event patterns were observed.

Most frequent patterns:

| Event ID | Event Template | Count |

|---|---|---:|

| E52 | data storage interrupt | 22 |

| E76 | instruction address | 13 |

| E50 | data address | 8 |

| E43 | core configuration register | 7 |

| E84 | machine check: i-fetch | 5 |

| E101 | program interrupt: illegal instruction | 5 |

**Interpretation:**

The high message volume and variety of KERNEL diagnostic patterns make this date a useful investigation target.

However, multiple diagnostic messages may belong to the same underlying failure.

We cannot infer the number of incidents or the business impact from these counts alone.

---

## 10. Local Setup Instructions

### Prerequisites

- Python 3.13

- Docker Desktop

- Git

- SereneDB container image

- Public Loghub BGL dataset

### Step 1 — Start SereneDB

The development environment used:

```text

Image: serenedb/serenedb:26.04.4

Container: serenedb

Port: 7890

Database: postgres

User: postgres

```

If the container already exists:

```bash

docker start serenedb

```

If setting up a new machine, create a SereneDB container using the official image and expose port 7890.

**Architecture note:** The image used during development did not provide a native ARM64 image, so Docker's AMD64 emulation was used on Apple Silicon.

### Step 2 — Verify the Database

```bash

docker exec -it serenedb psql -h localhost -p 7890 -U postgres -d postgres -c "SELECT 1 + 1 AS result;"

```

Expected result:

```text

result

------

2

```

### Step 3 — Create a Python Environment

From the `tushar-serenedb` directory:

```bash

python3 -m venv .venv

```

Activate it:

```bash

source .venv/bin/activate

```

Install dependencies:

```bash

python -m pip install "psycopg[binary]" "mcp>=1,<2"

```

### Step 4 — Download the Dataset

Clone Loghub:

```bash

git clone https://github.com/logpai/loghub.git

```

The required sample file is:

```text

loghub/BGL/BGL_2k.log_structured.csv

```

### Step 5 — Create the Database Table

Connect to SereneDB and execute:

```sql

CREATE TABLE bgl_logs_clean (

    line_id INTEGER,

    label TEXT,

    timestamp_raw BIGINT,

    date_raw TEXT,

    node TEXT,

    time_raw TEXT,

    node_repeat TEXT,

    type TEXT,

    component TEXT,

    level TEXT,

    content TEXT,

    event_id TEXT,

    event_template TEXT

);

```

### Step 6 — Load the BGL Sample

The loader is:

```text

scripts/load_bgl_sample.py

```

Check the dataset path configured in the script.

Then run:

```bash

python scripts/load_bgl_sample.py

```

**Important:** The loader appends records. Running it twice can create duplicates.

### Step 7 — Verify Imported Data

```sql

SELECT

    COUNT(*) AS total_records,

    COUNT(DISTINCT line_id) AS unique_records

FROM bgl_logs_clean;

```

Expected:

```text

total_records: 2000

unique_records: 2000

```

---

## 11. Run the MCP Server

From the `tushar-serenedb` directory:

```bash

PYTHONPATH=src python src/serene_log_insights/mcp_server.py

```

The server uses stdio transport.

Normally, an MCP-compatible AI client should launch it automatically.

### Register With Codex

From the `tushar-serenedb` directory:

```bash

codex mcp add serene-log-insights --env PYTHONPATH="$PWD/src" -- "$PWD/.venv/bin/python" "$PWD/src/serene_log_insights/mcp_server.py"

```

Using absolute paths avoids problems when the AI client starts the server from another working directory.

Check registration:

```bash

codex mcp list

```

The server should appear as:

```text

serene-log-insights

```

### Test Through Codex

Ask:

```text

Use the serene-log-insights MCP server.

Call count_log_events with level="FATAL".

Report the exact counts by component.

Explain the findings for a team lead.

Distinguish log-message counts from confirmed incidents.

Do not modify database records.

```

Expected findings:

```text

KERNEL: 240

APP: 107

```

---

## 12. Testing

The current test file is:

```text

tests/test_error_trends.py

```

Run:

```bash

PYTHONPATH=src python -m unittest discover -s tests -v

```

### What the Test Checks

The trend test is intended to verify:

- Chronological ordering of dates

- Non-negative daily counts

- Total FATAL message count of 347

- June 14 FATAL message count of 128

### Current Test Status

Codex implemented the integration test but could not execute it against SereneDB because its sandbox blocked connections to localhost:7890.

Local checks of the tool's SQL structure and MCP registration passed.

**The integration test must still be run in a terminal with access to SereneDB.**

---

## 13. Evidence and Reliability Principles

The system follows several important principles.

### 1. Use Actual Database Evidence

Counts and trends should come from SereneDB queries.

The AI must not invent numerical findings.

### 2. Distinguish Messages From Incidents

A repeated error message does not necessarily represent a separate incident.

Incident identification requires additional correlation.

### 3. Separate Facts From Hypotheses

Answers should distinguish:

**Observed evidence:** What the database actually shows.

**Possible explanation:** What might explain the observations.

**Recommended action:** What an engineer should investigate next.

### 4. State Query Scope

Answers should identify relevant limitations, including:

- Dataset

- Filters

- Time window

- Result limits

- Sample size

### 5. Avoid Unsupported Business Claims

Do not invent:

- Financial losses

- Downtime

- Customer impact

- Confirmed root causes

These claims require additional evidence.

---

## 14. Current Limitations

The current implementation is a working prototype, not a complete production system.

Known limitations:

1. Only the 2,000-record BGL sample has been imported.

2. OpenStack ingestion is not yet implemented.

3. Search currently uses `ILIKE`, not dedicated full-text search.

4. No component-to-team ownership mapping exists yet.

5. Manager-level prioritization is not yet implemented.

6. Trend integration testing is pending.

7. The database is local and is not shared through GitHub.

8. Database connection settings are currently local-development defaults.

9. The ingestion script is not designed to prevent duplicate imports.

10. Nearby-event retrieval is limited to 100 records.

11. The current tools are focused on BGL.

---

## 15. Recommended Next Steps

### Priority 1 — Validate the Trend Tool

Run:

```bash

PYTHONPATH=src python -m unittest discover -s tests -v

```

Confirm that the trend tool returns correct daily counts.

### Priority 2 — Implement Full-Text Search

Investigate SereneDB's dedicated full-text search capabilities.

Replace or extend the current substring search with genuine full-text retrieval.

### Priority 3 — Add OpenStack Logs

Import the OpenStack sample.

Develop tools for investigating:

- API errors

- Component-level failures

- Request IDs

- Error frequencies

### Priority 4 — Implement Team Ownership

Create a small mapping between components and responsible teams.

This mapping can be fictional for the hackathon but must be clearly documented.

### Priority 5 — Build Manager Prioritization

Combine:

- Error counts

- Trends

- Affected components

- Responsible teams

- Evidence-based recommendations

### Priority 6 — Expand Dataset Coverage

Move beyond the 2,000-row sample.

Test ingestion and query performance with larger datasets.

### Priority 7 — Prepare Final Demonstration

Demonstrate three role-specific workflows:

**Engineer:** Investigate an error.

**Team Lead:** Identify recurring problems.

**Manager:** Prioritize investigation targets.

Each workflow should show:

```text
Question → Tool selection → Database evidence → Explanation → Recommendation
```

---

## 16. Contribution Summary

### Implemented by Tushar Rao
- Configured and tested local SereneDB.
- Imported and validated a 2,000-record BGL sample.
- Identified and corrected CSV parsing problems.
- Implemented Python-based database tools.
- Built the MCP server.
- Connected the MCP server to Codex.
- Demonstrated AI-driven log searching and investigation.
- Demonstrated component-level FATAL message aggregation.
- Investigated repeated KERNEL errors.
- Analyzed daily FATAL message patterns.
- Implemented a daily trend tool with a pending integration test.
- Documented current findings, limitations, and next steps.

### Contributor

**Tushar Rao**

LogIQ Hackathon — SereneDB Log Insights Module

---

## 17. Final Summary

This module demonstrates how an AI agent can investigate operational logs using SereneDB-backed tools exposed through MCP.

The current prototype successfully supports:

- Keyword-based log search
- Node-specific error investigation
- Component-level severity aggregation
- AI-generated explanations grounded in database evidence

Daily trend analysis has been implemented and awaits database integration-test verification.

The next development phase will focus on full-text search, OpenStack support, trend validation, and manager-level prioritization.

**Core design principle:**
> Let SereneDB handle searching and calculations. Let the AI agent explain the evidence and recommend appropriate next steps.
