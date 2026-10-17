import psycopg
from psycopg.rows import dict_row

DB_CONNECTION = (
    "host=localhost "
    "port=7890 "
    "dbname=postgres "
    "user=postgres"
)


def get_error_trends(
    level: str | None = None,
    component: str | None = None,
) -> list[dict]:
    """Count BGL log messages by date, optionally filtering level and component.

    Return all matching dates in chronological order as date_raw/log_count rows.
    Counts represent log messages, not confirmed incidents. With no level filter,
    messages of all severities are counted.
    """
    with psycopg.connect(DB_CONNECTION, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT date_raw, COUNT(*) AS log_count
                FROM bgl_logs_clean
                WHERE (%s IS NULL OR level = %s)
                  AND (%s IS NULL OR component = %s)
                GROUP BY date_raw
                ORDER BY date_raw ASC
                """,
                (level, level, component, component),
            )
            return cur.fetchall()
