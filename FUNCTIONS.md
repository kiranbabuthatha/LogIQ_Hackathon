# LogIQ tool reference

The agent, the web UI ("Explore tools" tab) and the MCP server all call the same 19 functions in [tools.py](tools.py).
Each one runs parameterised SQL in SereneDB and returns limited, already-calculated results. The agent never writes SQL.

## Evidence envelope

Every tool returns the same shape, so any number in an answer can be traced back to the query behind it:

```json
{
  "tool": "search_logs",
  "scope": "bgl, 2005-09-01 → end of data, all lines",
  "filters": {"text": "\"link has been severed\"", "dataset": "bgl", "start": "2005-09-01", "limit": 20},
  "queries": [
    {"purpose": "Count every match (not just the returned rows)", "sql": "SELECT dataset, count(*) …",
     "params": ["\"link has been severed\"", "bgl", "2005-09-01"], "rows_returned": 1, "truncated": false},
    {"purpose": "Matching lines, most relevant (BM25) first", "sql": "SELECT … LIMIT %s",
     "params": ["…", 21], "rows_returned": 16, "truncated": false}
  ],
  "data": {"total_matches": 16, "matches_by_dataset": […], "lines": […]},
  "notes": ["Query syntax: words are AND-ed (stemmed), \"quoted phrase\", OR …"]
}
```

| Field | Meaning |
|---|---|
| `scope` | Which data the result covers: dataset(s), time window, problem lines or all lines |
| `filters` | The arguments that were applied |
| `queries` | Each SQL statement the tool ran, with its bound parameters, rows returned and whether rows were cut at the limit |
| `data` | The result |
| `notes` | Caveats to carry into the answer, such as a truncated sample, lines vs incidents, or correlation vs cause |

Limits work by fetching `limit + 1` rows. If the extra row exists, `truncated` is set to `true`. Search and count tools also run a separate `count(*)`, so totals stay exact even when the returned rows are a sample. Log text is cut to 300 characters.

## Terms

| Term | Meaning |
|---|---|
| Problem line | `severity ≥ 1` (WARNING and above) or a BGL expert alert label (`is_alert`). Severity: 3 = FATAL/SEVERE, 2 = ERROR, 1 = WARNING, 0 = INFO. |
| Template | `template_id` (e.g. `E344`): the same message shape with different values. `prepare_data.py` derives templates for the full data by masking variable tokens; the Loghub 2k samples use Loghub's own `EventId`s. |
| Source | Where a line came from: the BGL node, or the OpenStack log file. |
| Team | Owning team of a component, from the `ownership` table (longest matching prefix, via the `component_team` view). |
| Time window | `start` is inclusive and `end` exclusive, written like `2005-09-01` or `2005-09-01 08:00`. Timestamps are as recorded in each log. |
| Lines vs incidents | Every count is a number of log lines. One fault can write many lines, so a count is not a number of incidents. |

## Tools

### All roles

**`corpus_overview()`**
Computes whole-corpus totals with no filters: for each dataset, its lines, problem lines, labelled alerts, templates, components and time span. It also returns problem lines per owning team and lines per level. Use it to learn the scope, the dates and the team names.

### Engineer: what failed, what happened around it, why, and what to check next

**`search_logs(text, dataset?, start?, end?, team?, component?, level?, problems_only=false, sort="relevance", limit=20)`**
Full-text search over `content` through the SereneDB inverted index `events_idx`: `content @@ websearch_to_tsquery(text)`.
- Query syntax: words are AND-ed and stemmed, `"quoted phrase"` matches a phrase, `OR` gives alternatives, and `-word` excludes a word.
- Returns `total_matches` and `matches_by_dataset` (exact counts with first and last time), plus up to `limit` lines.
- Lines are ranked by BM25 relevance, or by time with `sort="time"`.

**`get_nearby_events(dataset, line_id, before=5, after=5, minutes?, same_source=false)`**
Returns the lines around one line, at all levels, each with `seconds_from_target` and a `position` of before, target or after.
- By default it returns the neighbours in log order.
- With `minutes`, it returns every line within ±minutes of the target's time, up to 60. Add `same_source` to keep only the target's node or file.

**`investigate_event(dataset, line_id, window=10)`**
A one-call investigation of a single line. It returns:
- `what_failed`: the line itself, with its component and owning team.
- `before` / `after`: the lines either side of it. Each line has `same_source_as_target`, because neighbours in the log are often from other nodes.
- `context_summary`: how many of those lines come from the same source and how many from others.
- `recurrence_in_dataset`: lines, sources, active days, and first and last time for the same template.
- `same_source_problems`: other problem lines from the same source.
- `candidate_cause`: the nearest earlier problem on the same source, or on any source within 1 hour. This is a lead to check, not proof.
- `suggested_checks`: next steps derived from those facts.

**`preceding_patterns(dataset, line_id, minutes=30, limit=10, min_lines=2)`**
Finds templates that are over-represented in the `minutes` before an event. `lift` = (template's share of the window) ÷ (template's share of the dataset). `same_source_lines` counts lines from the target's source. Templates seen fewer than `min_lines` times in the window are left out, because a lift of thousands from a single line is not evidence. This shows co-occurrence in time, not cause.

**`template_history(dataset, template_id)`**
The whole-dataset history of one template:
- totals, first and last time, active days and sources;
- levels, alert types, components and teams;
- the busiest sources and the busiest days.

**`get_traceback(dataset, line_id, max_lines=40)`**
Returns one complete multi-line exception: finds the nearest preceding `Traceback (most recent call last)` anchor in the same component and collects every continuation line (consecutive in log order, within 2 seconds of the anchor). Use whenever a single error is spread over many lines, so the exception can be quoted as one block. Returns `start_line_id`, the `lines`, the final `exception_last_line` and whether it was `truncated` by the cap.

**`trace_instance(dataset, instance_id, limit=50)`**
Follows one entity id (instance, request, tenant or node) through the whole dataset: every line mentioning it in time order, plus a per-template summary and first/last seen. The id must be at least 8 characters (substring match is a full scan). Use for "what happened to this VM/request?" questions. Returns `summary`, `templates` and the ordered `lines`.

**`extract_field(dataset, field, start?, end?, top=10)`**
Extracts a structured field that lives inside the message text and summarises it. Numeric fields (`latency` — the OpenStack `time:` value, `status` — the HTTP code) return count, min, max, average, p50 and p95 plus how much of the scope they cover; text fields (`req_id`, `tenant_id`, `instance_id`) return the most common values. Use when a question needs a value buried in free text.

**`filter_numeric(dataset, field, op, value, start?, end?, limit=20)`**
Filters lines by an extracted numeric field, e.g. `latency > 1` for slow requests or `status >= 500` for server errors. Numeric fields: `latency`, `status`. Returns the matching line count, min/max/avg and sample lines.

### Team lead: what recurs, how often, who owns it, and is it changing

**`recurring_problems(dataset?, start?, end?, team?, min_lines=2, limit=15)`**
Lists problem templates that recur in the period, with team, component, lines, active days, sources, and first and last time. `active_hours` is the number of distinct clock hours with at least one line, a rough count of separate episodes.

**`count_problems(group_by="team", dataset?, start?, end?, team?, component?, problems_only=true, limit=25)`**
A database aggregation grouped by `dataset`, `team`, `component`, `level`, `template`, `alert_type` or `source`. Each group shows its lines, templates, sources, max severity, first and last time, and `share_pct`. The result also includes `total_lines` and `total_groups`, so you can tell when only the top groups are shown.

**`problem_trend(bucket="week", dataset?, start?, end?, team?, component?, template_id?, split_by="team")`**
Problem lines per `hour`, `day`, `week` or `month`, split by `dataset`, `team`, `component`, `severity` or `none`. `template_id` accepts a comma-separated list (e.g. `E19,E20`), so one call can trend a whole set of related templates. Buckets with no problem lines are left out.

**`compare_periods(before_start, before_end, after_start, after_end, dataset?, group_by="template", limit=15)`**
Problem lines in two periods side by side. Each group gets a `change` and a status: `new`, `gone`, `rising`, `falling` or `flat`. Counts are raw: periods of different lengths are not normalised.

**`count_incidents(dataset?, start?, end?, bucket="minute", top=15, min_lines=2)`**
Collapses line bursts into episodes: one incident = one (source, time bucket) containing at least one line of a template. Each template gets `lines`, `episodes` and `avg_lines_per_episode`, so "how many separate times did this fail" has an answer that respects the lines-versus-incidents rule. A fault logging 500 lines in one minute counts as 1 episode; the same fault on 12 nodes counts as 12.

**`correlate_templates(dataset, start?, end?, min_r=0.9, top=20, min_active_days=5, min_lines=50)`**
Which templates fire together: Pearson correlation of daily per-template line counts (log1p-smoothed), returning pairs with `r`, both template texts, owning teams and `co_active_days`. Use for root-cause grouping before the chain tools. Correlation in time is not proof of cause.

### Chain analysis: which events always fire together, and in what order (lead, manager and engineer)

**`correlation_chains(dataset, start?, end?, min_r=0.999, min_repeats=2, top=20)`**
Builds candidate chains from strict (±1) correlations and groups correlated pairs into maximal cliques. A chain is `confirmed_chains` only when the same template bundle repeats on at least `min_repeats` distinct days; single-day coincidences stay in `candidate_chains`. Pairs with `r <= -min_r` are listed separately as `negative_pairs` (mutual exclusion between regimes). Every chain carries a deterministic `chain_id`, its member templates with teams, an `order_proposal` (templates ranked by their average first-line time of day inside episodes — a sequencing hint, not causation), the episode count and dates, total lines and a first-half/second-half trend.

**`chain_profile(dataset, chain_id | templates, context_minutes=60, top=10)`**
Treats one confirmed chain as a single repeated event and reports its attributes: frequency (episodes and dates), first-half vs second-half trend, the proposed order, per-episode per-template detail, and a ±`context_minutes` sample around the first episode (all levels, up to 60 lines). Use after `correlation_chains` to answer "tell me about this chain" questions.

### Manager: what deserves attention first, and who should act

**`risk_ranking(dataset?, start?, end?, top=5, by="pattern")`**
- `by="pattern"` ranks templates by severity, then active days, then lines. Each one has its team, `share_of_dataset_problems_pct`, sources and a trend.
- `by="team"` ranks teams by `priority_score`, the sum of each problem line's severity, where labelled alerts count as at least 2. Each team has a severity breakdown, a trend and its top problem.
- The trend compares the first and second halves of each dataset's own period. The two datasets cover different years, so they are never mixed. Labels:
  - `rising` or `falling`: one half has more than 1.5× the lines of the other;
  - `steady`: neither half has more than 1.5× the other;
  - `single burst`: every line falls within 5% of the period;
  - `too few lines to tell`: fewer than 4 lines.
- The ranking uses only severity and frequency. The logs contain no downtime, user or cost data.

## Same tools, different evidence per role

| Role | Typical calls | Answer contains |
|---|---|---|
| Engineer | `search_logs` → `investigate_event` → `preceding_patterns` / `get_nearby_events(minutes=…)` / `template_history` | Failing lines verbatim, the events around them, a labelled hypothesis, a concrete next diagnostic check |
| Team lead | `recurring_problems`, `count_problems(group_by="team")`, `problem_trend`, `compare_periods` | A table of recurring patterns with owner, frequency, spread and trend, plus what to investigate first and why |
| Manager | `risk_ranking` (pattern and team), `corpus_overview` | At most five ranked risks with evidence, owning team and action, plus what the data does not show |

## Other files

- `agent_tools.py` is a small command-line tool for quick looks at the raw `bgl` and `openstack` tables (`python agent_tools.py levels bgl`). The agent does not use it.
- `logiq_tools.py` is an earlier parallel version of the tool layer. Its analyses (BM25 ranking, preceding patterns, template history, period comparison, team risk) are now part of `tools.py`, with evidence envelopes added.
