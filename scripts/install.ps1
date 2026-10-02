param(
    [ValidateSet('Source','Release')][string]$Mode = 'Source',
    [string]$Version = 'v0.1.2',
    [string]$Repository = 'xiaotu6e/hongguo-desktop',
    [string]$PythonPath = '',
    [string]$BundlePath = '',
    [string]$InstallDirectory = '',
    [string]$ApkPath = '',
    [string]$WsaArchive = '',
    [switch]$InstallWsa,
    [switch]$RepairWsa,
    [switch]$EnableVirtualization,
    [switch]$Offline,
    [switch]$NoShortcut,
    [switch]$Launch,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-WindowsX64
if ($Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$' -or $Version -notmatch '^v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?$') { throw 'Invalid repository or version.' }
if ($DryRun) {
    [ordered]@{ mode=$Mode; version=$Version; repository=$Repository; install_wsa=[bool]$InstallWsa;
        repair_wsa=[bool]$RepairWsa; launch=[bool]$Launch; apk_supplied=[bool]$ApkPath;
        steps=@('Check Windows/WSA','Prepare helper','Optional WSA repair','Optional APK install','Create shortcut','Run doctor') } | ConvertTo-Json -Depth 4
    return
}
if (-not (Get-WsaInstallation)) {
    if (-not $InstallWsa) { throw 'No registered WSA. Re-run with -InstallWsa, or install the documented WSA build first.' }
    & (Join-Path $PSScriptRoot 'install-wsa.ps1') -ArchivePath $WsaArchive -EnableVirtualization:$EnableVirtualization -Offline:$Offline
}
if ($RepairWsa) { & (Join-Path $PSScriptRoot 'repair-wsa.ps1') -Offline:$Offline }
if ($Mode -eq 'Source') {
    & (Join-Path $PSScriptRoot 'prepare-source.ps1') -PythonPath $PythonPath -Offline:$Offline
    $executable = Join-Path $script:ProjectRoot '.venv\Scripts\pythonw.exe'
    $entry = Join-Path $script:ProjectRoot 'src\main.py'
    $launchArguments = '"' + $entry + '"'
    $working = $script:ProjectRoot
    $adb = Join-Path $script:ProjectRoot 'src\assets\platform-tools\adb.exe'
} else {
    $release = Get-Content -LiteralPath (Join-Path $script:ProjectRoot 'manifests\release.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($release.version -ne $Version) { throw 'Release version differs from this checkout. Use the matching source tag.' }
    if (-not $InstallDirectory) { $InstallDirectory = Join-Path (Get-DataDirectory) 'program' }
    $item = [pscustomobject]@{ filename=$release.filename; sha256=$release.sha256;
        url="https://github.com/$Repository/releases/download/$Version/$($release.filename)" }
    if ($BundlePath) {
        $bundle = (Resolve-Path -LiteralPath $BundlePath).Path
        if ((Get-Sha256 $bundle) -ne $item.sha256) { throw 'Release bundle checksum mismatch.' }
    } else { $bundle = Get-VerifiedDownload $item (Join-Path $script:ProjectRoot 'dist\cache') -Offline:$Offline }
    $working = Join-Path ([IO.Path]::GetFullPath($InstallDirectory)) ($Version + '-' + $release.sha256.Substring(0,12))
    $executable = Join-Path $working 'hongguo_desktop.exe'
    if (-not (Test-Path -LiteralPath (Join-Path $working '.installed'))) {
        $stage = $working + '.stage-' + [Guid]::NewGuid().ToString('N')
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $working) | Out-Null
        Expand-SafeZip $bundle $stage
        if (-not (Test-Path -LiteralPath (Join-Path $stage 'hongguo_desktop.exe'))) { throw 'Invalid helper bundle.' }
        if (Test-Path -LiteralPath $working) { throw 'Incomplete installation directory exists. Choose another -InstallDirectory.' }
        New-Item -ItemType File -Path (Join-Path $stage '.installed') | Out-Null
        Move-Item -LiteralPath $stage -Destination $working
    }
    $launchArguments = ''
    $adb = Join-Path $working 'app\assets\platform-tools\adb.exe'
}
Save-InstallRecord ([ordered]@{ version=$Version; mode=$Mode; executable=$executable; arguments=$launchArguments;
    working_directory=$working; adb=$adb; installed_at=[DateTime]::UtcNow.ToString('o') })
$shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
Invoke-Checked $shell @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',(Join-Path $PSScriptRoot 'doctor.ps1'),'-NoConnect')
if (-not $NoShortcut) { New-HelperShortcut $executable $launchArguments $working }
if ($ApkPath) {
    $apk = (Resolve-Path -LiteralPath $ApkPath).Path
    if ([IO.Path]::GetExtension($apk) -ne '.apk') { throw '-ApkPath must point to an APK.' }
    Invoke-Checked $adb @('-P','5038','connect','127.0.0.1:58526')
    $state = & $adb -P 5038 -s 127.0.0.1:58526 get-state 2>&1
    if ($LASTEXITCODE -ne 0 -or ($state | Out-String).Trim() -ne 'device') { throw 'Wake WSA, enable developer mode and allow ADB, then re-run with -ApkPath.' }
    Invoke-Checked $adb @('-P','5038','-s','127.0.0.1:58526','install','-r',$apk)
}
if ($Launch) {
    if ($launchArguments) { Start-Process -FilePath $executable -ArgumentList $launchArguments -WorkingDirectory $working -WindowStyle Hidden }
    else { Start-Process -FilePath $executable -WorkingDirectory $working -WindowStyle Hidden }
}
Write-Host "Helper installed ($Mode). Run scripts/doctor.ps1 and confirm actual playback."
