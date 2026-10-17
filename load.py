"""Build the LogIQ schema in SereneDB: python load.py

Sources default to the Loghub 2k samples on GitHub. Point them at other
structured CSVs (same columns) with BGL_SOURCE / OPENSTACK_SOURCE; a local
path must be visible to the SereneDB server (e.g. a Docker volume mount).
"""
import os
import time
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv()
LOGHUB = "https://raw.githubusercontent.com/logpai/loghub/master"
SOURCES = {
    "BGL_SOURCE": os.getenv("BGL_SOURCE", f"{LOGHUB}/BGL/BGL_2k.log_structured.csv"),
    "OPENSTACK_SOURCE": os.getenv(
        "OPENSTACK_SOURCE", f"{LOGHUB}/OpenStack/OpenStack_2k.log_structured.csv"
    ),
}


def statements(path):
    sql = Path(path).read_text()
    for key, value in SOURCES.items():
        sql = sql.replace("{" + key + "}", value.replace("'", "''"))
    lines = [ln for ln in sql.splitlines() if not ln.lstrip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def main():
    started = time.time()
    with psycopg.connect(os.environ["SERENEDB_DSN"], autocommit=True) as conn:
        for stmt in statements(Path(__file__).parent / "sql" / "schema.sql"):
            print("->", " ".join(stmt.split())[:80])
            conn.execute(stmt)
        counts = conn.execute(
            "SELECT dataset, count(*), sum(CASE WHEN is_problem THEN 1 ELSE 0 END) "
            "FROM events_v GROUP BY 1 ORDER BY 1"
        ).fetchall()
    for dataset, rows, problems in counts:
        print(f"{dataset}: {rows} events, {problems} problems")
    print(f"done in {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()
