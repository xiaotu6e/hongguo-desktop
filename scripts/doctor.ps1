param([switch]$Json, [switch]$NoConnect)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-WindowsX64
$checks = New-Object System.Collections.Generic.List[object]
function Add-Check { param($Name,$Status,$Detail); $checks.Add([pscustomobject]@{name=$Name;status=$Status;detail=$Detail}) }
$record = Get-InstallRecord
if ($record -and (Test-Path -LiteralPath $record.executable)) { Add-Check 'helper' 'ok' $record.mode }
else { Add-Check 'helper' 'missing' 'Run scripts/install.ps1.' }
$wsa = Get-WsaInstallation
if ($wsa) { Add-Check 'wsa' 'ok' ([string]$wsa.Version) }
else { Add-Check 'wsa' 'missing' 'Install the documented WSA build.' }
if ($record -and (Test-Path -LiteralPath $record.adb)) {
    Add-Check 'adb' 'ok' 'Connection component is present.'
    if (-not $NoConnect) {
        $null = & $record.adb -P 5038 connect 127.0.0.1:58526 2>&1
        $state = (& $record.adb -P 5038 -s 127.0.0.1:58526 get-state 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -eq 0 -and $state -eq 'device') {
            Add-Check 'connection' 'ok' '127.0.0.1:58526'
            $apk = & $record.adb -P 5038 -s 127.0.0.1:58526 shell pm path com.phoenix.read 2>&1
            if ($LASTEXITCODE -eq 0 -and ($apk | Out-String) -match 'package:') { Add-Check 'hongguo' 'ok' 'Installed.' }
            else { Add-Check 'hongguo' 'missing' 'Supply your APK or drag it into the helper.' }
        } else { Add-Check 'connection' 'attention' 'Wake WSA, enable developer mode and accept the ADB prompt.' }
    }
} else { Add-Check 'adb' 'missing' 'Re-run installation.' }
Add-Check 'playback' 'manual' 'Confirm picture, sound, episode switching and orientation in the app.'
$report = [ordered]@{ checks=$checks.ToArray(); installation_present=($null -ne $record) }
if ($Json) { $report | ConvertTo-Json -Depth 5 } else { $checks | Format-Table -AutoSize }
if (@($checks | Where-Object { $_.status -eq 'missing' }).Count) { exit 2 }
if (@($checks | Where-Object { $_.status -eq 'attention' }).Count) { exit 3 }
