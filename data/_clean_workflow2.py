import json
from pathlib import Path

path = Path(__file__).with_name("comfy_workflow2.json")
data = json.loads(path.read_text(encoding="utf-8"))

remove_nodes = {1297, 1298, 1299, 1300, 1302, 1304, 1305, 1306, 1307, 1308, 1309}
remove_links = {3146, 3147, 3148, 3149, 3150, 3151}
remove_subgraphs = {
    "40c32957-02e2-4ae4-ae95-91c56e70e14a",
    "baa0d1aa-4f72-48c6-af2c-258656248e7e",
    "94c1486b-cabf-4a0b-abe4-fd1f82ec7d47",
}

data["nodes"] = [n for n in data["nodes"] if n["id"] not in remove_nodes]

for node in data["nodes"]:
    if node["id"] == 563:
        for output in node["outputs"]:
            if output["name"] == "IMAGE":
                output["links"] = [3152]
    if node["id"] == 504:
        for input_port in node["inputs"]:
            if input_port["name"] == "IMAGE":
                input_port["link"] = 3152
    if node["id"] == 572:
        for key in ("widgets_values", "widgets_values_named"):
            widgets = node.get(key)
            if isinstance(widgets, dict):
                params = widgets.get("videopreview", {}).get("params", {})
                params.pop("fullpath", None)

data["links"] = [link for link in data["links"] if link[0] not in remove_links]
data["links"] = [link for link in data["links"] if link[0] != 3152]
data["links"].append([3152, 563, 0, 504, 0, "IMAGE"])

data["groups"] = [group for group in data.get("groups", []) if group.get("id") != 53]

if "definitions" in data and "subgraphs" in data["definitions"]:
    data["definitions"]["subgraphs"] = [
        subgraph
        for subgraph in data["definitions"]["subgraphs"]
        if subgraph.get("id") not in remove_subgraphs
    ]

max_node = max(node["id"] for node in data["nodes"])
max_link = max(link[0] for link in data["links"])
data["last_node_id"] = max_node
data["last_link_id"] = max_link

for subgraph in data.get("definitions", {}).get("subgraphs", []):
    state = subgraph.get("state", {})
    state["lastNodeId"] = max_node
    state["lastLinkId"] = max_link

path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(
    f"Removed {len(remove_nodes)} nodes, {len(remove_links)} links, "
    f"{len(remove_subgraphs)} subgraphs"
)
print(
    f"last_node_id={max_node}, last_link_id={max_link}, "
    f"nodes={len(data['nodes'])}, links={len(data['links'])}"
)
