$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

$env:TEMP = Join-Path $Root "_temp"
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $Root "app\.cache\pip"
$env:HF_HOME = Join-Path $Root "app\.cache\huggingface"
$env:TRANSFORMERS_CACHE = Join-Path $Root "app\.cache\huggingface\transformers"
$env:XDG_CACHE_HOME = Join-Path $Root "app\.cache"

New-Item -ItemType Directory -Force -Path `
  $env:TEMP, `
  $env:PIP_CACHE_DIR, `
  $env:HF_HOME, `
  $env:TRANSFORMERS_CACHE, `
  $env:XDG_CACHE_HOME | Out-Null

$Python = Join-Path $Root "app\.venv\Scripts\python.exe"
$Main = Join-Path $Root "app\main.py"

if (-not (Test-Path -LiteralPath $Python)) {
  Write-Error "Project venv not found: $Python"
}

& $Python $Main
exit $LASTEXITCODE
