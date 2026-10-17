"""LogIQ MCP server: exposes the same SereneDB tools to any MCP client (Claude Code, Claude Desktop).

    claude mcp add logiq -- /path/to/.venv/bin/python /path/to/mcp_server.py
"""
from mcp.server.mcpserver import MCPServer

import tools

INSTRUCTIONS = """LogIQ answers questions about BGL and OpenStack logs stored in SereneDB, for three roles:
engineer (search_logs, investigate_event, get_nearby_events), team lead (recurring_problems, count_problems, problem_trend),
manager (risk_ranking, corpus_overview). Every result includes the SQL it ran, its filters and whether rows were truncated.
Cite those numbers, treat counts as log lines rather than incidents, label hypotheses, and never invent business impact."""

server = MCPServer(name="logiq", instructions=INSTRUCTIONS)

for name, (fn, spec) in tools.TOOLS.items():
    params = "".join(f"\n- {p}: {s['description']}" for p, s in spec["properties"].items())
    server.add_tool(fn, name=name, description=spec["description"] + (f"\n\nParameters:{params}" if params else ""),
                    structured_output=False)

if __name__ == "__main__":
    server.run()
