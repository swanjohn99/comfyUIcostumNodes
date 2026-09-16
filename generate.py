#!/usr/bin/env python3
"""Batch image generation via OpenRouter Nano Banana Pro."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import mimetypes
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx
from dotenv import load_dotenv

from export_dataset import export_dataset

API_URL = "https://openrouter.ai/api/v1/images"
DEFAULT_MODEL = "google/gemini-3-pro-image"
DEFAULT_ASPECT_RATIO = "9:16"
RETRY_STATUSES = {429, 502}
MAX_RETRIES = 5
EXT_BY_MEDIA = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
}
MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


@dataclass
class PromptJob:
    index: int
    shot_id: str
    title: str
    prompt: str
    caption: str
    trigger: str
    aspect_ratio: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send one reference image + N prompts to OpenRouter Nano Banana Pro."
    )
    parser.add_argument("--image", default="input/reference.png", type=Path)
    parser.add_argument("--prompts", default="input/prompts.json", type=Path)
    parser.add_argument("--out", default="output", type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--resolution", default="2K")
    parser.add_argument(
        "--aspect-ratio",
        default=DEFAULT_ASPECT_RATIO,
        help="Fallback aspect ratio when a shot does not set aspect_ratio",
    )
    parser.add_argument("--concurrency", default=3, type=int)
    parser.add_argument(
        "--no-export-dataset",
        dest="export_dataset",
        action="store_false",
        help="Skip exporting images + caption .txt files to --dataset-dir",
    )
    parser.set_defaults(export_dataset=True)
    parser.add_argument("--dataset-dir", default="dataset", type=Path)
    parser.add_argument("--trigger", default="", help="Override trigger token from prompts.json")
    return parser.parse_args()


def compose_prompt(lock: str, shot: str, rules: str) -> str:
    parts = [part.strip() for part in (shot, lock, rules) if part and part.strip()]
    return "\n\n".join(parts)


def load_prompt_jobs(path: Path, trigger_override: str) -> list[PromptJob]:
    if not path.is_file():
        raise SystemExit(f"prompts file not found: {path}")

    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        lock = data.get("lock", "")
        rules = data.get("rules", "")
        trigger = (trigger_override or data.get("trigger") or "").strip()
        shots = data.get("shots") or []
        if not shots:
            raise SystemExit(f"no shots in {path}")
        jobs: list[PromptJob] = []
        for i, shot in enumerate(shots, start=1):
            if isinstance(shot, str):
                shot_id = f"{i:02d}"
                title = f"shot-{shot_id}"
                shot_text = shot
                caption = shot
                aspect_ratio = ""
            else:
                shot_id = str(shot.get("id") or f"{i:02d}")
                title = str(shot.get("title") or shot_id)
                shot_text = shot.get("shot") or shot.get("prompt") or ""
                caption = shot.get("caption") or ""
                aspect_ratio = str(shot.get("aspect_ratio") or "").strip()
            if not shot_text.strip():
                raise SystemExit(f"empty shot text for index {i} in {path}")
            if not caption.strip():
                raise SystemExit(f"empty caption for shot {shot_id} in {path}")
            jobs.append(
                PromptJob(
                    index=i,
                    shot_id=shot_id,
                    title=title,
                    prompt=compose_prompt(lock, shot_text, rules),
                    caption=caption.strip(),
                    trigger=trigger,
                    aspect_ratio=aspect_ratio,
                )
            )
        return jobs

    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    prompts = [line for line in lines if line]
    if not prompts:
        raise SystemExit(f"no prompts in {path}")
    trigger = trigger_override.strip()
    return [
        PromptJob(
            index=i,
            shot_id=f"{i:02d}",
            title=f"shot-{i:02d}",
            prompt=prompt,
            caption=prompt,
            trigger=trigger,
        )
        for i, prompt in enumerate(prompts, start=1)
    ]


def image_to_data_url(path: Path) -> str:
    if not path.is_file():
        raise SystemExit(f"reference image not found: {path}")
    mime, _ = mimetypes.guess_type(path.name)
    if mime not in MIME_BY_SUFFIX.values():
        mime = MIME_BY_SUFFIX.get(path.suffix.lower(), "image/png")
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def index_width(count: int) -> int:
    return max(2, len(str(count)))


def stem_for(index: int, width: int) -> str:
    return f"{index:0{width}d}"


def existing_output(out_dir: Path, stem: str) -> Path | None:
    matches = sorted(out_dir.glob(f"{stem}.*"))
    for path in matches:
        if path.suffix.lower() in MIME_BY_SUFFIX or path.suffix.lower() == ".svg":
            return path
    return None


def media_ext(media_type: str | None) -> str:
    if not media_type:
        return ".png"
    return EXT_BY_MEDIA.get(media_type.lower(), ".png")


def load_manifest(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def write_manifest(path: Path, entries: list[dict]) -> None:
    path.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def upsert_manifest(entries: list[dict], entry: dict) -> None:
    idx = entry["index"]
    for i, existing in enumerate(entries):
        if existing.get("index") == idx:
            entries[i] = entry
            return
    entries.append(entry)
    entries.sort(key=lambda item: item.get("index", 0))


def manifest_entry(job: PromptJob, **extra) -> dict:
    return {
        "index": job.index,
        "shot_id": job.shot_id,
        "title": job.title,
        "caption": job.caption,
        "trigger": job.trigger,
        "prompt": job.prompt,
        "aspect_ratio": extra.pop("aspect_ratio", job.aspect_ratio),
        **extra,
    }


async def generate_one(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    model: str,
    prompt: str,
    data_url: str,
    resolution: str,
    aspect_ratio: str,
) -> dict:
    payload = {
        "model": model,
        "prompt": prompt,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "input_references": [
            {
                "type": "image_url",
                "image_url": {"url": data_url},
            }
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    delay = 2.0
    last_error = "unknown error"
    for attempt in range(MAX_RETRIES):
        try:
            response = await client.post(API_URL, headers=headers, json=payload)
        except httpx.RequestError as exc:
            last_error = str(exc)
            if attempt == MAX_RETRIES - 1:
                break
            await asyncio.sleep(delay)
            delay *= 2
            continue

        if response.status_code in RETRY_STATUSES:
            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            retry_after = response.headers.get("Retry-After")
            wait = delay
            if retry_after:
                try:
                    wait = max(delay, float(retry_after))
                except ValueError:
                    pass
            if attempt == MAX_RETRIES - 1:
                break
            await asyncio.sleep(wait)
            delay *= 2
            continue

        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:500]}")

        body = response.json()
        images = body.get("data") or []
        if not images or not images[0].get("b64_json"):
            raise RuntimeError(f"no image in response: {json.dumps(body)[:500]}")
        return {
            "b64_json": images[0]["b64_json"],
            "media_type": images[0].get("media_type") or "image/png",
            "usage": body.get("usage") or {},
        }

    raise RuntimeError(last_error)


async def run_job(
    sem: asyncio.Semaphore,
    client: httpx.AsyncClient,
    *,
    api_key: str,
    model: str,
    job: PromptJob,
    width: int,
    data_url: str,
    resolution: str,
    aspect_ratio: str,
    out_dir: Path,
    manifest_path: Path,
    manifest: list[dict],
    manifest_lock: asyncio.Lock,
) -> dict:
    stem = stem_for(job.index, width)
    shot_aspect = job.aspect_ratio or aspect_ratio
    existing = existing_output(out_dir, stem)
    if existing:
        entry = manifest_entry(
            job,
            file=existing.name,
            status="skipped",
            cost=None,
            error=None,
            aspect_ratio=shot_aspect,
        )
        async with manifest_lock:
            upsert_manifest(manifest, entry)
            write_manifest(manifest_path, manifest)
        print(f"{stem} skip {existing.name}")
        return entry

    async with sem:
        print(f"{stem} generating {job.shot_id} {job.title}...")
        try:
            result = await generate_one(
                client,
                api_key=api_key,
                model=model,
                prompt=job.prompt,
                data_url=data_url,
                resolution=resolution,
                aspect_ratio=shot_aspect,
            )
            ext = media_ext(result["media_type"])
            dest = out_dir / f"{stem}{ext}"
            dest.write_bytes(base64.b64decode(result["b64_json"]))
            usage = result["usage"]
            entry = manifest_entry(
                job,
                file=dest.name,
                status="ok",
                cost=usage.get("cost"),
                error=None,
                aspect_ratio=shot_aspect,
            )
            print(f"{stem} wrote {dest.name} cost={entry['cost']}")
        except Exception as exc:
            entry = manifest_entry(
                job,
                file=None,
                status="error",
                cost=None,
                error=str(exc),
                aspect_ratio=shot_aspect,
            )
            print(f"{stem} error: {exc}", file=sys.stderr)

    async with manifest_lock:
        upsert_manifest(manifest, entry)
        write_manifest(manifest_path, manifest)
    return entry


async def main_async(args: argparse.Namespace) -> int:
    load_dotenv()
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY missing (set in .env)")

    jobs = load_prompt_jobs(args.prompts, args.trigger)
    data_url = image_to_data_url(args.image)
    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest = load_manifest(manifest_path)
    width = index_width(len(jobs))
    concurrency = max(1, args.concurrency)

    timeout = httpx.Timeout(300.0, connect=30.0)
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()

    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        tasks = [
            run_job(
                sem,
                client,
                api_key=api_key,
                model=args.model,
                job=job,
                width=width,
                data_url=data_url,
                resolution=args.resolution,
                aspect_ratio=args.aspect_ratio,
                out_dir=out_dir,
                manifest_path=manifest_path,
                manifest=manifest,
                manifest_lock=lock,
            )
            for job in jobs
        ]
        entries = await asyncio.gather(*tasks)

    ok = sum(1 for e in entries if e["status"] == "ok")
    skipped = sum(1 for e in entries if e["status"] == "skipped")
    errors = sum(1 for e in entries if e["status"] == "error")
    print(f"done ok={ok} skipped={skipped} error={errors} manifest={manifest_path}")

    if args.export_dataset:
        exported = export_dataset(manifest, out_dir, args.dataset_dir, args.trigger)
        print(f"dataset exported={exported} dir={args.dataset_dir}")

    return 1 if errors else 0


def main() -> None:
    args = parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
