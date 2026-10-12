$ErrorActionPreference = 'Stop'

$project = 'D:\Softwares\Programming Projects\move-your-lips'
$python = 'D:\cnvsrc-env\python.exe'
$vsr = 'D:\Softwares\Programming Projects\_githubs\CNVSRC2025\VSR'
$checkpoint = Join-Path $vsr 'pretrained_models\model_avg_cncvs_2_3_cnvsrc.pth'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found: $python"
}
if (-not (Test-Path -LiteralPath $vsr)) {
    throw "CNVSRC2025 VSR repository not found: $vsr"
}
if (-not (Test-Path -LiteralPath $checkpoint)) {
    throw "CNVSRC2025 checkpoint not found: $checkpoint"
}

Set-Location -LiteralPath $project
& $python "$project\scripts\lip_input_hud.py" `
    --vsr-root $vsr `
    --checkpoint $checkpoint `
    --camera 0 `
    --face-model mobilenet0.25 `
    --device cpu
