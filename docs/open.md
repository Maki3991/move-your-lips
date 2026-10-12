Set-Location 'D:\Softwares\Programming Projects\move-your-lips'

& '.\scripts\run_lip_input_mvp.ps1'

---
$repo = 'D:\Softwares\Programming Projects\_githubs\lipflow'
$tools = 'D:\Softwares\Programming Projects\_githubs\lipflow-tools'
$data = 'D:\Softwares\Programming Projects\_githubs\lipflow-data'

$env:UV_CACHE_DIR = "$data\uv-cache"
$env:UV_PYTHON_INSTALL_DIR = "$data\uv-python"
$env:UV_PYTHON_BIN_DIR = "$data\uv-python-bin"
$env:UV_PYTHON_CACHE_DIR = "$data\uv-python-cache"
$env:UV_PYTHON = '3.12'
$env:UV_PROJECT_ENVIRONMENT = "$repo\.venv"
$env:LIPFLOW_HOME = "$data\home"
$env:Path = "$tools;$env:Path"

Set-Location -LiteralPath $repo

& "$tools\uv.exe" run lipflow run `
  --key right_control `
  --cleanup basic `
  --copy-only