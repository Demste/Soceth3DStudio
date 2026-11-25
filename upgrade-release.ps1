param(
    [Parameter(Mandatory=$true)][string]$Archive,
    [Parameter(Mandatory=$true)][string]$Sha256,
    [string]$Destination = (Split-Path -Parent $PSScriptRoot)
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts/paths.ps1')
$paths = Get-SocethPaths $PSScriptRoot
Set-SocethEnvironment $paths
$port = if ($env:SOCETH_PORT) { $env:SOCETH_PORT } else { 17863 }
try { $service = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/health" -TimeoutSec 2 } catch { $service = $null }
if ($service.service -eq 'soceth') { throw 'Finish any jobs and close Studio before applying the update.' }
$python = Join-Path $paths.runtime 'app-venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Finish setup in the existing version first.' }
$backup = Join-Path $paths.data ("backups/pre-release-" + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.sqlite3')
Invoke-SocethChecked $python @((Join-Path $PSScriptRoot 'scripts/backup_store.py'),(Join-Path $paths.data 'studio.sqlite3'),$backup)
$next = & $python (Join-Path $PSScriptRoot 'scripts/unpack_release.py') (Resolve-Path -LiteralPath $Archive).Path $Destination $Sha256
if ($LASTEXITCODE -ne 0) { throw 'The release could not be staged. The previous installation remains available.' }
& (Join-Path $next 'setup.ps1') -Models Skip -NoLaunch
Write-Host "Run start.ps1 in $next. Keep the previous directory for rollback."
