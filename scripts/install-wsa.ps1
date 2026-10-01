param([string]$ArchivePath = '', [switch]$EnableVirtualization, [switch]$Offline)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-WindowsX64
if (Get-WsaInstallation) { Write-Host 'WSA is already registered; preserving existing installation.'; return }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$administrator = ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $administrator) { throw 'Installing WSA requires an administrator PowerShell. Re-run the same command there.' }
if ([Environment]::OSVersion.Version.Build -lt 22000) { throw 'Automatic WSA setup is supported on Windows 11 x64 only.' }
$feature = Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform
if ($feature.State -ne 'Enabled') {
    if (-not $EnableVirtualization) { throw 'VirtualMachinePlatform is disabled. Re-run with -EnableVirtualization, then restart Windows.' }
    Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -All -NoRestart | Out-Null
    throw 'VirtualMachinePlatform was enabled. Restart Windows, then re-run installation. No restart was forced.'
}
$disk = Get-Volume -DriveLetter ([IO.Path]::GetPathRoot($env:LOCALAPPDATA).Substring(0,1))
if ($disk.FileSystem -ne 'NTFS') { throw 'The WSA installation requires an NTFS disk.' }
$dependencies = Get-DependencyManifest
$cache = Join-Path $script:ProjectRoot 'dist\cache'
if ($ArchivePath) {
    $archive = (Resolve-Path -LiteralPath $ArchivePath).Path
    if ((Get-Sha256 $archive) -ne $dependencies.wsa.sha256) { throw 'WSA archive does not match the supported version.' }
} else { $archive = Get-VerifiedDownload $dependencies.wsa $cache -Offline:$Offline }
$sevenZip = Get-VerifiedDownload $dependencies.seven_zip $cache -Offline:$Offline
$wsaRoot = Join-Path (Get-DataDirectory) 'wsa\2407.40000.4.0-lts8'
if (-not (Test-Path -LiteralPath $wsaRoot)) {
    New-Item -ItemType Directory -Force -Path $wsaRoot | Out-Null
    Invoke-Checked $sevenZip @('x', $archive, ('-o' + $wsaRoot), '-y')
}
$manifests = @(Get-ChildItem -LiteralPath $wsaRoot -Recurse -Filter AppxManifest.xml -File)
if ($manifests.Count -ne 1) { throw 'The WSA archive layout is incomplete or unexpected.' }
$upstreamInstaller = Join-Path $manifests[0].DirectoryName 'Install.ps1'
if (-not (Test-Path -LiteralPath $upstreamInstaller)) { throw 'Upstream WSA installer is missing.' }
# Use the installer shipped by the hash-verified upstream; it handles Appx dependencies.
# It may display Windows prompts. Its files are not copied into this source repository.
$shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
Push-Location $manifests[0].DirectoryName
try { Invoke-Checked $shell @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $upstreamInstaller) }
finally { Pop-Location }
if (-not (Get-WsaInstallation)) { throw 'WSA registration was not completed. Inspect the upstream installer output.' }
Write-Host 'WSA registered. Keep its installation directory; enable developer mode in WSA Settings.'
