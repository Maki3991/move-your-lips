$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Push-Location -LiteralPath $project
try {
    if (-not (Test-Path -LiteralPath '.venv-web/Scripts/python.exe')) {
        & python -m venv .venv-web
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the API environment.' }
    }
    & '.venv-web/Scripts/python.exe' -m pip install -r requirements-web.lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'API dependencies failed to install.' }
    Push-Location -LiteralPath 'web'
    try {
        & npm.cmd ci
        if ($LASTEXITCODE -ne 0) { throw 'Web dependencies failed to install.' }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Web build failed.' }
    } finally { Pop-Location }
    Write-Host 'Setup complete. Run: .\scripts\run_web_demo.ps1'
} finally { Pop-Location }
