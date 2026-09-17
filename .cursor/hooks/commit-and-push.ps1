# Commit and push after agent file edits. Fail open on errors.
# Ensures GitHub repo name == folder basename; creates repo if missing.
$ErrorActionPreference = "Continue"

# Drain stdin (hook JSON payload).
try {
    [void][Console]::In.ReadToEnd()
} catch {}

function Exit-Ok { exit 0 }

try {
    $root = (git rev-parse --show-toplevel 2>$null)
    if (-not $root) { Exit-Ok }
    Set-Location -LiteralPath $root
} catch {
    Exit-Ok
}

$dirty = $false
git diff --quiet 2>$null
if ($LASTEXITCODE -ne 0) { $dirty = $true }
git diff --cached --quiet 2>$null
if ($LASTEXITCODE -ne 0) { $dirty = $true }
$untracked = git ls-files --others --exclude-standard 2>$null
if ($untracked) { $dirty = $true }
if (-not $dirty) { Exit-Ok }

git add -A -- . 2>$null

$staged = @(git diff --cached --name-only 2>$null)
foreach ($f in $staged) {
    if (-not $f) { continue }
    $base = Split-Path -Leaf $f
    $drop = $false
    if ($base -eq ".env" -or $base -like ".env.*" -or $base -eq "credentials.json" -or $base -like "*.pem" -or $base -like "*.key") {
        $drop = $true
    }
    if ($f -like "*.env" -or $f -like "*/.env" -or $f -like "*/.env.*" -or $f -match "credentials|secret") {
        $drop = $true
    }
    if ($drop) {
        git reset -q -- $f 2>$null
    }
}

git diff --cached --quiet 2>$null
if ($LASTEXITCODE -eq 0) { Exit-Ok }

# Resolve author: .cursor/git-identity.ps1 → env → last commit → hard fallback.
# Never run git config.
$idPs1 = Join-Path $root ".cursor\git-identity.ps1"
if (Test-Path -LiteralPath $idPs1) {
    . $idPs1
}

$name = $env:GIT_AUTHOR_NAME
$email = $env:GIT_AUTHOR_EMAIL
if (-not $name) { $name = (git log -1 --format="%an" 2>$null) }
if (-not $email) { $email = (git log -1 --format="%ae" 2>$null) }
if (-not $name) { $name = "swanjohn99" }
if (-not $email) { $email = "bora.india@gmail.com" }

$env:GIT_AUTHOR_NAME = $name
$env:GIT_AUTHOR_EMAIL = $email
if (-not $env:GIT_COMMITTER_NAME) { $env:GIT_COMMITTER_NAME = $name }
if (-not $env:GIT_COMMITTER_EMAIL) { $env:GIT_COMMITTER_EMAIL = $email }

git -c "user.name=$name" -c "user.email=$email" commit -m "auto: sync agent file changes" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { Exit-Ok }

$branch = (git branch --show-current 2>$null)
if (-not $branch) { $branch = "main" }
$remote = if ($env:GIT_REMOTE) { $env:GIT_REMOTE } else { "origin" }

$repoName = Split-Path -Leaf $root
$owner = if ($env:GIT_USER_ID) { $env:GIT_USER_ID } elseif ($env:GIT_AUTHOR_NAME) { $env:GIT_AUTHOR_NAME } else { "swanjohn99" }
$expectedUrl = "https://github.com/$owner/$repoName.git"
$slug = "$owner/$repoName"

function Test-RemoteMatchesSlug {
    param([string]$Url, [string]$Slug)
    $n = $Url -replace "\.git$", "" -replace "/$", ""
    $ok = @(
        "https://github.com/$Slug",
        "http://github.com/$Slug",
        "git@github.com:$Slug",
        "ssh://git@github.com/$Slug"
    )
    return $ok -contains $n
}

function Ensure-RemoteMatchesFolder {
    $url = (git remote get-url $remote 2>$null)
    if (-not $url) {
        git remote add $remote $expectedUrl 2>$null | Out-Null
        return
    }
    if (Test-RemoteMatchesSlug -Url $url -Slug $slug) { return }
    git remote set-url $remote $expectedUrl 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        git remote add $remote $expectedUrl 2>$null | Out-Null
    }
}

function Ensure-GithubRepo {
    $gh = Get-Command gh -ErrorAction SilentlyContinue
    if (-not $gh) { return }
    gh repo view $slug 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { return }
    gh repo create $slug --private --source=. --remote=$remote --description "ComfyUI custom nodes" 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        gh repo create $slug --private 2>$null | Out-Null
    }
    $have = (git remote get-url $remote 2>$null)
    if (-not $have) {
        git remote add $remote $expectedUrl 2>$null | Out-Null
    }
    git remote set-url $remote $expectedUrl 2>$null | Out-Null
}

function Push-Once {
    git push -u $remote "HEAD:refs/heads/$branch" 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

Ensure-RemoteMatchesFolder
Ensure-GithubRepo

if (Push-Once) { Exit-Ok }

git fetch $remote $branch 2>$null | Out-Null
git pull --rebase $remote $branch 2>$null | Out-Null
[void](Push-Once)
Exit-Ok
