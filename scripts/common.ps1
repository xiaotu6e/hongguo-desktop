Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:ProjectRoot = Split-Path -Parent $PSScriptRoot

# PowerShell 7 callers may pass a module path containing incompatible modules
# to Windows PowerShell 5.1 subprocesses. Prefer its own built-in modules.
if ($PSVersionTable.PSVersion.Major -le 5) {
    $env:PSModulePath = (Join-Path $PSHOME 'Modules') + ';' +
        (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\Modules') + ';' + $env:PSModulePath
}

function Get-Sha256 {
    param([string]$Path)
    $stream = [IO.File]::OpenRead($Path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $algorithm.Dispose() }
}

function Get-DependencyManifest {
    Get-Content -LiteralPath (Join-Path $script:ProjectRoot 'manifests\dependencies.json') -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Assert-WindowsX64 {
    if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitOperatingSystem -or
        $env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') {
        throw 'This release requires Windows 11 x64.'
    }
}

function Get-WsaInstallation {
    $shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $query = '$ProgressPreference="SilentlyContinue"; $env:PSModulePath=($PSHOME+"\Modules;")+($env:SystemRoot+"\System32\WindowsPowerShell\v1.0\Modules;")+$env:PSModulePath; $ErrorActionPreference="Stop"; [Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-AppxPackage -Name MicrosoftCorporationII.WindowsSubsystemForAndroid | Select-Object -First 1 InstallLocation,PackageFamilyName,Version | ConvertTo-Json -Compress'
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($query))
    $output = & $shell -NoProfile -NonInteractive -EncodedCommand $encoded
    if ($LASTEXITCODE -ne 0) { throw 'Unable to query WSA registration.' }
    if ($output) { return ($output | Out-String | ConvertFrom-Json) }
    return $null
}

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Executable" }
}

function Get-VerifiedDownload {
    param($Item, [string]$CacheDirectory, [switch]$Offline)
    if ($Item.sha256 -notmatch '^[0-9a-fA-F]{64}$') { throw 'Missing download SHA-256.' }
    New-Item -ItemType Directory -Force -Path $CacheDirectory | Out-Null
    $destination = Join-Path $CacheDirectory $Item.filename
    if (Test-Path -LiteralPath $destination) {
        if ((Get-Sha256 $destination) -eq $Item.sha256) { return $destination }
        throw "Cached file checksum mismatch: $destination. Remove this file and retry."
    }
    if ($Offline) { throw "Offline cache is missing: $($Item.filename)" }
    if ($Item.url -notmatch '^https://') { throw 'Downloads require HTTPS.' }
    $partial = $destination + '.' + [Guid]::NewGuid().ToString('N') + '.partial'
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $savedProgress = $ProgressPreference
        $ProgressPreference = 'SilentlyContinue'
        try { Invoke-WebRequest -UseBasicParsing -Uri $Item.url -OutFile $partial } finally { $ProgressPreference = $savedProgress }
        if ((Get-Sha256 $partial) -ne $Item.sha256) {
            throw "Downloaded file checksum mismatch: $($Item.filename)"
        }
        Move-Item -LiteralPath $partial -Destination $destination
    } finally {
        if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial }
    }
    return $destination
}

function Expand-SafeZip {
    param([string]$Archive, [string]$Destination)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $root = [IO.Path]::GetFullPath($Destination).TrimEnd('\') + '\'
    $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        foreach ($entry in $zip.Entries) {
            $target = [IO.Path]::GetFullPath((Join-Path $root $entry.FullName))
            if (-not $target.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
                throw "ZIP entry escapes installation directory: $($entry.FullName)"
            }
        }
    } finally { $zip.Dispose() }
    [IO.Compression.ZipFile]::ExtractToDirectory($Archive, $Destination)
}

function Get-DataDirectory {
    Join-Path $env:LOCALAPPDATA 'HongguoDesktopHelper'
}

function Get-InstallRecord {
    $path = Join-Path (Get-DataDirectory) 'installation.json'
    if (Test-Path -LiteralPath $path) { return (Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json) }
    return $null
}

function Save-InstallRecord {
    param($Record)
    $directory = Get-DataDirectory
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    $path = Join-Path $directory 'installation.json'
    $Record | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath ($path + '.tmp') -Encoding UTF8
    Move-Item -LiteralPath ($path + '.tmp') -Destination $path -Force
}

function New-HelperShortcut {
    param([string]$Executable, [string]$Arguments = '', [string]$WorkingDirectory, [string]$IconExecutable = '')
    $shell = New-Object -ComObject WScript.Shell
    foreach ($directory in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
        $shortcut = $shell.CreateShortcut((Join-Path $directory 'Hongguo Desktop Helper.lnk'))
        $shortcut.TargetPath = $Executable
        $shortcut.Arguments = $Arguments
        $shortcut.WorkingDirectory = $WorkingDirectory
        if ($IconExecutable) { $shortcut.IconLocation = $IconExecutable + ',0' }
        $shortcut.Description = 'Hongguo Desktop Helper - WSA'
        $shortcut.Save()
    }
}
