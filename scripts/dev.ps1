param(
    [string]$Agent = ""
)

$ErrorActionPreference = "Stop"
$ROOT_DIR = Split-Path -Parent $PSScriptRoot

# If an agent variant is specified, copy its .env as frontend/.env.local
if ($Agent) {
    $agentEnv = Join-Path $ROOT_DIR "agents\$Agent\.env"
    $envLocal = Join-Path $ROOT_DIR "frontend\.env.local"
    if (Test-Path $agentEnv) {
        Copy-Item $agentEnv $envLocal -Force
        Write-Host "Loaded branding from agents/$Agent/.env" -ForegroundColor Magenta
        # Also set backend env vars for prompt/kb
        $env:SYSTEM_PROMPT_FILE = "agents/$Agent/system_prompt.txt"
        $env:KNOWLEDGE_BASE_FILE = "agents/$Agent/knowledge_base.json"
    } else {
        Write-Host "WARNING: agents/$Agent/.env not found" -ForegroundColor Yellow
    }
}

# Refresh PATH to pick up newly installed tools (e.g. after winget install)
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")

# Kill any existing processes on port 5173 and 8000
foreach ($port in @(5173, 8000)) {
    $pids = netstat -ano | Select-String ":$port\s" | ForEach-Object {
        ($_ -split '\s+')[-1]
    } | Sort-Object -Unique | Where-Object { $_ -ne '0' }
    foreach ($p in $pids) {
        Write-Host "Killing existing process on port $port (PID $p)..." -ForegroundColor Yellow
        Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
    }
}

# Disable the Windows Store python alias if the real Python is available
$realPython = Get-Command python -ErrorAction SilentlyContinue |
    Where-Object { $_.Source -notlike "*WindowsApps*" } |
    Select-Object -First 1 -ExpandProperty Source
if (-not $realPython) {
    Write-Host "ERROR: Python not found. Install it with: winget install Python.Python.3.12" -ForegroundColor Red
    exit 1
}
$nodeCmd = Get-Command node -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
if (-not $nodeCmd) {
    Write-Host "ERROR: Node.js not found. Install it with: winget install OpenJS.NodeJS.LTS" -ForegroundColor Red
    exit 1
}

Write-Host "Using Python: $realPython" -ForegroundColor DarkGray
Write-Host "Using Node:   $nodeCmd" -ForegroundColor DarkGray

# ── Backend setup ──
Write-Host "Setting up backend..." -ForegroundColor Cyan
Push-Location "$ROOT_DIR\backend"

& $realPython -m pip install --user -q -r requirements.txt

Pop-Location

# ── Frontend setup ──
Write-Host "Setting up frontend..." -ForegroundColor Cyan
Push-Location "$ROOT_DIR\frontend"
npm install --silent
Pop-Location

# ── Run both ──
Write-Host "Starting backend (port 8000) and frontend (port 5173)..." -ForegroundColor Green

$nodeCmd = Get-Command node -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
$viteJs = "$ROOT_DIR\frontend\node_modules\vite\bin\vite.js"

$backendProc = Start-Process -PassThru -NoNewWindow -FilePath $realPython -ArgumentList "-m", "uvicorn", "app.main:app", "--reload", "--port", "8000" -WorkingDirectory "$ROOT_DIR\backend"
$frontendProc = Start-Process -PassThru -NoNewWindow -FilePath $nodeCmd -ArgumentList $viteJs -WorkingDirectory "$ROOT_DIR\frontend"

try {
    Write-Host "Press Ctrl+C to stop both servers." -ForegroundColor Yellow
    while (-not $backendProc.HasExited -or -not $frontendProc.HasExited) {
        Start-Sleep -Milliseconds 500
    }
} finally {
    Write-Host "`nStopping servers..." -ForegroundColor Red
    if (-not $backendProc.HasExited) { Stop-Process -Id $backendProc.Id -Force -ErrorAction SilentlyContinue }
    if (-not $frontendProc.HasExited) { Stop-Process -Id $frontendProc.Id -Force -ErrorAction SilentlyContinue }
    # Clean up agent-specific .env.local
    $envLocal = Join-Path $ROOT_DIR "frontend\.env.local"
    if ($Agent -and (Test-Path $envLocal)) { Remove-Item $envLocal -Force }
}
