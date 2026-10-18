#!/usr/bin/env python3
"""Generate the visual report figures (SVG) from the recorded evidence numbers."""
import json, os
BASE = os.path.dirname(os.path.abspath(__file__))
recs = [json.loads(l) for l in open(os.path.join(BASE, "evidence.jsonl"))]

def env(sec, tool, qid=None):
    for r in recs:
        ev = r.get("event", {})
        e = ev.get("envelope", {})
        if ev.get("type") == "tool_result" and (not sec or r.get("section") == sec) \
           and e.get("tool") == tool and (not qid or e.get("evidence_id") == qid):
            return e
b1 = env("baseline", "corpus_overview")["data"]
b2 = env("baseline", "search_logs")["data"]
b3 = env("baseline", "count_problems")["data"]
e1 = env("engineer", "search_logs", "Q1")["data"]
e2 = env("engineer", "investigate_event", "Q2")["data"]
l2 = env("team_lead", "count_problems", "Q2")["data"]
l5 = env("team_lead", "problem_trend", "Q5")["data"]
m2 = env("manager", "risk_ranking", "Q2")["data"]

OS_LINES = next(d["lines"] for d in b1["datasets"] if d["dataset"] == "openstack")
WIN_PROBLEMS = l2["total_lines"]          # 3,039 problem lines in the window
PT = 192                                   # periodic_task ERROR lines (lead Q2 group)
QEMU = b2["total_matches"]                 # 24 qemu-img ERROR matches
IDI = e1["total_matches"]                  # 8 InvalidDiskInfo matches
REP = 45631                                # representative line

def save(name, body, w, h):
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" font-family="Segoe UI, Arial">'
           f'<rect width="{w}" height="{h}" fill="white"/>' + body + "</svg>")
    open(os.path.join(BASE, name), "w").write(svg)
    try:
        import cairosvg; cairosvg.svg2png(bytestring=svg.encode(),
            write_to=os.path.join(BASE, name.replace(".svg", ".png")), output_width=w*2, background_color="white")
    except Exception as ex:
        print("png skip", name, ex)

def trapezoid(y, wtop, wbot, h, color, label_right):
    x1 = (700 - wtop) / 2; x2 = (700 - wbot) / 2
    return (f'<polygon points="{x1},{y} {x1+wtop},{y} {x2+wbot},{y+h} {x2},{y+h}" fill="{color}" '
            f'fill-opacity="0.85" stroke="#234"/>'
            f'<text x="700/2" y="{y+h/2}" text-anchor="middle" font-size="15" fill="#fff" font-weight="bold"></text>'
            + f'<text x="{x2+wbot+16}" y="{y+h/2+5}" font-size="14">{label_right}</text>')

def funnel(fname, title, stages, colors):
    """stages: list of (count_label, caption) top->bottom"""
    w = 760; body = f'<text x="20" y="30" font-size="17" font-weight="bold">{title}</text>'
    y = 60
    n = len(stages)
    wtop, wbot = 560, 130
    for i, (big, cap) in enumerate(stages):
        wt = wtop + (wbot - wtop) * i / n
        wb = wtop + (wbot - wtop) * (i + 1) / n
        h = 62
        x1, x2 = (w - wt) / 2, (w - wb) / 2
        body += (f'<polygon points="{x1},{y} {x1+wt},{y} {x2+wb},{y+h} {x2},{y+h}" '
                 f'fill="{colors[i % len(colors)]}" fill-opacity="0.88" stroke="#204"/>'
                 f'<text x="{w/2}" y="{y+h/2-2}" text-anchor="middle" font-size="16" font-weight="bold" fill="#fff">{big}</text>'
                 f'<text x="{w/2}" y="{y+h/2+16}" text-anchor="middle" font-size="11.5" fill="#123">{cap}</text>')
        y += h + 8
    save(fname, body, w, y + 10)

C = ["#4472c4", "#5b9bd5", "#7faede", "#a3c4e8", "#c7dbf2"]

# 1) Engineer funnel
funnel("funnel_engineer.svg", "Engineer: the filtering funnel (OpenStack, May 16–17, 2017)", [
    ("3,039 problem lines", "every WARNING+ line in the 2-day window"),
    ("192 ERROR lines", "oslo_service.periodic_task — one recurring traceback bundle"),
    ("24 qemu-img ERROR matches", "disk-inspection command + stderr + exception lines"),
    ("8 InvalidDiskInfo exceptions", "one per image-cache pass — 8 exceptions, not 8 causes"),
    ("1 representative line", f"line {REP} — full context investigated (±10 lines)"),
], C)

# 2) Engineer venn: qemu-img ⊂ periodic_task, InvalidDiskInfo ⊂ qemu-img (nested sets)
body = ('<text x="20" y="30" font-size="17" font-weight="bold">Engineer: how the search results nest</text>'
        '<circle cx="330" cy="200" r="150" fill="#5b9bd5" fill-opacity="0.30" stroke="#345"/>'
        '<circle cx="380" cy="185" r="105" fill="#ed7d31" fill-opacity="0.35" stroke="#542"/>'
        '<circle cx="415" cy="175" r="62" fill="#c00000" fill-opacity="0.35" stroke="#702"/>'
        '<text x="150" y="380" font-size="13">192 ERROR lines in oslo_service.periodic_task</text>'
        '<text x="330" y="315" font-size="13">24 qemu-img matches (exception + command + stderr)</text>'
        '<text x="385" y="130" font-size="13">8 InvalidDiskInfo exceptions</text>'
        '<text x="405" y="160" font-size="12" fill="#700">1 representative line 45631</text>'
        '<text x="20" y="420" font-size="12" fill="#555">The 8 exceptions are a subset of the 24 qemu-img lines '
        '(each exception ships with its command + stderr lines); all 24 sit inside the recurring traceback bundle.</text>')
save("venn_engineer.svg", body, 700, 445)

# 3) Team-lead funnel
funnel("funnel_lead.svg", "Team lead: from window to owner", [
    ("3,039 problem lines", "WARNING+ in the window"),
    ("2,789 in nova.virt.libvirt.imagecache", "the disk-processing subsystem"),
    ("192 in oslo_service.periodic_task", "the recurring traceback carrier"),
    ("24 templates × 8 occurrences", "one bundle written 8 times — May 16: 144, May 17: 48"),
    ("Owner: unassigned", "the mapping has no team for it — first action: assign one"),
], C)

# 4) Team-lead trend bars (May 16 vs 17)
body = '<text x="20" y="30" font-size="17" font-weight="bold">Team lead: periodic_task lines per day</text>'
data = [(s["period"][:10], s["lines"]) for s in l5["series"]]
bx, bw = 120, 200; base = 250
for i, (d, v) in enumerate(data):
    hgt = v / max(v2 for _, v2 in data) * 150
    body += (f'<rect x="{bx + i * (bw + 60)}" y="{base - hgt}" width="{bw}" height="{hgt}" fill="#5b9bd5" stroke="#345"/>'
             f'<text x="{bx + i * (bw + 60) + bw/2}" y="{base - hgt - 8}" text-anchor="middle" font-size="16" font-weight="bold">{v}</text>'
             f'<text x="{bx + i * (bw + 60) + bw/2}" y="{base + 20}" text-anchor="middle" font-size="13">{d}</text>')
body += f'<line x1="60" y1="{base}" x2="600" y2="{base}" stroke="#666"/>'
body += '<text x="330" y="300" font-size="12" fill="#555">Halved on day 2 — consistent with the cleanup/event ending, not a growing fault.</text>'
save("trend_lead.svg", body, 660, 320)

# 5) Manager pyramid
funnel("funnel_manager.svg", "Manager: what survived the filter", [
    (f"{OS_LINES:,} lines", "whole OpenStack corpus"),
    (f"{WIN_PROBLEMS:,} problem lines", "in the investigated window"),
    ("2 identity-token CRITICAL lines", "separate, highest-severity risk (E72) — needs an owner"),
    ("8-line ERROR patterns", "the disk-processing bundle (E50/E61/E63/E58...) — rising label"),
    ("Action: assign the unassigned owner", "and verify disk-path availability — the one next step"),
], ["#70ad47", "#4472c4", "#c00000", "#ed7d31", "#bf9000"])

# 6) Manager risk bars
body = '<text x="20" y="30" font-size="17" font-weight="bold">Manager: top risks in the window (lines per pattern)</text>'
risks = m2["risks"][:6]
base = 260; mx = max(r["lines"] for r in risks)
for i, r in enumerate(risks):
    y = 60 + i * 34
    wd = max(8, int(r["lines"] / mx * 380))
    colr = "#c00000" if r["severity"] == 3 else "#ed7d31"
    body += (f'<text x="20" y="{y+14}" font-size="12.5">{r["template_id"]} · sev {r["severity"]} · {r["trend"]}</text>'
             f'<rect x="330" y="{y}" width="{wd}" height="22" fill="{colr}" stroke="#333"/>'
             f'<text x="{330+wd+10}" y="{y+16}" font-size="13">{r["lines"]}</text>')
save("risks_manager.svg", body, 760, base + 40)
print("visuals written")
