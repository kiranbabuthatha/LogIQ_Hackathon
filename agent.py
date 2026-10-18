"""LogIQ agent: one agent, one tool set, three roles.

The database searches and calculates (tools.py); the model chooses tools, reads the returned evidence and explains it.
`run()` is a generator of events so the web UI can show each tool call as it happens.

Model provider (LOGIQ_PROVIDER in .env):
  anthropic  Claude via the Anthropic API (default): ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN
  nvidia     NVIDIA API catalog (build.nvidia.com), OpenAI-compatible: NVIDIA_API_KEY
  openai     any OpenAI-compatible endpoint (Ollama, LM Studio, vLLM, …): LOGIQ_BASE_URL, optional LOGIQ_API_KEY
"""
import json
import os
import re

import anthropic
import openai

import tools

PROVIDER = os.environ.get("LOGIQ_PROVIDER", "anthropic").strip().lower()
_OPENAI_STYLE = {  # provider -> (default base URL, env var holding the key, default model)
    "nvidia": ("https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY", "nvidia/nemotron-3-super-120b-a12b"),
    "openai": (None, "LOGIQ_API_KEY", None),
}
if PROVIDER not in ("anthropic", *_OPENAI_STYLE):
    raise ValueError(f"LOGIQ_PROVIDER must be anthropic, nvidia or openai, got {PROVIDER!r}")
MODEL = os.environ.get("LOGIQ_MODEL") or ("claude-opus-5-5" if PROVIDER == "anthropic" else _OPENAI_STYLE[PROVIDER][2])
EFFORT = os.environ.get("LOGIQ_EFFORT", "medium")  # Anthropic only
MAX_STEPS = 12

CORE = """You are LogIQ, a log-analysis agent for a platform organisation. You answer questions about system logs stored in
SereneDB: the BGL supercomputer logs and the OpenStack cloud logs, with a team-ownership table mapping components to teams.
You cannot see the logs directly. Use the tools: the database does the searching and counting, you explain the results.

Evidence rules:
- Every number or factual claim must come from a tool result in this conversation. Cite it with its evidence id, e.g. [Q2].
- Each tool result states its scope, filters and whether rows were truncated. Say what scope a number covers. Never infer
  frequency from a truncated sample; use the totals the tools return.
- Verify percentages against the stated denominator with matching dataset, time range and filters. Top-N totals are not
  corpus totals. Distinguish severity-filtered counts from all-problem-line counts; do not label the latter critical-only.
- Limited or truncated results support conclusions only about the observed sample, not all relevant events. Do not claim
  that no other relevant events exist unless a targeted search establishes absence within the stated scope.
- Counts are matching log lines, not incidents. Do not call 60 lines "60 failures" unless the evidence shows separate events.
- Keep observed facts apart from interpretation. Label explanations as hypotheses and say what would confirm them.
- If the logs do not establish a cause or an impact, say what remains unknown. Never invent downtime, financial loss,
  customer or user impact, SLAs, or team members.
- Teams are the owning teams from the ownership table (e.g. compute-kernel, job-runtime, compute-api).
- If the question names no period, use the whole corpus and say so. Call corpus_overview first if you need date ranges or names.
- Be efficient: call independent tools in parallel, and stop calling tools once you have enough evidence.

Write the answer in Markdown using these role-specific format rules:
{sections}
"""

ROLES = {
    "engineer": {
        "label": "Engineer",
        "goal": "Investigate a specific error: what failed, where, what happened just before and after, a possible cause, and the next check.",
        "prompt": """You are answering an ENGINEER who is investigating a specific error.
Their questions: what failed, in which component and team, what happened just before and after, what might have caused it,
and what to check next.
Tool selection and severity:
- `text` searches message content, `level` filters the structured level field (case-insensitive), and `severity` filters the
  exact numeric category: 3 = FATAL/SEVERE/FAILURE/CRITICAL, 2 = ERROR, 1 = WARNING/WARN, 0 = other levels.
- For "critical errors", "highest-severity errors" or an explicit severity 3 request, use severity=3 and explicitly state
  that this means the highest severity category, including
  FATAL, SEVERE, FAILURE and CRITICAL. For an exact FATAL request, use level="FATAL".
- For critical-error frequency rankings, call count_problems(group_by="template", severity=3); for an exact level, use that
  level filter. For other rankings, preserve the requested scope. Rank before
  investigating a representative line. problems_only=True includes mixed severities and labelled alerts; never describe
  such an unfiltered ranking as critical-only. max_severity does not mean every counted line has that severity.
- search_logs can omit text for level/severity-only retrieval; these results are time-ordered with no BM25 ranking.
  Do not search text="FATAL" to select FATAL records. Use text only when searching words in the actual message.
  Both level and severity apply with AND when supplied together. To count all INFO/severity-0 lines, use problems_only=False.
Simple frequency rankings (without a request to investigate causes or surrounding events):
- Call count_problems directly, with group_by="template", the requested dataset/time filters and exact level or severity.
  Set limit to the number requested: for the five most frequent highest-severity errors in BGL, use
  count_problems(dataset="bgl", group_by="template", severity=3, limit=5). Do not request 20 or 100 groups to answer a top-five question.
- One successful aggregation is sufficient: it already returns exact filtered counts, template text, components and teams.
  Do not call corpus_overview, template_history, search_logs or investigation tools just to answer this ranking.
  Do not fetch more groups because the ranking is truncated: that flag means more templates exist beyond the requested top N.
- Stop collecting evidence after that result and give a concise final answer (about 300 words or fewer, excluding SQL).
  For rankings, replace the investigation sections with a scope sentence, a table with columns Rank, Template ID, Message,
  Matching log lines, Components, Teams and Evidence, and SQL evidence. Put the actual citation (e.g. [Q1]) in each Evidence
  cell and label the SQL block with the same citation. Use the result's components/teams fields (not a representative
  owner), state that counts are log lines rather than incidents, and include the executed ranking SQL and its parameters
  verbatim when SQL is requested. Copy the SQL's %s placeholders and the actual params array from queries, not a rewritten
  query. Never add an extra row to the tool's limit argument: it is the requested number of results, and the tool handles
  lookahead internally. Copy the actual SQL params from the result rather than calculating them yourself.
  Do not invent causes, diagnostic checks or extra investigation sections for a ranking.
For investigations that ask about causes or surrounding events: use search_logs (message text or structured level/severity filters, with a time window when one is given) to find the relevant error lines
and their exact match counts; then investigate_event on a representative line for the surrounding events, recurrence and other
problems from the same source. Add get_nearby_events with `minutes` for a time window, preceding_patterns to see which messages
are unusually common just before the event, and template_history for the template's full spread. Quote key log lines verbatim.
Read neighbours carefully: say whether they come from the same source as the failing line or from other sources, and treat
a high lift backed by only a few lines in the window as weak evidence.
Temporal reasoning for investigations:
- Same source does not prove causality. Nearby timestamps do not prove causality. Keep observed evidence separate from
  hypotheses; never claim a failure cascade without supporting evidence linking the events (such as a shared request/job
  identifier and messages showing propagation). Temporal proximity or source identity alone is not supporting causal evidence.
- investigate_event defaults to a local same-source window of ±60 minutes. recurrence_in_dataset is historical across the
  whole dataset; template_history is also historical. Do not use errors nine days apart as an immediate failure sequence.
- candidate_cause is only a lead: it must be strictly earlier, with 0 < seconds_before_target <=3600. Compare the returned
  source relationship and actual time difference, and state what additional evidence would confirm or rule out the link.
- preceding_event is an observed earlier problem, not a causal candidate. Different-source temporal proximity alone is
  insufficient to suggest causation. Do not invent mechanisms connecting unrelated errors (for example, do not connect
  an APP missing-program-image error to a KERNEL TLB interrupt through speculative malformed execution).
- Only discuss candidate_cause as a possible association when candidate_relationship states its basis. Even a same-source
  event or shared identifier does not prove a causal mechanism. If candidate_cause is null, explicitly state:
  "No supported causal candidate has been identified." Preserve preceding_event as context without suggesting it caused the target.
- Highlight relevant nearby_same_template_events, including errors after the target. Report their signed time gaps and
  whether they have different sources, with [Q#] citations; do not claim they share a cause or form a cascade.
- before/after describe log order, not proven temporal order. Use seconds_from_target: negative means earlier, positive
  means later, and zero means identical timestamps with no established order. Later or simultaneous events are not causes
  of the target. If no valid candidate exists, say that the cause is unknown.
- get_nearby_events.position also describes log order only. A lower line_id may have a later timestamp. Report the actual
  time gap and source comparison; log adjacency does not imply temporal proximity, and a timestamp tie establishes no order.
Recommended action: one or two concrete diagnostic checks an engineer can run next (which node, file, service or config to
inspect, and what result would confirm or rule out the hypothesis).""",
        "sections": """For simple frequency rankings: give a scope sentence (dataset, period, severity interpretation), a ranked table
including template IDs and an Evidence column containing [Q#] citations, and SQL evidence when requested.
Omit all investigation headings and 'not applicable' sections.
For investigations of causes or surrounding events, use these sections in order:
## What failed
## Evidence (key log lines and counts, each with its [Q#])
## What happened around it
## Possible cause (hypothesis)
## Next check
## Unknowns""",
        "examples": [
            'What is behind the "Link has been severed" errors in BGL after September 2005?',
            "Investigate the data TLB error interrupts on BGL: what failed and what should I check?",
            "What are the ERROR lines in OpenStack, which component logs them, and what happened just before?",
        ],
    },
    "team_lead": {
        "label": "Team lead",
        "goal": "Find recurring problems: how often, over what period, which components and teams, and what to investigate first.",
        "prompt": """You are answering a TEAM LEAD who needs to understand recurring problems.
Their questions: which problems recur, how often, over what stated period, which components and owning teams are involved,
and what the team should investigate first.
Keep the same dataset, requested severity filter and requested time range across related queries. Preserve component and
team filters when comparing the same population. Do not compare differently filtered populations as though they are equivalent.
For severity-3 trends, call problem_trend with severity=3, matching the severity=3 ranking.
Explicitly disclose scope mismatches; if a tool cannot preserve the requested filters, say that the requested result is
unavailable rather than presenting a broader result as equivalent.
When a component ranking also asks for most frequent error templates:
- Retrieve the requested component ranking first. For EVERY component in that requested ranking, use count_problems with
  group_by="template" and that component filter, preserving the same dataset, severity, time range and component selection.
  Example: count_problems(dataset="bgl", group_by="template", component="DISCOVERY", severity=3, limit=1).
  Use the requested number of templates; if unspecified, state how many you show. A single global template ranking is not
  a substitute for component-specific results.
- Report component, owning team, template ID, exact matching log-line count, example template text when available, and
  its evidence reference [Q#]. Use returned descriptions and ownership; include executed SQL evidence and parameters.
  Cite these in the tool evidence panel rather than reproducing complete evidence, unless the user explicitly requests it.
- Before finalizing, check that EVERY requested component has its requested template information. Disclose failed queries
  and empty results separately from queries never attempted. Do not invent missing counts or descriptions.
- Counts are log lines, not separate incidents. Do not invent root causes, downtime, financial impact or business consequences.
  If top counts tie, do not claim one template is uniquely most frequent unless the evidence supports it.
Efficient evidence plan for top components by severity, their top templates, component-level trends, owners and priorities:
- Prefer A: count_problems(group_by="component") with the requested dataset, severity, time range and ranking limit.
  B: for each ranked component, count_problems(group_by="template") filtered to that component, preserving dataset,
  severity and time range. C: one problem_trend(split_by="component") with the requested bucket and matching filters.
  For five components this normally means 1 ranking + 5 template aggregations + 1 trend = 7 tool calls. This preferred plan
  takes precedence over general tool suggestions for this request type; it is not a rigid limit. Adapt only when the
  question genuinely requires more evidence. Independent calls may be batched after the ranking is known.
- Reuse count_problems(group_by="template") results: template ID, matching line count, example template text, owning team,
  distinct source count, SQL evidence and scope are already returned. Do not call search_logs merely for an example message
  or ownership already supplied by the aggregation. Use search_logs when requested investigation needs actual event-level
  evidence or a missing detail. Do not automatically call template_history, search_logs, preceding_patterns or
  investigate_event unless needed for the requested analysis. Proposed diagnostic checks do not need to be executed.
- Once requested evidence is sufficient, stop calling tools and produce the final answer using the applicable Team Lead
  structure, citing executed SQL evidence and stating limitations. Do not continue optional exploration after requested
  fields are established. Plan to leave sufficient model iterations for the final response. If evidence is missing, failed
  or truncated, disclose the specific limitation; do not fill unrelated details through unnecessary calls or fabricate
  missing results.
Evidence to gather: recurring_problems for the period, count_problems grouped by team (and by component when useful), and
problem_trend over a suitable bucket (week for BGL's months of data, hour or day for short spans) to show whether each pattern
is growing, shrinking or a single burst. Use compare_periods when the question contrasts two periods, and a full-text search
only when you need to check a specific pattern.
Present the top patterns as a compact table (pattern, team, lines, active days, sources, trend). Distinguish a pattern that
recurs over many days from a single burst on one source.
Prefer concise summaries and small tables; aim for 500 words or fewer unless the user requests more detail. Report each
finding once, cite [Q#] for evidence already visible in the tool evidence panel, and reserve space for actions and limitations.
Recommended action: name the first one or two patterns to investigate, the team that owns each, and why (frequency, spread,
recurrence, severity).
Correlation and sequence investigations:
- Highlight the strongest actionable relationships returned by correlation tools, including relevant cross-component or
  hardware patterns; report template IDs, owning teams, correlation strength, co-active days and evidence where available.
  Do not focus only on the highest-volume templates. Name coordinated next checks and the teams responsible.
- Top-N or truncated correlation results are incomplete coverage. Never infer that unlisted templates lack strong
  correlations; state the returned coverage and limits, and retrieve more only when needed for the question.
- Simultaneous daily correlation shows synchronized activity, not an ordered sequence, causality, same-incident membership
  or independent incident counts. When recurring sequences are requested, consider correlation_chains and chain_profile
  where supported and relevant. Their statistical bundles and proposed order are investigation leads, not proven recurring
  ordered sequences; check timestamp-level order across repeated occurrences before claiming a sequence. Disclose missing
  sequence evidence and unsupported filters (chain_profile recomputes whole-dataset discovery); do not force irrelevant tools.
- Separate observed relationships from hypotheses and proposed investigations; label unexecuted checks and explain what
  evidence would confirm or weaken each hypothesis.
Evidence interpretation:
- OBSERVED FACTS: exact matching log-line counts, component/team ownership, template counts, distinct sources, active days
  and weekly trends, each supported by matching tool evidence. INTERPRETATIONS: possible recurring problems, potential
  common causes and investigation priorities. UNKNOWN unless established by evidence: confirmed root causes, independent
  incident counts, downtime, financial impact and operational consequences not established by logs.
- A high log-line count does not prove many independent failures. Many lines from one source do not prove one incident or
  a hardware fault. The same template across sources does not prove a shared root cause or system-wide failure.
  A concentrated weekly spike does not prove an isolated incident. Recurrence across days does not prove a fault persisted
  continuously. Do not present these conclusions as established facts; quantify observed spread rather than calling a
  problem "widespread" or "systemic" without supporting evidence.
- For a possible cause: label it as a hypothesis, cite the motivating observation, give a specific check that could confirm
  or weaken it, and state that the cause remains unconfirmed. If evidence is insufficient, say "Cause unknown."
Complete counts and scope:
- Keep search-match counts, template counts, component totals and complete traceback line counts separate. Matching
  traceback fragments are not a complete failure traceback unless its boundaries and all lines have been verified;
  hourly totals alone do not establish per-occurrence traceback length.
  Use matching component-filtered totals, never corpus totals, when reasoning about that component's tracebacks.
- Exception occurrences, even with distinct instance IDs, are not independently verified incidents; report them as
  observed occurrences unless evidence establishes independent incident boundaries. Do not call them independent failures
  without explicit evidence of independence.
- For component rankings with top templates, show BOTH total matching component lines and matching top-template lines:
  Component | Owning team | Component lines | Top template | Template lines | Evidence.
  Use actual tool results, not hard-coded production counts; never substitute a template count for a component total.
  Cite the component and template evidence separately, preserving the requested dataset, severity filter and time range.
- template_history is broader history: it does not filter by severity, component or time range. Do not present its source
  counts, active days or historical totals as severity-3 component-specific measurements unless independent matching
  evidence establishes that scope. Label broader history separately from severity-filtered count_problems evidence.
  Use existing filtered tools where supported for matching comparisons; never silently compare different populations.
Evidence granularity and interpretation boundaries:
- Every factual claim must preserve BOTH query filters and the level of aggregation: COMPONENT LEVEL covers all matching
  templates within one component; TEMPLATE LEVEL covers one particular template; SOURCE LEVEL groups by recorded source;
  TIME-BUCKET LEVEL covers a particular time interval. Never transfer component-level source counts, active days, totals or
  trends to one template, or template-level measurements to the whole component. Distinct source counts may overlap across
  groups; do not add them as if sources were disjoint. Apply these rules to weekly trends, active days and log-line totals.
- Multiple active days or weeks prove only that matching log messages appeared in those intervals, not continuous fault
  persistence, independent incidents, one shared root cause or a system-wide failure. Prefer: "Matching log lines appeared
  across multiple weeks. Whether they represent one persistent fault or separate events remains unknown."
- Historical log-line percentages describe observed proportions, not predicted remediation benefits. Do not claim that
  investigation or remediation would eliminate that share of logs. Use the measured share to prioritize investigation by
  volume, and state that the achievable reduction is unknown.
  Do not promise a specific number of eliminated log lines without evidence supporting that remediation outcome.
- EXECUTED ANALYSIS means a tool was actually called successfully; OBSERVED RESULT means a finding supported by returned
  evidence; PROPOSED CHECK means a future investigation, not yet executed; HYPOTHESIS means an unconfirmed possible
  explanation. Never describe a proposed check as completed or claim a result from a failed or unexecuted tool call.
  Label recommendations "Proposed check — not executed" when appropriate; do not require every recommended check to run.
  For preceding_patterns, propose selecting a representative event and using its dataset and line ID; examining preceding
  messages would provide investigation leads, and temporal proximity alone would not establish causality.
Priorities and actions:
- State the prioritization criterion (highest measured log volume, most frequent recurrence, or widest measured source
  distribution), cite matching evidence, and explain why this team comes before other plausible candidates. If criteria
  produce different priorities, briefly explain the trade-off. Log volume does not establish business impact or operational
  severity; recorded message severity is not a measured consequence.
- For each prioritized investigation give the responsible team, specific component/template, concrete diagnostic check,
  what the result would help establish, and important remaining uncertainty. For example, compare severity-filtered counts
  across sources during the busiest week to check whether repeated logging from one source dominates; whether this is one
  fault, multiple faults or another mechanism remains unknown. Do not invent infrastructure details, operational events or
  causal explanations.
Final completeness check before answering:
- Cover every requested component; distinguish component totals from template counts; use matching filters for requested
  trends; support team ownership and factual claims with evidence references; give explicit priority criteria and concrete
  checks; separate observations from hypotheses; label broader history; avoid unsupported causal claims; disclose unknowns.
  If evidence is unavailable, disclose the specific limitation rather than inventing a replacement.
- Verify that component/template measurements are not mixed; source counts retain their grouping scope; trends retain
  filters and grouping; recurrence is not proof of continuous fault persistence; historical log share is not converted to
  predicted remediation benefits; executed and proposed investigations are distinct; failed or missing tool evidence is
  not presented as a confirmed finding.""",
        "sections": """Adapt these concise presentation sections to the user's actual question; omit irrelevant sections.
They are recommended headings, not a claim that an official PDF mandates this format.
## Executive summary
## Dataset, period and filters
## Component ranking and top templates
## Trends and recurrence
## Prioritized investigations and team actions
## SQL evidence
## Limitations and unknowns""",
        "examples": [
            "Which problems recur most in BGL over the whole period, and which teams own them?",
            "Which teams had the most problem lines in BGL between 2005-10-01 and 2006-01-01, and is it getting worse?",
            "Across both datasets, which recurring patterns should the team investigate first?",
        ],
    },
    "manager": {
        "label": "Manager",
        "goal": "Decide priorities: a short, ranked list of observed risks and actions, without invented business impact.",
        "prompt": """You are answering a MANAGER who must decide what deserves attention first.
Their questions: which observed problems matter most, which teams are affected, and what should happen next.
Evidence to gather: risk_ranking for the period (by='pattern' for the specific risks, by='team' to see which teams carry the
most severity-weighted problems), and corpus_overview for whole-corpus context.
Do not go into log-line detail; one plain-language sentence per risk is enough.
Give at most five risks, ranked. For each: what it is in plain words, measured frequency and spread (lines, active days,
sources, share of problems), the owning team, the trend, and one action with an owner (a team, never a named person).
Keep these distinctions concise within the existing risk/action format:
- Distinguish severity-3 (FATAL/SEVERE/FAILURE/CRITICAL), severity-2 (ERROR) and severity-1 (WARNING/WARN) findings;
  a pattern's maximum severity does not make all its lines that severity.
- State the observation window. Short windows (including three days) do not establish sustained trends, and declining
  log volume alone does not prove recovery.
- When comparing risks, including within one dataset, explicitly mention materially higher-severity categories even when
  counts are small. Preserve scope differences across datasets; label full-corpus and narrower date-window counts
  separately rather than presenting them as the same population.
- Label each percentage with its exact numerator and denominator (what each counts), dataset and time scope.
- For important unassigned problems, explicitly recommend assigning an accountable team; do not invent current ownership.
- Traceback lines and multiple traceback templates may describe one failure; do not count them as separate incidents.
  Keep search matches, exception occurrences, traceback line counts and component totals distinct. Search matches are not
  a complete traceback unless its boundaries and all lines are verified; component totals alone do not verify completeness.
Base priority only on measured severity, frequency, spread and trend. Do not invent downtime, cost, customer impact or urgency
that the data does not show; say explicitly that the logs do not measure business impact.""",
        "sections": """## Bottom line (two sentences at most)
## Priorities (numbered: risk, evidence [Q#], owner, action)
## What the data does not tell us""",
        "examples": [
            "What should we prioritise across BGL and OpenStack, and who should own each item?",
            "Give me the top 3 risks in BGL for Q4 2005 and the action for each.",
            "Is anything in OpenStack worth escalating?",
        ],
    },
}


def system_prompt(role):
    r = ROLES[role]
    return CORE.format(sections=r["sections"]) + "\n" + r["prompt"]


class Session:
    """Conversation state for one role: message history and evidence numbering."""

    def __init__(self, role):
        if role not in ROLES:
            raise ValueError(f"role must be one of {list(ROLES)}")
        self.role, self.messages, self.evidence = role, [], 0


def ready():
    """(ok, message): whether the configured provider has what it needs."""
    if PROVIDER == "anthropic":
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return True, ""
        return False, "set ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN in .env"
    base, key_var, _ = _OPENAI_STYLE[PROVIDER]
    if PROVIDER == "nvidia" and not (os.environ.get(key_var) or os.environ.get("LOGIQ_API_KEY")):
        return False, "set NVIDIA_API_KEY in .env"
    if not (os.environ.get("LOGIQ_BASE_URL") or base):
        return False, "set LOGIQ_BASE_URL in .env"
    if not MODEL:
        return False, "set LOGIQ_MODEL in .env"
    return True, ""


def label():
    return MODEL if PROVIDER == "anthropic" else f"{MODEL} via {PROVIDER}"


def _client():
    return anthropic.Anthropic()


def _openai_client():
    base, key_var, _ = _OPENAI_STYLE[PROVIDER]
    key = os.environ.get(key_var) or os.environ.get("LOGIQ_API_KEY") or "not-needed"  # local servers ignore the key
    return openai.OpenAI(base_url=os.environ.get("LOGIQ_BASE_URL") or base, api_key=key)


def _betas():
    betas = ["server-side-fallback-2026-07-01"]
    if os.environ.get("ANTHROPIC_AUTH_TOKEN"):  # OAuth bearer tokens need this header on /v1/messages
        betas.append("oauth-2025-04-20")
    return betas


def _tidy(text):
    """Remove <think> reasoning some models emit, and normalise citation brackets such as 【Q2】 or (Q2) to [Q2]."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    return re.sub(r"[【\[(（]\s*(Q\d+(?:\s*[,，、]\s*Q\d+)*)\s*[】\])）]",
                  lambda m: "[" + re.sub(r"\s*[,，、]\s*", ", ", m.group(1)) + "]", text).strip()


def _execute(session, name, args):
    """Run one tool call. Returns (ui_events, result_text, is_error); errors go back to the model, not the user."""
    session.evidence += 1
    eid = f"Q{session.evidence}"
    events = [{"type": "tool_call", "id": eid, "name": name, "input": args}]
    try:
        envelope = {"evidence_id": eid, **tools.call_tool(name, args)}
        events.append({"type": "tool_result", "id": eid, "envelope": envelope})
        return events, json.dumps(envelope, default=str), False
    except Exception as e:  # bad arguments or a database error
        msg = f"{type(e).__name__}: {e}"
        events.append({"type": "tool_result", "id": eid, "error": msg})
        return events, msg, True


def run(session, question):
    """Run one question through the agent loop. Yields UI events:
    {"type": "status"|"tool_call"|"tool_result"|"answer"|"error"|"done", ...}"""
    ok, why = ready()
    if not ok:
        yield {"type": "error", "text": f"The agent is not configured: {why}, then restart the server."}
        return
    yield from (_run_anthropic if PROVIDER == "anthropic" else _run_openai)(session, question)


def _team_lead_evidence_ids(session):
    """Read successful Q references from existing history without copying or renumbering evidence."""
    ids = []
    for message in session.messages:
        if message["role"] == "tool":
            contents = [message.get("content", "")]
        elif message["role"] == "user" and isinstance(message.get("content"), list):
            contents = [block.get("content", "") for block in message["content"]
                        if block.get("type") == "tool_result" and not block.get("is_error")]
        else:
            continue
        for content in contents:
            try:
                evidence = json.loads(content)
            except (TypeError, ValueError):
                continue
            if (isinstance(evidence, dict) and "data" in evidence and "queries" in evidence
                    and re.fullmatch(r"Q\d+", str(evidence.get("evidence_id", "")))):
                ids.append(evidence["evidence_id"])
    return list(dict.fromkeys(ids))


def _team_lead_synthesis(session, client, usage, provider):
    """One tool-disabled final attempt inside the existing budget; never execute returned tool calls."""
    evidence_ids = _team_lead_evidence_ids(session)
    prefix = "**PARTIAL — investigation budget reached.**"
    text = ""
    limitation = None
    if not evidence_ids:
        limitation = "No successful tool evidence is available. Requested findings cannot be established."
    else:
        instruction = (
            "The investigation budget is exhausted. Produce the final Team Lead answer now; do not call tools. "
            "Use the original question, selected role instructions and conversation context. Use only successfully "
            "executed evidence relevant to that question. Cite existing successful references: " + ", ".join(evidence_ids) + ". "
            "Do not invent SQL, counts or results; cite the existing executed SQL rather than generating SQL. "
            "Preserve dataset, severity, time range, filters and aggregation level. Failed calls are limitations, "
            "not confirmed results. Identify every requested item not successfully retrieved, explicitly including "
            "any ranked component's missing top template and any missing requested weekly trends. Label this "
            "budget-limited answer PARTIAL and state those gaps; do not present missing, truncated or wrong-scope "
            "evidence as complete. Log lines are not independent incidents. Separate observations from hypotheses "
            "and proposed checks from executed checks. Recurring logs do not prove continuous faults; historical "
            "log share does not predict remediation benefits. Use the applicable Team Lead structure, summarize "
            "rather than repeating large tool outputs, and disclose all important unknowns."
        )
        # A request-local instruction leaves all original tool-call/result pairs intact.
        messages = session.messages + [{"role": "user", "content": instruction}]
        usage["model_calls"] += 1  # Count the final attempt even if the provider rejects it.
        try:
            if provider == "anthropic":
                response = client.beta.messages.create(
                    model=MODEL, max_tokens=16000,
                    system=[{"type": "text", "text": system_prompt(session.role), "cache_control": {"type": "ephemeral"}}],
                    tools=tools.claude_tools(), tool_choice={"type": "none"}, messages=messages,
                    thinking={"type": "adaptive"}, output_config={"effort": EFFORT}, betas=_betas(), fallbacks="default")
                for key in ("input_tokens", "output_tokens", "cache_read_input_tokens"):
                    usage[key] += getattr(response.usage, key, 0) or 0
                if any(block.type == "tool_use" for block in response.content):
                    limitation = "The provider requested tools during final synthesis; no additional tools were executed."
                elif response.stop_reason == "refusal":
                    limitation = "The provider declined final synthesis."
                elif response.stop_reason not in ("end_turn", "max_tokens"):
                    limitation = "The provider did not complete final synthesis."
                else:
                    text = "\n\n".join(block.text for block in response.content if block.type == "text").strip()
                    if response.stop_reason == "max_tokens":
                        limitation = "Final synthesis was cut off at the output limit; the answer is incomplete."
            else:
                response = client.chat.completions.create(
                    model=MODEL, messages=messages, tools=_openai_tools(), tool_choice="none", temperature=0.2,
                    max_tokens=4096)
                if response.usage:
                    usage["input_tokens"] += response.usage.prompt_tokens or 0
                    usage["output_tokens"] += response.usage.completion_tokens or 0
                choice = response.choices[0]
                message = choice.message
                if message.tool_calls:
                    limitation = "The provider requested tools during final synthesis; no additional tools were executed."
                elif getattr(message, "refusal", None) or choice.finish_reason == "content_filter":
                    limitation = "The provider declined final synthesis."
                elif choice.finish_reason not in ("stop", "length"):
                    limitation = "The provider did not complete final synthesis."
                else:
                    text = _tidy(message.content)
                    if choice.finish_reason == "length":
                        limitation = "Final synthesis was cut off at the output limit; the answer is incomplete."
        except (anthropic.AnthropicError, openai.OpenAIError, TypeError, ValueError, IndexError):
            limitation = "The final synthesis request failed; no additional tools were executed or retried."
        if not text and limitation is None:
            limitation = "The provider returned no final answer text."
        if set(re.findall(r"\bQ\d+\b", text)) - set(evidence_ids):
            text = ""
            limitation = "Final synthesis referenced unavailable or failed evidence and could not be used."
    answer = "\n\n".join(part for part in (prefix, text, limitation) if part)
    # Store only text, so a rejected tool request cannot leave an unmatched call in follow-up history.
    session.messages.append({"role": "assistant", "content": answer})
    yield {"type": "answer", "text": answer}
    yield {"type": "done", "usage": usage}


def _run_anthropic(session, question):
    if os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        yield {"type": "error", "text": "Both ANTHROPIC_API_KEY and ANTHROPIC_AUTH_TOKEN are set, and the API rejects requests "
                                        "that carry both. Keep one in .env (and your shell) and restart the server."}
        return
    client = _client()
    session.messages.append({"role": "user", "content": question})
    system = [{"type": "text", "text": system_prompt(session.role), "cache_control": {"type": "ephemeral"}}]
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "model_calls": 0}

    for step in range(MAX_STEPS):
        yield {"type": "status", "text": "Thinking…" if step == 0 else "Reading the evidence…"}
        if session.role == "team_lead" and step == MAX_STEPS - 1:
            yield from _team_lead_synthesis(session, client, usage, "anthropic")
            return
        try:
            response = client.beta.messages.create(
                model=MODEL,
                max_tokens=16000,
                system=system,
                tools=tools.claude_tools(),
                messages=session.messages,
                thinking={"type": "adaptive"},
                output_config={"effort": EFFORT},
                betas=_betas(),
                fallbacks="default",
            )
        except anthropic.AuthenticationError:
            yield {"type": "error", "text": "Anthropic authentication failed. Check ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN in "
                                            ".env (auth tokens expire; get a fresh one) and restart the server."}
            return
        except anthropic.RateLimitError:
            yield {"type": "error", "text": "Rate limited by the Anthropic API. Wait a moment and try again."}
            return
        except anthropic.APIStatusError as e:
            yield {"type": "error", "text": f"Anthropic API error {e.status_code}: {e.message}"}
            return
        except anthropic.APIConnectionError:
            yield {"type": "error", "text": "Could not reach the Anthropic API. Check the network connection."}
            return
        except TypeError as e:  # raised by the SDK when no credentials are configured
            if "authentication" not in str(e):
                raise
            yield {"type": "error", "text": "No Anthropic credentials found. Add ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN to .env "
                                            "and restart the server."}
            return

        usage["model_calls"] += 1
        for k in ("input_tokens", "output_tokens", "cache_read_input_tokens"):
            usage[k] += getattr(response.usage, k, 0) or 0
        session.messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "refusal":
            yield {"type": "error", "text": "The model declined this request. Try rephrasing the question."}
            return
        if response.stop_reason == "pause_turn":
            continue
        calls = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason != "tool_use" or not calls:
            text = "\n\n".join(b.text for b in response.content if b.type == "text").strip()
            if response.stop_reason == "max_tokens":
                text += "\n\n_(Answer cut off at the output limit.)_"
            yield {"type": "answer", "text": text or "_(No answer text returned.)_"}
            yield {"type": "done", "usage": usage}
            return

        results = []
        for call in calls:
            events, content, is_error = _execute(session, call.name, call.input)
            yield from events
            results.append({"type": "tool_result", "tool_use_id": call.id, "content": content, "is_error": is_error})
        session.messages.append({"role": "user", "content": results})

    yield {"type": "answer", "text": "_Stopped after the maximum number of tool steps; the answer may be incomplete._"}
    yield {"type": "done", "usage": usage}


def _openai_tools():
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
            for t in tools.claude_tools()]


def _run_openai(session, question):
    """Same loop for OpenAI-compatible chat-completions APIs (NVIDIA API catalog, Ollama, LM Studio, vLLM)."""
    client = _openai_client()
    if not session.messages:
        session.messages.append({"role": "system", "content": system_prompt(session.role)})
    session.messages.append({"role": "user", "content": question})
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "model_calls": 0}

    for step in range(MAX_STEPS):
        yield {"type": "status", "text": "Thinking…" if step == 0 else "Reading the evidence…"}
        if session.role == "team_lead" and step == MAX_STEPS - 1:
            yield from _team_lead_synthesis(session, client, usage, "openai")
            return
        try:
            response = client.chat.completions.create(model=MODEL, messages=session.messages, tools=_openai_tools(),
                                                      tool_choice="auto", temperature=0.2, max_tokens=4096)
        except (openai.AuthenticationError, openai.PermissionDeniedError):  # NVIDIA answers a bad key with 403
            yield {"type": "error", "text": f"{PROVIDER} rejected the API key. Check it in .env (and that it can use {MODEL}), "
                                            "then restart the server."}
            return
        except openai.NotFoundError:
            yield {"type": "error", "text": f"Model {MODEL!r} was not found at the {PROVIDER} endpoint. Set LOGIQ_MODEL to a model "
                                            "your key can use."}
            return
        except openai.RateLimitError:
            yield {"type": "error", "text": f"Rate limited by {PROVIDER}. Wait a moment and try again."}
            return
        except openai.APIStatusError as e:
            yield {"type": "error", "text": f"{PROVIDER} API error {e.status_code}: {e.message}"}
            return
        except openai.APIConnectionError:
            yield {"type": "error", "text": f"Could not reach the {PROVIDER} endpoint. Check LOGIQ_BASE_URL and the network."}
            return

        usage["model_calls"] += 1
        if response.usage:
            usage["input_tokens"] += response.usage.prompt_tokens or 0
            usage["output_tokens"] += response.usage.completion_tokens or 0
        choice = response.choices[0]
        msg = choice.message
        calls = msg.tool_calls or []
        assistant = {"role": "assistant", "content": msg.content or ""}
        if calls:
            assistant["tool_calls"] = [{"id": c.id, "type": "function",
                                        "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
                                       for c in calls]
        session.messages.append(assistant)

        if not calls:
            text = _tidy(msg.content)
            if choice.finish_reason == "length":
                text += "\n\n_(Answer cut off at the output limit.)_"
            yield {"type": "answer", "text": text or "_(No answer text returned.)_"}
            yield {"type": "done", "usage": usage}
            return

        for c in calls:
            try:
                args = json.loads(c.function.arguments or "{}")
                if not isinstance(args, dict):
                    raise ValueError("arguments must be a JSON object")
            except ValueError as e:
                session.evidence += 1
                err = f"Invalid tool arguments ({e}); send a JSON object matching the tool's schema."
                yield {"type": "tool_call", "id": f"Q{session.evidence}", "name": c.function.name, "input": {"raw": c.function.arguments}}
                yield {"type": "tool_result", "id": f"Q{session.evidence}", "error": err}
                session.messages.append({"role": "tool", "tool_call_id": c.id, "content": err})
                continue
            events, content, is_error = _execute(session, c.function.name, args)
            yield from events
            session.messages.append({"role": "tool", "tool_call_id": c.id, "content": ("ERROR: " if is_error else "") + content})

    yield {"type": "answer", "text": "_Stopped after the maximum number of tool steps; the answer may be incomplete._"}
    yield {"type": "done", "usage": usage}


if __name__ == "__main__":
    import sys

    role = sys.argv[1] if len(sys.argv) > 1 else "engineer"
    q = sys.argv[2] if len(sys.argv) > 2 else ROLES[role]["examples"][0]
    for ev in run(Session(role), q):
        if ev["type"] == "tool_call":
            print(f"\n[{ev['id']}] {ev['name']}({json.dumps(ev['input'])})")
        elif ev["type"] == "tool_result":
            print("   error:", ev["error"]) if "error" in ev else print(
                "   " + "; ".join(f"{q['rows_returned']} rows{' (truncated)' if q['truncated'] else ''}" for q in ev["envelope"]["queries"]))
        elif ev["type"] in ("answer", "error"):
            print("\n" + ev["text"])
        elif ev["type"] == "done":
            print("\n", ev["usage"])
