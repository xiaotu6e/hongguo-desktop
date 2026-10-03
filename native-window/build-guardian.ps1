$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$cacheDirectory = Join-Path $projectDirectory 'build\guardian-tools'
New-Item -ItemType Directory -Force -Path $cacheDirectory | Out-Null
$vsWhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vsDirectory = & $vsWhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vsDirectory) { throw 'Visual C++ build tools are required for development.' }
$vcEnvironment = Join-Path $vsDirectory 'VC\Auxiliary\Build\vcvars64.bat'
$sourcePath = Join-Path $PSScriptRoot 'helper_guardian.cpp'
$exePath = Join-Path $projectDirectory 'src\assets\helper-guardian.exe'
$objPath = Join-Path $cacheDirectory 'helper-guardian.obj'
$scriptPath = Join-Path $cacheDirectory 'compile.cmd'
$scriptText = "@call `"$vcEnvironment`" >nul`r`n@cl /nologo /O2 /MT /EHsc /std:c++17 /utf-8 /DUNICODE /D_UNICODE /DNOMINMAX /Fo`"$objPath`" /Fe`"$exePath`" `"$sourcePath`" user32.lib shell32.lib ole32.lib oleaut32.lib uuid.lib wbemuuid.lib /link /SUBSYSTEM:WINDOWS`r`n@exit /b %errorlevel%`r`n"
[System.IO.File]::WriteAllText($scriptPath, $scriptText, [System.Text.Encoding]::Default)
& $env:ComSpec /d /c $scriptPath
if ($LASTEXITCODE -ne 0) { throw 'Background guardian compilation failed.' }
