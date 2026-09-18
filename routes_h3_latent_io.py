"""HTTP routes for listing/deleting saved H3 AV latent .h3latent files."""
from __future__ import annotations

from pathlib import Path

from aiohttp import web

from .nodes_h3_latent_io import DEFAULT_SUBFOLDER, EXTENSION, _clean_subfolder, _latents_dir

_ROUTES_REGISTERED = False


def _safe_h3_path(subfolder: str, filename: str) -> Path | None:
    """Resolve filename under latents dir; reject traversal / non-.h3latent."""
    if not filename or not isinstance(filename, str):
        return None
    if "/" in filename or "\\" in filename:
        return None
    name = Path(filename).name
    if name != filename or not name.endswith(EXTENSION) or name.startswith("."):
        return None
    root = _latents_dir(subfolder).resolve()
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
        print(
            "[h3_latent_io] PromptServer.instance not ready; /h3_latent_io routes deferred"
        )
        return False

    routes = PromptServer.instance.routes

    @routes.get("/h3_latent_io/list")
    async def h3_latent_io_list(request):
        sub = _clean_subfolder(request.rel_url.query.get("subfolder", DEFAULT_SUBFOLDER))
        d = _latents_dir(sub)
        files = []
        for p in sorted(d.glob(f"*{EXTENSION}")):
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

    @routes.post("/h3_latent_io/delete")
    async def h3_latent_io_delete(request):
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
            path = _safe_h3_path(sub, str(raw))
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
    print("[h3_latent_io] Registered /h3_latent_io/list and /h3_latent_io/delete")
    return True


register_routes()
