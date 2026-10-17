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
- Counts are matching log lines, not incidents. Do not call 60 lines "60 failures" unless the evidence shows separate events.
- Keep observed facts apart from interpretation. Label explanations as hypotheses and say what would confirm them.
- If the logs do not establish a cause or an impact, say what remains unknown. Never invent downtime, financial loss,
  customer or user impact, SLAs, or team members.
- Teams are the owning teams from the ownership table (e.g. compute-kernel, job-runtime, compute-api).
- If the question names no period, use the whole corpus and say so. Call corpus_overview first if you need date ranges or names.
- Be efficient: call independent tools in parallel, and stop calling tools once you have enough evidence.

Write the answer in Markdown with these sections, in this order:
{sections}
"""

ROLES = {
    "engineer": {
        "label": "Engineer",
        "goal": "Investigate a specific error: what failed, where, what happened just before and after, a possible cause, and the next check.",
        "prompt": """You are answering an ENGINEER who is investigating a specific error.
Their questions: what failed, in which component and team, what happened just before and after, what might have caused it,
and what to check next.
Evidence to gather: use search_logs (full-text search, with a time window when one is given) to find the relevant error lines
and their exact match counts; then investigate_event on a representative line for the surrounding events, recurrence and other
problems from the same source. Add get_nearby_events with `minutes` for a time window, preceding_patterns to see which messages
are unusually common just before the event, and template_history for the template's full spread. Quote key log lines verbatim.
Recommended action: one or two concrete diagnostic checks an engineer can run next (which node, file, service or config to
inspect, and what result would confirm or rule out the hypothesis).""",
        "sections": """## What failed
## Evidence (key log lines and counts, each with its [Q#])
## What happened around it
## Possible cause (hypothesis)
## Next check
## Unknowns""",
        "examples": [
            'What is behind the "Link has been severed" errors in BGL after September 2005?',
            "Investigate the data TLB error interrupts on BGL: what failed and what should I check?",
            "Why does OpenStack log 'Unknown base file' warnings, and what happened around them?",
        ],
    },
    "team_lead": {
        "label": "Team lead",
        "goal": "Find recurring problems: how often, over what period, which components and teams, and what to investigate first.",
        "prompt": """You are answering a TEAM LEAD who needs to understand recurring problems.
Their questions: which problems recur, how often, over what stated period, which components and owning teams are involved,
and what the team should investigate first.
Evidence to gather: recurring_problems for the period, count_problems grouped by team (and by component when useful), and
problem_trend over a suitable bucket (week for BGL's months of data, hour or day for short spans) to show whether each pattern
is growing, shrinking or a single burst. Use compare_periods when the question contrasts two periods, and a full-text search
only when you need to check a specific pattern.
Present the top patterns as a compact table (pattern, team, lines, active days, sources, trend). Distinguish a pattern that
recurs over many days from a single burst on one source.
Recommended action: name the first one or two patterns to investigate, the team that owns each, and why (frequency, spread,
recurrence, severity).""",
        "sections": """## Period and scope
## Recurring problems (table, with [Q#])
## Teams and components involved
## Trend
## Investigate first
## Caveats""",
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
