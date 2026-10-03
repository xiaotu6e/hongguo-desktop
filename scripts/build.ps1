param([string]$PythonPath = '', [switch]$PrepareOnly)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
& (Join-Path $PSScriptRoot 'prepare-source.ps1') -PythonPath $PythonPath
$python = Join-Path $script:ProjectRoot '.venv\Scripts\python.exe'
Invoke-Checked $python @('-m','pip','install','-r',(Join-Path $script:ProjectRoot 'requirements-build.txt'))
Push-Location $script:ProjectRoot
try {
    Invoke-Checked $python @('-m','unittest','discover','-s','tests','-v')
    if ($PrepareOnly) { return }
    & (Join-Path $script:ProjectRoot 'native-window\build.ps1')
    & (Join-Path $script:ProjectRoot 'native-window\build-guardian.ps1')
    & (Join-Path $script:ProjectRoot 'android-bridge\build.ps1')
    Invoke-Checked $python @('-m','flet.cli','build','windows','--output','app','--yes','--no-rich-output')
    Copy-Item -LiteralPath (Join-Path $script:ProjectRoot 'src\assets\helper-guardian.exe') -Destination (Join-Path $script:ProjectRoot 'app\helper-guardian.exe') -Force
    & (Join-Path $PSScriptRoot 'package-release.ps1') -AppDirectory (Join-Path $script:ProjectRoot 'app')
} finally { Pop-Location }
