# Forever Trader v0.3.3 — WoW Forever 1.60.1 desktop market terminal

## v0.3.3 — quick-sniper selection fix and safeguards

- Fixed a bug that could leave the optional quick-buy overlay stuck at CHECK because the Sniper's internal selection is private. The overlay now reads the actual selected row.
- All clicks still go through auxForever's live refresh and original Buy / Confirm mechanism.
- Limits overlay commodity orders to at most five units, estimates spending against a 5s reserve, and disables the overlay's Confirm when the actual server quote would violate that reserve.
- Prevents switching to another item while the existing buy bar has an outstanding server quote.
- Patch auxForever **separately**, with WoW closed: `powershell -ExecutionPolicy Bypass -File .\INSTALL_QUICK_SNIPE_WINDOWS.ps1`. Use `-Uninstall` to remove it.
- The in-game overlay has not been tested in the user's live client; begin with one low-value vendor-only flip.

## v0.3.2 — next-session trading improvements

- Merchant sale tracking waits for both bag changes and wallet changes before confirming a vendor payout. It caches item-link NPC prices while items are still in your bags. Mixed purchases/sales are **REVIEW**, not fictitious profit.
- AH commodity purchase detection listens to successful-purchase messages and captures your clicked quantity from the existing game API (without making purchases). Extra evidence may turn previous missing IDs into confirmed receipts.
- New `wallet_changes.csv` export tracks each observed wallet delta, character, time and context (AH, MERCHANT or WORLD). It helps distinguish an actual loss from a stale saved balance; `/reload` is still required.
- Optional `INSTALL_QUICK_SNIPE_WINDOWS.ps1` adds a **manual-click, cursor-adjacent vendor-deal button** to an installed auxForever Sniper. It selects a deal, waits for auxForever's existing live price check, then uses the original Buy/Confirm button logic on your separate clicks. Nothing buys itself; not tested in your live client. Restart WoW after patching. To remove it, use `-Uninstall`.
- No retroactive reconstruction of old trades. Use fresh deals after updating to validate receipts.

## v0.3.1 — Windows release build fix

- Explicit SQLite database lifetime management and guaranteed test cleanup on Windows.
- Close the app database connection on window close and before applying source updates.
- Fixes WinError 32 during GitHub Actions tests; no trading or SavedVariables data changes.


A **Windows 11 desktop trading-research app** for WoW Forever's Auction House, tested with Auctionator **dbversion 8 CBOR** `Auctionator.lua` market data and optional `auxForever.lua` item names. Designed for the early EU Horde PvP economy with a starting bankroll of **43s 38c** with a default aggressive 5s reserve; these settings are editable.

## NEW in v0.3.0 — automatic trading receipts and wallet tracking

**The Bridge addon has changed. You MUST reinstall `wow_addon/ForeverTraderBridge` by running `INSTALL_ADDON_WINDOWS.cmd` with WoW closed.** Then restart WoW. The Windows app still reads the same SavedVariables file.

1. When you use auxForever Sniper to buy an item, the Bridge listens to the official `COMMODITY_PURCHASED`, `ITEM_PURCHASED`, and `AUCTION_HOUSE_PURCHASE_COMPLETED` events. It watches the wallet debits. Only a purchase matched to **one** debit and a known quantity is a **CONFIRMED AH BUY**.
2. When you sell at an NPC merchant, the Bridge compares your before/after bag contents and wallet. Only item decreases whose known NPC prices exactly explain the wallet gain become **CONFIRMED NPC SELL** entries. Other changes are marked REVIEW, never automatically counted as profit.
3. Run `/ftmoney` in game to see the last wallet balance and transaction event count. Use `/reload` or logout to save data to disk. The Windows **AUTO ACTIVITY** tab then imports it and displays confirmed/reviewed events. Confirmed events are automatically written once into the existing FIFO Trade Journal (no manual duplicates).
4. Confirmed buy/sell matches generate measured *realised vendor trading profit* without AH selling fees. REVIEW events are excluded; use **Verify selected receipt** only when you have checked its actual item, quantity and total copper. Non-item-specific wallet changes require manual bookkeeping.
5. The **Last saved wallet** dashboard card shows the game's last saved balance, NOT the Python journal's extrapolated balance. This may lag until `/reload`. We do **not** reconstruct transactions from before the Bridge upgrade. The user-reported 54s 77c remains the prior reference point, not a proven profit baseline for new automatic receipts.

**Limitations:** The addon records in-game events only while it is enabled. NPC selling is inferred conservatively; ambiguous actions (bulk sales, repairs, missing item data, bids, auction mail payouts) are not treated as profit. It does not automatically buy, sell, click or send game data over the internet. Review event accuracy in the first live session; the new addon has passed file/parser tests but has not yet been tested inside your WoW client.

### GitHub Releases auto-update for BOTH Windows EXE and Python source

- `Check updates` and periodic checks now work for the *Python-source launcher* as well as the compiled EXE. Once a signed-in user publishes a newer GitHub Release, the app will ask permission, fetch and SHA-256 check the correct asset (`ForeverTrader-source.zip` or `ForeverTrader.exe`), exit, replace project files and restart. The data in `%APPDATA%\ForeverTrader` is unaffected. The ZIP installer allows only listed project files and rejects traversal paths. In **Settings**, check **Keep existing in-game Forever Trader Bridge synced to app updates** to have the app copy its bundled Bridge files into an already installed game addon when they change; an initial manual addon installation is still required.
- The GitHub Actions workflow builds and publishes `ForeverTrader.exe`, `ForeverTrader.exe.sha256`, `ForeverTrader-source.zip` and `ForeverTrader-source.zip.sha256` on `v*` tags.
- **Release publishing is NOT active until you create a GitHub repository.** For the initial setup, run `powershell -ExecutionPolicy Bypass -File .\SETUP_GITHUB_WINDOWS.ps1` from the extracted folder after installing Git and the `gh` CLI. It will ask you to authorise GitHub, create/push `simon-hirst/wow-forever-trader`, and publish tag `v0.3.0` (unless already present). No credentials are bundled. You can instead follow the manual instructions below.
- Later changes still require a new source release/commit; ChatGPT cannot edit or ship updates in the background.

## What's fixed in v0.2.1 — accurate scans and NPC flip tracking

- New **VENDOR FLIPS** tab isolates below-vendor candidates from captured auction depth. The item must still be checked in-game. Sniper alerts do not automatically reach the Windows app until the item is searched/captured and saved with `/reload`.
- **Complete NPC flip** button records an already-executed AH purchase and an NPC sale in one operation. Do not use it if you separately recorded either side.
- Trades explicitly distinguish **AH** sales (5% commission) and **VENDOR** sales (no auction-house commission). Existing journal entries are migrated and remain AH transactions. Realised NPC profit is shown separately.
- Buy-side miscellaneous fees are allocated to FIFO cost basis as inventory sells.

## What's fixed in v0.2.1 — reliable independent scans

- **Data-quality fix:** Auctionator can save changed prices without changing its `TimeOfLastBrowseScan`. The desktop app now updates that timestamp's existing observations instead of treating them as additional independent scans.
- **Automatic repair of v0.2.0 databases:** On launch, duplicate scan timestamps are collapsed, retaining the newest values. Your trade journal, watchlist and detailed order books are preserved.
- **Better names:** Order-book rows use previously learned item names from Auctionator/auxForever rather than `Item #954` when possible.
- The stronger `RECHECK` signals now require genuinely distinct scan timestamps. Thin-book manual inspections still work immediately.

## What's new in v0.2.0 — real auction price-depth research

- **ORDER BOOK** tab ranks freshly captured, **complete** detail-search results by affordability and market depth. It distinguishes **VENDOR** opportunities from speculative **INSPECT** candidates; others are SKIP, CAUTION, PARTIAL, STALE, or REVIEW.
- The bundled **ForeverTraderBridge** WoW addon passively records the price tiers of searches **you make in auxForever, Auctionator, or the default AH**, using official `C_AuctionHouse` search-result events. It never sends its own queries, places bids, buys, or uploads data.
- Analysis rejects cheap-tier inventory that would require too much of your 43s 38c bankroll to clear. The maximum speculative position defaults to **25% of unreserved cash**, with a hard cap of 5 units in a cheap tier. These are adjustable in source, not a guarantee of success.
- Only **commodities** get automated resale-gap scoring; individual equipment auctions can have varying stack sizes and are marked for manual review. If the game reports incomplete search results, the app marks them PARTIAL.
- `order_books.csv` is now included in ChatGPT exports, alongside existing Auctionator pricing history and journal data.
- The app treats depth older than **10 minutes as STALE**, even if the saved file remains unchanged. A hypothetical resale at the next asking price is **not evidence of demand, sales, or realizable profit**. Deposits are *not* included in the speculative net-profit estimate.
- Upgrade preserves the SQLite trading history under `%APPDATA%\ForeverTrader\market.sqlite3`.

### Install the companion addon (required for depth scanning)

1. Close WoW. Double-click **`INSTALL_ADDON_WINDOWS.cmd`** in this extracted project folder, or manually copy `wow_addon\ForeverTraderBridge` into `World of Warcraft\_forever_\Interface\AddOns\ForeverTraderBridge`. The `.toc` must be directly inside that last folder.
2. Restart WoW Forever and ensure **Forever Trader Bridge** is enabled on the character-selection addon list.
3. Search for any item in **auxForever → Search**, exactly as you did with Bronze Bars. The addon passively records full price tiers when the client finishes receiving its results. Type **`/ftb`** to see how many item books have been recorded.
4. Type **`/reload`**. This writes `ForeverTraderBridge.lua` to your existing account's `WTF\Account\<account>\SavedVariables` folder.
5. In Forever Trader **Settings**, point **ForeverTraderBridge.lua (depth)** to that file, or leave blank if your Auctionator file sits beside it: the desktop app will look there automatically.
6. Click **Refresh file**, open **ORDER BOOK**, then **Export for ChatGPT**. The app checks SavedVariables on disk every four seconds.

**Limitations:** Price depth is gathered only for items you actually open/search, not the entire auction house. Scans run inside WoW while you play, but **the desktop program does not see changes until `/reload` or logout**. No seller identities are saved. The companion has been checked against the documented 1.60.1 auction APIs and unit-tested through a sample SavedVariables file; it has **not yet been tested in a live WoW Forever client**. If the addon reports zero captured books after a detailed search, share any Lua/BugSack error and we'll adapt it.

## Run now on Windows 11

1. Extract the ZIP to a folder (for example `Desktop\ForeverTrader`).
2. **Double-click `START_WINDOWS.cmd`** if Python 3 is already installed. Uses only Python standard library; no `pip install` necessary to run.
3. Open **Settings**. Select your game's `Auctionator.lua` in `World of Warcraft\_forever_\WTF\Account\<account>\SavedVariables\` (or the app may find it automatically).
4. Optionally choose `auxForever.lua` to resolve some item IDs to names. The app will fall back to this sibling file automatically if present.
5. The app imports the stored scan and watches the file for changes every ~4 seconds. **A new WoW scan only reaches disk after `/reload` or logout**. The source data does not provide live listing updates between saves.
6. Use **ORDER BOOK** after saving a detailed search, and **MARKET MOVERS** after the second scan to view price drops and rises immediately. These are *observation-only*, not buy signals; availability is a daily peak and a previous min ask is not fair value.
7. Use the **WATCHLIST** tab to specify price targets. Alerts are triggered only by newly imported saved snapshots, with an optional Windows beep.
8. Click **Export for ChatGPT**, then upload the ZIP in the chat for manual trade-by-trade recommendations.

**No Python installed?** Install Python 3 from [python.org](https://www.python.org/downloads/windows/), or use the prebuilt EXE from your project's GitHub Releases after publishing a tagged build.

## Make your own single-file Windows EXE

Double-click `BUILD_WINDOWS_EXE.cmd`. This installs PyInstaller and builds `dist\ForeverTrader.exe` (no Python required for the *recipient* running the EXE). **A compiled EXE is not prebuilt in this source ZIP** because it must be compiled for Windows. Alternatively, the GitHub Actions workflow below builds it in a Windows runner.

## Enable super-fast GitHub releases and app auto-updates

The desktop app has **built-in auto-update checking**, checksum verification and user-confirmed install. Auto-update is *wired in* but does **not work until a repository publishes a valid release**.

1. Create an empty GitHub repo, e.g. `simon-hirst/wow-forever-trader` (or change repository under Settings).
2. Push this project, **including `.github/workflows/windows-release.yml`**:
   ```powershell
   git init
   git add .
   git commit -m "Initial Forever Trader dashboard"
   git branch -M main
   git remote add origin git@github.com:simon-hirst/wow-forever-trader.git
   git push -u origin main
   ```
3. For each release, increment `VERSION` in `trader/__init__.py`, commit your changes, and tag it:
   ```powershell
   git add .
   git commit -m "Improve trading signals"
   git push
   git tag v0.3.0
   git push origin v0.3.0
   ```
4. GitHub Actions builds the Windows EXE and publishes **`ForeverTrader.exe` plus `ForeverTrader.exe.sha256`** as release assets.
5. Both Python source and EXE check releases on startup and about every 20 minutes, then **ask before updating**. It downloads and verifies the SHA-256 checksum files over HTTPS from your configured GitHub repository. *SHA-256 protects download integrity, not the repo owner's trustworthiness; use a repo you control.*

For this first upgraded release use `v0.3.0`, which matches `trader/__init__.py`. The included setup script handles the initial commit, repository and tag. GitHub's default workflow token may need **read/write permissions** to create a Release.

> ChatGPT cannot change this program autonomously in the background. Auto-updates deliver releases **you or I have prepared and you have published**, not autonomous strategies or guaranteed profit. When you share new exports, I can revise the source and you can publish a new version; installed copies will then find that release automatically.

## How data works

- Auctionator uses a Lua SavedVariables string containing a **CBOR** database; this app decodes `m` (last observed minimum buyout), `a` (maximum observed daily availability), `h` (daily high observed minimum), and `l` (daily low observed minimum).
- The app stores each distinct saved-file version in a local SQLite database at `%APPDATA%\ForeverTrader\market.sqlite3` and compares recorded minima across snapshots. From v0.1.1, exports also include `market_movers.csv` with both prices, observed peak quantities and their changes.
- **Signals are not trade recommendations**: `RECHECK` requires 2 earlier snapshots, at least a 20% drop below their median, recorded availability >0, and sufficient cash after reserved funds. Every proposed buy must be verified in the in-game live AH listing.
- The Trade Journal tracks recorded manual purchases/sales and *estimated* realized profit after a 5% faction AH fee and manually added fees/deposits. Only completed trades you explicitly log are counted.
- The data does **not** contain a full order book, sale volumes, or confirmed sell-through. A low asking price does not imply a good flip. It does not perform auction-house transactions or inject into the game.
- Item names are resolved from auxForever when available, a small built-in common-item map, and user-editable local labels; unfamiliar IDs stay numeric rather than being guessed.
- Set personal buy-price alert thresholds and rename unfamiliar item IDs from the desktop app. Threshold hits still require manually checking the current listing.
- The app keeps data on your PC unless **you manually export** a ZIP to share. Automatic logs can include your character name and saved wallet balance; review exports before sharing. Exports include market observations, transaction records and your configured initial balance. Don't upload entire WTF folders or account secrets.

## Tests

```shell
python -m unittest discover -s tests -v
```

Initial tested baseline: uploaded 2026-11-05 Auctionator market with **472 price-bearing entries**, one internal version entry removed, with additional item names resolved by the bundled known-materials dictionary. This input is **not included in the public source ZIP**.

## Previous changes in v0.1.1

- Added **MARKET MOVERS** with Drops >20%, Rises >20%, and All changes; works from the second scan, while high-confidence OPPORTUNITIES still require two older snapshots.
- Added previous scan price and observed peak quantity to exported `market_latest.csv`, and new `market_movers.csv`.
- Only compare consecutive scans from the same market.
- No order book or automatic trading is implied; do not mistake price movement for realised profit.

## Roadmap

- Verify the passive Bridge addon on the live Forever client, and handle any differences in event timing.
- More complete equipment-auction/stack analysis and accurate listing deposits.
- Price-depth and sell-through proxies using multiple snapshots, with explicit uncertainty.
- Optional user-controlled near-live screen capture bridge (only if compliant with game rules and tested).

### One-command future releases

Once your GitHub repo exists, you can also publish a new version from PowerShell with:

```powershell
powershell -ExecutionPolicy Bypass -File .\PUBLISH_RELEASE.ps1 -Version 0.3.1
```

The script stages only project source files (never the live WoW `WTF` contents), bumps the version, commits, pushes, and tags. The GitHub Actions workflow builds and releases the new EXE automatically. **Review your Git diff and verify the configured remote before publishing.**

## Trading example from your first scan (5 November 2026)

Auctionator recorded these **asking-price minima**, NOT proven fair values or confirmed sales:

| Item | Item ID | Scanned min ask | Scan's peak observed qty |
| --- | ---: | ---: | ---: |
| Linen Cloth | 2589 | 22c | 5,348 |
| Copper Ore | 2770 | 18c | 6,063 |
| Copper Bar | 2840 | 18c | 5,055 |
| Light Leather | 2318 | 20c | 3,287 |
| Peacebloom | 2447 | 11c | 5,887 |
| Silverleaf | 765 | 10c | 5,815 |

No purchases can be justified solely from this single snapshot: none of these numbers tells you future demand or whether the lowest stack remains. Compare a later saved scan, inspect live listings and calculate fees before buying.
