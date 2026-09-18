import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const DEFAULT_SUBFOLDER = "latents/MiniMaxH3";

function viewUrl({ filename, subfolder = "", type = "output" }) {
  const params = new URLSearchParams({
    filename,
    type,
    subfolder: subfolder || "",
  });
  return api.apiURL(`/view?${params.toString()}`);
}

function toast(detail, severity = "warn") {
  const t = app.extensionManager?.toast;
  if (t?.add) {
    t.add({ severity, summary: "h3_latent_io", detail, life: 3500 });
  } else {
    console[severity === "error" ? "error" : "warn"](`[h3_latent_io] ${detail}`);
  }
}

function widgetByName(node, name) {
  return node.widgets?.find((x) => x.name === name);
}

function widgetValue(node, name) {
  return widgetByName(node, name)?.value;
}

function cleanSubfolder(raw) {
  const s = String(raw ?? DEFAULT_SUBFOLDER).replace(/^[/\\]+|[/\\]+$/g, "");
  return s || DEFAULT_SUBFOLDER;
}

function downloadFile(info) {
  if (!info?.filename) return;
  const a = document.createElement("a");
  a.href = viewUrl(info);
  a.download = info.filename;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(mtime) {
  try {
    return new Date(mtime * 1000).toLocaleString();
  } catch {
    return "";
  }
}

async function fetchH3LatentList(subfolder) {
  const params = new URLSearchParams({ subfolder });
  const res = await api.fetchApi(`/h3_latent_io/list?${params.toString()}`);
  if (!res.ok) throw new Error(`list failed (${res.status})`);
  return res.json();
}

async function deleteH3Latents(subfolder, filenames) {
  const res = await api.fetchApi("/h3_latent_io/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ subfolder, filenames }),
  });
  if (!res.ok) throw new Error(`delete failed (${res.status})`);
  return res.json();
}

function setLatentFileOnNode(node, filename) {
  const w = widgetByName(node, "latent_file");
  if (!w) return;
  const values = w.options?.values;
  if (Array.isArray(values)) {
    const next = values.filter((v) => v !== "(none)" && v !== filename);
    next.push(filename);
    next.sort();
    w.options.values = next;
  }
  w.value = filename;
  w.callback?.(filename, undefined, node, undefined, undefined);
  node.setDirtyCanvas?.(true, true);
}

function ensureStyles() {
  if (document.getElementById("h3-latent-io-picker-styles")) return;
  const style = document.createElement("style");
  style.id = "h3-latent-io-picker-styles";
  style.textContent = `
    .h3-latent-io-overlay {
      position: fixed; inset: 0; z-index: 10000;
      background: rgba(0,0,0,0.55);
      display: flex; align-items: center; justify-content: center;
      font-family: system-ui, sans-serif;
    }
    .h3-latent-io-dialog {
      background: #222; color: #eee; border: 1px solid #555;
      border-radius: 8px; width: min(520px, 92vw); max-height: 80vh;
      display: flex; flex-direction: column; box-shadow: 0 8px 32px rgba(0,0,0,0.45);
    }
    .h3-latent-io-dialog h3 {
      margin: 0; padding: 12px 14px; border-bottom: 1px solid #444; font-size: 14px;
    }
    .h3-latent-io-list {
      overflow: auto; padding: 8px 10px; flex: 1; min-height: 120px;
    }
    .h3-latent-io-row {
      display: grid; grid-template-columns: auto 1fr auto;
      gap: 8px; align-items: center;
      padding: 6px 4px; border-bottom: 1px solid #333; font-size: 12px;
      cursor: pointer;
    }
    .h3-latent-io-row:last-child { border-bottom: none; }
    .h3-latent-io-meta { color: #999; font-size: 11px; }
    .h3-latent-io-empty { padding: 24px; text-align: center; color: #999; }
    .h3-latent-io-actions {
      display: flex; gap: 8px; justify-content: flex-end; flex-wrap: wrap;
      padding: 10px 12px; border-top: 1px solid #444;
    }
    .h3-latent-io-actions button {
      cursor: pointer; border: 1px solid #666; background: #333; color: #eee;
      border-radius: 4px; padding: 6px 12px; font-size: 12px;
    }
    .h3-latent-io-actions button:hover { background: #444; }
    .h3-latent-io-actions button.primary { background: #3a6; border-color: #4b7; }
    .h3-latent-io-actions button.danger { background: #633; border-color: #844; }
    .h3-latent-io-actions button:disabled { opacity: 0.45; cursor: default; }
  `;
  document.head.appendChild(style);
}

/**
 * @param {object} node
 * @param {"manage"|"select"} mode
 */
async function openH3LatentPicker(node, mode = "manage") {
  ensureStyles();
  const subfolder = cleanSubfolder(widgetValue(node, "subfolder"));
  const selectMode = mode === "select";

  let data;
  try {
    data = await fetchH3LatentList(subfolder);
  } catch (e) {
    toast(`Could not list latents: ${e.message}`, "error");
    return;
  }

  const primaryLabel = selectMode ? "Select" : "Download";
  const primaryAct = selectMode ? "select" : "download";

  const overlay = document.createElement("div");
  overlay.className = "h3-latent-io-overlay";
  overlay.innerHTML = `
    <div class="h3-latent-io-dialog" role="dialog" aria-label="H3 latent files">
      <h3>H3 latent files - output/${subfolder}</h3>
      <div class="h3-latent-io-list"></div>
      <div class="h3-latent-io-actions">
        <button type="button" data-act="refresh">Refresh</button>
        <button type="button" data-act="close">Close</button>
        ${
          selectMode
            ? ""
            : `<button type="button" class="danger" data-act="delete" disabled>Delete</button>`
        }
        <button type="button" class="primary" data-act="${primaryAct}" disabled>${primaryLabel}</button>
      </div>
    </div>
  `;

  const listEl = overlay.querySelector(".h3-latent-io-list");
  const btnDelete = overlay.querySelector('[data-act="delete"]');
  const btnPrimary = overlay.querySelector(`[data-act="${primaryAct}"]`);
  let files = data.files || [];
  const current = selectMode ? String(widgetValue(node, "latent_file") || "") : "";
  const inputType = selectMode ? "radio" : "checkbox";
  const inputName = selectMode ? "h3-latent-io-pick" : undefined;

  function selectedNames() {
    return [...listEl.querySelectorAll(`input[type="${inputType}"]:checked`)].map(
      (el) => el.value
    );
  }

  function syncButtons() {
    const n = selectedNames().length;
    if (btnDelete) btnDelete.disabled = n === 0;
    btnPrimary.disabled = n === 0;
  }

  function applySelection(filename) {
    setLatentFileOnNode(node, filename);
    toast(`Selected ${filename}`, "success");
    close();
  }

  function render() {
    listEl.replaceChildren();
    if (!files.length) {
      const empty = document.createElement("div");
      empty.className = "h3-latent-io-empty";
      empty.textContent = `No .h3latent files in output/${subfolder}`;
      listEl.appendChild(empty);
      syncButtons();
      return;
    }
    for (const f of files) {
      const label = document.createElement("label");
      label.className = "h3-latent-io-row";
      const input = document.createElement("input");
      input.type = inputType;
      if (inputName) input.name = inputName;
      input.value = f.filename;
      if (selectMode && f.filename === current) input.checked = true;
      input.addEventListener("change", syncButtons);
      if (selectMode) {
        label.addEventListener("dblclick", () => applySelection(f.filename));
      }
      const span = document.createElement("span");
      const name = document.createElement("div");
      name.textContent = f.filename;
      const meta = document.createElement("div");
      meta.className = "h3-latent-io-meta";
      meta.textContent = `${formatBytes(f.size || 0)} · ${formatTime(f.mtime)}`;
      span.append(name, meta);
      label.append(input, span);
      listEl.appendChild(label);
    }
    syncButtons();
  }

  function close() {
    overlay.remove();
  }

  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close();
  });

  overlay.querySelector('[data-act="close"]').onclick = close;

  overlay.querySelector('[data-act="refresh"]').onclick = async () => {
    try {
      data = await fetchH3LatentList(subfolder);
      files = data.files || [];
      render();
    } catch (e) {
      toast(`Refresh failed: ${e.message}`, "error");
    }
  };

  if (selectMode) {
    btnPrimary.onclick = () => {
      const names = selectedNames();
      if (names.length !== 1) return;
      applySelection(names[0]);
    };
  } else {
    btnPrimary.onclick = () => {
      const names = selectedNames();
      if (!names.length) return;
      for (const filename of names) {
        downloadFile({ filename, subfolder, type: "output" });
      }
      toast(`Downloading ${names.length} file(s)`, "success");
    };

    btnDelete.onclick = async () => {
      const names = selectedNames();
      if (!names.length) return;
      if (!confirm(`Delete ${names.length} file(s) from output/${subfolder}?`)) return;
      try {
        const result = await deleteH3Latents(subfolder, names);
        const n = (result.deleted || []).length;
        toast(`Deleted ${n} file(s)`, n ? "success" : "warn");
        data = await fetchH3LatentList(subfolder);
        files = data.files || [];
        render();
      } catch (e) {
        toast(`Delete failed: ${e.message}`, "error");
      }
    };
  }

  document.body.appendChild(overlay);
  render();
}

function addPickerButton(nodeType, { label, mode }) {
  const onNodeCreated = nodeType.prototype.onNodeCreated;
  nodeType.prototype.onNodeCreated = function () {
    const r = onNodeCreated?.apply(this, arguments);
    this.addWidget("button", label, label, () => {
      openH3LatentPicker(this, mode);
    });
    return r;
  };
}

app.registerExtension({
  name: "h3_latent_io.picker",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name === "SaveH3AVLatent") {
      addPickerButton(nodeType, { label: "download", mode: "manage" });
    } else if (nodeData.name === "LoadH3AVLatent") {
      addPickerButton(nodeType, { label: "select", mode: "select" });
    }
  },
});
