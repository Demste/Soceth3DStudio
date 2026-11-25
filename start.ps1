param([switch]$BackendOnly,[switch]$NoProviders,[switch]$Dev)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
. (Join-Path $PSScriptRoot 'scripts/paths.ps1')
$paths = Get-SocethPaths $PSScriptRoot
Set-SocethEnvironment $paths
$env:PYTHONUTF8 = '1'
$env:PYTHONNOUSERSITE = '1'
$python = Join-Path $paths.runtime 'app-venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run setup.ps1 before launching Studio.' }
$arguments = @((Join-Path $PSScriptRoot 'apps/desktop/launcher.py'))
if ($BackendOnly) { $arguments += '--backend-only' }
if ($NoProviders) { $arguments += '--no-providers' }
if ($Dev) { $arguments += '--dev' }
& $python @arguments
exit $LASTEXITCODE
