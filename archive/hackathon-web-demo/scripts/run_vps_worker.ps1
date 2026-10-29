param(
    [string]$ConfigFile = '',
    [ValidateRange(1,32)][int]$Threads = 4
)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$workspace = (Resolve-Path -LiteralPath (Join-Path $project '../..')).Path
if (-not $ConfigFile) { $ConfigFile = Join-Path $workspace '.web-demo/vps-worker.json' }
if (-not (Test-Path -LiteralPath $ConfigFile)) { throw 'Missing private .web-demo/vps-worker.json. See docs/vps-deployment.md.' }
$config = Get-Content -LiteralPath $ConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
if ($config.url -notmatch '^https://[^/]+/?$' -or ([string]$config.workerSecret).Length -lt 32) { throw 'Invalid Worker URL or secret.' }
$settingsPath = Join-Path $workspace 'local.settings.json'
$settings = if (Test-Path -LiteralPath $settingsPath) { Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }
$modelPython = if ($settings -and $settings.python) { [string]$settings.python } else { Join-Path $workspace '.venv/Scripts/python.exe' }
if (-not (Test-Path -LiteralPath $modelPython)) { throw 'Configure model Python in local.settings.json.' }
New-Item -ItemType Directory -Force (Join-Path $workspace '.web-demo/logs') | Out-Null
$workerProcess = $null
$previousSecret = $env:MYL_WORKER_SECRET
Push-Location -LiteralPath $project
try {
    $env:MYL_WORKER_SECRET = [string]$config.workerSecret
    $url = ([string]$config.url).TrimEnd('/')
    $null = Invoke-RestMethod "$url/api/status" -TimeoutSec 15
    $workerProcess = Start-Process -FilePath $modelPython -ArgumentList @('-u','-m','laptop_worker','--url',$url,'--threads',"$Threads") -WorkingDirectory $project -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $workspace '.web-demo/logs/vps-worker.stdout.log') -RedirectStandardError (Join-Path $workspace '.web-demo/logs/vps-worker.stderr.log')
    Write-Host "VPS demo: $url"
    Write-Host 'Keep this terminal open. Ctrl+C stops the laptop Worker. The VPS site stays available.'
    $previousState = ''
    while (-not $workerProcess.HasExited) {
        try {
            $status = Invoke-RestMethod "$url/api/status" -TimeoutSec 10
            if ($status.state -ne $previousState) { Write-Host "Model: $($status.state)"; $previousState = $status.state }
        } catch { Write-Host 'VPS unreachable; Worker will reconnect automatically.' }
        Start-Sleep -Seconds 4
    }
    throw 'Worker exited. See .web-demo/logs/vps-worker.stderr.log.'
} finally {
    if ($workerProcess -and -not $workerProcess.HasExited) {
        & taskkill.exe /PID $workerProcess.Id /T /F 2>$null | Out-Null
    }
    $env:MYL_WORKER_SECRET = $previousSecret
    Pop-Location
}
