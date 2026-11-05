param(
  [Parameter(Mandatory=$true)][ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version,
  [string]$Message = "Forever Trader v$Version"
)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path .git)) { throw 'Create and push a GitHub repository first (see README.md).' }
if (-not (git remote get-url origin 2>$null)) { throw 'Git remote origin is missing.' }
if (git tag -l "v$Version") { throw "Tag v$Version already exists." }
$versionFile = Join-Path $PSScriptRoot 'trader\__init__.py'
$content = Get-Content $versionFile -Raw
if ($content -notmatch 'VERSION\s*=\s*') { throw 'VERSION not found.' }
$content = [regex]::Replace($content, 'VERSION\s*=\s*[^\r\n]*', "VERSION = '$Version'")
Set-Content $versionFile $content -Encoding UTF8
# Add only project files: never stage WoW SavedVariables, exports or secrets.
git add -- app.py trader tests wow_addon scripts INSTALL_ADDON_WINDOWS.cmd SETUP_GITHUB_WINDOWS.ps1 README.md .gitignore .github START_WINDOWS.cmd START_SILENT_WINDOWS.cmd BUILD_WINDOWS_EXE.cmd PUBLISH_RELEASE.ps1
if ($LASTEXITCODE -ne 0) { throw 'git add failed.' }
git commit -m $Message
if ($LASTEXITCODE -ne 0) { throw 'No staged changes or git commit failed. Make a change before releasing.' }
git push origin HEAD
if ($LASTEXITCODE -ne 0) { throw 'Push failed. Fix before tagging.' }
git tag "v$Version"
if ($LASTEXITCODE -ne 0) { throw 'Tagging failed.' }
git push origin "v$Version"
if ($LASTEXITCODE -ne 0) { throw 'Tag push failed.' }
Write-Host "Published tag v$Version. GitHub Actions will build ForeverTrader.exe and publish the update." -ForegroundColor Green
