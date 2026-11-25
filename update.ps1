param([switch]$Check)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
. (Join-Path $PSScriptRoot 'scripts/paths.ps1')
$paths = Get-SocethPaths $PSScriptRoot
Set-SocethEnvironment $paths
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.git'))) { throw 'For a ZIP installation use upgrade-release.ps1.' }
$branch = (& git symbolic-ref --quiet --short HEAD)
if ($LASTEXITCODE -ne 0 -or -not $branch) { throw 'Switch to the installed release branch.' }
$remote = (& git config --get "branch.$branch.remote")
if ($LASTEXITCODE -ne 0 -or -not $remote -or $remote -eq '.') { throw 'Configure a remote tracking branch.' }
if (-not $Check -and (git status --porcelain --untracked-files=normal)) {
    throw 'Commit or stash local changes before updating.'
}
Invoke-SocethChecked git @('fetch','--prune',$remote)
$previous = (& git rev-parse HEAD).Trim()
$next = & git rev-parse --verify '@{upstream}'
if ($LASTEXITCODE -ne 0 -or -not $next) { throw 'Tracking branch unavailable.' }
$next = $next.Trim()
if ($previous -eq $next) { Write-Host 'Studio is already current.'; exit 0 }
& git merge-base --is-ancestor $previous $next
if ($LASTEXITCODE -ne 0) { throw 'Local commits or divergence require a branch review.' }
Write-Host "Fast-forward available: $previous -> $next"
if ($Check) { exit 0 }
$port = if ($env:SOCETH_PORT) { $env:SOCETH_PORT } else { 17863 }
try { $service = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/health" -TimeoutSec 2 } catch { $service = $null }
if ($service.service -eq 'soceth') { throw 'Finish any jobs and close Studio before applying the update.' }
$python = Join-Path $paths.runtime 'app-venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = Join-Path $paths.runtime 'venv/Scripts/python.exe' }
if (-not (Test-Path -LiteralPath $python)) { throw 'Complete initial setup before updating.' }
$backup = Join-Path $paths.data ("backups/pre-update-" + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.sqlite3')
Invoke-SocethChecked $python @('scripts/backup_store.py',(Join-Path $paths.data 'studio.sqlite3'),$backup)
$receipt = Join-Path $paths.runtime 'last-update.json'
@{previous=$previous; next=$next; backup=$backup; branch=$branch} | ConvertTo-Json | Set-Content -LiteralPath $receipt -Encoding UTF8
Invoke-SocethChecked git @('merge','--ff-only',$next)
try { & (Join-Path $PSScriptRoot 'setup.ps1') -Models Skip -NoLaunch }
catch { throw "Retry setup or follow rollback in SETUP.md. Update receipt: $receipt" }
Write-Host "Update complete. Run .\start.ps1. Rollback information: $receipt"
