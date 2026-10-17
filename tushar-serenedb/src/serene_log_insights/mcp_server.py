from mcp.server.fastmcp import FastMCP

from serene_log_insights.tools.count_events import count_events

from serene_log_insights.tools.error_trends import get_error_trends as query_error_trends

from serene_log_insights.tools.nearby_events import get_nearby_events

from serene_log_insights.tools.search_logs import search_logs

mcp = FastMCP("SereneDB Log Insights")

@mcp.tool()

def search_log_messages(

    keyword: str,

    component: str | None = None,

    level: str | None = None,

    limit: int = 20,

) -> list[dict]:

    """Search BGL logs by keyword, optionally filtering by component and severity.

    Returns matching log records, not the total number of matches.

    """

    return search_logs(keyword, component, level, limit)

@mcp.tool()

def investigate_log(

    log_id: int,

    window_minutes: int = 60,

) -> list[dict]:

    """Find events on the same node within a time window around a BGL log.

    Use this to investigate what happened before and after a specific log.

    Results are limited to 100 records.

    """

    return get_nearby_events(log_id, window_minutes)

@mcp.tool()

def count_log_events(

    level: str | None = None,

    component: str | None = None,

) -> list[dict]:

    """Count BGL log messages grouped by component and severity.

    Counts represent log messages, not necessarily separate incidents.

    """

    return count_events(level, component)

@mcp.tool()
def get_error_trends(
    level: str | None = None,
    component: str | None = None,
) -> list[dict]:
    """Count log messages by date, optionally filtering severity and component.

    Returns all matching dates in chronological order.
    Counts represent log messages, not confirmed incidents.
    """
    return query_error_trends(level, component)


if __name__ == "__main__":

    mcp.run(transport="stdio")
