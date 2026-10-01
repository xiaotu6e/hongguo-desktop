param([string]$Distribution = 'Ubuntu', [switch]$Restore, [switch]$PrepareOnly, [switch]$Offline, [switch]$DryRun)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-WindowsX64
$installation = Get-WsaInstallation
if (-not $installation) { throw 'No registered WSA.' }
$root = [IO.Path]::GetFullPath($installation.InstallLocation)
$manifestPath = Join-Path $script:ProjectRoot 'manifests\wsa-compatibility.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$backup = Join-Path (Get-DataDirectory) 'wsa-backup\2407.40000.4.0-lts8'
if ($DryRun) {
    [ordered]@{ installation=$root; backup=$backup; restore=[bool]$Restore; prepare_only=[bool]$PrepareOnly;
        required_original_sha256=$manifest.images; distribution=$Distribution } | ConvertTo-Json -Depth 6
    return
}
$markerPath = Join-Path $backup 'deployed.json'
$knownInstalled = @{}
if (Test-Path -LiteralPath $markerPath) { $marker = Get-Content -LiteralPath $markerPath -Raw -Encoding UTF8 | ConvertFrom-Json }
else { $marker = $null }
$alreadyPatched = $true
foreach ($name in @('system','vendor')) {
    $hash = (Get-Sha256 (Join-Path $root ($name + '.vhdx'))).ToLowerInvariant()
    $knownInstalled[$name] = $hash
    $recorded = $manifest.images.$name.installed_sha256
    $generated = if ($marker) { $marker.images.$name.sha256 } else { '' }
    if ($hash -ne $recorded -and $hash -ne $generated) { $alreadyPatched = $false }
}
if (-not $Restore -and $alreadyPatched) { Write-Host 'The recorded compatibility repair is already installed.'; return }
foreach ($name in @('system','vendor')) {
    if (-not $Restore -and $knownInstalled[$name] -ne $manifest.images.$name.original_sha256) {
        throw "Unsupported or modified $name.vhdx. Only the documented original WSA build can be repaired."
    }
    if ($Restore -and $knownInstalled[$name] -ne $manifest.images.$name.original_sha256 -and
        $knownInstalled[$name] -ne $manifest.images.$name.installed_sha256 -and
        (-not $marker -or $knownInstalled[$name] -ne $marker.images.$name.sha256)) {
        throw "Unknown $name.vhdx. Refusing to restore across an unrecognized WSA modification."
    }
    $path = Join-Path $root ($name + '.vhdx')
    try { $handle = [IO.File]::Open($path, 'Open', 'ReadWrite', 'None'); $handle.Dispose() }
    catch { throw 'Shut down the Android subsystem in WSA Settings, then retry; an image is locked or not writable.' }
}
if ($Restore) {
    foreach ($name in @('system','vendor')) {
        $original = Join-Path $backup ($name + '.vhdx')
        if (-not (Test-Path -LiteralPath $original) -or (Get-Sha256 $original) -ne $manifest.images.$name.original_sha256) {
            throw 'A verified original backup is required for restoration.'
        }
    }
    $candidates = $backup
    $candidateManifest = [pscustomobject]@{images=$manifest.images}
} else {
    $dependencies = Get-DependencyManifest
    $archive = Get-VerifiedDownload $dependencies.autobridge (Join-Path $script:ProjectRoot 'dist\cache') -Offline:$Offline
    $work = Join-Path (Get-DataDirectory) ('wsa-work\' + [Guid]::NewGuid().ToString('N'))
    $wsl = (Get-Command wsl.exe -ErrorAction Stop).Source
    function Convert-WslPath {
        param([string]$Path)
        $value = & $wsl -d $Distribution -u root -- wslpath -a $Path
        if ($LASTEXITCODE -ne 0) { throw 'WSL distribution unavailable. See docs/compatibility.md.' }
        return ($value | Out-String).Trim()
    }
    $arguments = @('-d',$Distribution,'-u','root','--','python3',
        (Convert-WslPath (Join-Path $PSScriptRoot 'prepare_wsa_images.py')),
        '--installation',(Convert-WslPath $root),'--backup',(Convert-WslPath $backup),
        '--work',(Convert-WslPath $work),'--source-archive',(Convert-WslPath $archive),
        '--manifest',(Convert-WslPath $manifestPath),
        '--dependencies',(Convert-WslPath (Join-Path $script:ProjectRoot 'manifests\dependencies.json')))
    Invoke-Checked $wsl $arguments
    $candidates = $work
    $candidateManifest = Get-Content -LiteralPath (Join-Path $work 'candidate-manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($PrepareOnly) { Write-Host "Prepared candidates, not deployed: $work"; return }
}
# Recheck both images after preparation to catch a concurrent WSA change/start.
foreach ($name in @('system','vendor')) {
    $target = Join-Path $root ($name + '.vhdx')
    if ((Get-Sha256 $target) -ne $knownInstalled[$name]) { throw 'WSA image changed during preparation; nothing deployed.' }
    $handle = [IO.File]::Open($target, 'Open', 'ReadWrite', 'None'); $handle.Dispose()
    $expected = if ($Restore) { $manifest.images.$name.original_sha256 } else { $candidateManifest.images.$name.sha256 }
    if ((Get-Sha256 (Join-Path $candidates ($name + '.vhdx'))) -ne $expected) { throw 'Candidate checksum mismatch.' }
}
$transaction = [Guid]::NewGuid().ToString('N')
$swapped = New-Object System.Collections.Generic.List[string]
try {
    foreach ($name in @('system','vendor')) {
        $target = Join-Path $root ($name + '.vhdx')
        $stage = $target + '.new-' + $transaction
        Copy-Item -LiteralPath (Join-Path $candidates ($name + '.vhdx')) -Destination $stage
        $expected = if ($Restore) { $manifest.images.$name.original_sha256 } else { $candidateManifest.images.$name.sha256 }
        if ((Get-Sha256 $stage) -ne $expected) { throw 'Staged image checksum mismatch.' }
        Move-Item -LiteralPath $target -Destination ($target + '.previous-' + $transaction)
        $swapped.Add($name)
        Move-Item -LiteralPath $stage -Destination $target
    }
} catch {
    foreach ($name in $swapped) {
        $target = Join-Path $root ($name + '.vhdx')
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target }
        Move-Item -LiteralPath ($target + '.previous-' + $transaction) -Destination $target
    }
    throw
}
foreach ($name in $swapped) { Remove-Item -LiteralPath (Join-Path $root ($name + '.vhdx.previous-' + $transaction)) }
if ($Restore) {
    if (Test-Path -LiteralPath $markerPath) { Remove-Item -LiteralPath $markerPath }
    Write-Host 'Original system/vendor images restored. Android user data was not replaced.'
} else {
    $candidateManifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $markerPath -Encoding UTF8
    Write-Host 'Compatibility images deployed. Start WSA and verify real playback.'
}
