# Agent Template - setup wizard entrypoint (PowerShell).
# Bootstraps the Node wizard in setup-wizard\ and runs it.

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$wizardDir = Join-Path $scriptDir 'setup-wizard'

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Error 'Node.js is required. Install Node 18+ and re-run .\setup.ps1.'
    exit 1
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Error 'npm is required. Install Node.js (which bundles npm) and re-run.'
    exit 1
}

if (-not (Test-Path (Join-Path $wizardDir 'node_modules'))) {
    Write-Host 'Installing wizard dependencies (first run only)...'
    Push-Location $wizardDir
    try {
        & npm ci --silent --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) {
            & npm install --silent --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) {
                Write-Error 'Failed to install wizard dependencies.'
                exit 1
            }
        }
    } finally {
        Pop-Location
    }
}

& node (Join-Path $wizardDir 'setup.js') @args
exit $LASTEXITCODE
