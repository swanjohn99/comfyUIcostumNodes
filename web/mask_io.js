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

function warn(detail) {
  const toast = app.extensionManager?.toast;
  if (toast?.add) {
    toast.add({
      severity: "warn",
      summary: "mask_io",
      detail,
      life: 3000,
    });
  } else {
    console.warn(`[mask_io] ${detail}`);
  }
}

function downloadFile(info) {
  if (!info?.filename) {
    warn("No file to download. Run Save first (or pick a file on Load).");
    return;
  }
  const a = document.createElement("a");
  a.href = viewUrl(info);
  a.download = info.filename;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function widgetValue(node, name) {
  const w = node.widgets?.find((x) => x.name === name);
  return w?.value;
}

app.registerExtension({
  name: "mask_io.download",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name === "SaveMaskTensor") {
      const onNodeCreated = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function () {
        const r = onNodeCreated?.apply(this, arguments);
        this._mask_io_last = null;
        this.addWidget("button", "download", "download", () => {
          downloadFile(this._mask_io_last);
        });
        return r;
      };

      const onExecuted = nodeType.prototype.onExecuted;
      nodeType.prototype.onExecuted = function (message) {
        onExecuted?.apply(this, arguments);
        const files = message?.mask_files;
        this._mask_io_last = Array.isArray(files) ? files[0] : null;
      };
    }

    if (nodeData.name === "LoadMaskTensor") {
      const onNodeCreated = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function () {
        const r = onNodeCreated?.apply(this, arguments);
        this.addWidget("button", "download", "download", () => {
          const filename = widgetValue(this, "mask_file");
          const subfolder = widgetValue(this, "subfolder") || "masks";
          if (!filename || filename === "(none)") {
            downloadFile(null);
            return;
          }
          downloadFile({
            filename,
            subfolder: String(subfolder).replace(/^[/\\]+|[/\\]+$/g, "") || "masks",
            type: "output",
          });
        });
        return r;
      };
    }
  },
});
