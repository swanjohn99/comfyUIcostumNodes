from .nodes_mask_io import LoadMaskTensor, SaveMaskTensor

NODE_CLASS_MAPPINGS = {
    "SaveMaskTensor": SaveMaskTensor,
    "LoadMaskTensor": LoadMaskTensor,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "SaveMaskTensor": "Save Mask Tensor",
    "LoadMaskTensor": "Load Mask Tensor",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
