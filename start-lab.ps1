param([ValidateRange(1024, 65535)][int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$labPython = Join-Path $PSScriptRoot '.conda/env/python.exe'
if (-not (Test-Path -LiteralPath $labPython)) {
    throw 'Create the repository Conda environment as described in README first.'
}
Write-Host "Busy Beaver Lab：http://127.0.0.1:$Port （Ctrl+C to stop）"
& $labPython -m uvicorn server.app:app --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
