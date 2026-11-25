param([switch]$Detailed)
$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot 'scripts/paths.ps1')
$install = Get-SocethPaths $PSScriptRoot
Set-SocethEnvironment $install
Write-Host 'Soceth 3D Studio prerequisites'
foreach ($tool in 'python','node','npm.cmd','git','rustc','cargo','nvidia-smi','ollama','tar.exe','uv') {
    $found = Get-Command $tool -ErrorAction SilentlyContinue
    if ($found) {
        if ($tool -eq 'nvidia-smi') { & $tool --query-gpu=name,compute_cap,memory.total,memory.free,driver_version --format=csv,noheader }
        elseif ($tool -eq 'ollama') { Write-Host "Ollama       $($found.Source)"; if ($Detailed) { & $tool --version } }
        else { Write-Host ("{0,-12} {1}" -f $tool, (& $tool --version | Select-Object -First 1)) }
    } else { Write-Host ("{0,-12} unavailable" -f $tool) }
}
$blender = Get-ChildItem -Path "$env:ProgramFiles/Blender Foundation/Blender */blender.exe" -ErrorAction SilentlyContinue
if ($env:SOCETH_BLENDER) { $blender = Get-Item -LiteralPath $env:SOCETH_BLENDER -ErrorAction SilentlyContinue }
$blender | ForEach-Object { if ($_ -and $Detailed) { & $_.FullName --version | Select-Object -First 1 } else { $_.FullName } }
$edge = Join-Path ${env:ProgramFiles(x86)} 'Microsoft/EdgeWebView/Application'
if (Test-Path -LiteralPath $edge) { Get-ChildItem -LiteralPath $edge -Directory | Select-Object -ExpandProperty Name }
$unity = "$env:ProgramFiles/Unity/Hub/Editor"
if (Test-Path -LiteralPath $unity) { Get-ChildItem -LiteralPath $unity -Directory | ForEach-Object { Write-Host "Unity        $($_.Name)" } }
$python = Join-Path $install.runtime 'app-venv/Scripts/python.exe'
if (Test-Path -LiteralPath $python) {
    & $python (Join-Path $PSScriptRoot 'scripts/setup_studio.py') verify
    exit $LASTEXITCODE
}

