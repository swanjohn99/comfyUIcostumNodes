import json
from pathlib import Path
from collections import defaultdict, deque
from copy import deepcopy

p = Path(__file__).with_name("Workflow-2-Updated.json")
wf = json.loads(p.read_text(encoding="utf-8"))

incoming = defaultdict(list)
for L in wf["links"]:
    incoming[L[3]].append(L)

nodes = {n["id"]: n for n in wf["nodes"]}

sets = {}
for n in wf["nodes"]:
    if n["type"] != "SetNode":
        continue
    name = (n.get("widgets_values_named") or {}).get("Constant")
    if name is None and n.get("widgets_values"):
        name = n["widgets_values"][0]
    if name:
        sets[name] = n["id"]

TARGET = 526
keep = set()
q = deque([TARGET])
while q:
    nid = q.popleft()
    if nid in keep:
        continue
    keep.add(nid)
    n = nodes[nid]
    for L in incoming[nid]:
        if L[1] not in keep:
            q.append(L[1])
    if n["type"] == "GetNode":
        name = (n.get("widgets_values_named") or {}).get("Constant")
        if name is None and n.get("widgets_values"):
            name = n["widgets_values"][0]
        if name in sets and sets[name] not in keep:
            q.append(sets[name])

keep_link_ids = {L[0] for L in wf["links"] if L[1] in keep and L[3] in keep}
keep_links = [L for L in wf["links"] if L[0] in keep_link_ids]

new_nodes = []
for n in wf["nodes"]:
    if n["id"] not in keep:
        continue
    n = deepcopy(n)
    for inp in n.get("inputs") or []:
        link = inp.get("link")
        if link is not None and link not in keep_link_ids:
            inp["link"] = None
    for out in n.get("outputs") or []:
        links = out.get("links")
        if isinstance(links, list):
            filtered = [lid for lid in links if lid in keep_link_ids]
            out["links"] = filtered if filtered else None
        elif links is not None and links not in keep_link_ids:
            out["links"] = None
    new_nodes.append(n)

new_nodes.sort(key=lambda n: n["id"])


def in_group(node, g):
    bx, by, bw, bh = g["bounding"][:4]
    x, y = node["pos"][:2]
    return bx <= x <= bx + bw and by <= y <= by + bh


new_groups = []
for g in wf.get("groups") or []:
    members = [n for n in new_nodes if in_group(n, g)]
    if members:
        new_groups.append(g)

used_types = {n["type"] for n in new_nodes}
defs = deepcopy(wf.get("definitions") or {})
subs = defs.get("subgraphs") or []
defs["subgraphs"] = [s for s in subs if s.get("id") in used_types]

out = {
    "id": wf.get("id"),
    "revision": wf.get("revision", 0),
    "last_node_id": max(keep) if keep else 0,
    "last_link_id": max(keep_link_ids) if keep_link_ids else 0,
    "nodes": new_nodes,
    "links": keep_links,
    "groups": new_groups,
    "definitions": defs,
    "config": wf.get("config", {}),
    "extra": wf.get("extra", {}),
    "version": wf.get("version", 0.4),
}

p.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print("wrote", p)
print("nodes", len(out["nodes"]), sorted(n["id"] for n in out["nodes"]))
print("links", len(out["links"]))
print("groups", [(g.get("id"), g.get("title")) for g in out["groups"]])
print("subgraphs", len(out["definitions"].get("subgraphs", [])))
n526 = next(n for n in out["nodes"] if n["id"] == 526)
print("526 outputs", n526["outputs"])
print("526 inputs", [(i["name"], i.get("link")) for i in n526["inputs"]])
