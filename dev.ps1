param([switch]$NoProviders)
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath (Join-Path $PSScriptRoot 'apps/desktop')
try {
    & npm.cmd run build
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
} finally { Pop-Location }
& (Join-Path $PSScriptRoot 'start.ps1') -Dev -NoProviders:$NoProviders
