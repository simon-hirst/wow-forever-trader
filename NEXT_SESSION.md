# Forever Trader: next-session handoff

**World:** WoW Forever 1.60.1, EU Horde PvP. auxForever, AHledger and Auctionator installed.

**Goal:** maximise realised gold. Below-vendor flips first; resale limit orders as the evidence grows.

**Bankroll:** 54s 97c at last saved wallet (2026-11-05), started at 43s 38c.

## State of v0.4.0

Built and tested in a simulated client that runs the real auxForever, and run live for the first time on 2026-11-05: see "Live validation". A local `HANDOFF.md`, if present, has the full picture.

## Next steps

1. Finish anything still open under "Live validation".
2. Let the data build. Grades move from C to B after a day of AH-open time, and A needs 3 days plus a record of scored calls. Watch "How past calls did" in the report before trusting limits.
3. Run `/ft-review` in a Claude Code chat, or ask it to watch the exports folder.

## Live validation

First live session, 2026-11-05:

- [x] `getfenv` attaches to aux's Sniper (`/ft diag`)
- [x] Sniper auto-starts when the AH opens
- [x] Data strip reads: `/ft linktest` gave 149 of 150 frames, none corrupted
- [x] Full listing arrives over the link (806 frames; one was lost, so it was stored as partial)
- [x] Plan slots load, and a newer plan loads while the AH stays open (rev 3 to rev 6, no reload)
- [x] One press buys a stackable through auto-confirm (dozens of buys, no second press needed)
- [x] A relayed key reaches WoW: posted numpad divide works without focus; F13-F24 are ignored by the game
- [x] The Windows button buys: a relayed key press counts as a real press
- [ ] Mouse-wheel notch counts as a press
- [x] Gear buys, random-suffix items included (one press per auction)
- [ ] Some listings (all the Scalping Tomahawk variants) failed with "No longer listed"; the `unselectable` log line records why
- [x] Receipts: each purchase now gets one CONFIRMED receipt at its quoted price (before the fix, every commodity buy went to REVIEW)
- [x] The addon's own 15-minute full scan fires live (11:35, complete listing of 841 frames)

## Known limits

- The strip must be visible: covered or minimised WoW means no data until it is back.
- Plan limits refresh about every 10 minutes while the AH is open, 24 times per UI session.
- Estimated sales cannot see every relist or cancel, so treat them as a guide.
