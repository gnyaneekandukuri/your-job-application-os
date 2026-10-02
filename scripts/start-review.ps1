$ErrorActionPreference = 'Stop'
$productRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $productRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    throw 'Create .venv and install requirements.txt first. See README.md.'
}
if (-not (Test-Path -LiteralPath 'frontend/dist/index.html')) {
    throw 'Build the interface first: npm --prefix frontend run build'
}
if ([string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY)) {
    $env:OPENAI_API_KEY = [Environment]::GetEnvironmentVariable('OPENAI_API_KEY', 'User')
}
Write-Host 'Open http://127.0.0.1:8000 - your local Job Review workspace.'
if ([string]::IsNullOrWhiteSpace($env:TYPESAFE_API_KEY)) {
    $env:TYPESAFE_API_KEY = [Environment]::GetEnvironmentVariable('TYPESAFE_API_KEY', 'User')
}
& '.venv/Scripts/python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
if ($LASTEXITCODE -ne 0) { throw 'The local server exited with an error.' }
