$labPython = Join-Path $PSScriptRoot '.conda/env/python.exe'
if (-not (Test-Path -LiteralPath $labPython)) {
    Write-Output '{"interface_version":1,"ok":false,"error":{"code":3,"message":"Repository Conda environment not found"}}'
    exit 3
}
Push-Location -LiteralPath $PSScriptRoot
try {
    & $labPython -m experiments.cli @args
    $labExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $labExitCode
