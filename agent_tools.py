import os
from typing import Optional

import psycopg
from dotenv import load_dotenv

load_dotenv()
DSN = os.environ["SERENEDB_DSN"]
TABLES = {"bgl", "openstack"}  # whitelist: table names can't be query parameters


def db(sql, params=()):
    with psycopg.connect(DSN) as c:
        return c.execute(sql, params).fetchall()


def list_levels(dataset: str):
    """Distinct log levels in a dataset with row counts, most frequent first."""
    assert dataset in TABLES
    return db(f'SELECT "Level", count(*) AS n FROM {dataset} GROUP BY 1 ORDER BY n DESC')


def error_summary(dataset: str, level: Optional[str] = None):
    """Most frequent message templates at a given level, per component.

    If level is omitted or has no rows, falls back to the most severe level present.
    """
    assert dataset in TABLES
    present = {lvl for lvl, _ in list_levels(dataset)}
    if level not in present:
        severity = ["FATAL", "CRITICAL", "ERROR", "SEVERE", "FAILURE", "WARNING", "WARN"]
        level = next((s for s in severity if s in present), None)
        if level is None:
            return []
    return db(
        f'SELECT "Component", "EventTemplate", count("LineId") AS n FROM {dataset} '
        'WHERE "Level" = %s GROUP BY 1, 2 ORDER BY n DESC LIMIT 10',
        (level,),
    )


def get_context(dataset: str, line_id: int, window: int = 10):
    """Log lines around a given LineId."""
    assert dataset in TABLES
    return db(
        f'SELECT "LineId", "Level", "Component", "Content" FROM {dataset} '
        'WHERE "LineId" BETWEEN %s AND %s ORDER BY "LineId"',
        (line_id - window, line_id + window),
    )


def search_logs(dataset: str, keyword: str, limit: int = 20):
    """Log lines whose Content contains keyword (case-insensitive)."""
    assert dataset in TABLES
    return db(
        f'SELECT "LineId", "Level", "Component", "Content" FROM {dataset} '
        'WHERE "Content" ILIKE %s ORDER BY "LineId" LIMIT %s',
        (f"%{keyword}%", limit),
    )


def show(headers, rows, width=100):
    """Print rows as an aligned table, truncating long cells."""
    rows = [[str(c) if len(str(c)) <= width else str(c)[: width - 1] + "…" for c in r] for r in rows]
    if not rows:
        print("(no rows)")
        return
    w = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    print("  ".join(h.ljust(w[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * x for x in w))
    for r in rows:
        print("  ".join(c.ljust(w[i]) for i, c in enumerate(r)))


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Inspect log datasets")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("levels", "errors", "context", "search"):
        s = sub.add_parser(name)
        s.add_argument("dataset", choices=sorted(TABLES))
        if name == "errors":
            s.add_argument("--level")
        if name == "context":
            s.add_argument("line_id", type=int)
            s.add_argument("--window", type=int, default=10)
        if name == "search":
            s.add_argument("keyword")
            s.add_argument("--limit", type=int, default=20)
    a = p.parse_args()

    if a.cmd == "levels":
        show(["Level", "Count"], list_levels(a.dataset))
    elif a.cmd == "errors":
        show(["Component", "Template", "Count"], error_summary(a.dataset, a.level))
    elif a.cmd == "context":
        show(["LineId", "Level", "Component", "Content"], get_context(a.dataset, a.line_id, a.window))
    else:
        show(["LineId", "Level", "Component", "Content"], search_logs(a.dataset, a.keyword, a.limit))
