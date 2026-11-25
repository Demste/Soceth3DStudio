$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts/paths.ps1')
$paths = Get-SocethPaths $PSScriptRoot
$port = if ($env:SOCETH_PORT) { [int]$env:SOCETH_PORT } else { 17863 }
$record = Join-Path $paths.runtime "studio-$port.json"
if (-not (Test-Path -LiteralPath $record)) { Write-Host 'No managed Soceth instance was found.'; exit 0 }
$instance = Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
$parent = Get-Process -Id ([int]$instance.pid) -ErrorAction SilentlyContinue
if ($parent -and $parent.MainWindowHandle -ne 0 -and [Math]::Abs(([DateTimeOffset]$parent.StartTime).ToUnixTimeSeconds() - [double]$instance.time) -lt 30) {
    if ($parent.CloseMainWindow() -and $parent.WaitForExit(8000)) {
        Write-Host 'Closed Soceth Studio.'
        exit 0
    }
}
foreach ($processId in @($instance.children) + @($instance.pid)) {
    $process = Get-Process -Id ([int]$processId) -ErrorAction SilentlyContinue
    if ($process -and [Math]::Abs(([DateTimeOffset]$process.StartTime).ToUnixTimeSeconds() - [double]$instance.time) -lt 30) {
        Stop-Process -InputObject $process
    }
}
if (Test-Path -LiteralPath $record) { Remove-Item -LiteralPath $record }
Write-Host 'Stopped the managed Soceth instance.'
