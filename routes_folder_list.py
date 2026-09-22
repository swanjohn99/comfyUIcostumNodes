"""GET /folder/list?folder=&dir=&ext= lists files in a Comfy folder."""
from __future__ import annotations

import os

from aiohttp import web

_ROUTES_REGISTERED = False


def _rel_parts(sub: str) -> list[str]:
    text = (sub or "").replace("\\", "/").strip("/")
    parts = [p for p in text.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise ValueError("bad dir")
    return parts


def _ext_set(raw: str | None) -> set[str] | None:
    if raw is None or not str(raw).strip():
        return None
    found: set[str] = set()
    for part in str(raw).split(","):
        text = part.strip().lower()
        if not text:
            continue
        if not text.startswith("."):
            text = "." + text
        found.add(text)
    return found or None


def _bases(folder: str) -> list[str]:
    import folder_paths

    key = (folder or "").strip()
    if not key or any(c in key for c in "/\\") or key in {".", ".."}:
        raise ValueError("bad folder")
    special = {
        "input": folder_paths.get_input_directory,
        "output": folder_paths.get_output_directory,
        "temp": folder_paths.get_temp_directory,
    }
    if key in special:
        return [special[key]()]
    try:
        paths = folder_paths.get_folder_paths(key)
    except KeyError as exc:
        raise ValueError("bad folder") from exc
    if not paths:
        raise ValueError("bad folder")
    return list(paths)


def list_names(folder: str, rel: str, exts: set[str] | None) -> list[str]:
    parts = _rel_parts(rel)
    names: set[str] = set()
    found_dir = False
    for base in _bases(folder):
        base_real = os.path.realpath(base)
        root = os.path.realpath(os.path.join(base_real, *parts)) if parts else base_real
        if root != base_real and not root.startswith(base_real + os.sep):
            raise ValueError("bad dir")
        if not os.path.isdir(root):
            continue
        found_dir = True
        for entry in os.scandir(root):
            if not entry.is_file() or entry.name.startswith("."):
                continue
            if exts is not None and os.path.splitext(entry.name)[1].lower() not in exts:
                continue
            names.add(entry.name)
    if not found_dir:
        raise FileNotFoundError("not a directory")
    return sorted(names)


def register_routes() -> bool:
    global _ROUTES_REGISTERED
    if _ROUTES_REGISTERED:
        return True
    try:
        from server import PromptServer
    except ImportError:
        return False
    if getattr(PromptServer, "instance", None) is None:
        print("[folder_list] PromptServer.instance not ready; /folder/list deferred")
        return False

    routes = PromptServer.instance.routes

    @routes.get("/folder/list")
    async def folder_list(request):
        folder = request.rel_url.query.get("folder", "")
        rel = request.rel_url.query.get("dir", "")
        exts = _ext_set(request.rel_url.query.get("ext"))
        try:
            names = list_names(folder, rel, exts)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except FileNotFoundError as exc:
            return web.json_response({"error": str(exc)}, status=404)
        return web.json_response(names)

    _ROUTES_REGISTERED = True
    print("[folder_list] Registered /folder/list")
    return True


register_routes()
