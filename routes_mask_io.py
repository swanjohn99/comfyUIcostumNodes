"""HTTP routes for listing/deleting saved mask .pt files."""
from __future__ import annotations

from pathlib import Path

from aiohttp import web

from .nodes_mask_io import (
    _clean_subfolder,
    _is_mask_filename,
    _iter_mask_paths,
    _masks_dir,
)

_ROUTES_REGISTERED = False


def _safe_mask_path(subfolder: str, filename: str) -> Path | None:
    """Resolve filename under masks dir; reject traversal / non-.pt."""
    if not filename or not isinstance(filename, str):
        return None
    # Reject any path separators in the provided name
    if "/" in filename or "\\" in filename:
        return None
    name = Path(filename).name
    if name != filename or not name.endswith(".pt") or name.startswith("."):
        return None
    root = _masks_dir(subfolder).resolve()
    path = (root / name).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


def register_routes() -> bool:
    global _ROUTES_REGISTERED
    if _ROUTES_REGISTERED:
        return True
    try:
        from server import PromptServer
    except ImportError:
        return False
    if getattr(PromptServer, "instance", None) is None:
        print("[mask_io] PromptServer.instance not ready; /mask_io routes deferred")
        return False

    routes = PromptServer.instance.routes

    @routes.get("/mask_io/list")
    async def mask_io_list(request):
        sub = _clean_subfolder(request.rel_url.query.get("subfolder", "masks"))
        d = _masks_dir(sub)
        files = []
        for p in sorted(d.glob("*.pt")):
            if not p.is_file():
                continue
            st = p.stat()
            files.append(
                {
                    "filename": p.name,
                    "subfolder": sub,
                    "type": "output",
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                }
            )
        return web.json_response({"subfolder": sub, "files": files})

    @routes.post("/mask_io/delete")
    async def mask_io_delete(request):
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)

        sub = _clean_subfolder(body.get("subfolder", "masks"))
        names = body.get("filenames") or body.get("files") or []
        if isinstance(names, str):
            names = [names]
        if not isinstance(names, list):
            return web.json_response({"error": "filenames must be a list"}, status=400)

        deleted = []
        missing = []
        errors = []
        for raw in names:
            path = _safe_mask_path(sub, str(raw))
            if path is None:
                errors.append(str(raw))
                continue
            if not path.is_file():
                missing.append(path.name)
                continue
            try:
                path.unlink()
                deleted.append(path.name)
            except OSError as e:
                errors.append(f"{path.name}: {e}")

        return web.json_response(
            {
                "subfolder": sub,
                "deleted": deleted,
                "missing": missing,
                "errors": errors,
            }
        )

    _ROUTES_REGISTERED = True
    print("[mask_io] Registered /mask_io/list and /mask_io/delete")
    return True


register_routes()
