# Project git author for Cursor hooks/agents.
# Source this file; do not run git config.
# Usage: . .cursor/git-identity.sh
# Or: git -c user.name=... -c user.email=... commit ...

GIT_USER_NAME="swanjohn99"
GIT_USER_EMAIL="bora.india@gmail.com"
GIT_USER_ID="swanjohn99"

export GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME:-$GIT_USER_NAME}"
export GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL:-$GIT_USER_EMAIL}"
export GIT_COMMITTER_NAME="${GIT_COMMITTER_NAME:-$GIT_USER_NAME}"
export GIT_COMMITTER_EMAIL="${GIT_COMMITTER_EMAIL:-$GIT_USER_EMAIL}"
