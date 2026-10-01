param([string]$Repository = 'xiaotu6e/hongguo-desktop', [switch]$WithRelease, [string]$Version = 'v0.1.0')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
if ($Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') { throw 'Invalid repository name.' }
if (-not (Get-Command gh.exe -ErrorAction SilentlyContinue)) { throw 'Install GitHub CLI, then run gh auth login.' }
& gh auth status --hostname github.com
if ($LASTEXITCODE -ne 0) { throw 'Run gh auth login in your terminal, then re-run this script. Do not send tokens in chat.' }
Push-Location $script:ProjectRoot
try {
    if (-not (Test-Path -LiteralPath '.git')) {
        Invoke-Checked 'git' @('init','-b','main')
        Invoke-Checked 'git' @('add','.')
        Invoke-Checked 'git' @('commit','-m','Prepare open-source desktop helper and installer')
    }
    $repoResult = & gh repo view $Repository --json name 2>&1
    if ($LASTEXITCODE -ne 0) {
        Invoke-Checked 'gh' @('repo','create',$Repository,'--public','--source','.','--remote','origin','--push')
    } else {
        $remote = & git remote get-url origin 2>$null
        if ($LASTEXITCODE -ne 0) { Invoke-Checked 'git' @('remote','add','origin',"https://github.com/$Repository.git") }
        elseif ($remote -notmatch ([regex]::Escape($Repository) + '(?:\.git)?$')) { throw 'Existing origin points to a different repository.' }
        # Git's normal fast-forward check protects existing repository history.
        Invoke-Checked 'git' @('push','-u','origin','main')
    }
    if ($WithRelease) {
        $release = Get-Content -LiteralPath 'manifests\release.json' -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($release.version -ne $Version) { throw 'Release manifest version does not match.' }
        $bundle = Join-Path $script:ProjectRoot ('dist\' + $release.filename)
        if ((Get-Sha256 $bundle) -ne $release.sha256) { throw 'Release bundle checksum mismatch.' }
        Invoke-Checked 'git' @('tag',$Version)
        Invoke-Checked 'git' @('push','origin',$Version)
        Invoke-Checked 'gh' @('release','create',$Version,$bundle,(Join-Path $script:ProjectRoot 'dist\SHA256SUMS.txt'),
            (Join-Path $script:ProjectRoot 'dist\release.json'),'--title',("Hongguo Desktop Helper " + $Version),
            '--notes-file',(Join-Path $script:ProjectRoot 'docs\release-notes.md'))
    }
    Write-Host "Published: https://github.com/$Repository"
} finally { Pop-Location }
