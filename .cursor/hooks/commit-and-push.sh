#!/usr/bin/env bash
# Commit and push after agent file edits. Fail open on errors.
# Ensures GitHub repo name == folder basename; creates repo if missing.
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

# Resolve author: .cursor/git-identity.sh → env → last commit → hard fallback.
# Never run git config.
_id_file="${ROOT}/.cursor/git-identity.sh"
if [[ -f "$_id_file" ]]; then
  # shellcheck source=/dev/null
  . "$_id_file"
fi

_name="${GIT_AUTHOR_NAME:-}"
_email="${GIT_AUTHOR_EMAIL:-}"
[[ -z "$_name" ]] && _name="$(git log -1 --format='%an' 2>/dev/null || true)"
[[ -z "$_email" ]] && _email="$(git log -1 --format='%ae' 2>/dev/null || true)"
[[ -z "$_name" ]] && _name="swanjohn99"
[[ -z "$_email" ]] && _email="bora.india@gmail.com"

export GIT_AUTHOR_NAME="$_name"
export GIT_AUTHOR_EMAIL="$_email"
export GIT_COMMITTER_NAME="${GIT_COMMITTER_NAME:-$_name}"
export GIT_COMMITTER_EMAIL="${GIT_COMMITTER_EMAIL:-$_email}"

if ! git -c "user.name=${_name}" -c "user.email=${_email}" \
  commit -m "auto: sync agent file changes" >/dev/null 2>&1; then
  exit 0
fi

_branch="$(git branch --show-current 2>/dev/null || echo main)"
_remote="${GIT_REMOTE:-origin}"

# Folder name must match GitHub repo name.
_repo_name="$(basename "${ROOT}")"
_owner="${GIT_USER_ID:-${GIT_AUTHOR_NAME:-swanjohn99}}"
_expected_url="https://github.com/${_owner}/${_repo_name}.git"
_slug="${_owner}/${_repo_name}"

_ensure_remote_matches_folder() {
  local url
  url="$(git remote get-url "${_remote}" 2>/dev/null || true)"
  if [[ -z "$url" ]]; then
    git remote add "${_remote}" "${_expected_url}" 2>/dev/null || true
    return
  fi
  # Normalize: strip .git, trailing slash, convert ssh → owner/repo for compare
  local normalized expected_norm
  normalized="${url%.git}"
  normalized="${normalized%/}"
  expected_norm="https://github.com/${_slug}"
  case "$normalized" in
    "https://github.com/${_slug}"|"http://github.com/${_slug}"|"git@github.com:${_slug}"|"ssh://git@github.com/${_slug}")
      return 0
      ;;
  esac
  # Wrong repo (e.g. videogen vs folder name) → retarget origin
  git remote set-url "${_remote}" "${_expected_url}" 2>/dev/null || \
    git remote add "${_remote}" "${_expected_url}" 2>/dev/null || true
}

_ensure_github_repo() {
  if ! command -v gh >/dev/null 2>&1; then
    return 0
  fi
  if gh repo view "${_slug}" >/dev/null 2>&1; then
    return 0
  fi
  # Create empty repo matching folder name; do not push yet (caller pushes).
  gh repo create "${_slug}" --private --source=. --remote="${_remote}" --description "ComfyUI custom nodes" >/dev/null 2>&1 || \
    gh repo create "${_slug}" --private >/dev/null 2>&1 || true
  # Ensure remote URL after create
  git remote get-url "${_remote}" >/dev/null 2>&1 || \
    git remote add "${_remote}" "${_expected_url}" 2>/dev/null || true
  git remote set-url "${_remote}" "${_expected_url}" 2>/dev/null || true
}

_ensure_remote_matches_folder
_ensure_github_repo

_push_once() {
  git push -u "${_remote}" "HEAD:refs/heads/${_branch}" "$@" 2>/dev/null
}

if _push_once; then
  exit 0
fi

git fetch "${_remote}" "${_branch}" 2>/dev/null || true
git pull --rebase "${_remote}" "${_branch}" 2>/dev/null || true
_push_once || true
exit 0
