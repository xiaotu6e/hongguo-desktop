param([string]$PythonPath = '', [switch]$Offline)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-WindowsX64
$python = Join-Path $script:ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    if ($PythonPath) { $bootstrap = $PythonPath }
    elseif (Get-Command python.exe -ErrorAction SilentlyContinue) { $bootstrap = (Get-Command python.exe).Source }
    else { throw 'Install Python 3.11+ (python.org), add it to PATH, and retry; or pass -PythonPath.' }
    Invoke-Checked $bootstrap @('-c', 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"')
    Invoke-Checked $bootstrap @('-m', 'venv', (Join-Path $script:ProjectRoot '.venv'))
}
$arguments = @('-m', 'pip', 'install', '-r', (Join-Path $script:ProjectRoot 'requirements.lock.txt'))
if ($Offline) { $arguments += '--no-index' }
Invoke-Checked $python $arguments
$dependencies = Get-DependencyManifest
$cache = Join-Path $script:ProjectRoot 'dist\cache'
$archive = Get-VerifiedDownload $dependencies.adb $cache -Offline:$Offline
$adb = Join-Path $script:ProjectRoot 'src\assets\platform-tools\adb.exe'
if (-not (Test-Path -LiteralPath $adb)) {
    $stage = Join-Path $cache ('adb-' + [Guid]::NewGuid().ToString('N'))
    Expand-SafeZip $archive $stage
    Move-Item -LiteralPath (Join-Path $stage 'platform-tools') -Destination (Join-Path $script:ProjectRoot 'src\assets\platform-tools')
}
Write-Host "Source environment ready: $python"
