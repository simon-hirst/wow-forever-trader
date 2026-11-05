"""Market history: 5-minute bars and full listing snapshots, plus flow estimates.

A snapshot is every auction on the house at one moment. Comparing two of them is the
closest this game gets to a trade tape, since sales are never published.
"""
from __future__ import annotations
import json
import math
import re
import sqlite3
import zlib
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (item_id INTEGER PRIMARY KEY, name TEXT NOT NULL, quality INTEGER NOT NULL,
    level INTEGER NOT NULL, class TEXT NOT NULL, subclass TEXT NOT NULL, stack INTEGER NOT NULL, vendor INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS bars (item_id INTEGER NOT NULL, ts INTEGER NOT NULL, o INTEGER NOT NULL, h INTEGER NOT NULL,
    l INTEGER NOT NULL, c INTEGER NOT NULL, q INTEGER NOT NULL, out_units INTEGER NOT NULL, in_units INTEGER NOT NULL,
    PRIMARY KEY (item_id, ts)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_bars_ts ON bars(ts);
CREATE TABLE IF NOT EXISTS snapshots (ts INTEGER PRIMARY KEY, source TEXT NOT NULL, complete INTEGER NOT NULL,
    listings INTEGER NOT NULL, units INTEGER NOT NULL, items INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS snapshot_items (ts INTEGER NOT NULL, item_id INTEGER NOT NULL, units INTEGER NOT NULL,
    listings INTEGER NOT NULL, min_unit INTEGER NOT NULL, robust INTEGER NOT NULL, median INTEGER NOT NULL,
    PRIMARY KEY (ts, item_id)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_snapshot_items_item ON snapshot_items(item_id, ts);
CREATE TABLE IF NOT EXISTS flows (ts_from INTEGER NOT NULL, ts_to INTEGER NOT NULL, item_id INTEGER NOT NULL,
    sold_units INTEGER NOT NULL, sold_value INTEGER NOT NULL, new_units INTEGER NOT NULL, relisted_units INTEGER NOT NULL,
    expired_units INTEGER NOT NULL, cancelled_units INTEGER NOT NULL, PRIMARY KEY (ts_to, item_id)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_flows_item ON flows(item_id, ts_to);
CREATE TABLE IF NOT EXISTS snapshot_raw (ts INTEGER PRIMARY KEY, data BLOB NOT NULL);
"""

# Two captures of one listing (link and saved file, or two addons) are not a market move.
SAME_LISTING_SECONDS = 180
MAX_FLOW_GAP_SECONDS = 6 * 3600
KEEP_RAW_SNAPSHOTS = 3
SHORT_BAND = 1          # under 30 minutes left: gone next time probably means expired
MIN_RELIST_MATCH = 5    # a 1-unit "relist" match would swallow ordinary gear sales
GEAR_SALE_MAX_PREMIUM = 1.5
MAX_UNIT_PRICE = 10 ** 10


def ensure_schema(db: sqlite3.Connection):
    db.executescript(SCHEMA)
    db.commit()


def levels_of(rows) -> tuple[dict[int, int], dict[int, int]]:
    """Unit price -> quantity, and the part of each level about to expire."""
    levels: dict[int, int] = {}
    short: dict[int, int] = {}
    for count, buyout, band in rows:
        if count <= 0 or buyout <= 0:
            continue
        unit = math.ceil(buyout / count)
        if unit > MAX_UNIT_PRICE:
            continue
        levels[unit] = levels.get(unit, 0) + count
        if band == SHORT_BAND:
            short[unit] = short.get(unit, 0) + count
    return levels, short


def robust_price(levels: dict[int, int]) -> int:
    """Lowest price with real depth under it, so one cheap unit cannot set the market."""
    units = sum(levels.values())
    need = max(3, units * 0.01)
    if units < need:
        need = units
    cumulative = 0
    for price in sorted(levels):
        cumulative += levels[price]
        if cumulative >= need:
            return price
    return max(levels)


def median_price(levels: dict[int, int]) -> int:
    half = sum(levels.values()) / 2
    cumulative = 0
    for price in sorted(levels):
        cumulative += levels[price]
        if cumulative >= half:
            return price
    return max(levels)


def estimate_flow(prev: dict[int, int], cur: dict[int, int], prev_short: dict[int, int], stackable: bool) -> dict:
    """Split the change between two listings of one item into sales and everything else.

    Stackables always sell cheapest-first, so a sale can only take units from the front
    of the book: anything vanishing behind an untouched price level was cancelled or
    expired. Gear sells listing by listing, so only a premium cap applies.
    """
    down: dict[int, int] = {}
    up: dict[int, int] = {}
    for price in set(prev) | set(cur):
        change = cur.get(price, 0) - prev.get(price, 0)
        if change < 0:
            down[price] = -change
        elif change > 0:
            up[price] = change

    relisted = 0
    if stackable:
        # Same quantity gone from one price and back at another: the seller moved it.
        for price in sorted(down, reverse=True):
            amount = down[price]
            if amount >= MIN_RELIST_MATCH:
                match = next((p for p, q in up.items() if q == amount and p != price), None)
                if match is not None:
                    relisted += amount
                    del down[price], up[match]

    expired = 0
    for price in list(down):
        gone = min(down[price], prev_short.get(price, 0))
        if gone:
            expired += gone
            down[price] -= gone
            if not down[price]:
                del down[price]

    sold_units = sold_value = 0
    if stackable:
        for price in sorted(prev):
            taken = down.pop(price, 0)
            if not taken:
                break
            sold_units += taken
            sold_value += taken * price
    elif prev:
        cap = min(prev) * GEAR_SALE_MAX_PREMIUM
        for price in list(down):
            if price <= cap:
                taken = down.pop(price)
                sold_units += taken
                sold_value += taken * price
    return {'sold_units': sold_units, 'sold_value': sold_value, 'new_units': sum(up.values()),
            'relisted_units': relisted, 'expired_units': expired, 'cancelled_units': sum(down.values())}


def _pack_raw(items: dict[int, list]) -> bytes:
    return zlib.compress(json.dumps({str(k): v for k, v in items.items()}, separators=(',', ':')).encode('ascii'), 6)


def _unpack_raw(blob: bytes) -> dict[int, list]:
    return {int(k): [tuple(r) for r in v] for k, v in json.loads(zlib.decompress(blob)).items()}


def latest_snapshot_rows(db: sqlite3.Connection) -> tuple[int, dict[int, list]] | tuple[None, dict]:
    row = db.execute('SELECT ts, data FROM snapshot_raw ORDER BY ts DESC LIMIT 1').fetchone()
    return (row[0], _unpack_raw(row[1])) if row else (None, {})


def ingest_snapshot(db: sqlite3.Connection, ts: int, items: dict[int, list], complete: bool, source: str) -> bool:
    """Store one listing and the flows since the previous one. Returns False for a repeat."""
    near = db.execute('SELECT ts, complete FROM snapshots WHERE ts BETWEEN ? AND ? ORDER BY ts DESC LIMIT 1',
                      (ts - SAME_LISTING_SECONDS, ts + SAME_LISTING_SECONDS)).fetchone()
    if near and (near[1] or not complete):
        return False
    if not items:
        return False
    stack_sizes = dict(db.execute('SELECT item_id, stack FROM items'))
    previous = db.execute('SELECT ts, data FROM snapshot_raw WHERE ts < ? ORDER BY ts DESC LIMIT 1', (ts,)).fetchone()
    with db:
        if near:
            # A complete copy replaces a partial one of the same listing.
            for table in ('snapshot_items', 'snapshot_raw', 'snapshots'):
                db.execute(f'DELETE FROM {table} WHERE ts=?', (near[0],))
            db.execute('DELETE FROM flows WHERE ts_to=?', (near[0],))
        totals = [0, 0, 0]
        summaries = []
        cur_levels = {}
        cur_short = {}
        for item, rows in items.items():
            levels, short = levels_of(rows)
            if not levels:
                continue
            cur_levels[item], cur_short[item] = levels, short
            units = sum(levels.values())
            summaries.append((ts, item, units, len(rows), min(levels), robust_price(levels), median_price(levels)))
            totals[0] += len(rows)
            totals[1] += units
            totals[2] += 1
        db.executemany('INSERT OR REPLACE INTO snapshot_items VALUES(?,?,?,?,?,?,?)', summaries)
        db.execute('INSERT OR REPLACE INTO snapshots VALUES(?,?,?,?,?,?)',
                   (ts, source, int(complete), totals[0], totals[1], totals[2]))
        db.execute('INSERT OR REPLACE INTO snapshot_raw VALUES(?,?)', (ts, _pack_raw(items)))
        if previous and 0 < ts - previous[0] <= MAX_FLOW_GAP_SECONDS:
            prev_complete = db.execute('SELECT complete FROM snapshots WHERE ts=?', (previous[0],)).fetchone()
            prev_items = _unpack_raw(previous[1])
            flows = []
            # An item missing from a partial listing is unknown, not sold out.
            names = set(prev_items) | set(cur_levels) if complete and prev_complete and prev_complete[0] else set(prev_items) & set(cur_levels)
            for item in names:
                prev, prev_short = levels_of(prev_items.get(item, []))
                flow = estimate_flow(prev, cur_levels.get(item, {}), prev_short, stack_sizes.get(item, 1) > 1)
                if any(flow.values()):
                    flows.append((previous[0], ts, item, flow['sold_units'], flow['sold_value'], flow['new_units'],
                                  flow['relisted_units'], flow['expired_units'], flow['cancelled_units']))
            db.executemany('INSERT OR REPLACE INTO flows VALUES(?,?,?,?,?,?,?,?,?)', flows)
        db.execute('DELETE FROM snapshot_raw WHERE ts NOT IN (SELECT ts FROM snapshot_raw ORDER BY ts DESC LIMIT ?)',
                   (KEEP_RAW_SNAPSHOTS,))
    return True


def ingest_bars(db: sqlite3.Connection, bucket: int, rows) -> int:
    """rows: (item, open, high, low, close, qty, out, in). Replays are harmless."""
    with db:
        db.executemany('INSERT OR REPLACE INTO bars VALUES(?,?,?,?,?,?,?,?,?)',
                       [(r[0], bucket, r[1], r[2], r[3], r[4], r[5], r[6], r[7]) for r in rows])
    return len(rows)


def parse_aux_items(path: Path) -> dict[int, dict]:
    """Vendor price, stack size and class of every item from aux's saved item list.

    The list is a Lua array with nil holes, one line per slot; a slot's position is its
    item ID. Parsed from the right because item names can contain '#'.
    """
    text = Path(path).read_bytes().decode('utf-8', 'replace').replace('\r', '')
    start = text.find('["items"] = {')
    if start < 0:
        return {}
    end = text.find('\n},', start)
    items: dict[int, dict] = {}
    index = 0
    for line in text[start + len('["items"] = {'):end].split('\n'):
        line = line.strip().rstrip(',')
        if not line:
            continue
        index += 1
        if not line.startswith('"'):
            continue
        fields = line.strip('"').split('#')
        if len(fields) < 11 or not fields[-1].isdigit() or not fields[-9].isdigit():
            continue
        items[index] = {'name': fields[0].replace('\\"', '"'), 'quality': int(fields[-9]),
                        'level': int(fields[-8]) if fields[-8].isdigit() else 0, 'class': fields[-6],
                        'subclass': fields[-5], 'stack': int(fields[-3]) if fields[-3].isdigit() else 1,
                        'vendor': int(fields[-1])}
    return items


def ingest_items(db: sqlite3.Connection, items: dict[int, dict]) -> int:
    with db:
        db.executemany('INSERT OR REPLACE INTO items VALUES(?,?,?,?,?,?,?,?)',
                       [(k, v['name'], v['quality'], v['level'], v['class'], v['subclass'], v['stack'], v['vendor'])
                        for k, v in items.items()])
        db.executemany('INSERT OR IGNORE INTO names(item_id,name) VALUES(?,?)',
                       [(str(k), v['name']) for k, v in items.items() if v['name']])
    return len(items)


_AHLEDGER_SCAN = re.compile(r'\["ts"\] = (\d+),[^{}]*?\["data"\] = "([^"]*)"', re.S)


def parse_ahledger_scans(path: Path) -> list[tuple[int, dict[int, list]]]:
    """Full listings AHledger saved: item:stack:buyout:bid:timeleft;..."""
    text = Path(path).read_bytes().decode('utf-8', 'replace')
    out = []
    for match in _AHLEDGER_SCAN.finditer(text):
        items: dict[int, list] = {}
        for row in match.group(2).split(';'):
            parts = row.split(':')
            if len(parts) < 5:
                continue
            try:
                item, count, buyout, band = int(parts[0]), int(parts[1]), int(float(parts[2])), int(parts[4])
            except ValueError:
                continue
            if buyout > 0:
                items.setdefault(item, []).append((count, buyout, band))
        if items:
            out.append((int(match.group(1)), items))
    return out


def parse_bridge_snapshot(data: str) -> dict[int, list]:
    """The addon's text form: item|count.buyout.band,count.buyout.band;item|..."""
    items: dict[int, list] = {}
    for group in data.split(';'):
        head, _, body = group.partition('|')
        if not head.isdigit():
            continue
        rows = []
        for row in body.split(','):
            parts = row.split('.')
            if len(parts) == 3:
                try:
                    rows.append((int(parts[0]), int(parts[1]), int(parts[2])))
                except ValueError:
                    continue
        if rows:
            items[int(head)] = rows
    return items


def parse_bridge_bars(data: str) -> list[tuple]:
    rows = []
    for row in data.split(';'):
        parts = row.split(',')
        if len(parts) == 8:
            try:
                rows.append(tuple(int(float(p)) for p in parts))
            except ValueError:
                continue
    return rows


def prune(db: sqlite3.Connection, now: int, keep_bar_days: int = 30, keep_snapshot_days: int = 60):
    with db:
        db.execute('DELETE FROM bars WHERE ts < ?', (now - keep_bar_days * 86400,))
        db.execute('DELETE FROM snapshot_items WHERE ts < ?', (now - keep_snapshot_days * 86400,))
        db.execute('DELETE FROM snapshots WHERE ts < ?', (now - keep_snapshot_days * 86400,))
        db.execute('DELETE FROM flows WHERE ts_to < ?', (now - keep_snapshot_days * 86400,))
