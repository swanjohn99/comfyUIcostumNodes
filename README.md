# ComfyUI Mask Tensor I/O

Save and load ComfyUI `MASK` tensors as `.pt` files (exact float values).

Nodes (category `mask_io`):

- **Save Mask Tensor** - write `MASK` to `output/masks/{prefix}_{#####}.pt`, pass-through
- **Load Mask Tensor** - read a `.pt` back as `MASK` `[B,H,W]`

## Install

```bash
cd ComfyUI/custom_nodes
git clone <this-repo-url>
```

Restart ComfyUI. No pip install; uses Comfy's `torch` and `folder_paths`.

## Usage

1. Wire `SAM3_TrackToMask` `masks` into **Save Mask Tensor**, then into your mask consumers.
2. Later: **Load Mask Tensor** instead of re-running SAM3.
