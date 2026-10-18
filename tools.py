"""LogIQ tool layer: parameterised SereneDB queries the agent (and the web UI / MCP server) can call.

Every tool returns an *evidence envelope*:

    {
      "tool": name, "scope": what data was queried, "filters": the filters applied,
      "queries": [{"purpose", "sql", "params", "rows_returned", "truncated"}],
      "data": the result, "notes": caveats the reader must keep in mind
    }

Search and aggregation run in the database; only limited, already-calculated results come back.
Counts are matching *log lines*, not separate incidents.

Data model (created by the supplied loader):
  events        unified BGL + OpenStack lines (dataset, line_id, ts, source, component, level,
                is_alert, alert_type, template_id, template, content, severity 0-3)
  events_idx    inverted (full-text) index over events; `content @@ ...` must go through it
  events_v      events + owning team + is_problem (severity >= 1 or labelled alert)
  ownership     team-ownership table (dataset, component_prefix, team)
"""
import os
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

from pathlib import Path
load_dotenv(Path(__file__).resolve().parent / ".env")
DSN = os.environ["SERENEDB_DSN"]

DATASETS = ("bgl", "openstack")
SEVERITY_NAME = {3: "FATAL/SEVERE", 2: "ERROR", 1: "WARNING", 0: "INFO"}
BUCKETS = ("hour", "day", "week", "month")
GROUP_BY = {  # allowed grouping keys -> SQL expression (identifiers can't be parameters)
    "dataset": "dataset", "team": "team", "component": "component", "level": "level",
    "template": "template_id", "alert_type": "alert_type", "source": "source",
    "severity": "severity",
}
SPLIT_BY = {"none": None, "dataset": "dataset", "team": "team", "component": "component", "severity": "severity"}
CONTENT_CHARS = 300   # log text is cut to this length in returned rows
CAUSE_GAP = timedelta(hours=1)
LINES_NOT_INCIDENTS = "Counts are matching log lines, not separate incidents; one fault can log many lines."


# ------------------------------------------------------------------ plumbing

class Evidence:
    """Collects every query a tool runs so the result can be checked."""

    def __init__(self, tool, filters):
        self.tool, self.filters, self.queries, self.notes = tool, {k: v for k, v in filters.items() if v is not None}, [], []

    def run(self, purpose, sql, params=(), limit=None):
        with psycopg.connect(DSN, row_factory=dict_row) as c:
            rows = c.execute(sql, params).fetchall()
        truncated = limit is not None and len(rows) > limit
        rows = rows[:limit] if truncated else rows
        self.queries.append({"purpose": purpose, "sql": " ".join(sql.split()), "params": [_plain(p) for p in params],
                             "rows_returned": len(rows), "truncated": truncated})
        return [{k: _plain(v) for k, v in r.items()} for r in rows]

    def result(self, data, scope):
        return {"tool": self.tool, "scope": scope, "filters": self.filters,
                "queries": self.queries, "data": data, "notes": self.notes}


def _plain(v):
    if isinstance(v, datetime):
        return v.isoformat(sep=" ", timespec="seconds")
    if isinstance(v, Decimal):  # SUM() returns Decimal
        return int(v) if v == int(v) else float(v)
    return v


def _cut(text):
    return text if text is None or len(text) <= CONTENT_CHARS else text[:CONTENT_CHARS] + "…"


def _filters(dataset=None, start=None, end=None, team=None, component=None, problems_only=True, prefix=""):
    """Shared WHERE clause. `prefix` qualifies columns when joining."""
    if dataset is not None and dataset not in DATASETS:
        raise ValueError(f"dataset must be one of {DATASETS} or omitted")
    p = prefix
    sql, params = ["TRUE"], []
    if problems_only:
        sql.append(f"({p}severity >= 1 OR COALESCE({p}is_alert, false))")
    for col, val, op in ((f"{p}dataset", dataset, "="), (f"{p}ts", start, ">="), (f"{p}ts", end, "<"),
                         ("team", team, "="), (f"{p}component", component, "=")):
        if val is not None:
            sql.append(f"{col} {op} %s")
            params.append(val)
    return " AND ".join(sql), params


def _scope(dataset, start, end, problems_only=True):
    ds = dataset or "bgl + openstack"
    span = f"{start or 'start of data'} → {end or 'end of data'}"
    return f"{ds}, {span}, {'problem lines only (severity ≥ WARNING or labelled alert)' if problems_only else 'all lines'}"


# --------------------------------------------------------------- tools: all roles

def corpus_overview():
    """Whole-corpus facts: line counts, problem counts, alerts and time span per dataset, and problems per team."""
    ev = Evidence("corpus_overview", {})
    per_dataset = ev.run(
        "Totals per dataset over the whole corpus",
        "SELECT dataset, count(*) AS lines, sum(CASE WHEN is_problem THEN 1 ELSE 0 END) AS problem_lines, "
        "sum(CASE WHEN is_alert THEN 1 ELSE 0 END) AS labelled_alerts, count(DISTINCT template_id) AS templates, "
        "count(DISTINCT component) AS components, min(ts) AS first_ts, max(ts) AS last_ts "
        "FROM events_v GROUP BY dataset ORDER BY dataset")
    per_team = ev.run(
        "Problem lines per owning team over the whole corpus",
        "SELECT dataset, team, count(*) AS problem_lines, count(DISTINCT template_id) AS templates, "
        "max(severity) AS max_severity FROM events_v WHERE is_problem GROUP BY 1, 2 ORDER BY problem_lines DESC")
    levels = ev.run("Lines per level", "SELECT dataset, level, severity, count(*) AS lines FROM events_v GROUP BY 1, 2, 3 ORDER BY 1, 3 DESC")
    ev.notes += [LINES_NOT_INCIDENTS, "Timestamps are as recorded in each log (no timezone conversion)."]
    return ev.result({"datasets": per_dataset, "problems_by_team": per_team, "levels": levels}, "whole corpus: events_v (bgl + openstack), no filters")


# ------------------------------------------------------------------ tools: Engineer

def search_logs(text: str, dataset: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None,
                team: Optional[str] = None, component: Optional[str] = None, level: Optional[str] = None,
                problems_only: bool = False, sort: str = "relevance", limit: int = 20):
    """Full-text search over log content (SereneDB inverted index, BM25-ranked), with optional filters."""
    if sort not in ("relevance", "time"):
        raise ValueError("sort must be 'relevance' or 'time'")
    limit = max(1, min(int(limit), 100))
    ev = Evidence("search_logs", dict(text=text, dataset=dataset, start=start, end=end, team=team, component=component,
                                      level=level, problems_only=problems_only, sort=sort, limit=limit))
    # The @@ match must run against the index relation; team is resolved to its components inside the index scan.
    where, params = _filters(dataset, start, end, None, component, problems_only)
    if team:
        where += " AND component IN (SELECT component FROM component_team WHERE team = %s)"
        params.append(team)
    if level:
        where += " AND upper(level) = upper(%s)"
        params.append(level)
    match = f"FROM events_idx WHERE content @@ websearch_to_tsquery(%s) AND {where}"
    totals = ev.run("Count every match (not just the returned rows)",
                    f"SELECT dataset, count(*) AS matches, min(ts) AS first_ts, max(ts) AS last_ts {match} GROUP BY dataset",
                    (text, *params))
    order = "score DESC, ts" if sort == "relevance" else "ts, line_id"
    rows = ev.run(f"Matching lines, {'most relevant (BM25)' if sort == 'relevance' else 'oldest'} first",
                  "SELECT s.dataset, s.line_id, s.ts, s.level, s.component, ct.team, s.source, s.template_id, s.content, "
                  "round(s.score, 3) AS score FROM ("
                  "SELECT dataset, line_id, ts, level, component, source, template_id, content, "
                  f"BM25(events_idx.tableoid) AS score {match} ORDER BY {order} LIMIT %s"
                  f") s JOIN component_team ct USING (dataset, component) ORDER BY {order.replace('score', 's.score')}",
                  (text, *params, limit + 1), limit=limit)
    for r in rows:
        r["content"] = _cut(r["content"])
    total = sum(t["matches"] for t in totals)
    if total > len(rows):
        ev.notes.append(f"Showing {len(rows)} of {total} matching lines; use the per-dataset totals for frequency, not the sample.")
    ev.notes.append('Query syntax: words are AND-ed (stemmed), "quoted phrase", OR between alternatives, -word to exclude.')
    return ev.result({"total_matches": total, "matches_by_dataset": totals, "lines": rows},
                     _scope(dataset, start, end, problems_only))


def get_nearby_events(dataset: str, line_id: int, before: int = 5, after: int = 5,
                      minutes: Optional[float] = None, same_source: bool = False):
    """Lines around one log line, all levels. By default the neighbouring lines in log order; with `minutes`,
    every line within ±minutes of the target's timestamp (optionally only from the same source), up to 60 lines."""
    before, after = max(0, min(int(before), 50)), max(0, min(int(after), 50))
    ev = Evidence("get_nearby_events", dict(dataset=dataset, line_id=line_id, before=before, after=after,
                                            minutes=minutes, same_source=same_source or None))
    _filters(dataset)  # validates dataset
    cols = "line_id, ts, level, component, team, source, template_id, is_alert, alert_type, content"
    if minutes is None:
        rows = ev.run("Neighbouring lines in log order", f"SELECT {cols} FROM events_v "
                      "WHERE dataset = %s AND line_id BETWEEN %s AND %s ORDER BY line_id",
                      (dataset, line_id - before, line_id + after))
        scope = f"{dataset}, line_id {line_id - before}..{line_id + after}, all levels"
    else:
        t = ev.run("Target line", "SELECT ts, source FROM events_v WHERE dataset = %s AND line_id = %s", (dataset, line_id))
        if not t:
            ev.notes.append(f"line_id {line_id} not found in {dataset}.")
            return ev.result({"lines": []}, f"{dataset}, line_id {line_id}")
        ts = datetime.fromisoformat(t[0]["ts"])
        lo, hi = ts - timedelta(minutes=float(minutes)), ts + timedelta(minutes=float(minutes))
        extra, params = ("AND source = %s", (t[0]["source"],)) if same_source else ("", ())
        rows = ev.run(f"Lines within ±{minutes} min of the target", f"SELECT {cols} FROM events_v "
                      f"WHERE dataset = %s AND ts BETWEEN %s AND %s {extra} ORDER BY ts, line_id LIMIT 61",
                      (dataset, lo, hi, *params), limit=60)
        scope = f"{dataset}, {lo:%Y-%m-%d %H:%M:%S} → {hi:%Y-%m-%d %H:%M:%S}{', same source' if same_source else ''}, all levels"
    target = next((r for r in rows if r["line_id"] == line_id), None)
    for r in rows:
        r["content"] = _cut(r["content"])
        r["position"] = "target" if r["line_id"] == line_id else ("before" if r["line_id"] < line_id else "after")
        if target and r["ts"] and target["ts"]:
            r["seconds_from_target"] = int((datetime.fromisoformat(r["ts"]) - datetime.fromisoformat(target["ts"])).total_seconds())
    if not target:
        ev.notes.append(f"line_id {line_id} not found in {dataset}.")
    ev.notes.append("Neighbours are close in the log, not necessarily related; check sources and time gaps.")
    return ev.result({"lines": rows}, scope)


def preceding_patterns(dataset: str, line_id: int, minutes: float = 30, limit: int = 10, min_lines: int = 2):
    """Templates over-represented in the minutes before an event, compared with the whole dataset (lift).
    Templates seen fewer than `min_lines` times in the window are left out: lift from one line is not evidence."""
    limit, min_lines = max(1, min(int(limit), 30)), max(1, int(min_lines))
    ev = Evidence("preceding_patterns", dict(dataset=dataset, line_id=line_id, minutes=minutes, limit=limit,
                                             min_lines=min_lines))
    _filters(dataset)
    t = ev.run("Target line", "SELECT ts, source, template_id FROM events_v WHERE dataset = %s AND line_id = %s",
               (dataset, line_id))
    if not t:
        ev.notes.append(f"line_id {line_id} not found in {dataset}.")
        return ev.result({"patterns": []}, f"{dataset}, line_id {line_id}")
    ts = datetime.fromisoformat(t[0]["ts"])
    lo = ts - timedelta(minutes=float(minutes))
    rows = ev.run(
        f"Templates in the {minutes} min before the target vs their share of the dataset",
        "WITH win AS (SELECT * FROM events_v WHERE dataset = %s AND ts >= %s AND ts < %s), "
        "base AS (SELECT template_id, count(*) AS dataset_lines FROM events_v WHERE dataset = %s GROUP BY 1), "
        "tot AS (SELECT (SELECT count(*) FROM win) AS win_n, (SELECT count(*) FROM events_v WHERE dataset = %s) AS all_n) "
        "SELECT w.template_id, min(w.template) AS template, count(*) AS lines_in_window, "
        "sum(CASE WHEN w.source = %s THEN 1 ELSE 0 END) AS same_source_lines, max(w.severity) AS max_severity, "
        "b.dataset_lines, round((count(*) * 1.0 / t.win_n) / (b.dataset_lines * 1.0 / t.all_n), 1) AS lift "
        "FROM win w JOIN base b USING (template_id), tot t GROUP BY w.template_id, b.dataset_lines, t.win_n, t.all_n "
        "HAVING count(*) >= %s ORDER BY lift DESC, lines_in_window DESC LIMIT %s",
        (dataset, lo, ts, dataset, dataset, t[0]["source"], min_lines, limit + 1), limit=limit)
    if not rows:
        ev.notes.append(f"No template appears {min_lines}+ times in the {minutes} minutes before this event, so there is "
                        "no over-represented pattern; try a longer window or min_lines=1.")
    elif not any(r["same_source_lines"] for r in rows):
        ev.notes.append(f"None of these lines come from the failing line's source ({t[0]['source']}): they were logged by "
                        "other sources. Do not describe them as happening on the failing node.")
    ev.notes += ["lift = share of the window ÷ share of the whole dataset; lift > 1 means over-represented before the event.",
                 "This is co-occurrence in time, not proof of cause. Few lines in the window make lift unstable."]
    return ev.result({"target": {"line_id": line_id, **t[0]}, "window_start": f"{lo:%Y-%m-%d %H:%M:%S}", "patterns": rows},
                     f"{dataset}, {lo:%Y-%m-%d %H:%M:%S} → {t[0]['ts']} vs the whole dataset")


def template_history(dataset: str, template_id: str):
    """Full history of one message template: totals, spread, levels, busiest sources and days."""
    ev = Evidence("template_history", dict(dataset=dataset, template_id=template_id))
    _filters(dataset)
    summary = ev.run(
        "Totals for the template across the dataset",
        "SELECT min(template) AS template, count(*) AS lines, min(ts) AS first_ts, max(ts) AS last_ts, "
        "count(DISTINCT source) AS sources, count(DISTINCT CAST(ts AS DATE)) AS active_days, "
        "string_agg(DISTINCT level, ',') AS levels, string_agg(DISTINCT alert_type, ',') AS alert_types, "
        "string_agg(DISTINCT component, ',') AS components, string_agg(DISTINCT team, ',') AS teams "
        "FROM events_v WHERE dataset = %s AND template_id = %s", (dataset, template_id))[0]
    if not summary["lines"]:
        ev.notes.append(f"No template {template_id} in {dataset}.")
        return ev.result({"summary": summary}, f"{dataset}, template {template_id}")
    sources = ev.run("Busiest sources", "SELECT source, count(*) AS lines FROM events_v WHERE dataset = %s AND template_id = %s "
                     "GROUP BY 1 ORDER BY 2 DESC LIMIT 11", (dataset, template_id), limit=10)
    days = ev.run("Busiest days", "SELECT CAST(ts AS DATE) AS day, count(*) AS lines FROM events_v WHERE dataset = %s "
                  "AND template_id = %s GROUP BY 1 ORDER BY 2 DESC LIMIT 11", (dataset, template_id), limit=10)
    ev.notes.append(LINES_NOT_INCIDENTS)
    return ev.result({"summary": summary, "top_sources": sources, "busiest_days": days},
                     f"{dataset}, template {template_id}, whole dataset")


def investigate_event(dataset: str, line_id: int, window: int = 10):
    """One-call investigation of a log line: what failed, owning team, surrounding events, how often the same
    template occurs in the whole corpus, other problems from the same source, a candidate cause and next checks."""
    window = max(1, min(int(window), 30))
    ev = Evidence("investigate_event", dict(dataset=dataset, line_id=line_id, window=window))
    _filters(dataset)
    cols = "line_id, ts, level, severity, component, team, source, template_id, template, is_alert, alert_type, content"
    target = ev.run("The line under investigation", f"SELECT {cols} FROM events_v WHERE dataset = %s AND line_id = %s",
                    (dataset, line_id))
    if not target:
        ev.notes.append(f"line_id {line_id} not found in {dataset}.")
        return ev.result({}, f"{dataset}, line_id {line_id}")
    t = target[0]
    around = ev.run("Surrounding lines (all levels)", f"SELECT {cols} FROM events_v WHERE dataset = %s AND line_id BETWEEN %s AND %s "
                    "ORDER BY line_id", (dataset, line_id - window, line_id + window))
    recurrence = ev.run(
        "Same template across the whole dataset",
        "SELECT count(*) AS lines, count(DISTINCT source) AS sources, count(DISTINCT CAST(ts AS DATE)) AS active_days, "
        "min(ts) AS first_ts, max(ts) AS last_ts FROM events_v WHERE dataset = %s AND template_id = %s",
        (dataset, t["template_id"]))[0]
    same_source = ev.run(
        "Other problem lines from the same source",
        "SELECT line_id, ts, level, component, template_id, content FROM events_v "
        "WHERE dataset = %s AND source = %s AND is_problem AND line_id <> %s ORDER BY ts LIMIT 11",
        (dataset, t["source"], line_id), limit=10)

    tts = datetime.fromisoformat(t["ts"])
    earlier = [r for r in around if r["line_id"] < line_id and (r["severity"] >= 1 or r["is_alert"])]
    cause = next((r for r in reversed(earlier) if r["source"] == t["source"]), None) or next(
        (r for r in reversed(earlier) if tts - datetime.fromisoformat(r["ts"]) <= CAUSE_GAP), None)

    checks = []
    if recurrence["lines"] > 1 and recurrence["sources"] > 1:
        checks.append(f"Template {t['template_id']} appears {recurrence['lines']} times on {recurrence['sources']} sources over "
                      f"{recurrence['active_days']} day(s): look for a shared cause (common config, image, network or service).")
    elif recurrence["lines"] > 1:
        checks.append(f"Template {t['template_id']} repeats {recurrence['lines']} times but only on {t['source']}: start with that source.")
    else:
        checks.append("Only occurrence of this template: look for a one-off change around this time.")
    if cause:
        checks.append(f"Line {cause['line_id']} ({cause['level']}, {cause['component']}) came shortly before; confirm whether it leads to this event.")
    elif earlier:
        checks.append("Earlier problems in the window are on other sources and over 1 hour earlier; probably unrelated.")
    if same_source:
        checks.append(f"{len(same_source)} other problem line(s) from the same source; check them for a pattern.")

    for r in around + same_source + [t]:
        r["content"] = _cut(r["content"])
    before = [r for r in around if r["line_id"] < line_id]
    after = [r for r in around if r["line_id"] > line_id]
    for r in before + after:  # say it explicitly: neighbours in the log are often from other nodes
        r["same_source_as_target"] = r["source"] == t["source"]
    context = {f"{side}_{kind}": sum(r["same_source_as_target"] == same for r in rows)
               for side, rows in (("before", before), ("after", after)) for kind, same in (("same_source", True), ("other_sources", False))}
    ev.notes += [f"Context: {context['before_same_source']} of {len(before)} lines before and {context['after_same_source']} of "
                 f"{len(after)} after come from the same source ({t['source']}); the rest are from other sources. "
                 "Only attribute a line to the failing node when same_source_as_target is true.",
                 "candidate_cause is the nearest earlier problem on the same source (or any source within 1 hour). "
                 "It is a lead to check, not an established cause.", LINES_NOT_INCIDENTS]
    return ev.result({"what_failed": t, "context_summary": context, "before": before, "after": after,
                      "candidate_cause": cause, "recurrence_in_dataset": recurrence, "same_source_problems": same_source,
                      "suggested_checks": checks}, f"{dataset}, line {line_id} ± {window} plus whole-dataset recurrence")


# --------------------------------------------------------- tools: Team lead & Manager

def count_problems(group_by: str = "team", dataset: Optional[str] = None, start: Optional[str] = None,
                   end: Optional[str] = None, team: Optional[str] = None, component: Optional[str] = None,
                   problems_only: bool = True, limit: int = 25):
    """Aggregate counts grouped by dataset, team, component, level, template, alert_type or source."""
    if group_by not in GROUP_BY:
        raise ValueError(f"group_by must be one of {list(GROUP_BY)}")
    limit = max(1, min(int(limit), 100))
    key = GROUP_BY[group_by]
    ev = Evidence("count_problems", dict(group_by=group_by, dataset=dataset, start=start, end=end, team=team,
                                         component=component, problems_only=problems_only, limit=limit))
    where, params = _filters(dataset, start, end, team, component, problems_only)
    extra = ", min(template) AS example_template, min(team) AS team" if group_by == "template" else ""
    groups = ev.run(
        f"Lines grouped by {group_by}",
        f"SELECT dataset, {key} AS {group_by}, count(*) AS lines, count(DISTINCT template_id) AS templates, "
        f"count(DISTINCT source) AS sources, max(severity) AS max_severity, min(ts) AS first_ts, max(ts) AS last_ts{extra} "
        f"FROM events_v WHERE {where} GROUP BY dataset, {key} ORDER BY lines DESC LIMIT %s",
        (*params, limit + 1), limit=limit)
    total = ev.run("Total lines and groups in scope",
                   f"SELECT count(*) AS lines, count(DISTINCT dataset || ':' || COALESCE({key}, '')) AS groups "
                   f"FROM events_v WHERE {where}", params)[0]
    for g in groups:
        g["share_pct"] = round(100 * g["lines"] / total["lines"], 1) if total["lines"] else 0
    if total["groups"] > len(groups):
        ev.notes.append(f"Showing the top {len(groups)} of {total['groups']} groups.")
    ev.notes.append(LINES_NOT_INCIDENTS)
    return ev.result({"total_lines": total["lines"], "total_groups": total["groups"], "groups": groups},
                     _scope(dataset, start, end, problems_only))


def problem_trend(bucket: str = "week", dataset: Optional[str] = None, start: Optional[str] = None,
                  end: Optional[str] = None, team: Optional[str] = None, component: Optional[str] = None,
                  template_id: Optional[str] = None, split_by: str = "team"):
    """Problem lines per time bucket (hour/day/week/month), optionally split by dataset, team or component."""
    if bucket not in BUCKETS:
        raise ValueError(f"bucket must be one of {BUCKETS}")
    if split_by not in SPLIT_BY:
        raise ValueError(f"split_by must be one of {list(SPLIT_BY)}")
    ev = Evidence("problem_trend", dict(bucket=bucket, dataset=dataset, start=start, end=end, team=team,
                                        component=component, template_id=template_id, split_by=split_by))
    where, params = _filters(dataset, start, end, team, component)
    if template_id:
        ids = [t.strip() for t in template_id.split(",") if t.strip()]
        where += " AND template_id IN (%s)" % ", ".join(["%s"] * len(ids))
        params.extend(ids)
    split = SPLIT_BY[split_by]
    sel = f", {split} AS {split_by}" if split else ""
    rows = ev.run(f"Problem lines per {bucket}" + (f" and {split_by}" if split else ""),
                  f"SELECT dataset, date_trunc(%s, ts) AS period{sel}, count(*) AS lines FROM events_v WHERE {where} "
                  f"GROUP BY dataset, period{', ' + split if split else ''} ORDER BY period, lines DESC LIMIT 501",
                  (bucket, *params), limit=500)
    ev.notes += ["Buckets with zero problem lines are omitted.", LINES_NOT_INCIDENTS]
    return ev.result({"series": rows}, _scope(dataset, start, end))


def recurring_problems(dataset: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None,
                       team: Optional[str] = None, min_lines: int = 2, limit: int = 15):
    """Problem templates that recur in the period: lines, active days/hours, sources, owning team, first/last seen."""
    limit = max(1, min(int(limit), 50))
    ev = Evidence("recurring_problems", dict(dataset=dataset, start=start, end=end, team=team, min_lines=min_lines, limit=limit))
    where, params = _filters(dataset, start, end, team)
    rows = ev.run(
        "Recurring problem templates",
        "SELECT dataset, team, component, template_id, min(template) AS template, string_agg(DISTINCT level, ',') AS levels, "
        "max(alert_type) AS alert_type, count(*) AS lines, count(DISTINCT CAST(ts AS DATE)) AS active_days, "
        "count(DISTINCT date_trunc('hour', ts)) AS active_hours, count(DISTINCT source) AS sources, "
        f"min(ts) AS first_ts, max(ts) AS last_ts FROM events_v WHERE {where} "
        "GROUP BY dataset, team, component, template_id HAVING count(*) >= %s ORDER BY lines DESC LIMIT %s",
        (*params, int(min_lines), limit + 1), limit=limit)
    ev.notes += [LINES_NOT_INCIDENTS,
                 "active_hours is the number of distinct clock hours with at least one line: a rough count of separate episodes."]
    return ev.result({"patterns": rows}, _scope(dataset, start, end))


def compare_periods(before_start: str, before_end: str, after_start: str, after_end: str,
                    dataset: Optional[str] = None, group_by: str = "template", limit: int = 15):
    """Problem lines in two periods side by side, flagging groups that are new, gone, rising or falling."""
    if group_by not in GROUP_BY:
        raise ValueError(f"group_by must be one of {list(GROUP_BY)}")
    limit = max(1, min(int(limit), 50))
    key = GROUP_BY[group_by]
    ev = Evidence("compare_periods", dict(before_start=before_start, before_end=before_end, after_start=after_start,
                                          after_end=after_end, dataset=dataset, group_by=group_by, limit=limit))
    where, params = _filters(dataset)
    extra = ", min(template) AS example_template" if group_by == "template" else ""
    rows = ev.run(
        f"Problem lines per {group_by} in each period",
        f"SELECT * FROM (SELECT dataset, {key} AS {group_by}{extra}, "
        "sum(CASE WHEN ts >= %s AND ts < %s THEN 1 ELSE 0 END) AS before_lines, "
        "sum(CASE WHEN ts >= %s AND ts < %s THEN 1 ELSE 0 END) AS after_lines "
        f"FROM events_v WHERE {where} GROUP BY dataset, {key}) g WHERE before_lines + after_lines > 0 "
        "ORDER BY abs(after_lines - before_lines) DESC LIMIT %s",
        (before_start, before_end, after_start, after_end, *params, limit + 1), limit=limit)
    for r in rows:
        b, a = r["before_lines"], r["after_lines"]
        r["change"] = a - b
        r["status"] = "new" if b == 0 else "gone" if a == 0 else "rising" if a > b else "falling" if a < b else "flat"
    ev.notes += ["Raw line counts; periods of different length are not normalised.", LINES_NOT_INCIDENTS]
    return ev.result({"groups": rows}, f"{dataset or 'bgl + openstack'}, {before_start}→{before_end} vs "
                     f"{after_start}→{after_end}, problem lines only")


def _trend(first, second, lines, active, span):
    if lines < 4:
        return "too few lines to tell"
    if active is not None and active <= span * 0.05:
        return "single burst"
    if second > first * 1.5:
        return "rising"
    if first > second * 1.5:
        return "falling"
    return "steady"


def _team_risk(ev, dataset, start, end, top):
    """Teams ranked by severity-weighted problem lines, per dataset (each dataset covers a different period)."""
    teams, periods = [], {}
    for ds in ([dataset] if dataset else DATASETS):
        where, params = _filters(ds, start, end)
        b = ev.run(f"{ds}: time span and total problem lines in scope",
                   f"SELECT min(ts) AS lo, max(ts) AS hi, count(*) AS lines FROM events_v WHERE {where}", params)[0]
        if not b["lines"]:
            continue
        lo, hi = datetime.fromisoformat(b["lo"]), datetime.fromisoformat(b["hi"])
        mid = lo + (hi - lo) / 2
        periods[ds] = {"from": b["lo"], "to": b["hi"], "midpoint": f"{mid:%Y-%m-%d %H:%M:%S}", "problem_lines": b["lines"]}
        rows = ev.run(
            f"{ds}: problem lines per team with severity weighting and first-half count",
            "SELECT dataset, team, count(*) AS lines, "
            "sum(CASE WHEN severity >= 2 THEN severity WHEN is_alert THEN 2 ELSE severity END) AS priority_score, "
            "sum(CASE WHEN severity = 3 THEN 1 ELSE 0 END) AS fatal_severe, sum(CASE WHEN severity = 2 THEN 1 ELSE 0 END) AS errors, "
            "sum(CASE WHEN severity = 1 THEN 1 ELSE 0 END) AS warnings, sum(CASE WHEN is_alert THEN 1 ELSE 0 END) AS labelled_alerts, "
            "count(DISTINCT template_id) AS templates, count(DISTINCT source) AS sources, "
            "count(DISTINCT CAST(ts AS DATE)) AS active_days, sum(CASE WHEN ts < %s THEN 1 ELSE 0 END) AS first_half "
            f"FROM events_v WHERE {where} GROUP BY dataset, team ORDER BY priority_score DESC", (mid, *params))
        tops = ev.run(f"{ds}: most frequent problem template per team",
                      "SELECT team, template_id, template, lines FROM (SELECT team, template_id, min(template) AS template, "
                      "count(*) AS lines, row_number() OVER (PARTITION BY team ORDER BY count(*) DESC) AS rn "
                      f"FROM events_v WHERE {where} GROUP BY team, template_id) t WHERE rn = 1", params)
        top_by = {t["team"]: t for t in tops}
        for r in rows:
            first = r.pop("first_half")
            r["share_of_dataset_problems_pct"] = round(100 * r["lines"] / b["lines"], 1)
            r["trend"] = _trend(first, r["lines"] - first, r["lines"], None, None)
            r["trend_counts"] = {"first_half": first, "second_half": r["lines"] - first}
            t = top_by.get(r["team"])
            r["top_problem"] = t and {"template_id": t["template_id"], "template": t["template"], "lines": t["lines"]}
        teams += rows
    teams.sort(key=lambda r: r["priority_score"], reverse=True)
    ev.notes += ["priority_score = sum over problem lines of severity (3 fatal/severe, 2 error, 1 warning; labelled alerts count "
                 "at least 2). It weights frequency by severity; it is not a measure of business impact.",
                 "Trend compares the first and second half of each dataset's period.",
                 "The logs do not show downtime, users affected or cost.", LINES_NOT_INCIDENTS]
    return ev.result({"periods": periods, "teams": teams[:top]}, _scope(dataset, start, end))


def risk_ranking(dataset: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None,
                 top: int = 5, by: str = "pattern"):
    """Prioritised risks for a manager. by='pattern': problem templates ranked by severity, then active days, then lines.
    by='team': owning teams ranked by severity-weighted problem lines, with their top problem.
    Both include share of problems, spread and a trend (first vs second half of the period)."""
    if by not in ("pattern", "team"):
        raise ValueError("by must be 'pattern' or 'team'")
    top = max(1, min(int(top), 15))
    ev = Evidence("risk_ranking", dict(dataset=dataset, start=start, end=end, top=top, by=by))
    if by == "team":
        return _team_risk(ev, dataset, start, end, top)
    risks, periods = [], {}
    # Each dataset has its own time span, so trends are computed per dataset and then merged.
    for ds in ([dataset] if dataset else DATASETS):
        where, params = _filters(ds, start, end)
        b = ev.run(f"{ds}: time span and total problem lines in scope",
                   f"SELECT min(ts) AS lo, max(ts) AS hi, count(*) AS lines FROM events_v WHERE {where}", params)[0]
        if not b["lines"]:
            continue
        lo, hi = datetime.fromisoformat(b["lo"]), datetime.fromisoformat(b["hi"])
        mid, span = lo + (hi - lo) / 2, (hi - lo) or timedelta(seconds=1)
        periods[ds] = {"from": b["lo"], "to": b["hi"], "midpoint": f"{mid:%Y-%m-%d %H:%M:%S}", "problem_lines": b["lines"]}
        rows = ev.run(
            f"{ds}: problem patterns with severity, spread and first-half count",
            "SELECT dataset, team, template_id, min(template) AS template, max(severity) AS severity, count(*) AS lines, "
            "count(DISTINCT CAST(ts AS DATE)) AS active_days, count(DISTINCT source) AS sources, "
            "sum(CASE WHEN ts < %s THEN 1 ELSE 0 END) AS first_half, min(ts) AS first_ts, max(ts) AS last_ts "
            f"FROM events_v WHERE {where} GROUP BY dataset, team, template_id "
            "ORDER BY severity DESC, active_days DESC, lines DESC LIMIT %s",
            (mid, *params, top + 1), limit=top)
        for r in rows:
            first = r.pop("first_half")
            second = r["lines"] - first
            r["severity_label"] = SEVERITY_NAME.get(r["severity"], str(r["severity"]))
            r["share_of_dataset_problems_pct"] = round(100 * r["lines"] / b["lines"], 1)
            active = datetime.fromisoformat(r["last_ts"]) - datetime.fromisoformat(r["first_ts"])
            r["trend"] = _trend(first, second, r["lines"], active, span)
            if r["trend"] == "single burst":
                r["trend"] = f"single burst ({r['first_ts'][:10]})"
        risks += rows
    risks.sort(key=lambda r: (r["severity"], r["active_days"], r["lines"]), reverse=True)
    if not risks:
        ev.notes.append("No problem lines in this scope.")
    ev.notes += ["Trend compares the first half of each dataset's period (up to its midpoint) with the second half.",
                 "Ranking uses only log severity and frequency. The logs do not show downtime, users affected or cost.",
                 LINES_NOT_INCIDENTS]
    return ev.result({"periods": periods, "risks": risks[:top]}, _scope(dataset, start, end))



# ------------------------------------------- tools: structured extraction & correlation

# Fields that live inside free text (dataset-aware registry; identical schema for any dataset).
FIELDS = {
    "latency":     (r"time: ([0-9]+\.?[0-9]*)", "number", "request latency in seconds (OpenStack 'time:' field)"),
    "status":      (r"status: (\d{3})", "number", "HTTP status code (OpenStack)"),
    "req_id":      (r"req-([0-9a-fA-F-]{36})", "text", "OpenStack request id"),
    "tenant_id":   (r"\] [0-9a-fA-F]{32} ([0-9a-fA-F]{32}) ", "text", "second 32-hex token in the request bracket = tenant id"),
    "instance_id": (r"instances? ([0-9a-fA-F-]{36})", "text", "UUID right after the word 'instance(s)'"),
}


def get_traceback(dataset: str, line_id: int, max_lines: int = 40):
    """Return one complete multi-line exception (all continuation lines) for a traceback header line."""
    ev = Evidence("get_traceback", {"dataset": dataset, "line_id": line_id})
    cols = "line_id, ts, level, component, source, template_id, content"
    hdr = ev.run("Header line", f"SELECT {cols} FROM events_v WHERE dataset = %s AND line_id = %s", (dataset, line_id))
    if not hdr:
        raise ValueError("line_id not found in this dataset")
    h = hdr[0]
    anchor = ev.run(
        "Traceback anchor (nearest preceding 'Traceback (most recent call last)' in the same component)",
        f"SELECT {cols} FROM events_v WHERE dataset = %s AND line_id <= %s AND line_id > %s - 60 "
        "AND component = %s AND content LIKE '%%Traceback (most recent call last)%%' "
        "ORDER BY line_id DESC LIMIT 1",
        (dataset, line_id, line_id, h["component"]))
    start = anchor[0] if anchor else h
    rows = ev.run(
        "All exception lines: consecutive in log order, same component, within 2 s of the anchor",
        f"SELECT {cols} FROM events_v WHERE dataset = %s AND line_id >= %s AND component = %s "
        "AND ts <= (SELECT ts FROM events_v WHERE dataset = %s AND line_id = %s) + INTERVAL 2 SECOND "
        "ORDER BY line_id LIMIT %s",
        (dataset, start["line_id"], h["component"], dataset, start["line_id"], max_lines + 1))
    complete = len(rows) <= max_lines
    ev.notes += ["All returned lines belong to the same exception; treat them as one incident.",
                 "Window heuristic: consecutive log order, same component, within 2 s — may include the first line of the next message."]
    body = [{k: (_cut(v) if k == "content" else v) for k, v in r.items()} for r in rows]
    return ev.result({"start_line_id": start["line_id"], "lines": body,
                      "exception_last_line": body[-1]["content"] if body else None,
                      "truncated": not complete},
                     f"{dataset}, one exception around line_id {line_id}")


def trace_instance(dataset: str, instance_id: str, limit: int = 50):
    """Ordered lifecycle of one entity id (instance / request / tenant / node) across the dataset."""
    if len(instance_id) < 8:
        raise ValueError("instance_id must be at least 8 characters (avoid full-table scans)")
    ev = Evidence("trace_instance", {"dataset": dataset, "instance_id": instance_id})
    where, params = _filters(dataset=dataset, problems_only=False)
    like = f"%{instance_id}%"
    summary = ev.run(
        "Lines mentioning the id", f"SELECT count(*) AS lines, count(DISTINCT source) AS sources, "
        "count(DISTINCT template_id) AS templates, min(ts) AS first_ts, max(ts) AS last_ts "
        f"FROM events_v WHERE {where} AND content ILIKE %s", (*params, like))
    by_tpl = ev.run(
        "Templates involved (most lines first)",
        f"SELECT template_id, ANY_VALUE(template) AS template, count(*) AS lines "
        f"FROM events_v WHERE {where} AND content ILIKE %s GROUP BY template_id ORDER BY lines DESC LIMIT 15",
        (*params, like))
    rows = ev.run(
        "Lines in time order (the lifecycle)",
        f"SELECT line_id, ts, level, component, source, template_id, content FROM events_v "
        f"WHERE {where} AND content ILIKE %s ORDER BY ts, line_id LIMIT %s",
        (*params, like, limit + 1))
    truncated = len(rows) > limit
    ev.notes += ["Lines are matched by substring, so an id may also appear in messages about other objects.",
                 "Matching is a full scan without an index; keep the window small on large datasets."]
    return ev.result({"summary": summary[0], "templates": by_tpl,
                      "lines": [{k: (_cut(v) if k == "content" else v) for k, v in r.items()} for r in rows[:limit]],
                      "truncated": truncated},
                     f"{dataset}, lines mentioning {instance_id}")


def extract_field(dataset: str, field: str, start: Optional[str] = None, end: Optional[str] = None, top: int = 10):
    """Extract a structured field that lives inside the message text (latency, status, ids) and summarise it."""
    if field not in FIELDS:
        raise ValueError(f"field must be one of {sorted(FIELDS)}")
    pattern, kind, descr = FIELDS[field]
    ev = Evidence("extract_field", {"dataset": dataset, "field": field, "kind": kind})
    where, params = _filters(dataset=dataset, start=start, end=end, problems_only=False)
    total = ev.run("Lines in scope", f"SELECT count(*) AS n FROM events_v WHERE {where}", params)
    if kind == "number":
        rows = ev.run(
            "Distribution of the extracted numeric field (TRY_CAST keeps non-matching lines out)",
            f"SELECT count(*) AS n, MIN(v) AS min, MAX(v) AS max, AVG(v) AS avg, "
            "QUANTILE_CONT(v, 0.5) AS p50, QUANTILE_CONT(v, 0.95) AS p95 FROM "
            f"(SELECT TRY_CAST(regexp_extract(content, %s, 1) AS DOUBLE) AS v FROM events_v WHERE {where}) "
            "WHERE v IS NOT NULL", (pattern, *params))
        extracted = rows[0]["n"] or 0
        data = {"field": field, "description": descr, "lines_in_scope": total[0]["n"],
                "lines_with_field": extracted,
                "coverage_pct": round(100 * extracted / max(1, total[0]["n"]), 2),
                "stats": rows[0]}
    else:
        rows = ev.run(
            "Most common values of the extracted text field",
            f"SELECT regexp_extract(content, %s, 1) AS value, count(*) AS n FROM events_v WHERE {where} "
            "GROUP BY 1 ORDER BY n DESC LIMIT %s", (pattern, *params, top))
        extracted = sum(r["n"] for r in rows)
        data = {"field": field, "description": descr, "lines_in_scope": total[0]["n"],
                "distinct_values_shown": len(rows), "top_values": rows,
                "notes": "Counts cover the top values only; long tail not enumerated."}
    ev.notes += ["Extraction is a regex over message text; values not matching the pattern are excluded."]
    return ev.result(data, _scope(dataset, start, end, problems_only=False))


def filter_numeric(dataset: str, field: str, op: str, value: float,
                   start: Optional[str] = None, end: Optional[str] = None, limit: int = 20):
    """Filter lines by a numeric field extracted from the message text (e.g. latency > 1 s)."""
    if field not in FIELDS or FIELDS[field][1] != "number":
        raise ValueError(f"field must be a numeric field, one of {[k for k, v in FIELDS.items() if v[1] == 'number']}")
    if op not in ("<", "<=", ">", ">=", "="):
        raise ValueError("op must be one of < <= > >= =")
    pattern = FIELDS[field][0]
    ev = Evidence("filter_numeric", {"dataset": dataset, "field": field, "op": op, "value": value})
    where, params = _filters(dataset=dataset, start=start, end=end, problems_only=False)
    num = f"TRY_CAST(regexp_extract(content, %s, 1) AS DOUBLE)"
    stats = ev.run(
        "How many lines satisfy the numeric condition",
        f"SELECT count(*) AS n, MIN(v) AS min, MAX(v) AS max, AVG(v) AS avg FROM "
        f"(SELECT {num} AS v FROM events_v WHERE {where}) WHERE v IS NOT NULL AND v {op} %s",
        (pattern, *params, value))
    rows = ev.run(
        f"Sample lines with {field} {op} {value} (oldest first)",
        f"SELECT line_id, ts, level, component, source, template_id, content FROM events_v WHERE {where} "
        f"AND {num} {op} %s ORDER BY ts LIMIT %s",
        (*params, pattern, value, limit + 1))
    truncated = len(rows) > limit
    ev.notes += [f"Field extracted with regex {pattern!r}; TRY_CAST skips lines where the pattern does not match.",
                 LINES_NOT_INCIDENTS]
    return ev.result({"matching_lines": stats[0]["n"], "stats": stats[0],
                      "lines": [{k: (_cut(v) if k == "content" else v) for k, v in r.items()} for r in rows[:limit]],
                      "truncated": truncated},
                     f"{dataset}, {field} {op} {value}")


def count_incidents(dataset: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None,
                    bucket: str = "minute", top: int = 15, min_lines: int = 2):
    """Collapse line bursts into episodes: one incident = one (source, time bucket) with >=1 line of a template."""
    if bucket not in ("minute", "hour", "day"):
        raise ValueError("bucket must be minute, hour or day")
    ev = Evidence("count_incidents", {"dataset": dataset, "start": start, "end": end, "bucket": bucket})
    where, params = _filters(dataset=dataset, start=start, end=end)
    rows = ev.run(
        f"Episodes = distinct (source, {bucket}) pairs per template",
        f"SELECT template_id, ANY_VALUE(template) AS template, ANY_VALUE(team) AS team, count(*) AS lines, "
        f"count(DISTINCT source || '|' || date_trunc(%s, ts)) AS episodes, "
        f"count(DISTINCT source) AS sources, min(ts) AS first_ts, max(ts) AS last_ts "
        f"FROM events_v WHERE {where} GROUP BY template_id HAVING count(*) >= %s ORDER BY lines DESC LIMIT %s",
        (bucket, *params, min_lines, top))
    total = ev.run("Total problem lines in scope", f"SELECT count(*) AS n FROM events_v WHERE {where}", params)
    for r in rows:
        r["avg_lines_per_episode"] = round(r["lines"] / max(1, r["episodes"]), 1)
    ev.notes += [f"One episode = one (source, {bucket}) bucket; a fault logging 500 lines in one minute counts as 1 episode.",
                 "Episodes across different sources are counted separately (same fault on 12 nodes = 12 episodes).",
                 LINES_NOT_INCIDENTS]
    return ev.result({"total_problem_lines": total[0]["n"], "templates": rows}, _scope(dataset, start, end))


def _pearson(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    vx = sum((a - mx) ** 2 for a in xs) ** 0.5
    vy = sum((b - my) ** 2 for b in ys) ** 0.5
    return cov / (vx * vy) if vx and vy else 0.0


def correlate_templates(dataset: str, start: Optional[str] = None, end: Optional[str] = None,
                        min_r: float = 0.9, top: int = 20, min_active_days: int = 5, min_lines: int = 50):
    """Which templates fire together: Pearson correlation of daily per-template counts (like our offline analysis)."""
    ev = Evidence("correlate_templates", {"dataset": dataset, "start": start, "end": end,
                                          "min_r": min_r, "min_active_days": min_active_days})
    where, params = _filters(dataset=dataset, start=start, end=end)
    rows = ev.run(
        "Daily line counts per template",
        f"SELECT template_id, CAST(ts AS DATE) AS day, count(*) AS c FROM events_v "
        f"WHERE {where} GROUP BY 1, 2", params)
    names = ev.run(
        "Template texts", f"SELECT template_id, ANY_VALUE(template) AS template, ANY_VALUE(team) AS team, "
        f"count(*) AS lines FROM events_v WHERE {where} GROUP BY 1", params)
    text = {r["template_id"]: r for r in names}
    days = sorted({r["day"] for r in rows})
    per_tpl = {}
    for r in rows:
        per_tpl.setdefault(r["template_id"], {})[r["day"]] = r["c"]
    kept = {t: d for t, d in per_tpl.items()
            if len(d) >= min_active_days and sum(d.values()) >= min_lines and t in text}
    keys = sorted(kept)
    import math
    series = {t: [math.log1p(kept[t].get(d, 0)) for d in days] for t in keys}
    pairs = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = keys[i], keys[j]
            r = _pearson(series[a], series[b])
            if r >= min_r:
                pairs.append({"r": round(r, 3), "a": a, "b": b,
                              "template_a": text[a]["template"], "template_b": text[b]["template"],
                              "team_a": text[a]["team"], "team_b": text[b]["team"],
                              "lines_a": text[a]["lines"], "lines_b": text[b]["lines"],
                              "co_active_days": len(set(kept[a]) & set(kept[b]))})
    pairs.sort(key=lambda p: -p["r"])
    ev.notes += ["Pearson r over log1p(daily counts); high r means the templates rise and fall on the same days.",
                 "Correlation in time is not proof of cause; templates that always co-occur may be one incident written several ways."]
    return ev.result({"templates_considered": len(keys), "days": len(days), "pairs": pairs[:top]},
                     _scope(dataset, start, end))



# ------------------------------------------------- chain analysis (strict ±1 correlations)

import hashlib

def _daily_series(ev, where, params, min_active_days, min_lines):
    """Shared daily per-template series for correlation tools."""
    rows = ev.run("Daily line counts per template",
                  f"SELECT template_id, CAST(ts AS DATE) AS day, count(*) AS c "
                  f"FROM events_v WHERE {where} GROUP BY 1, 2", params)
    names = ev.run("Template texts",
                   f"SELECT template_id, ANY_VALUE(template) AS template, ANY_VALUE(team) AS team, "
                   f"count(*) AS lines FROM events_v WHERE {where} GROUP BY 1", params)
    text = {r["template_id"]: r for r in names}
    days = sorted({r["day"] for r in rows})
    per_tpl = {}
    for r in rows:
        per_tpl.setdefault(r["template_id"], {})[r["day"]] = r["c"]
    kept = {t: d for t, d in per_tpl.items()
            if len(d) >= min_active_days and sum(d.values()) >= min_lines and t in text}
    return days, kept, text


def _cliques(nodes, edges):
    """Maximal cliques (Bron-Kerbosch) over the strict-correlation graph."""
    adj = {n: set() for n in nodes}
    for a, b in edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    out = []

    def bk(R, P, X):
        if not P and not X:
            if len(R) >= 2:
                out.append(sorted(R))
            return
        pivot = max(P | X, key=lambda v: len(adj.get(v, set()) & P), default=None)
        for v in list(P - (adj.get(pivot, set()) & P if pivot else set())):
            bk(R | {v}, P & adj.get(v, set()), X & adj.get(v, set()))
            P = P - {v}
            X = X | {v}

    bk(set(), set(adj), set())
    return out


def _chain_id(templates):
    return "CH-" + hashlib.md5("|".join(sorted(templates)).encode()).hexdigest()[:8]


def correlation_chains(dataset: str, start: Optional[str] = None, end: Optional[str] = None,
                       min_r: float = 0.999, min_repeats: int = 2, top: int = 20):
    """Candidate chains from strict (+1 / -1) daily correlations. A chain is CONFIRMED only when the
    same template bundle repeats on >= min_repeats distinct days; otherwise it stays a candidate.
    Order inside a chain is a timestamp-based PROPOSAL (earliest average first line per episode), not causation."""
    ev = Evidence("correlation_chains", {"dataset": dataset, "start": start, "end": end,
                                         "min_r": min_r, "min_repeats": min_repeats})
    where, params = _filters(dataset=dataset, start=start, end=end)
    days, kept, text = _daily_series(ev, where, params, min_active_days=5, min_lines=50)
    keys = sorted(kept)
    import math
    series = {t: [math.log1p(kept[t].get(d, 0)) for d in days] for t in keys}

    def pearson(xs, ys):
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
        vx = sum((a - mx) ** 2 for a in xs) ** 0.5
        vy = sum((b - my) ** 2 for b in ys) ** 0.5
        return cov / (vx * vy) if vx and vy else 0.0

    pos, neg = [], []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            r = pearson(series[keys[i]], series[keys[j]])
            if r >= min_r:
                pos.append((keys[i], keys[j], r))
            elif r <= -min_r:
                neg.append((keys[i], keys[j], r))

    cliques = _cliques(keys, [(a, b) for a, b, _ in pos])
    # per-template earliest line per day (for the order proposal)
    tmin = ev.run("Earliest line per template and day",
                  f"SELECT template_id, CAST(ts AS DATE) AS day, min(ts) AS first_ts FROM events_v "
                  f"WHERE {where} GROUP BY 1, 2", params)
    first_ts = {}
    for r in tmin:
        first_ts.setdefault(r["template_id"], {})[r["day"]] = r["first_ts"]

    chains, candidates = [], []
    for c in cliques:
        active = [set(kept[t]) for t in c]
        co_days = sorted(set.intersection(*active))
        if not co_days:
            continue
        import datetime as _dt
        mid = days[len(days) // 2]
        first_half = sum(kept[t].get(d, 0) for t in c for d in co_days if d <= mid)
        second_half = sum(kept[t].get(d, 0) for t in c for d in co_days if d > mid)
        order_scores = {}
        for t in c:
            vals = [first_ts.get(t, {}).get(d) for d in co_days if first_ts.get(t, {}).get(d)]
            vals = [v if hasattr(v, "hour") else datetime.fromisoformat(str(v)) for v in vals]
            if vals:
                order_scores[t] = sum(v.hour * 3600 + v.minute * 60 + v.second for v in vals) / len(vals)
        order = [t for t, _ in sorted(order_scores.items(), key=lambda kv: kv[1])]
        info = {
            "chain_id": _chain_id(c), "size": len(c),
            "templates": [{"template_id": t, "template": text[t]["template"], "team": text[t]["team"]}
                          for t in sorted(c)],
            "order_proposal": order,
            "episodes": len(co_days), "episode_dates": [str(d) for d in co_days][:top],
            "lines_total": sum(kept[t].get(d, 0) for t in c for d in co_days),
            "trend_first_half_lines": first_half, "trend_second_half_lines": second_half,
        }
        (chains if len(co_days) >= min_repeats else candidates).append(info)
    chains.sort(key=lambda x: (-x["episodes"], -x["size"]))
    candidates.sort(key=lambda x: (-x["episodes"], -x["size"]))
    neg_out = [{"a": a, "b": b, "r": round(r, 3),
                "template_a": text[a]["template"], "template_b": text[b]["template"]}
               for a, b, r in sorted(neg, key=lambda x: x[2])[:top]]
    ev.notes += [
        "CONFIRMED chains repeated on >= min_repeats distinct days; candidates did not repeat yet (single-day coincidences).",
        "order_proposal ranks templates by their average first-line time of day inside episodes — a sequencing hint, NOT causation.",
        "Negative pairs fire on opposite days (mutual exclusion); they are indicators of distinct regimes, not co-firing chains.",
        "Correlation uses log1p(daily counts); r >= 0.999 is required for a pair to enter a chain.",
    ]
    return ev.result({"templates_considered": len(keys), "days": len(days),
                      "confirmed_chains": chains[:top], "candidate_chains": candidates[:top],
                      "negative_pairs": neg_out}, _scope(dataset, start, end))


def chain_profile(dataset: str, chain_id: Optional[str] = None, templates: Optional[list] = None,
                  context_minutes: int = 60, top: int = 10):
    """First-class profile of one chain event: frequency, trend, episode dates, proposed order and ±context.
    The chain must be a CONFIRMED one from correlation_chains (same bundle repeated >= 2 days)."""
    ev = Evidence("chain_profile", {"dataset": dataset, "chain_id": chain_id, "templates": templates})
    inner = correlation_chains(dataset)
    ev.queries.extend(inner["queries"])
    all_chains = inner["data"]["confirmed_chains"]
    if templates:
        want = sorted(templates)
        match = [c for c in all_chains if sorted(t["template_id"] for t in c["templates"]) == want]
    elif chain_id:
        match = [c for c in all_chains if c["chain_id"] == chain_id]
    else:
        raise ValueError("pass chain_id or templates")
    if not match:
        raise ValueError("chain not found among confirmed chains (it may not have repeated yet) — call correlation_chains")
    ch = match[0]
    tids = [t["template_id"] for t in ch["templates"]]
    where, params = _filters(dataset=dataset)
    ph = ", ".join(["%s"] * len(tids))
    episodes = ev.run(
        "Per-episode per-template first line and volume",
        f"SELECT CAST(ts AS DATE) AS day, template_id, count(*) AS lines, min(ts) AS first_ts "
        f"FROM events_v WHERE {where} AND template_id IN ({ph}) "
        f"AND CAST(ts AS DATE) IN ({', '.join(['%s'] * len(ch['episode_dates'][:top]))}) "
        f"GROUP BY 1, 2 ORDER BY 1, 3 DESC",
        (*params, *tids, *ch["episode_dates"][:top]))
    first_day = ch["episode_dates"][0] if ch["episode_dates"] else None
    ctx_min = int(context_minutes)
    context = ev.run(
        f"±{ctx_min} min around the first episode (all levels)",
        f"SELECT line_id, ts, level, component, source, template_id, content FROM events_v "
        f"WHERE dataset = %s AND CAST(ts AS DATE) = %s "
        f"AND ts >= (SELECT min(ts) FROM events_v WHERE dataset = %s AND CAST(ts AS DATE) = %s) - INTERVAL '{ctx_min}' MINUTE "
        f"AND ts <= (SELECT max(ts) FROM events_v WHERE dataset = %s AND CAST(ts AS DATE) = %s) + INTERVAL '{ctx_min}' MINUTE "
        f"ORDER BY ts LIMIT 60",
        (dataset, first_day, dataset, first_day, dataset, first_day))
    ev.notes += ["The chain is treated as ONE repeated event; all its templates share the same episodes.",
                 "order_proposal is derived from timestamps (earliest average first line per episode) — sequencing proposal, not causation.",
                 "Context is a sample from the first episode only (up to 60 lines).",
                 LINES_NOT_INCIDENTS]
    return ev.result({"chain_id": ch["chain_id"], "size": ch["size"], "templates": ch["templates"],
                      "order_proposal": ch["order_proposal"], "frequency": {"episodes": ch["episodes"],
                      "episode_dates": ch["episode_dates"][:top],
                      "trend_first_half_lines": ch["trend_first_half_lines"],
                      "trend_second_half_lines": ch["trend_second_half_lines"]},
                      "episodes_detail": episodes, "context_sample": context},
                     f"{dataset}, chain {ch['chain_id']} ({ch['size']} templates)")


# ------------------------------------------------------------- tool registry

_DATASET = {"type": "string", "enum": list(DATASETS), "description": "bgl or openstack. Omit for both."}
_START = {"type": "string", "description": "Inclusive start time, e.g. '2005-06-01' or '2005-06-01 08:00'. Omit for the start of data."}
_END = {"type": "string", "description": "Exclusive end time. Omit for the end of data."}
_TEAM = {"type": "string", "description": "Owning team, e.g. 'compute-kernel', 'job-runtime', 'compute-api' (see corpus_overview)."}
_COMPONENT = {"type": "string", "description": "Exact component name, e.g. 'KERNEL', 'APP', 'nova.compute.manager'."}

TOOLS = {
    "corpus_overview": (corpus_overview, {
        "description": "Whole-corpus summary: total lines, problem lines, labelled alerts and time span per dataset, problem lines per "
                       "owning team, and lines per level. Use first to learn the scope, the date range and team names, and whenever "
                       "an answer needs whole-corpus counts. Returns {datasets, problems_by_team, levels}.",
        "properties": {}, "required": []}),
    "search_logs": (search_logs, {
        "description": "Full-text search over log message text using the SereneDB inverted index (BM25 relevance ranking), with "
                       "optional time window, dataset, team, component and level filters. Use to find the specific error lines an "
                       "engineer asks about. Returns {total_matches, matches_by_dataset (exact counts and first/last time), lines "
                       "(at most `limit` rows)}. Use the totals, not the number of returned rows, for frequency.",
        "properties": {"text": {"type": "string", "description": 'Search text: words are AND-ed (stemmed); "exact phrase"; a OR b; -exclude.'},
                       "dataset": _DATASET, "start": _START, "end": _END, "team": _TEAM, "component": _COMPONENT,
                       "level": {"type": "string", "description": "Exact level, e.g. FATAL, ERROR, WARNING, INFO."},
                       "problems_only": {"type": "boolean", "description": "Only lines with severity ≥ WARNING or a labelled alert. Default false."},
                       "sort": {"type": "string", "enum": ["relevance", "time"], "description": "relevance (BM25, default) or time (oldest first)."},
                       "limit": {"type": "integer", "description": "Maximum lines to return (1-100, default 20)."}},
        "required": ["text"]}),
    "get_nearby_events": (get_nearby_events, {
        "description": "Lines around one log line, all levels, with seconds from the target. By default the neighbouring lines in log "
                       "order; with `minutes`, every line within ±minutes of the target's time (up to 60), optionally only from the "
                       "same source (node or log file). Use after search_logs to see what happened just before and after. Returns {lines}.",
        "properties": {"dataset": {**_DATASET, "description": "Dataset of the line."},
                       "line_id": {"type": "integer", "description": "line_id from a search result."},
                       "before": {"type": "integer", "description": "Lines before in log order (0-50, default 5). Ignored with minutes."},
                       "after": {"type": "integer", "description": "Lines after in log order (0-50, default 5). Ignored with minutes."},
                       "minutes": {"type": "number", "description": "Use a time window of ±minutes instead of log order."},
                       "same_source": {"type": "boolean", "description": "With minutes: only lines from the target's source."}},
        "required": ["dataset", "line_id"]}),
    "preceding_patterns": (preceding_patterns, {
        "description": "Message templates over-represented in the N minutes before an event compared with the whole dataset (lift). "
                       "Correlation in time, not proof: use it to propose a possible cause and the next check. Returns {target, patterns}.",
        "properties": {"dataset": {**_DATASET, "description": "Dataset of the line."},
                       "line_id": {"type": "integer", "description": "line_id of the event."},
                       "minutes": {"type": "number", "description": "Window before the event in minutes (default 30)."},
                       "limit": {"type": "integer", "description": "Maximum templates (1-30, default 10)."},
                       "min_lines": {"type": "integer", "description": "Ignore templates seen fewer times than this in the window (default 2)."}},
        "required": ["dataset", "line_id"]}),
    "template_history": (template_history, {
        "description": "Whole-dataset history of one message template (template_id such as 'E29'): lines, first/last seen, active "
                       "days, sources, levels, alert types, owning teams, busiest sources and days. Returns {summary, top_sources, busiest_days}.",
        "properties": {"dataset": {**_DATASET, "description": "Dataset of the template."},
                       "template_id": {"type": "string", "description": "Template id from a search or count result, e.g. 'E29'."}},
        "required": ["dataset", "template_id"]}),
    "investigate_event": (investigate_event, {
        "description": "Complete investigation of one log line: the failing line with component and owning team, the lines before "
                       "and after, how often the same template occurs in the whole dataset (lines, sources, active days), other "
                       "problems from the same source, a candidate cause (a lead, not proof) and suggested next checks. Use for "
                       "'what failed and why' questions once you have a line_id.",
        "properties": {"dataset": {**_DATASET, "description": "Dataset of the line."},
                       "line_id": {"type": "integer", "description": "line_id to investigate."},
                       "window": {"type": "integer", "description": "Lines either side to include (1-30, default 10)."}},
        "required": ["dataset", "line_id"]}),
    "count_problems": (count_problems, {
        "description": "Database aggregation: problem line counts grouped by dataset, team, component, level, template, alert_type or "
                       "source over a stated period, with templates, sources, max severity, first/last seen and share of total. "
                       "Use for 'which teams/components are involved' and whole-corpus counts. Returns {total_lines, total_groups, groups}.",
        "properties": {"group_by": {"type": "string", "enum": list(GROUP_BY), "description": "Grouping key (default team)."},
                       "dataset": _DATASET, "start": _START, "end": _END, "team": _TEAM, "component": _COMPONENT,
                       "problems_only": {"type": "boolean", "description": "Count only problem lines (default true). False counts all lines."},
                       "limit": {"type": "integer", "description": "Maximum groups to return (1-100, default 25)."}},
        "required": []}),
    "problem_trend": (problem_trend, {
        "description": "Trend of problem lines per hour, day, week or month, optionally split by dataset, team or component and "
                       "filtered to one template. Use to show whether a problem is growing, shrinking or a single burst. Returns {series}.",
        "properties": {"bucket": {"type": "string", "enum": list(BUCKETS), "description": "Time bucket (default week)."},
                       "dataset": _DATASET, "start": _START, "end": _END, "team": _TEAM, "component": _COMPONENT,
                       "template_id": {"type": "string", "description": "Restrict to one template, e.g. 'E55'."},
                       "split_by": {"type": "string", "enum": list(SPLIT_BY), "description": "Series split (default team)."}},
        "required": []}),
    "recurring_problems": (recurring_problems, {
        "description": "Problem templates that recur in a period, with owning team, component, lines, active days, active hours "
                       "(rough episode count), sources and first/last seen. Use for team-lead questions about recurring problems "
                       "and what to investigate first. Returns {patterns}.",
        "properties": {"dataset": _DATASET, "start": _START, "end": _END, "team": _TEAM,
                       "min_lines": {"type": "integer", "description": "Minimum lines for a template to count as recurring (default 2)."},
                       "limit": {"type": "integer", "description": "Maximum patterns (1-50, default 15)."}},
        "required": []}),
    "compare_periods": (compare_periods, {
        "description": "Problem lines in two periods side by side, grouped by template, team, component or other key, flagging each "
                       "group as new, gone, rising, falling or flat. Use for 'what changed' or 'is it getting worse' questions. Returns {groups}.",
        "properties": {"before_start": _START, "before_end": _END, "after_start": _START, "after_end": _END, "dataset": _DATASET,
                       "group_by": {"type": "string", "enum": list(GROUP_BY), "description": "Grouping key (default template)."},
                       "limit": {"type": "integer", "description": "Maximum groups (1-50, default 15)."}},
        "required": ["before_start", "before_end", "after_start", "after_end"]}),
    "risk_ranking": (risk_ranking, {
        "description": "Manager view of priorities, based only on observed log severity and frequency. by='pattern' (default): problem "
                       "templates ranked by severity, then active days, then lines, with owning team, share of the dataset's problem "
                       "lines, sources and trend (rising, falling, steady, single burst). by='team': owning teams ranked by "
                       "severity-weighted problem lines, with severity breakdown, trend and top problem. Returns {periods, risks|teams}.",
        "properties": {"dataset": _DATASET, "start": _START, "end": _END,
                       "by": {"type": "string", "enum": ["pattern", "team"], "description": "Rank patterns (default) or teams."},
                       "top": {"type": "integer", "description": "How many to return (1-15, default 5)."}},
        "required": []}),
    "get_traceback": (get_traceback, {
        "description": "Return one complete multi-line exception: finds the 'Traceback (most recent call last)' anchor and all "
                       "continuation lines (same component, consecutive in log order, within 2 s). Use for OpenStack Python "
                       "tracebacks where a single error is spread over many lines. Returns {start_line_id, lines, exception_last_line}.",
        "properties": {"dataset": {**_DATASET, "description": "Dataset of the line."},
                       "line_id": {"type": "integer", "description": "Any line of the exception, e.g. from search_logs."},
                       "max_lines": {"type": "integer", "description": "Safety cap on returned lines (default 40)."}},
        "required": ["dataset", "line_id"]}),
    "trace_instance": (trace_instance, {
        "description": "Ordered lifecycle of one entity id (instance, request, tenant or node): every line mentioning it, in time "
                       "order, plus per-template summary and first/last seen. Use to follow one UUID through the log. "
                       "Returns {summary, templates, lines}.",
        "properties": {"dataset": _DATASET,
                       "instance_id": {"type": "string", "description": "Entity id, at least 8 characters (e.g. an instance or request UUID)."},
                       "limit": {"type": "integer", "description": "Maximum lines returned (1-100, default 50)."}},
        "required": ["dataset", "instance_id"]}),
    "extract_field": (extract_field, {
        "description": "Extract a structured field that lives inside the message text and summarise it: numeric fields get "
                       "count/min/max/avg/p50/p95, text fields get the most common values. Fields: latency (OpenStack 'time:'), "
                       "status (HTTP code), req_id, tenant_id, instance_id. Use when the question needs a value buried in the text. "
                       "Returns distribution or top values with coverage.",
        "properties": {"dataset": _DATASET,
                       "field": {"type": "string", "enum": sorted(FIELDS), "description": "Field to extract."},
                       "start": _START, "end": _END,
                       "top": {"type": "integer", "description": "For text fields: how many top values (1-50, default 10)."}},
        "required": ["dataset", "field"]}),
    "filter_numeric": (filter_numeric, {
        "description": "Filter lines by a numeric field extracted from the message text (e.g. latency > 1 s, status >= 500). "
                       "Numeric fields: latency, status. Returns matching line count, min/max/avg and sample lines. Use for "
                       "'show me the slow requests' style questions.",
        "properties": {"dataset": _DATASET,
                       "field": {"type": "string", "enum": ["latency", "status"], "description": "Numeric field to filter on."},
                       "op": {"type": "string", "enum": ["<", "<=", ">", ">=", "="], "description": "Comparison operator."},
                       "value": {"type": "number", "description": "Threshold value."},
                       "start": _START, "end": _END,
                       "limit": {"type": "integer", "description": "Maximum sample lines (1-100, default 20)."}},
        "required": ["dataset", "field", "op", "value"]}),
    "count_incidents": (count_incidents, {
        "description": "Collapse line bursts into episodes: one incident = one (source, time bucket) containing >=1 line of a "
                       "template. Use instead of raw counts when you need 'how many separate times did this fail', per the "
                       "lines-vs-incidents rule. Returns {total_problem_lines, templates: [{lines, episodes, avg_lines_per_episode}]}.",
        "properties": {"dataset": _DATASET, "start": _START, "end": _END,
                       "bucket": {"type": "string", "enum": ["minute", "hour", "day"], "description": "Episode time bucket (default minute)."},
                       "top": {"type": "integer", "description": "Maximum templates returned (1-50, default 15)."},
                       "min_lines": {"type": "integer", "description": "Ignore templates with fewer lines (default 2)."}},
        "required": []}),
    "correlate_templates": (correlate_templates, {
        "description": "Which templates fire together: Pearson correlation of daily per-template line counts (log1p-smoothed), "
                       "returning highly correlated pairs with teams and co-active days. Use for root-cause grouping and "
                       "'which problems should be investigated as one'. Returns {templates_considered, days, pairs}.",
        "properties": {"dataset": _DATASET, "start": _START, "end": _END,
                       "min_r": {"type": "number", "description": "Correlation threshold (0-1, default 0.9)."},
                       "top": {"type": "integer", "description": "Maximum pairs returned (1-100, default 20)."},
                       "min_active_days": {"type": "integer", "description": "Ignore templates active on fewer days (default 5)."},
                       "min_lines": {"type": "integer", "description": "Ignore templates with fewer total lines (default 50)."}},
        "required": ["dataset"]}),
    "correlation_chains": (correlation_chains, {
        "description": "Candidate event CHAINS from strict (+1/-1) daily correlations. A chain is CONFIRMED only when the same "
                       "template bundle repeats on >= min_repeats distinct days; single-day coincidences stay candidates. Negative "
                       "pairs (mutual exclusion) are listed separately as regime indicators. Each chain has a timestamp-based "
                       "order_proposal (sequencing hint, NOT causation). Use after correlate_templates for root-cause grouping. "
                       "Returns {confirmed_chains, candidate_chains, negative_pairs}.",
        "properties": {"dataset": _DATASET, "start": _START, "end": _END,
                       "min_r": {"type": "number", "description": "Strict correlation threshold (default 0.999 ~ +/-1)."},
                       "min_repeats": {"type": "integer", "description": "Distinct days the bundle must repeat to be confirmed (default 2)."},
                       "top": {"type": "integer", "description": "Maximum chains returned (1-50, default 20)."}},
        "required": ["dataset"]}),
    "chain_profile": (chain_profile, {
        "description": "First-class profile of one CONFIRMED chain (from correlation_chains): frequency (episodes), episode dates, "
                       "first-half vs second-half trend, the proposed chain order and a +/-60 min context sample around the first "
                       "episode. The chain is treated as ONE repeated event with its own attributes. Returns {chain_id, templates, "
                       "order_proposal, frequency, episodes_detail, context_sample}.",
        "properties": {"dataset": _DATASET,
                       "chain_id": {"type": "string", "description": "Chain id from correlation_chains, e.g. 'CH-1a2b3c4d'. Pass this OR templates."},
                       "templates": {"type": "array", "items": {"type": "string"},
                                     "description": "Alternative to chain_id: exact template_id list of the chain."},
                       "context_minutes": {"type": "integer", "description": "Context window around the first episode (default 60)."},
                       "top": {"type": "integer", "description": "Maximum episodes returned (1-30, default 10)."}},
        "required": ["dataset"]}),
}


def claude_tools():
    """Tool definitions in Anthropic Messages API format."""
    return [{"name": n, "description": s["description"],
             "input_schema": {"type": "object", "properties": s["properties"], "required": s["required"]}}
            for n, (_, s) in TOOLS.items()]


def call_tool(name, args):
    if name not in TOOLS:
        raise ValueError(f"unknown tool {name}")
    return TOOLS[name][0](**(args or {}))
