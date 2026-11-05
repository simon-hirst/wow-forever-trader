"""Auditable Sniper outcomes and strictly confirmed vendor-flip profit.

Sniper telemetry is *not* proof of a trade. Only matched, confirmed AH_BUY and
VENDOR_SELL receipts enter realised vendor P&L. Live link messages and SavedVariables
use the same immutable attempt UID, preventing double-counting on /reload.
"""
from __future__ import annotations

import re
import statistics
from collections import defaultdict, deque
from datetime import datetime, timezone

PREFIX = "FTPM1|"
OUTCOMES = {"BOUGHT", "LOST", "EXPIRED", "SKIPPED", "CANCELLED"}
REASONS = {"vendor", "usual", "plan", "unknown"}
SOURCES = {"early", "full", "unknown"}
FIELDS = (
    "uid", "ts", "character", "item_id", "outcome", "qty", "cost",
    "estimated_profit", "early", "page_ready_ms", "page_finish_ms",
    "offer_ready_ms", "offer_finish_ms", "reason", "source",
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS snipe_attempts (
    uid TEXT PRIMARY KEY, ts INTEGER NOT NULL, character TEXT NOT NULL,
    item_id INTEGER NOT NULL, outcome TEXT NOT NULL, qty INTEGER NOT NULL,
    cost INTEGER NOT NULL, estimated_profit INTEGER NOT NULL,
    early INTEGER NOT NULL, page_ready_ms INTEGER NOT NULL,
    page_finish_ms INTEGER NOT NULL, offer_ready_ms INTEGER NOT NULL,
    offer_finish_ms INTEGER NOT NULL, reason TEXT NOT NULL, source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snipe_attempts_time ON snipe_attempts(ts);
CREATE INDEX IF NOT EXISTS idx_snipe_attempts_item ON snipe_attempts(item_id, ts);
"""


def ensure_schema(db):
    db.executescript(SCHEMA)


def normalise(raw):
    """Validated DB row or ValueError; never trust unparsed addon/log input."""
    if not isinstance(raw, dict):
        raise ValueError("Sniper attempt must be a dict")
    char = str(raw.get("character", ""))
    uid = str(raw.get("uid", ""))
    if not char or len(char) > 120 or "|" in char or "\n" in char:
        raise ValueError("Invalid character")
    if not uid.startswith(char + ":") or not uid.rsplit(":", 1)[-1].isdigit() or len(uid) > 160:
        raise ValueError("Invalid attempt UID")
    if int(uid.rsplit(":", 1)[-1]) <= 0:
        raise ValueError("Invalid attempt sequence")
    outcome = str(raw.get("outcome", "")).upper()
    if outcome not in OUTCOMES:
        raise ValueError("Invalid outcome")
    ts = int(raw.get("ts", 0))
    if not 1_600_000_000 < ts < 4_102_444_800:
        raise ValueError("Invalid timestamp")
    item_id = int(raw.get("item_id", 0))
    if not 0 < item_id < 10_000_000:
        raise ValueError("Invalid item ID")
    numeric = {}
    for key, top in (
        ("qty", 1_000_000), ("cost", 10**12), ("estimated_profit", 10**12),
        ("page_ready_ms", 86_400_000), ("page_finish_ms", 86_400_000),
        ("offer_ready_ms", 86_400_000), ("offer_finish_ms", 86_400_000),
    ):
        value = int(raw.get(key, 0))
        if not 0 <= value <= top:
            raise ValueError("Invalid " + key)
        numeric[key] = value
    early = 1 if raw.get("early") in (True, 1, "1") else 0
    reason = str(raw.get("reason", "unknown"))
    source = str(raw.get("source", "unknown"))
    if reason not in REASONS or source not in SOURCES:
        raise ValueError("Invalid reason/source")
    return dict(uid=uid, ts=ts, character=char, item_id=item_id,
                outcome=outcome, early=early, reason=reason, source=source,
                **numeric)


def parse_log_line(line):
    """Decode fixed-width, delimiter-safe messages from the in-game log link."""
    if not isinstance(line, str) or not line.startswith(PREFIX):
        return None
    parts = line.rstrip("\r\n").split("|")
    if len(parts) != len(FIELDS) + 1:
        raise ValueError("Truncated or malformed Sniper metric")
    return normalise(dict(zip(FIELDS, parts[1:])))


def ingest(db, raw):
    row = normalise(raw)
    values = tuple(row[field] for field in FIELDS)
    with db:
        cur = db.execute(
            "INSERT OR IGNORE INTO snipe_attempts (" + ",".join(FIELDS) +
            ") VALUES (" + ",".join("?" for _ in FIELDS) + ")", values)
    return cur.rowcount


def attempts(db, limit=300):
    return db.execute("""
        SELECT a.uid,a.ts,a.character,a.item_id,
               COALESCE(n.name, i.name, 'Item #'||a.item_id),
               a.outcome,a.qty,a.cost,a.estimated_profit,a.early,
               a.page_ready_ms,a.page_finish_ms,a.offer_ready_ms,
               a.offer_finish_ms,a.reason,a.source
        FROM snipe_attempts a
        LEFT JOIN names n ON n.item_id=CAST(a.item_id AS TEXT)
        LEFT JOIN items i ON i.item_id=a.item_id
        ORDER BY a.ts DESC, a.rowid DESC LIMIT ?
    """, (limit,)).fetchall()


def confirmed_vendor_pnl(db, since=None):
    """Match FIFO *within character and item*; never assume a wallet delta is a sale.

    Unmatched sells don't count as profit. A buy lot's exact total is allocated
    proportionally on partial sales so rounding copper cannot create extra money.
    """
    rows = db.execute("""
        SELECT time,character,kind,item_id,qty,total
        FROM automatic_events
        WHERE status='confirmed' AND kind IN ('AH_BUY','VENDOR_SELL')
          AND CAST(item_id AS INTEGER)>0 AND qty>0 AND total>0
        ORDER BY time,CAST(uid AS INTEGER)
    """).fetchall()
    holdings = defaultdict(deque)
    realised = matched_qty = unmatched_qty = 0
    buy_receipts = sale_receipts = 0
    for ts, char, kind, item, qty, total in rows:
        key = (char, str(item))
        if kind == "AH_BUY":
            holdings[key].append([int(qty), int(total)])
            buy_receipts += 1
            continue
        sale_receipts += 1
        remaining = int(qty)
        for lot in holdings[key]:
            if remaining <= 0:
                break
            if lot[0] <= 0:
                continue
            matched = min(remaining, lot[0])
            # Sale receipts are confirmed only for exact, integral unit payouts.
            revenue = matched * (int(total) // int(qty))
            allocated = lot[1] if matched == lot[0] else (lot[1] * matched) // lot[0]
            lot[0] -= matched
            lot[1] -= allocated
            remaining -= matched
            if since is None or ts >= since:
                matched_qty += matched
                realised += revenue - allocated
        if since is None or ts >= since:
            unmatched_qty += remaining
    return dict(realised=realised, matched_qty=matched_qty, unmatched_qty=unmatched_qty,
                confirmed_buys=buy_receipts, confirmed_sales=sale_receipts,
                open_units=sum(lot[0] for q in holdings.values() for lot in q))


def _active_seconds(timestamps):
    """Approximate active time from attempts, ignoring overnight/offline gaps.

    Don't publish a profit/hour estimate with less than ten observed active
    minutes. A clock-based proxy is not an in-game session timer.
    """
    times = sorted(set(timestamps))
    active = sum(min(b - a, 600) for a, b in zip(times, times[1:]) if b > a and b - a <= 600)
    return active


def summary(db, now=None):
    rows = db.execute("""
        SELECT ts,outcome,early,page_finish_ms,reason
        FROM snipe_attempts ORDER BY ts
    """).fetchall()
    counts = {k: 0 for k in OUTCOMES}
    for _, outcome, _, _, _ in rows:
        counts[outcome] += 1
    offered = counts["BOUGHT"] + counts["LOST"]
    page_ms = [r[3] for r in rows if r[1] == "BOUGHT" and r[2] and r[3] > 0]
    pnl = confirmed_vendor_pnl(db)
    active = _active_seconds([r[0] for r in rows])
    # This rate is an approximation until activity is reconciled with all
    # saved vendor receipts. The dashboard labels it as such.
    recent_pnl = confirmed_vendor_pnl(db, since=rows[0][0]) if rows else None
    hourly = (recent_pnl["realised"] * 3600 / active) if active >= 600 and recent_pnl else None
    return dict(attempts=len(rows), bought=counts["BOUGHT"], lost=counts["LOST"],
                expired=counts["EXPIRED"], skipped=counts["SKIPPED"],
                cancelled=counts["CANCELLED"],
                capture_rate=(counts["BOUGHT"] / offered) if offered else None,
                early_buys=sum(1 for r in rows if r[1] == "BOUGHT" and r[2]),
                median_page_to_buy_ms=round(statistics.median(page_ms)) if page_ms else None,
                active_seconds=active, profit_per_active_hour=hourly,
                confirmed_vendor=pnl)
