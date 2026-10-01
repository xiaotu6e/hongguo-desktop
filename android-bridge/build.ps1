param([string]$JavaDirectory = $env:JAVA_HOME)
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$cacheDirectory = Join-Path $projectDirectory 'build\bridge-tools'
$classesDirectory = Join-Path $cacheDirectory 'classes'
$manifest = Get-Content -LiteralPath (Join-Path $projectDirectory 'manifests\dependencies.json') -Raw -Encoding UTF8 | ConvertFrom-Json
. (Join-Path $projectDirectory 'scripts\common.ps1')
$compilerJar = Get-VerifiedDownload $manifest.r8 (Join-Path $projectDirectory 'dist\cache')
if (-not $JavaDirectory) {
    $javac = Get-Command javac.exe -ErrorAction SilentlyContinue
    if (-not $javac) { throw 'Install JDK 21 and set JAVA_HOME, or pass -JavaDirectory.' }
    $JavaDirectory = Split-Path -Parent (Split-Path -Parent $javac.Source)
}
New-Item -ItemType Directory -Force -Path $classesDirectory | Out-Null
& (Join-Path $JavaDirectory 'bin\javac.exe') --release 8 -encoding UTF-8 -g:none -d $classesDirectory (Join-Path $PSScriptRoot 'HongguoUi.java')
if ($LASTEXITCODE -ne 0) { throw 'Java bridge compilation failed.' }
$bridgeClasses = @(Get-ChildItem -LiteralPath $classesDirectory -Filter '*.class' | ForEach-Object FullName)
& (Join-Path $JavaDirectory 'bin\java.exe') -cp $compilerJar com.android.tools.r8.D8 --min-api 26 --output (Join-Path $projectDirectory 'src\assets\hongguo-ui-bridge.jar') @bridgeClasses
if ($LASTEXITCODE -ne 0) { throw 'DEX compilation failed.' }
