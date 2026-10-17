import psycopg

from psycopg.rows import dict_row

DB_CONNECTION = (

    "host=localhost "

    "port=7890 "

    "dbname=postgres "

    "user=postgres"

)

def get_nearby_events(log_id: int, window_minutes: int = 60) -> list[dict]:

    """

    Retrieve events from the same node within a time window

    around a selected BGL log entry.

    """

    if log_id <= 0:

        raise ValueError("log_id must be a positive integer")

    if not 1 <= window_minutes <= 1440:

        raise ValueError("window_minutes must be between 1 and 1440")

    with psycopg.connect(DB_CONNECTION, row_factory=dict_row) as conn:

        with conn.cursor() as cur:

            # First, find the selected log entry.

            cur.execute(

                """

                SELECT node, timestamp_raw

                FROM bgl_logs_clean

                WHERE line_id = %s

                """,

                (log_id,),

            )

            target = cur.fetchone()

            if target is None:

                raise ValueError(f"Log ID {log_id} was not found")

            # Convert minutes into seconds.

            window_seconds = window_minutes * 60

            start_time = target["timestamp_raw"] - window_seconds

            end_time = target["timestamp_raw"] + window_seconds

            # Retrieve nearby events from the same node.

            cur.execute(

                """

                SELECT

                    line_id,

                    time_raw,

                    node,

                    component,

                    level,

                    content,

                    event_id

                FROM bgl_logs_clean

                WHERE node = %s

                  AND timestamp_raw BETWEEN %s AND %s

                ORDER BY timestamp_raw, line_id

                LIMIT 100

                """,

                (target["node"], start_time, end_time),

            )

            return cur.fetchall()