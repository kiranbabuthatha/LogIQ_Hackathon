import psycopg

from psycopg.rows import dict_row

DB_CONNECTION = (

    "host=localhost "

    "port=7890 "

    "dbname=postgres "

    "user=postgres"

)

def search_logs(

    keyword: str,

    component: str | None = None,

    level: str | None = None,

    limit: int = 20,

) -> list[dict]:

    """Search BGL log messages using optional component and severity filters."""

    if not keyword.strip():

        raise ValueError("keyword cannot be empty")

    if not 1 <= limit <= 100:

        raise ValueError("limit must be between 1 and 100")

    with psycopg.connect(DB_CONNECTION, row_factory=dict_row) as conn:

        with conn.cursor() as cur:

            cur.execute(

                f"""

                SELECT

                    line_id,

                    time_raw,

                    node,

                    component,

                    level,

                    content,

                    event_id

                FROM bgl_logs_clean

                WHERE content ILIKE %s

                  AND (%s IS NULL OR component = %s)

                  AND (%s IS NULL OR level = %s)

                ORDER BY timestamp_raw, line_id

                LIMIT {limit}

                """,

                (

                    f"%{keyword.strip()}%",

                    component,

                    component,

                    level,

                    level,

                ),

            )

            return cur.fetchall()