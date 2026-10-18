#!/usr/bin/env python3
"""Generate flow_chart.drawio + flow_chart.svg + flow_chart.png from flow_chart.json."""
import json, os, html

BASE = os.path.dirname(os.path.abspath(__file__))
G = json.load(open(os.path.join(BASE, "flow_chart.json")))

# ---------- layout ----------
LANES = [("baseline", "Shared baseline"), ("engineer", "Engineer"),
         ("team_lead", "Team lead"), ("manager", "Manager")]
W, H = 250, 56          # node size
GX, GY = 300, 130       # lane start x / row start y
ROW = 84
LANE_W = W + 60

pos = {}
pos["user"] = (60, 24); pos["agent"] = (330, 24)
for i, (sec, title) in enumerate(LANES):
    x = GX + i * LANE_W
    pos[f"lane_{sec}"] = (x - 20, 92)
    y = 160
    for c in [c for c in G["nodes"] if c["section"] == sec and c["kind"] == "tool"]:
        pos[c["id"]] = (x, y); y += ROW
    ans = [c for c in G["nodes"] if c.get("section") == sec and c["kind"] == "answer"]
    if ans:
        pos[ans[0]["id"]] = (x, y)
pos["db"] = (GX + 2 * LANE_W + 40, 30)
maxy = max(y for x, y in pos.values()) + 120
SW, SH = GX + 3 * LANE_W + 140, maxy

def fill(kind):
    return {"user": "#ffe6cc", "agent": "#d5e8d4", "section": "#fff2cc",
            "tool": "#dae8fc", "answer": "#d4edda", "db": "#f8cecc"}.get(kind, "#ffffff")

# ---------- SVG ----------
def svg_box(x, y, w, h, fill, label, sub="", rx=10):
    t = f'<text x="{x+w/2}" y="{y+h/2-4}" text-anchor="middle" font-size="12.5" font-weight="bold">{html.escape(label)}</text>'
    s = f'<text x="{x+w/2}" y="{y+h/2+12}" text-anchor="middle" font-size="9.5" fill="#444">{html.escape(sub[:70])}</text>' if sub else ""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
            f'stroke="#333"/><ellipse cx="{x+w-12}" cy="{y+h-10}" rx="7" ry="4" fill="none" stroke="#888"/>' + t + s)

def svg_arrow(x1, y1, x2, y2, label="", dashed=False):
    dash = ' stroke-dasharray="5,4"' if dashed else ""
    lx, ly = (x1 + x2) / 2, (y1 + y2) / 2
    lt = f'<text x="{lx+6}" y="{ly-4}" font-size="9" fill="#555">{html.escape(label)}</text>' if label else ""
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#456" stroke-width="1.4" '
            f'marker-end="url(#arr)"{dash}/>' + lt)

svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{SW}" height="{SH}" font-family="Segoe UI, Arial">',
       '<defs><marker id="arr" markerWidth="9" markerHeight="8" refX="8" refY="4" orient="auto">'
       '<path d="M0,0 L9,4 L0,8 z" fill="#456"/></marker></defs>',
       f'<rect width="{SW}" height="{SH}" fill="white"/>',
       f'<text x="16" y="26" font-size="16" font-weight="bold">LogIQ worked investigation — information flow '
       f'({html.escape(G["metadata"]["dataset"])}, {html.escape(str(G["metadata"]["window"]))})</text>']

ux, uy = pos["user"]; ax, ay = pos["agent"]
svg.append(svg_box(ux, uy, W, H, fill("user"), "User — 3 role questions"))
svg.append(svg_box(ax, ay, W, H, fill("agent"), "LogIQ agent", G["metadata"]["model"]))
svg.append(svg_arrow(ax + W / 2, ay, ux + W / 2, uy, "role answers"))
svg.append(svg_arrow(ax + W, ay + H / 2, ux + W + 40, ay + H / 2) if False else "")
svg.append(svg_arrow(ux + W, uy + H / 2, ax, ay + H / 2, "questions"))
dx, dy = pos["db"]
svg.append(svg_box(dx, dy, W, H, fill("db"), "SereneDB", "events + inverted index", rx=26))

for sec, title in LANES:
    lx, ly = pos[f"lane_{sec}"]
    svg.append(f'<rect x="{lx-6}" y="{ly-24}" width="{LANE_W-28}" height="26" fill="#eef2f7" stroke="#aab"/>'
               f'<text x="{lx+8}" y="{ly-7}" font-size="12" font-weight="bold">{html.escape(title)} lane</text>')
    lane_tools = [c for c in G["nodes"] if c["section"] == sec and c["kind"] == "tool"]
    nsql = sum(len(c.get("queries", [])) if isinstance(c.get("queries"), list) else 0 for c in lane_tools)
    tx, ty = pos[lane_tools[0]["id"]]
    svg.append(svg_arrow(ax + W / 2, ay + H, tx + W / 2, ty - 8, "evidence gathering"))
    prev = None
    for c in lane_tools:
        x, y = pos[c["id"]]
        svg.append(svg_box(x, y, W, H, fill("tool"), c["label"],
                           c.get("sublabel", "").replace("&quot;", '"')))
        if prev:
            svg.append(svg_arrow(prev[0] + W / 2, prev[1] + H, x + W / 2, y, "next call"))
        prev = (x, y)
    if prev:
        svg.append(svg_arrow(prev[0] + W / 2, prev[1] + H, dx + W / 2, dy + H, f"{nsql} SQL queries"))
    ans = [c for c in G["nodes"] if c.get("section") == sec and c["kind"] == "answer"]
    if ans:
        x, y = pos[ans[0]["id"]]
        svg.append(svg_box(x, y, W, H, fill("answer"), ans[0]["label"]))
        svg.append(svg_arrow(prev[0] + W / 2, prev[1] + H, x + W / 2, y, "interpret"))

svg_text = "\n".join(svg) + "</svg>"
open(os.path.join(BASE, "flow_chart.svg"), "w").write(svg_text)

import cairosvg
cairosvg.svg2png(bytestring=svg_text.encode(), write_to=os.path.join(BASE, "flow_chart.png"),
                 output_width=2400, background_color="white")
print("flow_chart.svg + flow_chart.png rendered")

# ---------- draw.io ----------
cells = []
cells.append('<mxCell id="0"/><mxCell id="1" parent="0"/>')
def dnode(cid, x, y, w, h, fillc, label, style_extra=""):
    style = (f"rounded=1;whiteSpace=wrap;html=1;fillColor={fillc};strokeColor=#333333;"
             f"fontSize=12;fontStyle=1;{style_extra}")
    return (f'<mxCell id="{cid}" value="{html.escape(label)}" style="{style}" vertex="1" parent="1">'
            f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')

for nid, (x, y) in pos.items():
    if nid.startswith("lane_"):
        continue
    n = next(c for c in G["nodes"] if c["id"] == nid)
    cells.append(dnode(nid, x, y, W, H, fill(n["kind"]), n["label"]))
for e in G["edges"]:
    if e["from"] in pos and e["to"] in pos:
        cells.append(f'<mxCell id="e{len(cells)}" value="{html.escape(e.get("label",""))}" style="edgeStyle=orthogonalEdgeStyle;" '
                     f'edge="1" parent="1" source="{e["from"]}" target="{e["to"]}">'
                     '<mxGeometry relative="1" as="geometry"/></mxCell>')
drawio = (f'<mxfile host="LogIQ"><diagram id="logiq-flow" name="Information flow">'
          f'<mxGraphModel dx="1200" dy="800" grid="0" pageWidth="{SW}" pageHeight="{SH}">'
          f'<root>{"".join(cells)}</root></mxGraphModel></diagram></mxfile>')
open(os.path.join(BASE, "flow_chart.drawio"), "w").write(drawio)
print("flow_chart.drawio written")
