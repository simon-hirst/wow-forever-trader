# Forever Trader

If `HANDOFF.md` exists in this folder, read it first: it says where the work stands and what to do next. It is local only and gitignored because it holds machine and account details.

Windows desktop app plus a WoW addon for the WoW Forever auction house. Stdlib-only Python; the
app runs from source on the player's PC out of this folder, or as a PyInstaller EXE.

## Layout

- `wow_addon/ForeverTraderBridge/`: the addon. `Snipe.lua` is the Confirm button, `Market.lua` records
  the market, `Link.lua` draws the data strip, `SniperAdapter.lua` reaches into auxForever at runtime, `Plan.lua` loads buy limits.
- `trader/link.py`: decodes the strip. Must match `Link.lua` and `NS.BuildState` in `Main.lua`.
- `trader/marketdata.py`: bars, full listings, sale estimates. `trader/quant.py`: the engine. `trader/strategy.py`: plan files and reports.
- `trader/autopilot.py`: background worker. `trader/overlay.py`: the Windows Confirm button. `trader/ui.py`: the Tk app.

## Rules that matter

- Every purchase starts from one real press. `C_AuctionHouse.StartCommoditiesPurchase` and `PlaceBid`
  require a hardware event; the tests fail if addon code calls them without one.
- `overlay.py` relays one key press per click to WoW and has no repeat, timer or queue. Do not add any:
  an input burst is what Blizzard's EULA calls a bot, and this repository is public.
- No anti-detection of any kind, and nothing touching the game's process or memory.
- An addon cannot write files at runtime and `C_UI.Reload` needs a hardware event, which is why the link exists.
- Do not copy auxForever's source into this repo. `tests/wow_sim.py` loads the installed copy.

## Testing

```shell
python -m unittest discover -s tests -t .
```

Lua tests need `pip install lupa` and otherwise skip. Those in `ConfirmWithRealAux` also need auxForever
installed; set `FT_AUX_DIR` if it is not at the usual path. They run the real aux in `tests/wow_stub.lua`, a
simulated client whose server enforces the hardware-event rule.

## Live checks

The game can only be reached by asking the player to run things. `/ft diag` in game logs to
`%APPDATA%\ForeverTrader\link.log` over the link. WoW needs a full restart, not `/reload`, to see new addon files.

Working-tree files may have CRLF endings from the auto-updater while git holds LF. Check real changes
with `git diff --ignore-space-at-eol`, and do not commit line-ending noise.

## Sniper performance experiment

The 1.60.1 Sniper uses a server-paginated browse of the whole auction house; do not
remove `C_AuctionHouse.IsThrottledMessageSystemReady` or issue overlapping queries.
`SniperAdapter.lua` overrides only the idle pause and prioritizes new below-vendor
browse candidates before live verification. `/ft scanstats` reports first page,
full browse and verification timings. Speed modes: fast 0.35s, normal 2.5s,
safe 6s. The priority list must leave the original C_AuctionHouse rows unchanged
because Market.OnBrowse needs the complete list. No price-free/unverified buys.
