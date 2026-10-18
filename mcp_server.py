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
engineer (search_logs, investigate_event, get_nearby_events), team lead (recurring_problems, count_problems, problem_trend),
manager (risk_ranking, corpus_overview). Every result includes the SQL it ran, its filters and whether rows were truncated.
Cite those numbers, treat counts as log lines rather than incidents, label hypotheses, and never invent business impact.
In the narrative, interpret results only: never mention tool names, tool inputs or SQL - the reader wants answers, not
plumbing. Structure every answer in tiers: '## Level 0 summary' first (one or two sentences, the whole result compressed, no plumbing, only the essential numbers), then '## Level 1 summary' (a short paragraph with the key findings, measured numbers and the main recommendation), then the detailed role sections, and finally the '## Show your work' appendix listing each query used: tool name, inputs, the SQL that
ran, rows returned and whether truncated, each tagged with its evidence id. All plumbing lives only in that appendix."""

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
