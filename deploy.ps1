# Agent Template - deploy entrypoint (PowerShell).
# Runs the Node deploy orchestrator in setup-wizard\deploy.js.
# Must be run from the scaffolded project root.

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$wizardDir = Join-Path $scriptDir 'setup-wizard'

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Error 'Node.js is required. Install Node 18+ and re-run .\deploy.ps1.'
    exit 1
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Error 'npm is required. Install Node.js (which bundles npm) and re-run.'
    exit 1
}
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Error 'Azure CLI (az) is required. Install: https://learn.microsoft.com/cli/azure/install-azure-cli'
    exit 1
}

if (-not (Test-Path (Join-Path $wizardDir 'node_modules'))) {
    Write-Host 'Installing deploy dependencies (first run only)...'
    Push-Location $wizardDir
    try {
        & npm ci --silent --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) {
            & npm install --silent --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) {
                Write-Error 'Failed to install deploy dependencies.'
                exit 1
            }
        }
    } finally {
        Pop-Location
    }
}

# Run from the project root so deploy.js sees settings.json via process.cwd().
Push-Location $scriptDir
try {
    & node (Join-Path $wizardDir 'deploy.js') @args
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
