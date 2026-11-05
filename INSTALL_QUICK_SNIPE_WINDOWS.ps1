param(
    [string]$WoWRoot = '',
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
$roots = @()
if ($WoWRoot) { $roots += $WoWRoot }
$roots += @(
    "${env:ProgramFiles(x86)}\World of Warcraft\_forever_",
    "$env:ProgramFiles\World of Warcraft\_forever_",
    'C:\World of Warcraft\_forever_',
    'D:\World of Warcraft\_forever_',
    'D:\Games\World of Warcraft\_forever_',
    'E:\World of Warcraft\_forever_'
)
$file = $null
foreach ($root in $roots) {
    $candidate = Join-Path $root 'Interface\AddOns\auxForever\tabs\sniper\frame.lua'
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { $file = $candidate; break }
}
if (-not $file) { throw 'auxForever Sniper not found. Re-run with -WoWRoot "D:\...\_forever_"' }
$startMark='-- FOREVER_TRADER_QUICK_SNIPE_BEGIN'
$endMark='-- FOREVER_TRADER_QUICK_SNIPE_END'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$original = [System.IO.File]::ReadAllText($file)
$start = $original.IndexOf($startMark,[StringComparison]::Ordinal)
if ($start -ge 0) {
    $end = $original.IndexOf($endMark,$start,[StringComparison]::Ordinal)
    if ($end -lt 0) { throw 'Found an incomplete existing quick-snipe patch; refused to edit.' }
    $end += $endMark.Length
    $original = ($original.Substring(0,$start).TrimEnd() + "`r`n" + $original.Substring($end).TrimStart())
}
if ($Uninstall) {
    [System.IO.File]::WriteAllText($file,$original,$utf8)
    Write-Host "Removed Forever Trader quick-buy overlay from $file"
    exit 0
}
if ($original -notmatch "select\(2, \.\.\.\) 'aux\.tabs\.sniper'") { throw 'Not a recognized auxForever Sniper module; refused to patch.' }
if ($original -notmatch 'function M\.click_deal\(' -or $original -notmatch 'listing\s*=\s*auction_listing\.new') {
    throw 'Unsupported auxForever version. No files changed.'
}
$snippet = Join-Path $PSScriptRoot 'tools\aux_sniper_quick_ui.lua'
if (-not (Test-Path -LiteralPath $snippet -PathType Leaf)) { throw 'Missing quick-buy snippet.' }
$backup="$file.forevertrader.bak"
if (-not (Test-Path -LiteralPath $backup)) { [System.IO.File]::WriteAllText($backup,$original,$utf8) }
$updated = $original.TrimEnd() + "`r`n`r`n" + [System.IO.File]::ReadAllText($snippet).TrimEnd() + "`r`n"
[System.IO.File]::WriteAllText($file,$updated,$utf8)
Write-Host "Installed user-click-only quick-snipe button into $file"
Write-Host 'Restart WoW. While auxForever Sniper runs, a new below-vendor deal gets a button near your cursor.'
Write-Host 'First click selects/rechecks; further clicks use auxForever Buy/Confirm. /ftquick toggles it.'
