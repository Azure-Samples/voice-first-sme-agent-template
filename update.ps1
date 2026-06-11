# Agent Template — pull upstream changes into your fork (PowerShell).
#
# Fetches release tags from the upstream template repo and merges the
# latest v* release tag into your current branch by default.
# Customer-owned files (settings.json, agents/<name>/*) are
# allowed to be dirty. Platform files (backend/, frontend/, infra/,
# Dockerfile, scripts/) come from upstream and will update on merge.
# Prompt prose under agents/ can still conflict if upstream changed the
# same prompt before the data-first prompt model lands.
#
# Usage: .\update.ps1 [-Remote <name>] [-Ref <name>] [-DryRun]
#   -Remote   upstream remote name (default: upstream)
#   -Ref      branch, tag, or commit to merge (default: latest v* release tag)
#   -DryRun   show the upstream diff preview without merging
#
# Setup (first time): add the upstream remote
#   git remote add upstream <url-of-template-repo>

param(
    [string]$Remote = 'upstream',
    [string]$Ref = '',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Error 'git is required.'
    exit 1
}

& git rev-parse --git-dir *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error 'not inside a git repository.'
    exit 1
}

& git remote get-url $Remote *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error "git remote '$Remote' not configured. Add it once with: git remote add $Remote <template-repo-url>"
    exit 1
}

# Allowlist customer-owned paths and flag any dirty file outside them.
# New platform files added in future template versions are protected
# automatically. Customer-owned paths live under agents/<name>/.
# settings.json and .env are gitignored and won't show in `git status`.
$dirty = & git status --porcelain | Where-Object { $_ -notmatch '^.. agents/' }
if ($dirty) {
    Write-Warning "Local modifications outside customer-owned paths:`n$($dirty -join "`n")"
    Write-Host ''
    Write-Host '  Upstream changes may conflict with these. Stash or commit before continuing.'
    Write-Host '  Customer-owned paths under agents/<name>/ are safe to keep dirty.'
    if ($DryRun) {
        Write-Host '  Continuing because -DryRun will not merge.'
    } else {
        exit 1
    }
}

function Test-GitCommit {
    param([string]$Rev)
    & git rev-parse --verify "$Rev^{commit}" *> $null
    return $LASTEXITCODE -eq 0
}

function Read-LocalVersion {
    if (Test-Path 'VERSION') {
        return (Get-Content 'VERSION' -Raw).Trim()
    }
    return 'unknown'
}

function Resolve-LatestReleaseTag {
    $remoteMain = "refs/remotes/$Remote/main"

    & git show-ref --verify --quiet $remoteMain
    if ($LASTEXITCODE -eq 0) {
        $tags = & git tag -l 'v*' --sort=-v:refname --merged $remoteMain
    } else {
        $tags = & git tag -l 'v*' --sort=-v:refname
    }

    $latest = @($tags | Where-Object { $_ -and $_.Trim() } | Select-Object -First 1)
    if (-not $latest) {
        Write-Error "No upstream release tags found. Expected tags like v0.1.0. Ask upstream to publish a release tag, or opt into unreleased work explicitly with: .\update.ps1 -Ref $Remote/main"
        exit 1
    }

    return $latest[0]
}

function Resolve-MergeTarget {
    param([string]$RequestedRef)

    if (-not $RequestedRef) {
        return (Resolve-LatestReleaseTag)
    }

    if (Test-GitCommit $RequestedRef) {
        return $RequestedRef
    }

    $remoteRef = "$Remote/$RequestedRef"
    if (Test-GitCommit $remoteRef) {
        return $remoteRef
    }

    Write-Host "==> Fetching ref '$RequestedRef' from $Remote..."
    & git fetch $Remote $RequestedRef
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    return 'FETCH_HEAD'
}

function Show-ChangelogPreview {
    param([string]$TargetRef)

    $currentVersion = Read-LocalVersion
    $targetVersion = (& git show "$($TargetRef):VERSION" 2>$null | Out-String).Trim()
    if (-not $targetVersion) {
        $targetVersion = 'unknown'
    }

    Write-Host "==> Resolved update target: $TargetRef"
    Write-Host "    Current VERSION: $currentVersion"
    Write-Host "    Target VERSION:  $targetVersion"
    Write-Host ''

    $currentVersionTag = "v$currentVersion"
    if (Test-GitCommit $currentVersionTag) {
        $baseRef = $currentVersionTag
    } else {
        $baseRef = 'HEAD'
    }

    Write-Host "==> CHANGELOG preview ($baseRef..$TargetRef)"
    $diff = & git diff --no-ext-diff --no-color $baseRef $TargetRef -- CHANGELOG.md
    if ($diff) {
        foreach ($line in $diff) {
            Write-Host "    $line"
        }
    } else {
        Write-Host '    No CHANGELOG.md changes found.'
    }
    Write-Host ''
}

function Show-DiffPreview {
    param([string]$TargetRef)

    $mergeBase = (& git merge-base HEAD $TargetRef | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $mergeBase) {
        Write-Error "Could not determine a merge base between HEAD and $TargetRef."
        exit 1
    }

    Write-Host "==> Dry run: upstream diff preview (${mergeBase}..${TargetRef})"
    Write-Host '    This shows upstream changes since your current merge base.'
    Write-Host '    The real merge may still report conflicts if your local edits overlap.'
    Write-Host ''

    & git diff --quiet --no-ext-diff $mergeBase $TargetRef
    if ($LASTEXITCODE -eq 0) {
        Write-Host '    No file changes found.'
        return
    }
    if ($LASTEXITCODE -gt 1) {
        exit $LASTEXITCODE
    }

    Write-Host '==> File summary'
    & git diff --stat --no-ext-diff --no-color $mergeBase $TargetRef | ForEach-Object { Write-Host "    $_" }
    Write-Host ''
    Write-Host '==> Full diff'
    & git diff --no-ext-diff --no-color $mergeBase $TargetRef | ForEach-Object { Write-Host "    $_" }
    Write-Host ''
}

Write-Host "==> Fetching tags from $Remote..."
& git fetch $Remote --tags
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$mergeTarget = Resolve-MergeTarget $Ref
Show-ChangelogPreview $mergeTarget

if ($DryRun) {
    Show-DiffPreview $mergeTarget
    Write-Host 'Dry run complete. No merge performed.'
    exit 0
}

$currentBranch = (& git rev-parse --abbrev-ref HEAD).Trim()
Write-Host "==> Merging $mergeTarget into $currentBranch..."
& git merge -m "Merge template update $mergeTarget into $currentBranch" $mergeTarget
if ($LASTEXITCODE -ne 0) {
    $conflicts = & git diff --name-only --diff-filter=U
    $promptConflicts = $conflicts | Where-Object { $_ -match '^agents/[^/]+/system_prompt\.txt$' }
    if ($promptConflicts) {
        Write-Host ''
        Write-Warning 'Prompt conflict detected in agents/*/system_prompt.txt. See docs/updating.md#prompt-update-conflicts for the recommended resolution path.'
    }
    exit $LASTEXITCODE
}

Write-Host ''
Write-Host 'Done. Review CHANGELOG.md for what changed.'
