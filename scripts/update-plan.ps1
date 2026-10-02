param([string]$CurrentVersion = '', [switch]$Json)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-WindowsX64
[Console]::OutputEncoding = [Text.Encoding]::UTF8

function Read-Version {
    param([string]$Value)
    if ($Value -notmatch '^v?([0-9]+\.[0-9]+\.[0-9]+)$') { throw "Unsupported version: $Value" }
    return [Version]$Matches[1]
}

$updates = Get-Content -LiteralPath (Join-Path $script:ProjectRoot 'manifests\updates.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$release = Get-Content -LiteralPath (Join-Path $script:ProjectRoot 'manifests\release.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($updates.schema_version -ne 1 -or $updates.latest_helper_version -ne $release.version) { throw 'Update metadata does not match the release manifest.' }
$target = Read-Version $release.version
$record = Get-InstallRecord
$versionSource = 'explicit'
if (-not $CurrentVersion) {
    $versionSource = 'unknown'
    if ($record) { $CurrentVersion = [string]$record.version; $versionSource = 'installation_record' }
}
$current = $null
if ($CurrentVersion) { $current = Read-Version $CurrentVersion; $CurrentVersion = 'v' + $current.ToString() }
$status = 'unknown'
if ($current) {
    if ($current -lt $target) { $status = 'update_required' }
    elseif ($current -eq $target) { $status = 'current' }
    else { $status = 'newer_than_manifest' }
}
$changes = @()
if ($current -and $current -lt $target) {
    $changes = @($updates.releases | Where-Object {
        $entryVersion = Read-Version $_.version
        $entryVersion -gt $current -and $entryVersion -le $target
    } | Sort-Object { Read-Version $_.version })
}
$components = @($changes | ForEach-Object { $_.components })
$report = [ordered]@{
    status = $status
    current_version = $(if ($current) { $CurrentVersion } else { $null })
    version_source = $versionSource
    target_version = $release.version
    changes = $changes
    components_to_update_or_review = $components
    documentation_updated_at = $updates.documentation_updated_at
    documentation_changes = @($updates.documentation_changes)
    requires_helper_reinstall = ($status -eq 'update_required')
    must_verify_actual_executable_or_source = $true
    unknown_version_action = $(if ($status -eq 'unknown') { 'Check the real shortcut, executable version or source entry; then pass -CurrentVersion.' } else { $null })
    guide = 'docs/updating.md'
    modifies_system = $false
}
if ($Json) { $report | ConvertTo-Json -Depth 8 }
else {
    Write-Output ("Status: {0}; current: {1}; target: {2}" -f $status, $CurrentVersion, $release.version)
    foreach ($change in $changes) {
        Write-Output ($change.version + ': ' + $change.summary)
        $change.components | Format-Table id,action,reason -Wrap
        Write-Output ('Unchanged: ' + ($change.unchanged_components -join ', '))
        Write-Output ('Preserve: ' + ($change.preserve -join ', '))
    }
    if ($status -eq 'unknown') { Write-Output $report.unknown_version_action }
    Write-Output 'Verify the actual shortcut and running program version. See docs/updating.md.'
}
