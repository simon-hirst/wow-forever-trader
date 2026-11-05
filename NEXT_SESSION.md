# Forever Trader — next-session handoff

**World:** WoW Forever 1.60.1, EU Horde PvP, using auxForever Sniper and Auctionator.

**Goal:** Maximize actual realized gold profit. Prefer riskless NPC-vendor arbitrage until there is enough independent price history to test AH resale liquidity.

**Last balance:** `/ftmoney` reported **5,497 copper (54s 97c)**. The previous manual balance was **5,611 copper (56s 11c)**. This is a timestamp/reconciliation gap, not proof of a loss. Initial trading bankroll was **4,338 copper**. Actual profit depends on whether all wallet changes came from trading.

**Latest exported market data:** 845 catalog items, 20 detailed order books, 17 transaction observations; 2 confirmed purchases, 0 confirmed NPC sales and 15 review events on the old Bridge. No speculative AH buys recommended from that stale snapshot.

## New code in v0.3.2

- Better NPC sale matching: wait until bags and cash match, and use cached item-link vendor payouts.
- AH commodity purchase evidence from the player's quote/confirm and game purchase events.
- Wallet delta history, available under the desktop app and `wallet_changes.csv` in exports.
- Optional quick-vendor-sniper cursor button patch for auxForever's current Sniper UI. This is **not a bot**. It offers a user-click to select an active below-vendor deal, awaits auxForever's normal live refresh, then forwards subsequent manual clicks into auxForever's existing Buy / server-price Confirm flow.
- Main branch CI should auto-publish *new* version tags after a successful Windows run. Each app update still asks the player before installation.

## Next steps when returning

1. Start Forever Trader and accept the v0.3.2 update if published. Verify Bridge sync is enabled in Settings; `/reload` WoW after update, or restart WoW.
2. Optional quick-buy setup (with WoW closed): `powershell -ExecutionPolicy Bypass -File .\INSTALL_QUICK_SNIPE_WINDOWS.ps1`. To undo: run with `-Uninstall`. It modifies only `auxForever\tabs\sniper\frame.lua` and keeps a backup. Use `/ftquick` to toggle the overlay in game.
3. Stay with Sniper's **1c minimum profit** and **vendor-only purchases** while the historical scans accrue. Confirm price & quantity before buying. Keep 5s available.
4. Execute a few NEW vendor flips after the Bridge update; type `/ftmoney`, then `/reload` to persist. Export the ZIP in Forever Trader and upload it to ChatGPT.
5. Compare `wallet_changes.csv`, `automatic_activity.csv`, `wallet_checkpoints.csv` and trade journal against the in-game wallet. Prior receipt ambiguity will not magically disappear; inspect newly captured events.
6. Run auxForever **Full Scan once per day for at least 3 different days** before relying on historical-price speculative deals. A quoted resale price is not proof of demand.

## Known limitations

- In-game overlay and event timing have **not yet been validated inside the live client**. Turn off the overlay using `/ftquick` or uninstall the patch if buggy.
- Game addon's SavedVariables only sync to Windows on `/reload` or logout.
- A player-click is required for each action; no unattended bidding/purchasing.
- Wallet change totals include potential unrelated repairs, quest money and other activity. Do not equate change in cash to audited trading profit without reconciliation.
