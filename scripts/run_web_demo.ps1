param(
    [ValidateRange(1024,65535)][int]$Port = 8000,
    [switch]$NoWorker,
    [switch]$NoBuild
)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$apiPython = Join-Path $project '.venv-web/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $apiPython)) { throw 'Run scripts/setup_web_demo.ps1 first.' }
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use. Stop the earlier demo or use -Port."
}
$settingsFile = Join-Path $project 'local.settings.json'
$settings = if (Test-Path -LiteralPath $settingsFile) { Get-Content -LiteralPath $settingsFile -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }
$modelPython = if ($settings -and $settings.python) { [string]$settings.python } else { Join-Path $project '.venv/Scripts/python.exe' }
if (-not $NoWorker -and -not (Test-Path -LiteralPath $modelPython)) { throw 'Configure model Python in local.settings.json, or use -NoWorker to inspect Offline UI.' }
Push-Location -LiteralPath $project
$serverProcess = $null
$workerProcess = $null
$previousSecret = $env:MYL_WORKER_SECRET
$previousPort = $env:MYL_PORT
try {
    if (-not $NoBuild) {
        Push-Location -LiteralPath 'web'
        try {
            & npm.cmd run build
            if ($LASTEXITCODE -ne 0) { throw 'Web build failed. Run setup_web_demo.ps1 first.' }
        } finally { Pop-Location }
    }
    if (-not (Test-Path -LiteralPath 'web/dist/index.html')) { throw 'Web build missing.' }
    New-Item -ItemType Directory -Force '.web-demo/logs' | Out-Null
    $configFile = Join-Path $project '.web-demo/local-config.json'
    if (-not (Test-Path -LiteralPath $configFile)) {
        $secretBytes = New-Object byte[] 32
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        $rng.GetBytes($secretBytes)
        $rng.Dispose()
        @{ workerSecret = ([BitConverter]::ToString($secretBytes)).Replace('-','').ToLowerInvariant() } | ConvertTo-Json | Set-Content -LiteralPath $configFile -Encoding UTF8
    }
    $config = Get-Content -LiteralPath $configFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $env:MYL_WORKER_SECRET = [string]$config.workerSecret
    $env:MYL_PORT = [string]$Port
    $serverProcess = Start-Process -FilePath $apiPython -ArgumentList @('-m','demo_server') -WorkingDirectory $project -WindowStyle Hidden -PassThru -RedirectStandardOutput '.web-demo/logs/server.stdout.log' -RedirectStandardError '.web-demo/logs/server.stderr.log'
    $url = "http://127.0.0.1:$Port"
    $available = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if ($serverProcess.HasExited) { throw 'API exited. See .web-demo/logs/server.stderr.log.' }
        try { $null = Invoke-RestMethod "$url/api/status" -TimeoutSec 2; $available = $true; break } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $available) { throw 'API startup timed out.' }
    if (-not $NoWorker) {
        $workerProcess = Start-Process -FilePath $modelPython -ArgumentList @('-u','-m','laptop_worker','--url',$url) -WorkingDirectory $project -WindowStyle Hidden -PassThru -RedirectStandardOutput '.web-demo/logs/worker.stdout.log' -RedirectStandardError '.web-demo/logs/worker.stderr.log'
    }
    Write-Host "Web demo: $url"
    Write-Host 'Keep this terminal open. Ctrl+C stops API and Worker. Logs: .web-demo/logs/'
    $previousState = ''
    while (-not $serverProcess.HasExited) {
        if ($workerProcess -and $workerProcess.HasExited) { throw 'Worker exited. See .web-demo/logs/worker.stderr.log and worker.stdout.log.' }
        try {
            $status = Invoke-RestMethod "$url/api/status" -TimeoutSec 2
            if ($status.state -ne $previousState) { Write-Host "Model: $($status.state)"; $previousState = $status.state }
        } catch { Write-Host 'API not responding.' }
        Start-Sleep -Seconds 2
    }
} finally {
    foreach ($childProcess in @($workerProcess,$serverProcess)) {
        if ($childProcess -and -not $childProcess.HasExited) {
            & taskkill.exe /PID $childProcess.Id /T /F 2>$null | Out-Null
        }
    }
    $env:MYL_WORKER_SECRET = $previousSecret
    $env:MYL_PORT = $previousPort
    Pop-Location
}
