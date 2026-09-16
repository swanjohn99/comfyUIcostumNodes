# Tools — image and video prep

Wrappers around still-image and clip preprocessing for image-to-video models (Wan, Kling, Runway, Veo, LTX).

| Wrapper | Use for |
|---------|---------|
| [`./tools/prep`](prep) | Stills: inspect, crop, resize, convert, fit to a model preset |
| [`./tools/video`](video) | Clips: probe, compress, extract frames, trim, concat |

Both scripts `cd` to the **repo root**, create `.venv` if missing, and run the matching Python file. Paths you pass are relative to the repo root (or absolute).

```bash
./tools/prep --help
./tools/video --help
```

Do **not** `cd tools/` first — the wrappers expect to live at `tools/prep` / `tools/video` and bootstrap from there.

## Setup

Same venv as generation (`./run.sh`). First run of either wrapper:

```bash
python3 -m venv .venv          # if you have not already
.venv/bin/pip install -r requirements.txt
```

| Extra | Needed for |
|-------|------------|
| `ffmpeg` + `ffprobe` on PATH | all `./tools/video` commands |
| `ultralytics` + `yolov8n.pt` in repo root | `./tools/prep … --smart` subject crop (optional; falls back to center) |

`pillow` and `numpy` are in `requirements.txt`. Uncomment `ultralytics` there if you want `--smart` without a pre-existing venv install.

## Layout

```
tools/
├── prep            # wrapper → prep.py
├── video           # wrapper → video.py
├── _env.sh         # venv + PYTHONPATH
├── prep.py
├── presets.py      # model input constraints
├── video.py
├── vtools/         # ffmpeg command builders
└── README.md
```

Outputs stay at the repo root (gitignored): `prepped/` for images. Video commands write wherever you pass `-o`.

---

# `./tools/prep` — stills

Every load: EXIF orientation applied, embedded ICC converted to sRGB. Every save: metadata stripped. Alpha is flattened (default `#ffffff`) when the output is JPEG or the preset forbids alpha.

Inputs: files, folders, globs, or `--from-manifest output/manifest.json` (generation or a previous prep run). Transforms write `--out` (default `prepped/`) plus `prepped/prep_manifest.json`. Existing dest files are **skipped** unless `--overwrite`. `--dry-run` computes sizes/bytes without writing.

### Shared flags (resize / crop / pad / convert / fit / pair)

| Flag | Default | Meaning |
|------|---------|---------|
| `--out DIR` | `prepped` | Output directory |
| `--suffix STR` | `""` | Appended to the output stem |
| `--overwrite` | off | Replace existing outputs |
| `--dry-run` | off | Compute only |
| `--workers N` | `1` | Process pool size |
| `--format jpg\|png\|webp` | keep / preset default | Output format |
| `--quality N` | `92` | JPEG/WebP quality |
| `--max-bytes N` | preset limit | Lower quality then shrink until under N bytes |
| `--flatten #hex` | `#ffffff` | Background when dropping alpha |

### Commands

```bash
./tools/prep presets
./tools/prep inspect output/
./tools/prep inspect output/ --json --no-blur
./tools/prep check --preset runway output/
./tools/prep fit --preset wan-720p output/ --smart
./tools/prep fit --preset kling img.png --long-edge 2048
./tools/prep batch --workers 4 fit --preset ltx-base --from-manifest output/manifest.json
./tools/prep resize --long-edge 1024 --snap 16 img.jpg
./tools/prep crop --aspect 9:16 --smart img.jpg
./tools/prep pad --aspect 16:9 --fill blur img.jpg
./tools/prep convert --format jpg --max-bytes 3300000 img.png
./tools/prep pair --preset runway start.png end.png
./tools/prep datauri prepped/01.jpg --preset runway --out uri.txt
./tools/prep sheet prepped/ --out sheet.jpg --cols 6
```

| Command | Purpose |
|---------|---------|
| `presets` | Print built-in model constraints |
| `inspect` | WxH, aspect, MB, mode, alpha, EXIF, ICC, Laplacian blur, per-preset pass/fail. `--json`, `--no-blur`. Pass `--preset X` to check one preset only. |
| `check` | Strict validation vs `--preset`. Exit 1 on any failure; prints a `./tools/prep fit …` fix line. |
| `fit` | One-shot: crop to allowed aspect → resize → snap → flatten → size cap. Main “make it valid” command. `--smart`, `--no-upscale` (area presets), `--long-edge` (kling). |
| `resize` | `--width` / `--height` / `--long-edge` / `--short-edge` / `--scale` / `--area WxH`. `--fit contain\|cover\|exact` when both width and height given. `--snap N` rounds dims down. `--no-upscale`. `--preset` fills size from the table. Lanczos. |
| `crop` | `--aspect W:H`, `--size WxH` (crop then resize), `--box x,y,w,h`. `--anchor center\|top\|bottom\|left\|right\|fx,fy`. `--smart` YOLO person box (union), else center. |
| `pad` | Letterbox to `--aspect` or `--size`. `--fill #rrggbb\|blur\|edge`. |
| `convert` | Format / quality / `--max-bytes` / `--flatten-always`. |
| `pair START END` | First + last frame forced to **identical** dims and format (Kling / Runway keyframes). Same crop/smart flags as `fit`. |
| `batch` | `batch [--workers N] [--from-manifest F] CMD …`. Default workers = half the CPUs when you omit `--workers` on the inner command. |
| `datauri PATH` | Print `data:…;base64,…`. Warns if over the preset byte limit (Runway: 5 MB encoded URI). `--out FILE` writes instead of stdout. |
| `sheet DIR` | Contact sheet. `--out sheet.jpg`, `--cols`, `--thumb`. |

### Presets (`presets.py`)

`--preset NAME` or `--preset-file custom.json` (JSON keys match the `Preset` fields).

| Preset | Rule |
|--------|------|
| `wan-480p` | Area bucket 832×480, dims %16, aspect follows input |
| `wan-720p` | Area 1280×720, dims %16 |
| `wan-5b-720p` | Area 1280×704, dims %32 (TI2V-5B) |
| `ltx-base` | Area 960×544, dims %32 |
| `ltx-720p` | Area 1280×704, dims %32 |
| `ltx-1080p` | Area 1920×1088, dims %32 |
| `kling` | Sides 300–8000, aspect 0.4–2.5, JPG/PNG, ≤10 MB, no alpha |
| `runway` | Exact 1280×720, 720×1280, 960×960, 1104×832, 832×1104, 1584×672; ≤3.3 MB binary for data URI |
| `veo3` | 1280×720 or 720×1280, JPG/PNG, ≤7 MB, no alpha |
| `veo3-1080p` | 1920×1080 or 1080×1920, same limits |

Wan I2V keeps source aspect and hits the area budget. Exact-size presets (Runway, Veo) pick the nearest allowed ratio, crop, then resize.

Custom JSON example:

```json
{
  "sizes": [[1024, 1024]],
  "formats": ["png"],
  "default_format": "png",
  "max_bytes": 500000
}
```

### Manifest

`prepped/prep_manifest.json` is a list of:

```json
{
  "src": "output/01.jpg",
  "dst": "prepped/01.jpg",
  "status": "ok",
  "before": {"width": 1536, "height": 2752, "format": "jpg", "bytes": 2620000},
  "after": {"width": 704, "height": 1280, "format": "jpg", "bytes": 224658},
  "ops": [{"op": "crop", "box": [11, 0, 1525, 2752], "aspect": 0.55}, {"op": "resize", "to": [704, 1280], "fit": "cover"}],
  "warnings": [],
  "error": null
}
```

`status`: `ok`, `skipped`, `dry-run`, `error`. Re-running `fit` on the same dest skips unless `--overwrite`.

---

# `./tools/video` — clips

ffmpeg wrappers. File **or** folder as input (folder → folder). Shared flags: `-y` / `--overwrite`, `--dry-run` (print argv, do not run), `--jobs N` (parallel ffmpeg for folder inputs). In-place overwrite of the source file is refused. Existing dest files are skipped unless `-y`.

Size names for `resize` / `from-images --size`: `480p` (854×480), `720p` (1280×720), `1080p` (1920×1080), `wan480` (832×480), `wan720` (1280×720), or `WxH`. `--ar` is `16:9`, `9:16`, `1:1`, `4:3`, `4:5`. `--fit crop` (default) or `pad`. `--codec h264|h265`, x264/x265 `--preset` (default `medium`).

```bash
./tools/video probe clip.mp4
./tools/video probe clips/ --json
./tools/video compress raw/ -o small/ --crf 28 --scale 1280:-2 --no-audio
./tools/video extract-frames clip.mp4 -o frames/ --fps 2
./tools/video extract-frames clip.mp4 -o i2v/ --first --last
./tools/video resize clip.mp4 -o out.mp4 --size wan720 --fit crop
./tools/video trim clip.mp4 -o out.mp4 --start 1.5 --frames 81
./tools/video from-images dataset/ -o out.mp4 --fps 16
./tools/video split-scenes clip.mp4 -o scenes/ --threshold 0.3
./tools/video audio clip.mp4 -o out.mp4 --strip
./tools/video audio clip.mp4 -o out.wav --extract
./tools/video concat a.mp4 b.mp4 -o joined.mp4
./tools/video concat clips/ -o joined.mp4
```

| Command | Purpose |
|---------|---------|
| `probe` | WxH, fps, duration, frames, codec, size. `--json`. |
| `compress` | CRF size-reduce (`libx264` / `libx265`). `--crf` (default 23), `--scale 1280:-2`, `--fps`, `--no-audio`. |
| `extract-frames` | PNG/JPEG sequence. `--fps`, `--every N`, `--first` / `--last` (I2V start/end), `--format png\|jpg`. |
| `resize` | Crop or pad to a named size or `WxH`. `--ar`, `--fit crop\|pad`. Default CRF 18. |
| `trim` | `--start` / `--duration` / `--end` and/or `--frames` (e.g. 81 for Wan: 4n+1). |
| `from-images` | Folder (or list) of images → mp4. Default `--fps 16`. Optional `--size` / `--ar`. Needs ≥2 images. |
| `split-scenes` | Cut at scene changes. `--threshold` (default 0.3). Writes into `-o` dir. |
| `audio` | `--strip` mute (copy video) or `--extract` wav/aac. |
| `concat` | Join clips. Stream-copy if codecs match, else re-encode. Folder input uses sorted listing. |

Needs `ffmpeg` and `ffprobe`. Missing binaries exit before any command runs.

---

# Typical pipelines

**Generated stills → Wan I2V first frames**

```bash
./run.sh                                          # output/01.jpg …
./tools/prep fit --preset wan-720p --smart output/
# → prepped/01.jpg …  (area ~1280×720, dims %16)
```

**Clip → Kling start/end pair**

```bash
./tools/video extract-frames clip.mp4 -o i2v/ --first --last
./tools/prep pair --preset kling i2v/clip_first.png i2v/clip_last.png -o prepped/
```

**Runway exact ratio + data URI**

```bash
./tools/prep fit --preset runway --smart output/01.jpg
./tools/prep check --preset runway prepped/01.jpg
./tools/prep datauri prepped/01.jpg --preset runway
```

**QC grid**

```bash
./tools/prep sheet prepped/ --out sheet.jpg --cols 6
```

**Compress a dump of raw clips, then trim to Wan frame count**

```bash
./tools/video compress raw/ -o small/ --crf 28 --scale 1280:-2 --jobs 4
./tools/video trim small/clip.mp4 -o wan/clip.mp4 --start 0 --frames 81
./tools/video resize wan/clip.mp4 -o wan/clip720.mp4 --size wan720 --fit crop
```

---

# Troubleshooting

| Problem | Fix |
|---------|-----|
| `python: command not found` | Wrappers use `python3` / `.venv/bin/python` |
| `ffmpeg` / `ffprobe` missing | Install ffmpeg; it must be on PATH |
| `unknown preset` | `./tools/prep presets` |
| `--smart` warning, center crop | Install ultralytics in `.venv`; keep `yolov8n.pt` at repo root |
| `refusing to overwrite source` | Pass `--out` or `--suffix` (prep) — never write onto the input |
| `in-place not supported` | Video dest must differ from source |
| Dest skipped | Pass `--overwrite` / `-y` |
| File still fails `check` after `fit` | Area presets allow ±15% area; exact-size presets must match a listed WxH |
| Import error `presets` / `vtools` | Use the wrappers, not a bare `python tools/prep.py` unless `PYTHONPATH=tools` |
