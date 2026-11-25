param(
    [ValidateSet('Ask','Recommended','Existing','Custom','Skip')][string]$Models = 'Ask',
    [string[]]$Model,
    [switch]$WithComfy,
    [switch]$ComfyOnly,
    [ValidateSet('Auto','CUDA126','Nvidia')][string]$ComfyRuntime = 'Auto',
    [switch]$InstallPrerequisites,
    [switch]$NoLaunch,
    [string]$RuntimePath, [string]$DataPath, [string]$ProjectsPath, [string]$ModelsPath,
    [string]$CachePath, [string]$LogsPath, [string]$OllamaModelsPath
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
. (Join-Path $PSScriptRoot 'scripts/paths.ps1')
$paths = Get-SocethPaths $PSScriptRoot
$overrides = @{runtime=$RuntimePath; data=$DataPath; projects=$ProjectsPath; models=$ModelsPath
               cache=$CachePath; logs=$LogsPath; ollama_models=$OllamaModelsPath}
foreach ($key in $overrides.Keys) {
    if ($overrides[$key]) { $paths[$key] = [IO.Path]::GetFullPath($overrides[$key]) }
}
if ($DataPath) {
    if (-not $ProjectsPath) { $paths.projects = Join-Path $paths.data 'projects' }
    if (-not $LogsPath) { $paths.logs = Join-Path $paths.data 'logs' }
}
Set-SocethEnvironment $paths
$runtime = $paths.runtime
$python = Join-Path $runtime 'app-venv/Scripts/python.exe'
$port = if ($env:SOCETH_PORT) { $env:SOCETH_PORT } else { 17863 }
if (Test-Path -LiteralPath (Join-Path $runtime "studio-$port.json")) {
    try { $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/health" -TimeoutSec 2 } catch { $health = $null }
    if ($health.service -eq 'soceth') { throw 'Close Studio before setup or updates.' }
}
foreach ($folder in @($runtime,$paths.data,$paths.projects,$paths.logs,$paths.cache,$paths.models,$paths.comfy_data)) {
    [void][IO.Directory]::CreateDirectory($folder)
}
if (-not (Test-Path -LiteralPath $paths.config) -or ($overrides.Values | Where-Object { $_ })) {
    $configData = if (Test-Path -LiteralPath $paths.config) {
        Get-Content -Raw -LiteralPath $paths.config -Encoding UTF8 | ConvertFrom-Json
    } else { [pscustomobject]@{} }
    foreach ($key in $paths.Keys) {
        if ($key -ne 'config') { $configData | Add-Member -NotePropertyName $key -NotePropertyValue $paths[$key] -Force }
    }
    [void][IO.Directory]::CreateDirectory((Split-Path -Parent $paths.config))
    $configData | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $paths.config -Encoding UTF8
}

function Install-Prerequisite([string]$Name,[string]$Package) {
    if (Get-Command $Name -ErrorAction SilentlyContinue) { return }
    $approved = $InstallPrerequisites -or ($Models -eq 'Ask' -and (Read-Host "Install $Package with Windows Package Manager? [y/N]") -match '^(y|yes)$')
    if (-not $approved -or -not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Install $Package and open a new PowerShell window, or run setup with -InstallPrerequisites."
    }
    Invoke-SocethChecked winget @('install','--id',$Package,'--exact','--source','winget',
        '--accept-package-agreements','--accept-source-agreements','--disable-interactivity')
    $env:Path = (@($env:Path,[Environment]::GetEnvironmentVariable('Path','User'),
        [Environment]::GetEnvironmentVariable('Path','Machine')) -join ';')
}

if (-not $ComfyOnly) {
    $env:PYTHONUTF8 = '1'
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    $env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
    $env:PIP_CACHE_DIR = Join-Path $paths.cache 'pip-cache'
    $env:npm_config_cache = Join-Path $paths.cache 'npm-cache'
    $env:npm_config_update_notifier = 'false'
    if (-not (Test-Path -LiteralPath $python)) {
        $interpreter = $env:SOCETH_BOOTSTRAP_PYTHON
        if (-not $interpreter) {
            $probe = if (Get-Command python -ErrorAction SilentlyContinue) {
                & python -c 'import sys; print(int((3,11) <= sys.version_info[:2] <= (3,13)))' 2>$null
            } else { '0' }
            if ($probe -eq '1') { $interpreter = (Get-Command python).Source }
        }
        if (-not $interpreter -and (Get-Command py -ErrorAction SilentlyContinue)) {
            $interpreter = & py -3.12 -c 'import sys; print(sys.executable)' 2>$null
        }
        if (-not $interpreter) {
            if (-not ($InstallPrerequisites -or ($Models -eq 'Ask' -and (Read-Host 'Install Python 3.12 with winget? [y/N]') -match '^(y|yes)$'))) {
                throw 'Studio requires Python 3.11–3.13. Install Python 3.12 or set SOCETH_BOOTSTRAP_PYTHON.'
            }
            Invoke-SocethChecked winget @('install','--id','Python.Python.3.12','--exact','--source','winget',
                '--accept-package-agreements','--accept-source-agreements','--disable-interactivity')
            $interpreter = & py -3.12 -c 'import sys; print(sys.executable)'
        }
        Invoke-SocethChecked $interpreter @('-m','venv',(Join-Path $runtime 'app-venv'))
    }
    $dependencyKey = ((Get-FileHash requirements.txt,requirements.lock.txt).Hash -join '-')
    $receipt = Join-Path $runtime 'python-dependencies'
    if (-not (Test-Path -LiteralPath $receipt) -or (Get-Content -LiteralPath $receipt -Raw).Trim() -ne $dependencyKey) {
        & $python scripts/check_dependencies.py
        if ($LASTEXITCODE -ne 0) {
            Invoke-SocethChecked $python @('-m','pip','install','-r','requirements.lock.txt')
        }
        Invoke-SocethChecked $python @('-m','pip','check')
        Set-Content -LiteralPath $receipt -Value $dependencyKey
    } else { Write-Host 'Python dependencies are unchanged.' }

    $desktop = Join-Path $PSScriptRoot 'apps/desktop'
    $stamp = Join-Path $desktop 'dist/.soceth-build'
    $source = @(Get-ChildItem -LiteralPath (Join-Path $desktop 'src') -File -Recurse)
    $source += @(Get-Item (Join-Path $desktop 'package-lock.json'),(Join-Path $desktop 'package.json'),
                          (Join-Path $desktop 'index.html'),(Join-Path $desktop 'tsconfig.json'))
    $bytes = [Text.Encoding]::UTF8.GetBytes((($source | Sort-Object FullName | Get-FileHash).Hash -join ''))
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { $desktopKey = [BitConverter]::ToString($hasher.ComputeHash($bytes)).Replace('-','') } finally { $hasher.Dispose() }
    if (-not (Test-Path -LiteralPath $stamp) -or (Get-Content -LiteralPath $stamp -Raw).Trim() -ne $desktopKey) {
        Install-Prerequisite npm.cmd 'OpenJS.NodeJS.LTS'
        $nodeVersion = [version]((& node --version).TrimStart('v').Split('-')[0])
        if ($nodeVersion -lt [version]'22.12') { throw 'Install Node.js 22.12 or later.' }
        Push-Location -LiteralPath $desktop
        try {
            & node (Join-Path $PSScriptRoot 'scripts/check_node_dependencies.cjs')
            if ($LASTEXITCODE -ne 0) { Invoke-SocethChecked npm.cmd @('ci','--no-audit','--no-fund') }
            Invoke-SocethChecked npm.cmd @('run','build')
            Set-Content -LiteralPath $stamp -Value $desktopKey
        } finally { Pop-Location }
    } else { Write-Host 'Desktop sources are unchanged.' }
    Invoke-SocethChecked $python @('scripts/package_addon.py')
}

if (-not (Test-Path -LiteralPath $python)) { throw 'Run application setup before installing a provider.' }
$stateRaw = & $python scripts/setup_studio.py plan
if ($LASTEXITCODE -ne 0) { throw 'Hardware and provider inspection failed.' }
$state = $stateRaw | ConvertFrom-Json
if ($state.hardware.gpus) {
    $state.hardware.gpus | Format-Table name,capability,total,free,driver
}
Write-Host "Recommended profile $($state.tier) · RAM $($state.hardware.ram.total) MiB"
foreach ($row in $state.plan.models) {
    Write-Host ("{0} | {1:N2} GiB download | {2:N2} GiB model storage | {3}" -f $row.name,($row.download_bytes/1GB),($row.disk_bytes/1GB),$row.license)
    Write-Host "$($row.source)  $($row.vram)"
    $state.models.($row.id).reasons | ForEach-Object { Write-Host "  $_" }
}
if ($state.existing.matches) { Write-Host "Existing model candidates: $($state.existing.matches -join ', ')" }
if ($state.hardware.runtime.status -ne 'verified' -and $state.hardware.runtime_archive) {
    Write-Host 'The plan may also require a roughly 2 GiB pinned ComfyUI runtime download and extraction space.'
}
if ($Models -eq 'Ask' -and -not $ComfyOnly) {
    Write-Host '1 Install Recommended Models  2 Customize Models  3 Use Existing Local Models  4 Skip Model Installation'
    $choice = Read-Host 'Choose an option [4]'
    $Models = switch ($choice) { '1' { 'Recommended' } '2' { 'Custom' } '3' { 'Existing' } default { 'Skip' } }
    if ($Models -eq 'Custom') {
        $state.catalog |
            Where-Object { $_.operation -in 'generate','concept','interpret' } |
            Format-Table id,name,license,disk_bytes
        $Model = (Read-Host 'Enter comma-separated catalog IDs') -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    }
}
if ($Models -eq 'Recommended') { $Model = @($state.plan.models | ForEach-Object { $_.id }) }
if ($Models -eq 'Custom' -and -not $Model) { throw 'Select at least one model ID with -Model.' }
$catalog = @($state.catalog)
$chosen = @($catalog | Where-Object { $_.id -in $Model })
if ($Model -and @($Model | Where-Object { $_ -notin $chosen.id })) { throw 'The selection includes an unknown model ID.' }

if ($WithComfy -or $ComfyOnly -or (($Models -in 'Recommended','Custom') -and ($chosen.provider -contains 'comfy'))) {
    if ($state.hardware.runtime.status -eq 'verified' -and $ComfyRuntime -eq 'Auto') {
        Write-Host 'Reusing the PyTorch runtime that passed the CUDA kernel check.'
    } elseif (-not $state.dependencies.comfy -or $ComfyRuntime -ne 'Auto') {
        $variant = switch ($ComfyRuntime) { 'CUDA126' { 'cu126' } 'Nvidia' { 'cu130' } default { $state.hardware.runtime_archive } }
        $bundles = @{
            cu130=@{name='ComfyUI_windows_portable_nvidia.7z'; size=2085169944; sha='6a5255c7ec01fdc54bdbd8e4507073cf6acda2fbeea74ae1829a50681af5fac6'}
            cu126=@{name='ComfyUI_windows_portable_nvidia_cu126.7z'; size=1976423950; sha='06ef2069702a7d7db7262e29a70ec524207f86c7bc4212f9dfcb7c977bf3e9ed'}
        }
        if (-not $variant -or -not $bundles.ContainsKey($variant)) { throw 'No pinned CUDA runtime matches the detected GPU and driver.' }
        $bundle = $bundles[$variant]
        $target = Join-Path $runtime "ComfyUI-$variant"
        $entry = Join-Path $target 'ComfyUI_windows_portable/ComfyUI/main.py'
        if (-not (Test-Path -LiteralPath $entry)) {
            Write-Host ("ComfyUI runtime {0} · {1:N2} GiB download and additional extraction space" -f $variant,($bundle.size/1GB))
            $archive = Join-Path $paths.cache ('v0.46.0-' + $bundle.name)
            $url = "https://github.com/Comfy-Org/ComfyUI/releases/download/v0.46.0/$($bundle.name)"
            $length = if (Test-Path -LiteralPath $archive) { (Get-Item -LiteralPath $archive).Length } else { 0 }
            if ($length -lt $bundle.size) { Invoke-SocethChecked curl.exe @('-fsSL','--retry','2','-C','-','-o',$archive,$url) }
            if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $bundle.sha) {
                throw "Runtime hash mismatch at $archive. Move that archive aside and retry."
            }
            $staging = Join-Path $runtime ("provider-staging-" + [guid]::NewGuid().ToString('N'))
            [void][IO.Directory]::CreateDirectory($staging)
            Invoke-SocethChecked tar.exe @('-xf',$archive,'-C',$staging)
            if (-not (Test-Path -LiteralPath (Join-Path $staging 'ComfyUI_windows_portable/ComfyUI/main.py'))) {
                throw "The pinned provider did not extract as expected in $staging."
            }
            if (Test-Path -LiteralPath $target) { throw "Choose another runtime path or move the incomplete directory aside: $target" }
            Move-Item -LiteralPath $staging -Destination $target
        }
    }
    $checks = & $python scripts/setup_studio.py verify | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $checks.runtime.status -ne 'verified') {
        throw 'The CUDA kernel smoke test failed. Existing runtimes and weights were preserved.'
    }
    Write-Host "CUDA kernel verified with PyTorch $($checks.runtime.torch), CUDA $($checks.runtime.cuda)."
}
if (-not $ComfyOnly -and $Models -ne 'Skip') {
    if (($chosen.provider -contains 'ollama') -and -not $state.dependencies.ollama) {
        Install-Prerequisite ollama 'Ollama.Ollama'
    }
    $apply = @('scripts/setup_studio.py','apply','--mode',$Models.ToLowerInvariant())
    foreach ($item in $Model) { $apply += @('--model',$item) }
    Invoke-SocethChecked $python $apply
}
Write-Host 'Setup complete.'
if (-not $NoLaunch -and -not $ComfyOnly) { & (Join-Path $PSScriptRoot 'start.ps1') }

