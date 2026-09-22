#!/usr/bin/env bash
# Model and node lines run after this script, from /workspace/pod-actions.sh.
# 4090.txt files:
#   sam3.1_multiplex_fp16.safetensors
#     SAM3.1 multiplex - video track / geometry masks
#   minimax_h3_ref2va_pruned_int8_convrot.safetensors
#     H3 ref2va DiT official int8 ~21GB - Workflow-2.json
#   minimax_h3_ref2va_pruned-w4a8_convrot_pruned.safetensors
#     H3 ref2va DiT community W4A8 ~12.5GB - Workflow-2_low_vram.json
#     needs recent Comfy (asym_w4a8_int8)
#   minimax_h3_audio_vae_fp32.safetensors
#     H3 audio VAE
#   minimax_h3_video_vae_fp16.safetensors
#     H3 video VAE (~5GB)
#   qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
#     H3 text encoder Qwen3-VL 32B NVFP4
#   minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors
#     LightX2V turbo LoRA 4-step ref2v
#   minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors
#     Kijai turbo LoRA 4-step fl2v (Workflow-2)
#   birefnet.safetensors
#     BiRefNet background removal
set -euo pipefail
mkdir -p /workspace

LOG=/workspace/startup.log
: > "$LOG"
if [[ -n "${BASH_VERSION:-}" ]] && [[ -t 1 ]]; then
  exec > >(tee -a "$LOG") 2>&1
else
  exec >>"$LOG" 2>&1
fi
printf '%s\n' '#!/bin/sh' 'exec tail -F /workspace/startup.log' > /workspace/watch-startup
chmod +x /workspace/watch-startup

log() { echo "[$(date -u +%H:%M:%S)] [startup] $*"; }
log "begin pid=$$ shell=${SHELL:-?} bash=${BASH_VERSION:-no}"
log "watch: /workspace/watch-startup   or   tail -F $LOG"

comfy_root() {
  local c
  for c in /workspace/runpod-slim/ComfyUI /workspace/ComfyUI /ComfyUI; do
    if [[ -d "$c/custom_nodes" ]]; then
      printf '%s\n' "$c"
      return 0
    fi
  done
  return 1
}

expect_comfy() {
  [[ -d /workspace/runpod-slim ]] || [[ -d /workspace/ComfyUI ]] || [[ -d /ComfyUI ]] ||
    grep -qi comfy /start.sh 2>/dev/null
}

COMFY="$(comfy_root || true)"
if [[ -z "${COMFY:-}" ]]; then
  if expect_comfy; then
    WAIT_N=180
    log "waiting for ComfyUI (up to $((WAIT_N * 2))s)"
  else
    WAIT_N=15
    log "no ComfyUI signal, brief wait $((WAIT_N * 2))s"
  fi
  for _ in $(seq 1 "$WAIT_N"); do
    COMFY="$(comfy_root || true)"
    [[ -n "${COMFY:-}" ]] && break
    sleep 2
  done
fi

if [[ -n "${COMFY:-}" ]]; then
  NODES="$COMFY/custom_nodes"
  log "comfy $COMFY"
else
  COMFY=/workspace
  NODES=/workspace/custom_nodes
  log "no comfy — models/nodes under /workspace"
fi
mkdir -p "$NODES" "$COMFY/models" "$COMFY/input" "$COMFY/output"
cd "$NODES"

# /folder/list comes from comfyUIcostumNodes (clone below). Restart Comfy after pull.
MODELS="$COMFY/models"
INPUT="$COMFY/input"
OUTPUT="$COMFY/output"
B2_INPUT=":b2:runpodFiles-hudson/input"
B2_OUTPUT=":b2:runpodFiles-hudson/output"
DROPBOX_INPUT=":dropbox:runpodFiles-hudson/input"
DROPBOX_OUTPUT=":dropbox:runpodFiles-hudson/output"
DROPBOX_SYNC_PID=/workspace/dropbox-sync.pid
DROPBOX_READY=

cat > /workspace/pod.env <<EOF
COMFY=$COMFY
NODES=$NODES
MODELS=$MODELS
INPUT=$INPUT
OUTPUT=$OUTPUT
B2_INPUT=$B2_INPUT
B2_OUTPUT=$B2_OUTPUT
DROPBOX_INPUT=$DROPBOX_INPUT
DROPBOX_OUTPUT=$DROPBOX_OUTPUT
LOG=$LOG
EOF

cat > /workspace/savelog <<'EOF'
#!/bin/sh
set -eu
. /workspace/pod.env
stamp=$(date -u +%Y%m%dT%H%M%SZ)
dir=$OUTPUT/logs/$stamp
mkdir -p "$dir"
cp -a /workspace/startup.log "$dir/" 2>/dev/null || true
cp -a /workspace/startup.errors "$dir/" 2>/dev/null || true
if [ -d "$COMFY/user" ]; then
  find "$COMFY/user" -type f \( -name '*.log' -o -name '*.txt' \) -exec cp -a {} "$dir/" \; 2>/dev/null || true
fi
command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi > "$dir/nvidia-smi.txt" 2>&1 || true
echo "$dir"
if command -v rclone >/dev/null 2>&1; then
  rclone copy "$dir" "$DROPBOX_OUTPUT/logs/$stamp" --fast-list --transfers 8 \
    --stats-one-line --stats 5s --stats-log-level NOTICE -v
else
  echo "rclone missing; local copy only (dropbox loop will push $OUTPUT)" >&2
fi
EOF
chmod +x /workspace/savelog

cat > /workspace/dropbox-push <<'EOF'
#!/bin/sh
set -eu
. /workspace/pod.env

usage() {
  cat <<EOH
usage: /workspace/dropbox-push [-h]

Copy pod output to Dropbox now. Does not delete local files.

  src  $OUTPUT
  dst  $DROPBOX_OUTPUT

Background loop also pushes every 60s (often 0 new files).
rclone "config not found" NOTICE is expected (env token, no rclone.conf).
EOH
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  "") ;;
  *) echo "unknown arg: $1" >&2; usage >&2; exit 2 ;;
esac

if ! command -v rclone >/dev/null 2>&1; then
  echo "[dropbox-push] rclone missing" >&2
  exit 1
fi

n=$(find "$OUTPUT" -type f | wc -l)
echo "[dropbox-push] $OUTPUT -> $DROPBOX_OUTPUT  local_files=$n"
rclone copy "$OUTPUT" "$DROPBOX_OUTPUT" \
  --fast-list --transfers 8 --checkers 16 \
  --stats-one-line --stats 5s --stats-log-level NOTICE -v
echo "[dropbox-push] done"
EOF
chmod +x /workspace/dropbox-push

cat > /workspace/b2-push <<'EOF'
#!/bin/sh
set -eu
. /workspace/pod.env

usage() {
  cat <<EOH
usage: /workspace/b2-push [-h]

Copy pod output to B2 now. Does not delete local files.

  src  $OUTPUT
  dst  $B2_OUTPUT

Startup sync uses Dropbox. This is a one-shot B2 push.
rclone "config not found" NOTICE is expected (env keys, no rclone.conf).
EOH
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  "") ;;
  *) echo "unknown arg: $1" >&2; usage >&2; exit 2 ;;
esac

if ! command -v rclone >/dev/null 2>&1; then
  echo "[b2-push] rclone missing" >&2
  exit 1
fi

n=$(find "$OUTPUT" -type f | wc -l)
echo "[b2-push] $OUTPUT -> $B2_OUTPUT  local_files=$n"
rclone copy "$OUTPUT" "$B2_OUTPUT" \
  --fast-list --transfers 8 --checkers 16 \
  --stats-one-line --stats 5s --stats-log-level NOTICE -v
echo "[b2-push] done"
EOF
chmod +x /workspace/b2-push

cat > /workspace/help <<'EOF'
#!/bin/sh
cat <<'EOH'
/workspace/watch-startup   tail -F startup.log
/workspace/savelog         copy logs -> output/logs/<utc> + rclone to Dropbox
/workspace/dropbox-push    rclone output -> Dropbox now
/workspace/dropbox-push -h usage
/workspace/b2-push         rclone output -> B2 now
/workspace/b2-push -h      usage
/workspace/help            this list
bash /workspace/startup.sh re-run this setup script
bash /workspace/pod-actions.sh   re-run kick and clone lines
kick DIR URL           download one model
clone URL              clone or pull a custom node
clone_pin URL REF      clone and checkout REF
EOH
EOF
chmod +x /workspace/help

log "cmds: /workspace/help  savelog  dropbox-push  b2-push  watch-startup  kick  clone  clone_pin"

ensure_rclone() {
  if command -v rclone >/dev/null 2>&1; then
    log "rclone $(rclone version 2>/dev/null | head -n1)"
    return 0
  fi
  log "install rclone"
  if curl -fsSL https://rclone.org/install.sh | bash; then
    log "rclone installed"
    return 0
  fi
  log "FAIL rclone install" | tee -a /workspace/startup.errors
  return 1
}

dropbox_copy() {
  local src="$1" dst="$2" label="$3"
  shift 3
  log "dropbox $label"
  rclone copy "$src" "$dst" --fast-list --transfers 8 --checkers 16 \
    --stats-one-line --stats 5s --stats-log-level NOTICE "$@" || {
    log "FAIL dropbox $label" | tee -a /workspace/startup.errors
    return 0
  }
}

dropbox_pull_input() { dropbox_copy "$DROPBOX_INPUT" "$INPUT" "input pull"; }
dropbox_push_output() { dropbox_copy "$OUTPUT" "$DROPBOX_OUTPUT" "output push" --min-age 15s; }

dropbox_loop() {
  while true; do
    sleep 60
    dropbox_pull_input
    dropbox_push_output
  done
}

if [[ -z "${RCLONE_DROPBOX_TOKEN:-}" ]]; then
  log "FAIL dropbox token empty, skip rclone" | tee -a /workspace/startup.errors
elif ensure_rclone; then
  DROPBOX_READY=1
fi

fetch() {
  local dir="$1" url="$2"
  local name dest part
  name="$(basename "${url%%\?*}")"
  dest="$MODELS/$dir/$name"
  part="$dest.part"
  mkdir -p "$MODELS/$dir"
  if [[ -s "$dest" ]]; then
    log "skip $dir/$name"
    return 0
  fi
  log "get $dir/$name"
  if command -v aria2c >/dev/null 2>&1; then
    aria2c -x 8 -s 8 -c --summary-interval=10 --console-log-level=notice \
      --allow-overwrite=true --auto-file-renaming=false \
      -d "$MODELS/$dir" -o "$name" "$url" || {
      log "FAIL download $dir/$name" | tee -a /workspace/startup.errors
      return 0
    }
    log "ok $dir/$name"
    return 0
  fi
  if command -v wget >/dev/null 2>&1; then
    wget --progress=dot:giga -c -O "$part" "$url" && mv -f "$part" "$dest"
  else
    curl -fL --retry 3 -C - --progress-bar -o "$part" "$url" && mv -f "$part" "$dest"
  fi || {
    log "FAIL download $dir/$name" | tee -a /workspace/startup.errors
    return 0
  }
  log "ok $dir/$name"
}

kick() {
  if [[ -n "${BASH_VERSION:-}" ]]; then
    fetch "$1" "$2" &
  else
    fetch "$1" "$2"
  fi
}

if [[ -n "${DROPBOX_READY:-}" ]]; then
  if [[ -n "${BASH_VERSION:-}" ]]; then
    dropbox_pull_input &
  else
    dropbox_pull_input
  fi
fi

if [[ -n "${BASH_VERSION:-}" ]]; then
  log "parallel downloads"
else
  log "sequential downloads"
fi
# @kick

clone_pin() {
  local url="$1"
  local ref="$2"
  local name
  name="$(basename "${url%/}" .git)"
  log "$name @ $ref"
  if [[ -d "$name/.git" ]]; then
    git -C "$name" fetch --tags --prune || {
      log "FAIL fetch $name" | tee -a /workspace/startup.errors
      return 0
    }
  else
    git clone "$url" "$name" || {
      log "FAIL clone $name" | tee -a /workspace/startup.errors
      return 0
    }
  fi
  git -C "$name" checkout "$ref" || {
    log "FAIL checkout $name $ref (left on default branch)" | tee -a /workspace/startup.errors
    return 0
  }
}

clone() {
  local url="$1"
  local name
  name="$(basename "${url%/}" .git)"
  if [[ -d "$name/.git" ]]; then
    log "pull $name"
    git -C "$name" pull --ff-only || {
      log "FAIL pull $name" | tee -a /workspace/startup.errors
    }
    return 0
  fi
  log "clone $name"
  git clone "$url" "$name" || {
    log "FAIL clone $name" | tee -a /workspace/startup.errors
  }
}

# @clone

if [[ -n "${BASH_VERSION:-}" ]]; then
  log "waiting for model downloads"
  wait
fi

if [[ -n "${DROPBOX_READY:-}" ]]; then
  dropbox_push_output
  if [[ -f "$DROPBOX_SYNC_PID" ]] && kill -0 "$(cat "$DROPBOX_SYNC_PID")" 2>/dev/null; then
    log "dropbox loop already pid=$(cat "$DROPBOX_SYNC_PID")"
  else
    dropbox_loop &
    echo $! > "$DROPBOX_SYNC_PID"
    disown $! 2>/dev/null || true
    log "dropbox loop pid=$(cat "$DROPBOX_SYNC_PID") interval=60s"
  fi
fi
log "done"
