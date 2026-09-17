from .nodes_mask_io import LoadMaskTensor, SaveMaskTensor
from .routes_mask_io import register_routes

register_routes()

NODE_CLASS_MAPPINGS = {
    "SaveMaskTensor": SaveMaskTensor,
    "LoadMaskTensor": LoadMaskTensor,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "SaveMaskTensor": "Save Mask Tensor",
    "LoadMaskTensor": "Load Mask Tensor",
}

WEB_DIRECTORY = "./web"

__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
]
