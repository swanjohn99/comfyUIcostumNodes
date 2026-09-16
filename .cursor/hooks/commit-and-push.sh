#!/usr/bin/env bash
# Commit and push after agent file edits. Fail open on errors.
set -u

# Drain stdin (hook JSON payload).
cat >/dev/null || true

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "${ROOT}" ]]; then
  exit 0
fi
cd "${ROOT}" || exit 0

if git diff --quiet && git diff --cached --quiet && [[ -z "$(git ls-files --others --exclude-standard)" ]]; then
  exit 0
fi

git add -A -- . 2>/dev/null || true

# Drop staged secrets / credential-like paths
while IFS= read -r f; do
  [[ -z "$f" ]] && continue
  base="$(basename "$f")"
  case "$base" in
    .env|.env.*|credentials.json|*.pem|*.key)
      git reset -q -- "$f" 2>/dev/null || true
      ;;
  esac
  case "$f" in
    *.env|*/.env|*/.env.*|*credentials*|*secret*)
      git reset -q -- "$f" 2>/dev/null || true
      ;;
  esac
done < <(git diff --cached --name-only 2>/dev/null)

if git diff --cached --quiet; then
  exit 0
fi

# Resolve author: env → local git config → last commit → hard fallback.
_name="${GIT_AUTHOR_NAME:-}"
_email="${GIT_AUTHOR_EMAIL:-}"
[[ -z "$_name" ]] && _name="$(git config --local user.name 2>/dev/null || true)"
[[ -z "$_email" ]] && _email="$(git config --local user.email 2>/dev/null || true)"
[[ -z "$_name" ]] && _name="$(git config user.name 2>/dev/null || true)"
[[ -z "$_email" ]] && _email="$(git config user.email 2>/dev/null || true)"
[[ -z "$_name" ]] && _name="$(git log -1 --format='%an' 2>/dev/null || true)"
[[ -z "$_email" ]] && _email="$(git log -1 --format='%ae' 2>/dev/null || true)"
[[ -z "$_name" ]] && _name="swanjohn99"
[[ -z "$_email" ]] && _email="bora.india@gmail.com"

export GIT_AUTHOR_NAME="$_name"
export GIT_AUTHOR_EMAIL="$_email"
export GIT_COMMITTER_NAME="${GIT_COMMITTER_NAME:-$_name}"
export GIT_COMMITTER_EMAIL="${GIT_COMMITTER_EMAIL:-$_email}"

if ! git commit -m "auto: sync agent file changes" >/dev/null 2>&1; then
  exit 0
fi

_branch="$(git branch --show-current 2>/dev/null || echo main)"
_remote="${GIT_REMOTE:-origin}"

_push_once() {
  git push -u "${_remote}" HEAD "$@" 2>/dev/null
}

if _push_once; then
  exit 0
fi

git fetch "${_remote}" "${_branch}" 2>/dev/null || exit 0
git pull --rebase "${_remote}" "${_branch}" 2>/dev/null || exit 0
_push_once || true
exit 0
