#!/usr/bin/env python3
"""Build flow_chart.json + flow_chart.html (mermaid) from evidence.jsonl — the information-flow graph
of the worked investigation: questions -> agent -> tool calls (SQL) -> SereneDB -> role answers."""
import json, os, sys, html

BASE = os.path.dirname(os.path.abspath(__file__))
recs = [json.loads(l) for l in open(os.path.join(BASE, "evidence.jsonl"))]

meta, sections = None, {}
cur = None
for rec in recs:
    sec, ev = rec.get("section"), rec.get("event", {})
    t = ev.get("type")
    if t == "metadata":
        meta = ev
    elif t == "question":
        cur = sec
        sections.setdefault(sec, {"question": ev.get("question", ""), "calls": []})
    elif t == "tool_call":
        sections.setdefault(sec, {"calls": []})["calls"].append({"tool": ev.get("name"), "input": ev.get("input", {})})
    elif t == "tool_result":
        calls = sections.get(sec, {}).get("calls")
        if calls:
            env = ev.get("envelope", {})
            calls[-1].update({"evidence_id": env.get("evidence_id"), "tool": env.get("tool", calls[-1]["tool"]),
                              "n_queries": len(env.get("queries", [])),
                              "queries": [{"purpose": q.get("purpose"),
                                           "sql": " ".join(q.get("sql", "").split())[:200],
                                           "params": q.get("params"), "rows": q.get("rows_returned"),
                                           "truncated": q.get("truncated")} for q in env.get("queries", [])],
                              "truncated": any(q.get("truncated") for q in env.get("queries", []))})
    elif t == "answer" and cur:
        sections[cur]["answer_chars"] = len(ev.get("text", ""))

# ---- graph ----
ORDER = ["baseline", "engineer", "team_lead", "manager"]
NID = {}
nodes, edges = [], []
def node(nid, label, sub, kind, section=""):
    nodes.append({"id": nid, "label": label, "sublabel": sub, "kind": kind, "section": section})

node("user", "User", "asks one question per role: engineer / team_lead / manager", "user")
node("agent", "LogIQ agent", f"model: {meta.get('model', '')}", "agent")
node("db", "SereneDB", "unified events table + inverted index — every call runs parameterised SQL", "db")
node("baseline", "Shared baseline", "scope: corpus overview + first pattern located (qemu-img)", "section")

bcount = 0
prev = {"baseline": "baseline"}
for sec in ORDER:
    calls = sections.get(sec, {}).get("calls", [])
    chain_prev = "agent"
    for i, c in enumerate(calls):
        eid = c.get("evidence_id") or ("B%d" % (bcount + 1))
        if sec == "baseline":
            bcount += 1
            nid = f"base_{eid}"; label = f"{eid}: {c['tool']}"
        else:
            nid = f"{sec}_{eid}"; label = f"{eid}: {c['tool']}"
        inp = json.dumps(c.get("input", {}), default=str)
        node(nid, label, html.escape(inp[:120], quote=False) + ("…" if len(inp) > 120 else ""), "tool", sec)
        if sec == "baseline":
            edges.append({"from": "baseline", "to": nid, "label": "step %d" % (i + 1)})
        else:
            edges.append({"from": chain_prev, "to": nid, "label": "next call"})
            chain_prev = nid
        edges.append({"from": nid, "to": "db", "label": "%d SQL quer%s" % (c.get("n_queries", 0), "y" if c.get("n_queries", 0) == 1 else "ies")})
        NID[(sec, eid)] = nid
    if sec != "baseline":
        ans_id = f"{sec}_answer"
        q = meta.get("questions", {}).get(sec, "")
        node(ans_id, f"{sec.capitalize()} answer", html.escape(q[:100], quote=False) + f"  [{sections[sec].get('answer_chars', 0)} chars]",
             "answer", sec)
        edges.append({"from": chain_prev, "to": ans_id, "label": "interpret evidence"})
        edges.append({"from": ans_id, "to": "user", "label": "role answer"})

graph = {"metadata": {"dataset": meta.get("dataset"),
                      "window": f"{meta.get('start_inclusive')} → {meta.get('end_exclusive')}",
                      "timestamp_scope": meta.get("timestamp_scope"),
                      "provider": meta.get("provider"), "model": meta.get("model")},
         "questions": meta.get("questions", {}),
         "nodes": nodes, "edges": edges}
json.dump(graph, open(os.path.join(BASE, "flow_chart.json"), "w"), indent=1)
print("flow_chart.json: %d nodes, %d edges" % (len(nodes), len(edges)))

# ---- mermaid html ----
def esc(s): return html.escape(s, quote=False)
lines = ["flowchart TD"]
lines.append('    user(["User — 3 role questions"])')
lines.append('    agent["LogIQ agent\\n(model picks tools)"]')
lines.append('    db[("SereneDB\\nevents table + index")]')
lines.append('    baseline{{"Shared baseline\\ncorpus scope + first pattern"}}')
seen = set()
for n in nodes:
    if n["id"] in ("user", "agent", "db", "baseline"):
        continue
    shape = '(["%s"])' if n["kind"] == "user" else ('{"%s"}' if n["kind"] == "section" else ('[("%s")]' if n["kind"] == "answer" else '["%s"]'))
    label = esc(n["label"]) + (("\\n" + esc(n["sublabel"][:90]).replace(chr(34), "&quot;")) if n["kind"] == "tool" else "")
    lines.append('    %s%s' % (n["id"], shape % label))
for e in edges:
    a, b, lbl = e["from"], e["to"], esc(e.get("label", ""))
    if (a, b) in seen:
        continue
    seen.add((a, b))
    lines.append('    %s -->|%s| %s' % (a, lbl, b))
open(os.path.join(BASE, "flow_chart.mmd"), "w").write("\n".join(lines))

page = """<!doctype html><html><head><meta charset="utf-8"><title>LogIQ worked investigation — information flow</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<style>body{font-family:system-ui;margin:24px;background:#fafafa}h1{font-size:20px}
.meta{color:#555;font-size:14px;margin-bottom:12px}</style></head><body>
<h1>LogIQ worked investigation — information flow</h1>
<div class="meta">__META__</div>
<div class="mermaid">__MMD__</div>
<script>mermaid.initialize({startOnLoad:true, flowchart:{htmlLabels:true, curve:'basis'}});</script>
</body></html>"""
page = page.replace("__MMD__", "\n".join(lines).replace("`", "'"))
page = page.replace("__META__", "%s | %s | %s" % (graph["metadata"]["dataset"], graph["metadata"]["window"], graph["metadata"]["model"]))
open(os.path.join(BASE, "flow_chart.html"), "w").write(page)
print("flow_chart.html + flow_chart.mmd written")
