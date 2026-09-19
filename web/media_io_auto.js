import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const VIDEO_LOAD_TYPES = new Set([
  "VHS_LoadVideo",
  "VHS_LoadVideoPath",
  "VHS_LoadVideoFFmpeg",
  "VHS_LoadVideoFFmpegPath",
  "LoadVideo",
  "LoadVideoPath",
  "LoadVideoUpload",
]);
const VIDEO_WIDGET_NAMES = ["video", "file", "video_path", "filename"];
const SAVE_TYPES = new Set(["SaveMaskTensor", "SaveH3AVLatent", "BuildMediaIoBasename"]);
const LOAD_MASK = "LoadMaskTensor";
const LOAD_H3 = "LoadH3AVLatent";

function nodeType(node) {
  return node?.comfyClass || node?.type || "";
}

function isVideoLoad(node) {
  const t = nodeType(node);
  if (VIDEO_LOAD_TYPES.has(t)) return true;
  if (/Save|Combine|Write|Preview|Encode/i.test(t)) return false;
  return /LoadVideo/i.test(t);
}

function widgetByName(node, name) {
  return node.widgets?.find((x) => x.name === name);
}

function isInputLinked(node, name) {
  return node.inputs?.some((inp) => inp.name === name && inp.link != null) === true;
}

function filenameFromNode(node) {
  for (const name of VIDEO_WIDGET_NAMES) {
    const value = widgetByName(node, name)?.value;
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function mediaStem(filename) {
  const base = String(filename || "")
    .split(/[/\\]/)
    .pop()
    .trim();
  if (!base) return "";
  const cut = base.lastIndexOf(".");
  return (cut > 0 ? base.slice(0, cut) : base).replace(/\s+/g, "_");
}

function matchesStem(filename, stem) {
  if (!filename || !stem) return false;
  const fileStem = String(filename).replace(/\.(cmask|pt|h3latent)$/i, "");
  return fileStem === stem || fileStem.startsWith(`${stem}_`);
}

function walkAncestors(node) {
  const graph = app.graph;
  const seen = new Set();
  const out = [];
  const queue = [node];
  while (queue.length) {
    const current = queue.shift();
    if (!current || seen.has(current.id)) continue;
    seen.add(current.id);
    out.push(current);
    for (const inp of current.inputs || []) {
      if (inp.link == null) continue;
      const link = graph.links?.[inp.link];
      if (!link) continue;
      queue.push(graph.getNodeById(link.origin_id));
    }
  }
  return out;
}

function videoFilenameFor(target) {
  for (const node of walkAncestors(target)) {
    if (node === target) continue;
    if (!isVideoLoad(node)) continue;
    const name = filenameFromNode(node);
    if (name) return name;
  }
  const names = [
    ...new Set(
      (app.graph?._nodes || [])
        .filter(isVideoLoad)
        .map(filenameFromNode)
        .filter(Boolean)
    ),
  ];
  return names.length === 1 ? names[0] : "";
}

function setWidget(node, name, value) {
  if (isInputLinked(node, name)) return false;
  const widget = widgetByName(node, name);
  if (!widget) return false;
  const values = widget.options?.values;
  if (Array.isArray(values)) {
    const next = values.filter((v) => v !== "(none)" && v !== value);
    next.push(value);
    next.sort();
    widget.options.values = next;
  }
  if (widget.value === value) return false;
  widget.value = value;
  widget.callback?.(value, undefined, node, undefined, undefined);
  node.setDirtyCanvas?.(true, true);
  return true;
}

function wrapVideoWidget(node) {
  for (const name of VIDEO_WIDGET_NAMES) {
    const widget = widgetByName(node, name);
    if (!widget || widget._mediaIoWrapped) continue;
    widget._mediaIoWrapped = true;
    const orig = widget.callback;
    widget.callback = function () {
      const result = orig?.apply(this, arguments);
      scheduleSync();
      return result;
    };
  }
}

async function fetchFiles(path, subfolder) {
  const params = new URLSearchParams({ subfolder: subfolder || "" });
  const res = await api.fetchApi(`${path}?${params.toString()}`);
  if (!res.ok) throw new Error(`list failed (${res.status})`);
  return res.json();
}

function pickLatest(files, stem, current) {
  const matches = (files || []).filter((f) => matchesStem(f.filename, stem));
  if (!matches.length) return "";
  if (current && matches.some((f) => f.filename === current)) return current;
  return matches.sort((a, b) => (b.mtime || 0) - (a.mtime || 0))[0].filename;
}

function syncSaveNodes() {
  for (const node of app.graph?._nodes || []) {
    if (!SAVE_TYPES.has(nodeType(node))) continue;
    const video = videoFilenameFor(node);
    if (!video) continue;
    setWidget(node, "video_filename", video);
  }
}

async function syncLoadNode(node, kind) {
  const video = videoFilenameFor(node);
  const stem = mediaStem(video);
  if (!stem) return;
  const subfolder = widgetByName(node, "subfolder")?.value;
  const widgetName = kind === "mask" ? "mask_file" : "latent_file";
  const path = kind === "mask" ? "/mask_io/list" : "/h3_latent_io/list";
  try {
    const data = await fetchFiles(path, subfolder);
    const current = String(widgetByName(node, widgetName)?.value || "");
    const next = pickLatest(data.files || [], stem, current);
    if (next) setWidget(node, widgetName, next);
  } catch (err) {
    console.warn("[media_io] auto-select failed:", err);
  }
}

async function syncAll() {
  syncSaveNodes();
  const jobs = [];
  for (const node of app.graph?._nodes || []) {
    const t = nodeType(node);
    if (t === LOAD_MASK) jobs.push(syncLoadNode(node, "mask"));
    else if (t === LOAD_H3) jobs.push(syncLoadNode(node, "h3"));
  }
  await Promise.all(jobs);
}

let syncTimer = 0;
function scheduleSync() {
  clearTimeout(syncTimer);
  syncTimer = setTimeout(() => {
    syncAll();
  }, 150);
}

function bindNode(node) {
  if (!node) return;
  if (isVideoLoad(node)) wrapVideoWidget(node);
  const t = nodeType(node);
  if (SAVE_TYPES.has(t) || t === LOAD_MASK || t === LOAD_H3) scheduleSync();
}

app.registerExtension({
  name: "media_io.auto_filename",
  nodeCreated(node) {
    bindNode(node);
  },
  loadedGraphNode(node) {
    bindNode(node);
  },
  async setup() {
    const orig = app.graph?.onNodeAdded;
    if (app.graph) {
      app.graph.onNodeAdded = function (node) {
        const result = orig?.apply(this, arguments);
        bindNode(node);
        return result;
      };
    }
    api.addEventListener("graphChanged", scheduleSync);
    api.addEventListener("execution_success", scheduleSync);
    scheduleSync();
  },
});
