$ErrorActionPreference = "Stop"

$demoRoot = $PSScriptRoot
$repoRoot = Split-Path -Parent $demoRoot
$shellExe = (Get-Process -Id $PID).Path
$uvicornExe = Join-Path $repoRoot ".venv\Scripts\uvicorn.exe"
$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
$cloudflaredExe = "C:\Program Files (x86)\cloudflared\cloudflared.exe"

if (-not (Test-Path $cloudflaredExe)) {
    throw "Missing cloudflared at $cloudflaredExe"
}

$uvicornCommand = $null
if (Test-Path $uvicornExe) {
    $uvicornCommand = "& '$uvicornExe' app:app --host 127.0.0.1 --port 8000"
} else {
    $resolvedUvicorn = Get-Command uvicorn -ErrorAction SilentlyContinue
    if ($resolvedUvicorn) {
        $uvicornCommand = "& '$($resolvedUvicorn.Source)' app:app --host 127.0.0.1 --port 8000"
    } elseif (Test-Path $pythonExe) {
        $moduleCheck = & $pythonExe -c "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('uvicorn') else 1)"
        if ($LASTEXITCODE -eq 0) {
            $uvicornCommand = "& '$pythonExe' -m uvicorn app:app --host 127.0.0.1 --port 8000"
        }
    }
}

if (-not $uvicornCommand) {
    throw "Could not find a usable Uvicorn launcher. Install uvicorn in the repo virtualenv or make 'uvicorn' available on PATH."
}

$appCommand = "Set-Location '$demoRoot'; $uvicornCommand"
$tunnelCommand = "& '$cloudflaredExe' tunnel run velura-demo"

Write-Host "Starting TikTok demo app on 127.0.0.1:8000..."
Start-Process -FilePath $shellExe -ArgumentList "-NoExit", "-Command", $appCommand | Out-Null

$isReady = $false
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Seconds 1
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/status" -UseBasicParsing -TimeoutSec 5 | Out-Null
        $isReady = $true
        break
    } catch {
    }
}

if (-not $isReady) {
    throw "The local FastAPI app did not become ready on http://127.0.0.1:8000."
}

Write-Host "Starting Cloudflare tunnel for demo.veluraesthetics.com..."
Start-Process -FilePath $shellExe -ArgumentList "-NoExit", "-Command", $tunnelCommand | Out-Null

Write-Host ""
Write-Host "Review demo started."
Write-Host "Leave both spawned terminal windows open overnight."
Write-Host "Check these URLs from another device if you want to verify reachability:"
Write-Host "  https://demo.veluraesthetics.com/"
Write-Host "  https://demo.veluraesthetics.com/api/status"
