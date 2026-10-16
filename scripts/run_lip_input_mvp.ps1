param(
    [ValidateSet('cnvsrc', 'cmlr')]
    [string]$Model = 'cnvsrc',
    [switch]$SaveLosslessNextInput,
    [string]$PracticeSet = '',
    [string]$Checkpoint = '',
    [string]$Python = '',
    [string]$VsrRoot = ''
)

$ErrorActionPreference = 'Stop'

$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$settingsPath = Join-Path $project 'local.settings.json'
$settings = if (Test-Path -LiteralPath $settingsPath) {
    Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
} else { $null }
$python = if ($Python) { $Python } elseif ($settings -and $settings.python) { $settings.python } else { Join-Path $project '.venv\Scripts\python.exe' }
$vsr = if ($VsrRoot) { $VsrRoot } elseif ($settings -and $settings.vsrRoot) { $settings.vsrRoot } else { Join-Path $project 'third_party\CNVSRC2025\VSR' }
$cmlrRoot = Join-Path $project 'third_party\Visual_Speech_Recognition_for_Multiple_Languages'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found: $python"
}
if (-not (Test-Path -LiteralPath $vsr)) {
    throw "CNVSRC2025 VSR repository not found: $vsr"
}
Set-Location -LiteralPath $project
if ($SaveLosslessNextInput -and $Model -ne 'cnvsrc') {
    throw 'SaveLosslessNextInput is only supported with -Model cnvsrc.'
}
$extraArgs = @()
if ($SaveLosslessNextInput) {
    $extraArgs += '--save-lossless-next-input'
}
if ($PracticeSet) {
    if ($Model -ne 'cnvsrc') {
        throw 'PracticeSet is only supported with -Model cnvsrc.'
    }
    $extraArgs += @('--practice-set', $PracticeSet)
}
if ($Model -eq 'cmlr') {
    if (-not (Test-Path -LiteralPath $cmlrRoot)) {
        throw "CMLR repository not found: $cmlrRoot"
    }
    $cmlrCheckpoint = Join-Path $cmlrRoot 'benchmarks\CMLR\models\CMLR_V_WER8.0\model.pth'
    if (-not (Test-Path -LiteralPath $cmlrCheckpoint)) {
        throw "CMLR checkpoint not found: $cmlrCheckpoint"
    }
    & $python "$project\scripts\lip_input_hud.py" `
        --model cmlr `
        --vsr-root $vsr `
        --cmlr-root $cmlrRoot `
        --cmlr-checkpoint $cmlrCheckpoint `
        --camera 0 `
        --face-model mobilenet0.25 `
        --device cpu
} else {
    if ($Checkpoint) {
        $checkpoint = (Resolve-Path -LiteralPath $Checkpoint).Path
    } else {
        $checkpoint = Join-Path $vsr 'pretrained_models\model_avg_cncvs_2_3_cnvsrc.pth'
        $activeConfig = Join-Path $project 'outputs\mvp\active-model.json'
        if (Test-Path -LiteralPath $activeConfig) {
            $active = Get-Content -LiteralPath $activeConfig -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($active.checkpoint) { $checkpoint = [string]$active.checkpoint }
        }
    }
    if (-not (Test-Path -LiteralPath $checkpoint)) {
        throw "CNVSRC2025 checkpoint not found: $checkpoint"
    }
    & $python "$project\scripts\lip_input_hud.py" `
        --model cnvsrc `
        --vsr-root $vsr `
        --checkpoint $checkpoint `
        --camera 0 `
        --face-model mobilenet0.25 `
        --device cpu `
        --beam-size 10 `
        @extraArgs
}
