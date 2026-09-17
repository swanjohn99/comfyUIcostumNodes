# Project git author for Cursor hooks/agents (Windows).
# Dot-source this file; do not run git config.
# Usage: . .cursor/git-identity.ps1

$GIT_USER_NAME = "swanjohn99"
$GIT_USER_EMAIL = "bora.india@gmail.com"
$GIT_USER_ID = "swanjohn99"

if (-not $env:GIT_AUTHOR_NAME) { $env:GIT_AUTHOR_NAME = $GIT_USER_NAME }
if (-not $env:GIT_AUTHOR_EMAIL) { $env:GIT_AUTHOR_EMAIL = $GIT_USER_EMAIL }
if (-not $env:GIT_COMMITTER_NAME) { $env:GIT_COMMITTER_NAME = $GIT_USER_NAME }
if (-not $env:GIT_COMMITTER_EMAIL) { $env:GIT_COMMITTER_EMAIL = $GIT_USER_EMAIL }
if (-not $env:GIT_USER_ID) { $env:GIT_USER_ID = $GIT_USER_ID }
