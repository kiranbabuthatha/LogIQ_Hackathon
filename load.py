"""Build the LogIQ schema in SereneDB: python load.py

Where the data comes from, in order of precedence:
  1. BGL_SOURCE / OPENSTACK_SOURCE environment variables: a local CSV file, an HTTPS URL, or a path the SereneDB
     server itself can read.
  2. data/BGL.log_structured.csv and data/OpenStack.log_structured.csv, the full datasets written by
     `python prepare_data.py`.
  3. The Loghub 2k samples on GitHub.

Local CSV files are streamed from this machine with COPY ... FROM STDIN, so they work even when SereneDB runs in
Docker or on another host. URLs and server paths are read by SereneDB itself with read_csv.
"""
import os
import time
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv()
HERE = Path(__file__).parent
LOGHUB = "https://raw.githubusercontent.com/logpai/loghub/master"
DEFAULTS = {
    "BGL_SOURCE": (HERE / "data" / "BGL.log_structured.csv", f"{LOGHUB}/BGL/BGL_2k.log_structured.csv"),
    "OPENSTACK_SOURCE": (HERE / "data" / "OpenStack.log_structured.csv",
                         f"{LOGHUB}/OpenStack/OpenStack_2k.log_structured.csv"),
}
TABLE = {"BGL_SOURCE": "bgl", "OPENSTACK_SOURCE": "openstack"}
# Column types for local COPY; they match what read_csv infers from the Loghub structured CSVs.
DDL = {
    "bgl": 'CREATE TABLE bgl ("LineId" BIGINT, "Label" VARCHAR, "Timestamp" BIGINT, "Date" VARCHAR, "Node" VARCHAR, '
           '"Time" VARCHAR, "NodeRepeat" VARCHAR, "Type" VARCHAR, "Component" VARCHAR, "Level" VARCHAR, '
           '"Content" VARCHAR, "EventId" VARCHAR, "EventTemplate" VARCHAR)',
    "openstack": 'CREATE TABLE openstack ("LineId" BIGINT, "Logrecord" VARCHAR, "Date" DATE, "Time" TIME, "Pid" BIGINT, '
                 '"Level" VARCHAR, "Component" VARCHAR, "ADDR" VARCHAR, "Content" VARCHAR, "EventId" VARCHAR, '
                 '"EventTemplate" VARCHAR)',
}


def sources():
    out = {}
    for key, (local, sample) in DEFAULTS.items():
        value = os.getenv(key) or (str(local) if local.exists() else sample)
        out[key] = {"value": value, "local": Path(value).is_file()}
    return out


def statements(path, srcs):
    sql = Path(path).read_text()
    for key, s in srcs.items():
        sql = sql.replace("{" + key + "}", s["value"].replace("'", "''"))
    lines = [ln for ln in sql.splitlines() if not ln.lstrip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def copy_local(conn, table, path):
    """Stream a local CSV into a new table with COPY ... FROM STDIN."""
    conn.execute(DDL[table])
    size, sent, last = path.stat().st_size, 0, time.time()
    with open(path, "rb") as f, conn.cursor().copy(f"COPY {table} FROM STDIN (FORMAT csv, HEADER true)") as cp:
        while chunk := f.read(8 << 20):
            cp.write(chunk)
            sent += len(chunk)
            if time.time() - last > 5:
                print(f"     {table}: {100 * sent / size:.0f}% of {size / 1e6:.0f} MB")
                last = time.time()


def main():
    started = time.time()
    srcs = sources()
    for key, s in srcs.items():
        print(f"{TABLE[key]}: {'local file (COPY)' if s['local'] else 'read by SereneDB'} {s['value']}")
    with psycopg.connect(os.environ["SERENEDB_DSN"], autocommit=True) as conn:
        for stmt in statements(HERE / "sql" / "schema.sql", srcs):
            t0 = time.time()
            print("->", " ".join(stmt.split())[:80])
            target = next((k for k, t in TABLE.items() if stmt.startswith(f"CREATE TABLE {t} AS")), None)
            if target and srcs[target]["local"]:
                copy_local(conn, TABLE[target], Path(srcs[target]["value"]))
            else:
                conn.execute(stmt)
            if time.time() - t0 > 2:
                print(f"   ({time.time() - t0:.1f}s)")
        counts = conn.execute(
            "SELECT dataset, count(*), sum(CASE WHEN is_problem THEN 1 ELSE 0 END) "
            "FROM events_v GROUP BY 1 ORDER BY 1"
        ).fetchall()
    for dataset, rows, problems in counts:
        print(f"{dataset}: {rows:,} events, {problems:,} problems")
    print(f"done in {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()
