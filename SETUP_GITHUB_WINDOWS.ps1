# One-time setup of Forever Trader GitHub Releases. Run from the app folder.
# Requires Git and GitHub CLI (gh); gh auth login is interactive.
param([string]$Repository='simon-hirst/wow-forever-trader', [switch]$Private)
$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Install Git for Windows first: https://git-scm.com/download/win' }
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw 'Install GitHub CLI: winget install GitHub.cli' }
& gh auth status
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Please sign into GitHub in the browser when prompted.'
    & gh auth login -h github.com -p https -w
    if ($LASTEXITCODE -ne 0) { throw 'GitHub login failed.' }
}
if (-not (Test-Path '.git')) { & git init }
& git add -- app.py trader tests wow_addon scripts .github README.md .gitignore START_WINDOWS.cmd START_SILENT_WINDOWS.cmd BUILD_WINDOWS_EXE.cmd INSTALL_ADDON_WINDOWS.cmd PUBLISH_RELEASE.ps1 SETUP_GITHUB_WINDOWS.ps1
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
& git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    & git commit -m 'Forever Trader v0.3.0 with automatic trade evidence and source updates'
    if ($LASTEXITCODE -ne 0) { throw 'Could not commit. Configure git user.name and user.email first.' }
}
$remote = & git remote get-url origin 2>$null
if (-not $remote) {
    $visibility='--public'
    if ($Private) { $visibility='--private' }
    & gh repo create $Repository $visibility --source . --remote origin --push
    if ($LASTEXITCODE -ne 0) { throw 'Failed creating/pushing the repository.' }
} else {
    Write-Host "Using existing origin: $remote"
    & git push -u origin HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Git push failed.' }
}
$tag='v0.3.0'
& git tag -l $tag
if ($LASTEXITCODE -ne 0) { throw 'git tag failed' }
if (-not (& git tag -l $tag)) {
    & git tag $tag
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create release tag' }
}
& git push origin $tag
if ($LASTEXITCODE -ne 0) { throw 'Cannot push release tag' }
Write-Host "Release triggered: https://github.com/$Repository/actions" -ForegroundColor Green
Write-Host "Once completed, auto-updates: https://github.com/$Repository/releases" -ForegroundColor Green
