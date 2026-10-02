param([Parameter(Mandatory=$true)][string]$AppDirectory, [string]$Version = 'v0.1.2')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
if ($Version -notmatch '^v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?$') { throw 'Invalid version.' }
$app = (Resolve-Path -LiteralPath $AppDirectory).Path
if (-not (Test-Path -LiteralPath (Join-Path $app 'hongguo_desktop.exe'))) { throw 'App directory lacks hongguo_desktop.exe.' }
$destination = Join-Path $script:ProjectRoot 'dist'
New-Item -ItemType Directory -Force -Path $destination | Out-Null
$filename = "hongguo-desktop-$Version-windows-x64.zip"
$archive = Join-Path $destination $filename
if (Test-Path -LiteralPath $archive) { throw 'Release archive already exists. Use a new version or remove that archive first.' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::CreateFromDirectory($app,$archive,[IO.Compression.CompressionLevel]::Optimal,$false)
$hash = (Get-Sha256 $archive).ToLowerInvariant()
$record = [ordered]@{version=$Version;filename=$filename;sha256=$hash;bytes=(Get-Item -LiteralPath $archive).Length}
$record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $script:ProjectRoot 'manifests\release.json') -Encoding UTF8
"$hash  $filename" | Set-Content -LiteralPath (Join-Path $destination 'SHA256SUMS.txt') -Encoding ASCII
Copy-Item -LiteralPath (Join-Path $script:ProjectRoot 'manifests\release.json') -Destination (Join-Path $destination 'release.json')
Write-Host "Release package: $archive"
