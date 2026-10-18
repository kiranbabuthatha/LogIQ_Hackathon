"""LogIQ MCP server: exposes the same SereneDB tools to any MCP client (Claude Code, Claude Desktop).

    claude mcp add logiq -- /path/to/.venv/bin/python /path/to/mcp_server.py
"""
import functools

import psycopg
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

import agent
import os

import tools

INSTRUCTIONS = """LogIQ answers questions about BGL and OpenStack logs stored in SereneDB, for three roles:
engineer, team_lead, manager. The database searches and calculates; you interpret the returned evidence.
Every tool result ships an evidence envelope: scope, filters, each SQL statement with its parameters, rows
returned, a truncated flag, results and caveat notes. Evidence IDs [Q1], [Q2]... must be cited for every number.

Choosing tools (capabilities, not procedures - pick what fits the question):
- Fundamentals: corpus_overview (scope/names), search_logs (full-text, exact totals), get_nearby_events (context),
  count_problems (grouped exact counts), problem_trend (trends; template_id accepts comma lists; severity split),
  extract_field / filter_numeric (latency, status, request/tenant/instance ids from message text).
- Analytics: investigate_event (one-line deep dive + candidate cause), preceding_patterns (lift before an event),
  template_history (one template's biography), get_traceback (stitch a multi-line exception),
  trace_instance (ordered lifecycle of one UUID), count_incidents (line bursts -> episodes),
  correlate_templates / correlation_chains / chain_profile (co-firing and confirmed chains).
- Levels filter exactly (level="FATAL"); "highest-severity" = FATAL + SEVERE + FAILURE + CRITICAL - query per
  level and sum, stating coverage. Lines are not incidents: pair line counts with count_incidents episodes.

Interpretation discipline:
- Log order is not time order; same source or nearby timestamps do not prove causality; candidate_cause is a lead,
  never a cause. Recurrence does not prove continuous persistence; a high line count does not prove many failures.
- Never transfer component-level measurements to one template or vice versa; label hypotheses as hypotheses,
  proposed checks as not executed, and state what remains unknown.
- Never invent downtime, cost, customer impact, SLAs or team members.

Response style (match agent.py):
- Adapt role sections to the question and omit what does not apply.
- The narrative interprets results in plain language: tool names, inputs and SQL appear only in the
  "## SQL evidence" (or appendix) section - the reader wants answers, not plumbing.
- Label what the data does not establish."""

MCP_TRANSPORT = os.environ.get("LOGIQ_MCP_TRANSPORT", "stdio")
server = MCPServer(name="logiq", instructions=INSTRUCTIONS)


def _report_errors(fn):
    """Send bad-argument and database errors back to the model as the tool's error text, so it can correct the call.
    (MCP only passes the message through for ToolError; anything else reaches the client as a generic failure.)"""
    @functools.wraps(fn)  # keeps the signature, so the input schema is unchanged
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, TypeError) as e:
            raise ToolError(f"invalid arguments: {e}") from e
        except psycopg.Error as e:
            raise ToolError(f"database error ({type(e).__name__}): {str(e).strip().splitlines()[0]}. "
                            "Check that SereneDB is running and the data is loaded.") from e
    return wrapper


for name, (fn, spec) in tools.TOOLS.items():
    params = "".join(f"\n- {p}: {s['description']}" for p, s in spec["properties"].items())
    server.add_tool(_report_errors(fn), name=name,
                    description=spec["description"] + (f"\n\nParameters:{params}" if params else ""),
                    structured_output=False)

# Role prompts: the same instructions the web app's agent uses, so MCP users get the same role behaviour.
# In Claude Code they appear as /mcp__logiq__engineer, /mcp__logiq__team_lead and /mcp__logiq__manager.
MCP_NOTE = ("Tool results in this session carry no evidence_id: number your LogIQ tool calls Q1, Q2, … in the order you "
            "make them, cite those ids, and list each id with its tool and arguments at the end of the answer.")


def _role_prompt(role):
    def prompt(question: str) -> str:
        return f"{agent.system_prompt(role)}\n{MCP_NOTE}\n\nQuestion: {question}"
    return prompt


for role, spec in agent.ROLES.items():
    server.prompt(name=role, title=f"LogIQ: {spec['label']}",
                  description=f"{spec['goal']} Uses LogIQ's tools with the {spec['label'].lower()} instructions and "
                              f"evidence rules. Example: {spec['examples'][0]}")(_role_prompt(role))

if __name__ == "__main__":
    if MCP_TRANSPORT == "streamable-http":
        server.run(transport=MCP_TRANSPORT,
                   host=os.environ.get("LOGIQ_MCP_HOST", "0.0.0.0"),
                   port=int(os.environ.get("LOGIQ_MCP_PORT", "8001")))
    else:
        server.run(transport=MCP_TRANSPORT)
