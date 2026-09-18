# ComfyUI Mask Tensor I/O

Save and load ComfyUI `MASK` tensors as `.cmask` files (exact float values).

Nodes (category `mask_io`):

- **Save Mask Tensor** - write `MASK` to `output/masks/{prefix}_{#####}.cmask`, pass-through
- **Load Mask Tensor** - read a `.cmask` (or legacy `.pt`) back as `MASK` `[B,H,W]`
- **download** (both nodes) - opens a picker of `output/{subfolder}/*.cmask`; download and/or delete selected files (survives ComfyUI restart)

Legacy `.pt` mask files still load, but ComfyUI may false-flag them as missing models. Re-save with **Save Mask Tensor** and switch the workflow to the new `.cmask` file.

## Install

```bash
cd ComfyUI/custom_nodes
git clone <this-repo-url>
```

Restart ComfyUI. No pip install; uses Comfy's `torch` and `folder_paths`.

### Comfy Registry / Manager

1. Create a publisher + API key at https://registry.comfy.org (set `PublisherId` in `pyproject.toml` if different from `swanjohn99`).
2. Publish: `comfy node publish`
3. Install from ComfyUI Manager, or search the registry.

## Usage

1. Wire `SAM3_TrackToMask` `masks` into **Save Mask Tensor**, then into your mask consumers.
2. Later: **Load Mask Tensor** instead of re-running SAM3.
