import json
from collections import defaultdict, deque

path = r"c:\Users\borai\work\comfyUIcostumNodes\data\Workflow-2-Updated.json"
with open(path, "r", encoding="utf-8") as f:
    w = json.load(f)

nodes = {n["id"]: n for n in w["nodes"]}
links = w["links"]

by_id = {}
incoming = defaultdict(list)
outgoing = defaultdict(list)
for L in links:
    lid, src, ss, tgt, ts = L[0], L[1], L[2], L[3], L[4]
    typ = L[5] if len(L) > 5 else None
    by_id[lid] = (lid, src, ss, tgt, ts, typ)
    incoming[tgt].append((lid, src, ss, tgt, ts, typ))
    outgoing[src].append((lid, src, ss, tgt, ts, typ))


def get_constant_name(n):
    wv = n.get("widgets_values")
    if wv is None:
        return None
    if isinstance(wv, list) and len(wv) > 0:
        v = wv[0]
        if isinstance(v, dict):
            return v.get("name") or v.get("constant") or v.get("value")
        if isinstance(v, str):
            return v
    if isinstance(wv, dict):
        return wv.get("constant_name") or wv.get("name") or wv.get("value")
    return None


print("=== SetNode / GetNode inventory ===")
sets = {}
gets = {}
for nid, n in nodes.items():
    t = n.get("type")
    if t == "SetNode":
        c = get_constant_name(n)
        sets[nid] = c
        print(f"SetNode {nid}: const={c!r} title={n.get('title')!r} wv={n.get('widgets_values')!r}")
        for inp in n.get("inputs") or []:
            print(f"  in: {inp.get('name')} link={inp.get('link')}")
    elif t == "GetNode":
        c = get_constant_name(n)
        gets[nid] = c
        print(f"GetNode {nid}: const={c!r} title={n.get('title')!r} wv={n.get('widgets_values')!r}")

const_to_sets = defaultdict(list)
for nid, c in sets.items():
    if c is not None:
        const_to_sets[c].append(nid)

print("\nconst_to_sets:", dict(const_to_sets))

START = 526
keep = set()
why = {}
queue = deque()


def enqueue(nid, reason):
    if nid is None or nid not in nodes:
        return
    if nid in keep:
        return
    keep.add(nid)
    why[nid] = reason
    queue.append(nid)


enqueue(START, "target node NKDAVLatent")

get_set_pairs = []

while queue:
    nid = queue.popleft()
    n = nodes[nid]
    for lid, src, ss, tgt, ts, typ in incoming.get(nid, []):
        enqueue(src, f"upstream of {nid} via link {lid} ({typ})")
    for inp in n.get("inputs") or []:
        link = inp.get("link")
        if link is not None and link in by_id:
            lid, src, ss, tgt, ts, typ = by_id[link]
            enqueue(src, f"upstream of {nid} via input {inp.get('name')} link {lid}")
        elif link is not None and link not in by_id:
            print(f"WARNING: node {nid} input {inp.get('name')} link {link} not in links array")

    if n.get("type") == "GetNode":
        const = gets.get(nid) or get_constant_name(n)
        matching = const_to_sets.get(const, [])
        if not matching:
            print(f"WARNING: GetNode {nid} const={const!r} has no matching SetNode")
        for set_id in matching:
            get_set_pairs.append((nid, const, set_id))
            enqueue(set_id, f"SetNode for GetNode {nid} constant {const!r}")

print(f"\nKEEP count: {len(keep)}")
print("KEEP ids sorted:", sorted(keep))

keep_links = []
for L in links:
    lid, src, tgt = L[0], L[1], L[3]
    if src in keep and tgt in keep:
        keep_links.append(lid)

print("KEEP link ids:", sorted(keep_links))
print("REMOVE ids:", sorted(set(nodes) - keep))

print("\n=== Groups ===")
for i, g in enumerate(w.get("groups") or []):
    print(f"group[{i}]: id={g.get('id')} title={g.get('title')!r} bounding={g.get('bounding')}")
    # check which keep nodes fall in bounding box [x,y,w,h]
    b = g.get("bounding")
    if b and len(b) >= 4:
        gx, gy, gw, gh = b[0], b[1], b[2], b[3]
        inside_keep = []
        inside_all = []
        for nid, n in nodes.items():
            pos = n.get("pos")
            if not pos:
                continue
            # pos can be [x,y] or {"0":x,"1":y}
            if isinstance(pos, dict):
                px, py = pos.get(0, pos.get("0")), pos.get(1, pos.get("1"))
            else:
                px, py = pos[0], pos[1]
            if gx <= px <= gx + gw and gy <= py <= gy + gh:
                inside_all.append(nid)
                if nid in keep:
                    inside_keep.append(nid)
        print(f"  nodes inside: {sorted(inside_all)}")
        print(f"  keep inside: {sorted(inside_keep)}")
        print(f"  only_kept={set(inside_all).issubset(keep) and len(inside_all)>0}")

print("\n=== KEEP NODES DETAIL ===")
for nid in sorted(keep):
    n = nodes[nid]
    print(f"{nid}\t{n.get('type')}\ttitle={n.get('title')!r}\twhy={why[nid]}")

print("\n=== Get/Set pairs used ===")
for g, c, s in get_set_pairs:
    print(f"GetNode {g} const={c!r} <- SetNode {s}")

# Also dump full keep chain edges for clarity
print("\n=== KEEP EDGES ===")
for lid in sorted(keep_links):
    L = by_id[lid]
    print(f"link {lid}: {L[1]}:{L[2]} -> {L[3]}:{L[4]} ({L[5]})")

# Check for Reroute
print("\n=== Reroute in keep ===")
for nid in sorted(keep):
    if "eroute" in (nodes[nid].get("type") or "") or nodes[nid].get("type") == "Reroute":
        print(nid, nodes[nid].get("type"))
