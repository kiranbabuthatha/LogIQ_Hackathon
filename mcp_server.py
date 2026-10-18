"""LogIQ MCP server: exposes the same SereneDB tools to any MCP client (Claude Code, Claude Desktop).

    claude mcp add logiq -- /path/to/.venv/bin/python /path/to/mcp_server.py
"""
import functools

import psycopg
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

import tools

INSTRUCTIONS = """LogIQ answers questions about BGL and OpenStack logs stored in SereneDB, for three roles:
engineer (search_logs, investigate_event, get_nearby_events), team lead (recurring_problems, count_problems, problem_trend),
manager (risk_ranking, corpus_overview). Every result includes the SQL it ran, its filters and whether rows were truncated.
Cite those numbers, treat counts as log lines rather than incidents, label hypotheses, and never invent business impact."""

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

if __name__ == "__main__":
    server.run()
