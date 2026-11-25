function Get-SocethPaths {
    param([string]$Root)
    $base = Join-Path $env:LOCALAPPDATA 'Soceth3DStudio'
    $config = if ($env:SOCETH_CONFIG) { $env:SOCETH_CONFIG } else { Join-Path $base 'install.json' }
    $saved = if (Test-Path -LiteralPath $config) { Get-Content -Raw -LiteralPath $config -Encoding UTF8 | ConvertFrom-Json } else { [pscustomobject]@{} }
    $paths = [ordered]@{ config = $config }
    $defaults = [ordered]@{
        runtime = if (Test-Path -LiteralPath (Join-Path $Root 'runtime/venv')) { Join-Path $Root 'runtime' } else { Join-Path $base 'runtime' }
        data = if (Test-Path -LiteralPath (Join-Path $Root 'user_data/studio.sqlite3')) { Join-Path $Root 'user_data' } else { Join-Path $base 'data' }
        projects = $null
        logs = $null
        cache = $null
        models = $null
        comfy_data = $null
        ollama_models = $null
    }
    foreach ($key in $defaults.Keys) {
        $value = [Environment]::GetEnvironmentVariable('SOCETH_' + $key.ToUpperInvariant())
        if (-not $value) { $value = $saved.$key }
        if (-not $value) {
            $value = switch ($key) {
                'projects' { Join-Path $paths.data 'projects' }
                'logs' { Join-Path $paths.data 'logs' }
                'cache' { $paths.runtime }
                'comfy_data' { Join-Path $paths.runtime 'comfy-data' }
                'models' { Join-Path $(if ($saved.comfy_data) { $saved.comfy_data } elseif ($env:SOCETH_COMFY_DATA) { $env:SOCETH_COMFY_DATA } else { Join-Path $paths.runtime 'comfy-data' }) 'models' }
                'ollama_models' {
                    $legacy = Join-Path $paths.runtime 'ollama-models'
                    if (Test-Path -LiteralPath $legacy) { $legacy } elseif ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { Join-Path $env:USERPROFILE '.ollama/models' }
                }
                default { $defaults[$key] }
            }
        }
        $paths[$key] = [IO.Path]::GetFullPath($value)
    }
    return $paths
}

function Set-SocethEnvironment {
    param($Paths)
    foreach ($key in $Paths.Keys) {
        [Environment]::SetEnvironmentVariable('SOCETH_' + $key.ToUpperInvariant(), $Paths[$key], 'Process')
    }
}

function Invoke-SocethChecked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe exited with code $LASTEXITCODE." }
}
