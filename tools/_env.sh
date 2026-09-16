# Shared bootstrap for tools/ wrappers. Sourced, not executed.
# Resolves repo root, ensures .venv, sets PYTHONPATH so `presets` and `vtools` import.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi

export PYTHONPATH="$ROOT/tools${PYTHONPATH:+:$PYTHONPATH}"
PYTHON="$ROOT/.venv/bin/python"
