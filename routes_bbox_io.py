"""HTTP routes for listing/deleting saved bounding box .cbbox files."""
from __future__ import annotations

from pathlib import Path

from aiohttp import web

from .nodes_bbox_io import (
    BBOX_EXTENSION,
    DEFAULT_SUBFOLDER,
    _bboxes_dir,
    _clean_subfolder,
    _is_bbox_filename,
    _iter_bbox_paths,
)

_ROUTES_REGISTERED = False


def _safe_bbox_path(subfolder: str, filename: str) -> Path | None:
    """Resolve filename under bboxes dir; reject traversal / non-.cbbox."""
    if not filename or not isinstance(filename, str):
        return None
    if "/" in filename or "\\" in filename:
        return None
    name = Path(filename).name
    if name != filename or not _is_bbox_filename(name) or name.startswith("."):
        return None
    if name.lower().endswith(".pt"):
        return None
    root = _bboxes_dir(subfolder).resolve()
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
        print("[bbox_io] PromptServer.instance not ready; /bbox_io routes deferred")
        return False

    routes = PromptServer.instance.routes

    @routes.get("/bbox_io/list")
    async def bbox_io_list(request):
        sub = _clean_subfolder(request.rel_url.query.get("subfolder", DEFAULT_SUBFOLDER))
        d = _bboxes_dir(sub)
        files = []
        for p in sorted(_iter_bbox_paths(d), key=lambda path: path.name):
            if not p.is_file():
                continue
            if not _is_bbox_filename(p.name):
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

    @routes.get("/bbox_io/file")
    async def bbox_io_file(request):
        sub = _clean_subfolder(request.rel_url.query.get("subfolder", DEFAULT_SUBFOLDER))
        name = request.rel_url.query.get("filename", "")
        path = _safe_bbox_path(sub, name)
        if path is None or not path.is_file():
            return web.json_response({"error": "not found"}, status=404)
        return web.FileResponse(
            path,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Content-Disposition": f'attachment; filename="{path.name}"',
            },
        )

    @routes.post("/bbox_io/delete")
    async def bbox_io_delete(request):
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)

        sub = _clean_subfolder(body.get("subfolder", DEFAULT_SUBFOLDER))
        names = body.get("filenames") or body.get("files") or []
        if isinstance(names, str):
            names = [names]
        if not isinstance(names, list):
            return web.json_response({"error": "filenames must be a list"}, status=400)

        deleted = []
        missing = []
        errors = []
        for raw in names:
            path = _safe_bbox_path(sub, str(raw))
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
    print("[bbox_io] Registered /bbox_io/list, /bbox_io/file, and /bbox_io/delete")
    return True


register_routes()
