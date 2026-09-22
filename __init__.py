from .nodes_bbox_io import LoadBoundingBoxes, SaveBoundingBoxes
from .nodes_h3_latent_io import LoadH3AVLatent, SaveH3AVLatent
from .nodes_io_basename import BuildMediaIoBasename
from .nodes_mask_io import LoadMaskTensor, SaveMaskTensor
from .routes_bbox_io import register_routes as register_bbox_routes
from .routes_folder_list import register_routes as register_folder_list_routes
from .routes_h3_latent_io import register_routes as register_h3_latent_routes
from .routes_mask_io import register_routes

register_routes()
register_h3_latent_routes()
register_bbox_routes()
register_folder_list_routes()

NODE_CLASS_MAPPINGS = {
    "SaveMaskTensor": SaveMaskTensor,
    "LoadMaskTensor": LoadMaskTensor,
    "SaveH3AVLatent": SaveH3AVLatent,
    "LoadH3AVLatent": LoadH3AVLatent,
    "SaveBoundingBoxes": SaveBoundingBoxes,
    "LoadBoundingBoxes": LoadBoundingBoxes,
    "BuildMediaIoBasename": BuildMediaIoBasename,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "SaveMaskTensor": "Save Mask Tensor",
    "LoadMaskTensor": "Load Mask Tensor",
    "SaveH3AVLatent": "Save MiniMax H3 AV Latent",
    "LoadH3AVLatent": "Load MiniMax H3 AV Latent",
    "SaveBoundingBoxes": "Save Bounding Boxes",
    "LoadBoundingBoxes": "Load Bounding Boxes",
    "BuildMediaIoBasename": "Build Media IO Basename",
}

WEB_DIRECTORY = "./web"

__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
]
