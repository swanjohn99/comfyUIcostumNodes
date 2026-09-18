import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

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
    t.add({ severity, summary: "mask_io", detail, life: 3500 });
  } else {
    console[severity === "error" ? "error" : "warn"](`[mask_io] ${detail}`);
  }
}

function widgetByName(node, name) {
  return node.widgets?.find((x) => x.name === name);
}

function widgetValue(node, name) {
  return widgetByName(node, name)?.value;
}

function cleanSubfolder(raw) {
  const s = String(raw ?? "masks").replace(/^[/\\]+|[/\\]+$/g, "");
  return s || "masks";
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

async function fetchMaskList(subfolder) {
  const params = new URLSearchParams({ subfolder });
  const res = await api.fetchApi(`/mask_io/list?${params.toString()}`);
  if (!res.ok) throw new Error(`list failed (${res.status})`);
  return res.json();
}

async function deleteMasks(subfolder, filenames) {
  const res = await api.fetchApi("/mask_io/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ subfolder, filenames }),
  });
  if (!res.ok) throw new Error(`delete failed (${res.status})`);
  return res.json();
}

function setMaskFileOnNode(node, filename) {
  const w = widgetByName(node, "mask_file");
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
  if (document.getElementById("mask-io-picker-styles")) return;
  const style = document.createElement("style");
  style.id = "mask-io-picker-styles";
  style.textContent = `
    .mask-io-overlay {
      position: fixed; inset: 0; z-index: 10000;
      background: rgba(0,0,0,0.55);
      display: flex; align-items: center; justify-content: center;
      font-family: system-ui, sans-serif;
    }
    .mask-io-dialog {
      background: #222; color: #eee; border: 1px solid #555;
      border-radius: 8px; width: min(520px, 92vw); max-height: 80vh;
      display: flex; flex-direction: column; box-shadow: 0 8px 32px rgba(0,0,0,0.45);
    }
    .mask-io-dialog h3 {
      margin: 0; padding: 12px 14px; border-bottom: 1px solid #444; font-size: 14px;
    }
    .mask-io-select-all {
      display: flex; align-items: center; gap: 8px;
      padding: 8px 14px; border-bottom: 1px solid #333;
      font-size: 12px; cursor: pointer; user-select: none;
    }
    .mask-io-list {
      overflow: auto; padding: 8px 10px; flex: 1; min-height: 120px;
    }
    .mask-io-row {
      display: grid; grid-template-columns: auto 1fr auto;
      gap: 8px; align-items: center;
      padding: 6px 4px; border-bottom: 1px solid #333; font-size: 12px;
      cursor: pointer;
    }
    .mask-io-row:last-child { border-bottom: none; }
    .mask-io-meta { color: #999; font-size: 11px; }
    .mask-io-empty { padding: 24px; text-align: center; color: #999; }
    .mask-io-actions {
      display: flex; gap: 8px; justify-content: flex-end; flex-wrap: wrap;
      padding: 10px 12px; border-top: 1px solid #444;
    }
    .mask-io-actions button {
      cursor: pointer; border: 1px solid #666; background: #333; color: #eee;
      border-radius: 4px; padding: 6px 12px; font-size: 12px;
    }
    .mask-io-actions button:hover { background: #444; }
    .mask-io-actions button.primary { background: #3a6; border-color: #4b7; }
    .mask-io-actions button.danger { background: #633; border-color: #844; }
    .mask-io-actions button:disabled { opacity: 0.45; cursor: default; }
  `;
  document.head.appendChild(style);
}

/**
 * @param {object} node
 * @param {"manage"|"select"} mode
 */
async function openMaskPicker(node, mode = "manage") {
  ensureStyles();
  const subfolder = cleanSubfolder(widgetValue(node, "subfolder"));
  const selectMode = mode === "select";

  let data;
  try {
    data = await fetchMaskList(subfolder);
  } catch (e) {
    toast(`Could not list masks: ${e.message}`, "error");
    return;
  }

  const primaryLabel = selectMode ? "Select" : "Download";
  const primaryAct = selectMode ? "select" : "download";

  const overlay = document.createElement("div");
  overlay.className = "mask-io-overlay";
  overlay.innerHTML = `
    <div class="mask-io-dialog" role="dialog" aria-label="Mask files">
      <h3>Mask files - output/${subfolder}</h3>
      ${
        selectMode
          ? ""
          : `<label class="mask-io-select-all" hidden>
        <input type="checkbox" data-act="select-all"> Select All
      </label>`
      }
      <div class="mask-io-list"></div>
      <div class="mask-io-actions">
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

  const listEl = overlay.querySelector(".mask-io-list");
  const selectAllEl = overlay.querySelector('[data-act="select-all"]');
  const selectAllRow = overlay.querySelector(".mask-io-select-all");
  const btnDelete = overlay.querySelector('[data-act="delete"]');
  const btnPrimary = overlay.querySelector(`[data-act="${primaryAct}"]`);
  let files = data.files || [];
  let selectAllChecked = false;
  const current = selectMode ? String(widgetValue(node, "mask_file") || "") : "";
  const inputType = selectMode ? "radio" : "checkbox";
  const inputName = selectMode ? "mask-io-pick" : undefined;

  function fileInputs() {
    return [...listEl.querySelectorAll(`input[type="${inputType}"]`)];
  }

  function selectedNames() {
    return fileInputs().filter((el) => el.checked).map((el) => el.value);
  }

  function syncSelectAll() {
    if (!selectAllEl) return;
    const inputs = fileInputs();
    const n = inputs.filter((el) => el.checked).length;
    selectAllEl.indeterminate = n > 0 && n < inputs.length;
    selectAllEl.checked = inputs.length > 0 && n === inputs.length;
    selectAllChecked = selectAllEl.checked;
  }

  function syncButtons() {
    const n = selectedNames().length;
    if (btnDelete) btnDelete.disabled = n === 0;
    btnPrimary.disabled = n === 0;
    syncSelectAll();
  }

  function applySelection(filename) {
    setMaskFileOnNode(node, filename);
    toast(`Selected ${filename}`, "success");
    close();
  }

  function render() {
    listEl.replaceChildren();
    if (selectAllRow) selectAllRow.hidden = !files.length;
    if (!files.length) {
      selectAllChecked = false;
      const empty = document.createElement("div");
      empty.className = "mask-io-empty";
      empty.textContent = `No .pt files in output/${subfolder}`;
      listEl.appendChild(empty);
      syncButtons();
      return;
    }
    for (const f of files) {
      const label = document.createElement("label");
      label.className = "mask-io-row";
      const input = document.createElement("input");
      input.type = inputType;
      if (inputName) input.name = inputName;
      input.value = f.filename;
      if (selectMode && f.filename === current) input.checked = true;
      if (!selectMode && selectAllChecked) input.checked = true;
      input.addEventListener("change", syncButtons);
      if (selectMode) {
        label.addEventListener("dblclick", () => applySelection(f.filename));
      }
      const span = document.createElement("span");
      const name = document.createElement("div");
      name.textContent = f.filename;
      const meta = document.createElement("div");
      meta.className = "mask-io-meta";
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
      data = await fetchMaskList(subfolder);
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
        const result = await deleteMasks(subfolder, names);
        const n = (result.deleted || []).length;
        toast(`Deleted ${n} file(s)`, n ? "success" : "warn");
        data = await fetchMaskList(subfolder);
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
      openMaskPicker(this, mode);
    });
    return r;
  };
}

app.registerExtension({
  name: "mask_io.download",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name === "SaveMaskTensor") {
      addPickerButton(nodeType, { label: "download", mode: "manage" });
    } else if (nodeData.name === "LoadMaskTensor") {
      addPickerButton(nodeType, { label: "select", mode: "select" });
    }
  },
});
