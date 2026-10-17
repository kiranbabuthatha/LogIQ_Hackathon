import psycopg

from psycopg.rows import dict_row

DB_CONNECTION = (

    "host=localhost "

    "port=7890 "

    "dbname=postgres "

    "user=postgres"

)

def count_events(

    level: str | None = None,

    component: str | None = None,

) -> list[dict]:

    """

    Count BGL log messages grouped by component and severity.

    Optional filters:

        level: e.g. FATAL, ERROR, INFO

        component: e.g. KERNEL, APP, MMCS

    """

    with psycopg.connect(DB_CONNECTION, row_factory=dict_row) as conn:

        with conn.cursor() as cur:

            cur.execute(

                """

                SELECT

                    component,

                    level,

                    COUNT(*) AS log_count

                FROM bgl_logs_clean

                WHERE (%s IS NULL OR level = %s)

                  AND (%s IS NULL OR component = %s)

                GROUP BY component, level

                ORDER BY log_count DESC

                """,

                (level, level, component, component),

            )

            return cur.fetchall()