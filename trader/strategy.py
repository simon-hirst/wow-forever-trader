"""Runs the market engine over stored history, keeps its calls, and hands the result to
the game (plan files), the app (result dict) and the analyst (report and exports)."""
from __future__ import annotations
import hashlib
import json
import os
import time
from pathlib import Path

from . import marketdata as md
from . import quant

PLAN_SLOTS = 24
HISTORY_DAYS = 14
INTERFACE_VERSION = '16001'

SCHEMA = """
CREATE TABLE IF NOT EXISTS plan_runs (rev INTEGER PRIMARY KEY, ts INTEGER NOT NULL, digest TEXT NOT NULL, summary TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS plan_calls (rev INTEGER NOT NULL, ts INTEGER NOT NULL, item_id INTEGER NOT NULL, action TEXT NOT NULL,
    ask INTEGER NOT NULL, limit_price INTEGER NOT NULL, target INTEGER NOT NULL, fair INTEGER NOT NULL, max_units INTEGER NOT NULL,
    margin REAL NOT NULL, ev_day REAL NOT NULL, fill_hours REAL NOT NULL, sold_per_hour REAL NOT NULL, grade TEXT NOT NULL, why TEXT NOT NULL,
    PRIMARY KEY (rev, item_id)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_plan_calls_item ON plan_calls(item_id, ts);
"""


def ensure_schema(db):
    md.ensure_schema(db)
    db.executescript(SCHEMA)
    db.commit()


def load_views(db, now: int) -> list[quant.ItemView]:
    ts, rows = md.latest_snapshot_rows(db)
    if ts is None:
        return []
    since = now - HISTORY_DAYS * quant.DAY
    meta = {r[0]: r for r in db.execute('SELECT item_id, name, quality, stack, vendor FROM items')}
    known_names = dict(db.execute('SELECT item_id, name FROM names'))
    history: dict[int, list] = {}
    for item, t, robust in db.execute('SELECT item_id, ts, robust FROM snapshot_items WHERE ts >= ? ORDER BY ts', (since,)):
        history.setdefault(item, []).append((t, robust))
    flows: dict[int, list] = {}
    for row in db.execute('''SELECT item_id, ts_from, ts_to, sold_units, sold_value, new_units, relisted_units
                             FROM flows WHERE ts_to >= ? ORDER BY ts_to''', (since,)):
        flows.setdefault(row[0], []).append(row[1:])
    views = []
    for item, listing in rows.items():
        levels, _ = md.levels_of(listing)
        if not levels:
            continue
        info = meta.get(item)
        name = info[1] if info and info[1] else known_names.get(str(item), f'Item #{item}')
        points = history.get(item, [])
        views.append(quant.ItemView(
            item_id=item, name=name, stack=info[3] if info else 1, vendor=info[4] if info else 0,
            quality=info[2] if info else 1, levels=sorted(levels.items()),
            times=[p[0] for p in points], robust=[p[1] for p in points], flows=flows.get(item, [])))
    return views


def score_past_calls(db, now: int) -> dict:
    """The first order per item per day, checked against what the ask then did."""
    since = now - HISTORY_DAYS * quant.DAY
    seen, history = set(), []
    for item, ts, limit_price, target in db.execute(
            """SELECT item_id, ts, limit_price, target FROM plan_calls
               WHERE ts >= ? AND action IN ('LIMIT', 'BUY NOW') ORDER BY ts""", (since,)):
        key = (item, ts // quant.DAY)
        if key not in seen:
            seen.add(key)
            history.append({'item_id': item, 'ts': ts, 'limit': limit_price, 'target': target})
    lows: dict[int, list] = {}
    for item, ts, low in db.execute('SELECT item_id, ts, l FROM bars WHERE ts >= ?', (since,)):
        lows.setdefault(item, []).append((ts, low))
    for item, ts, low in db.execute('SELECT item_id, ts, min_unit FROM snapshot_items WHERE ts >= ?', (since,)):
        lows.setdefault(item, []).append((ts, low))
    for series in lows.values():
        series.sort()
    return quant.evaluate_calls(history, lows, now)


def notes_path(data_dir: Path) -> Path:
    return Path(data_dir) / 'analyst' / 'notes.json'


def load_notes(data_dir: Path, now: int) -> dict | None:
    """The analyst's notes while they are valid; a broken or expired file is ignored."""
    path = notes_path(data_dir)
    try:
        notes = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(notes, dict):
        return None
    expires = notes.get('expires_at')
    if isinstance(expires, (int, float)) and now > expires:
        return None
    return notes


def render_plan_lua(plan: dict, rev: int, spec: float) -> str:
    entries = quant.plan_items_for_game(plan)
    body = ','.join(f'[{item}]={{l={e["l"]},q={e["q"]},t={e["t"]},f={e["f"]},g="{e["g"]}"}}' for item, e in sorted(entries.items()))
    return f'ForeverTrader_ReceivePlan({{rev={rev},ts={plan["now"]},reserve={plan["reserve"]},spec={spec:g},items={{{body}}}}})\n'


def plan_digest(plan: dict) -> str:
    return hashlib.sha256(json.dumps(quant.plan_items_for_game(plan), sort_keys=True).encode('ascii')).hexdigest()


def _write_atomic(path: Path, text: str):
    tmp = path.with_name(path.name + '.new')
    tmp.write_text(text, encoding='utf-8', newline='\n')
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            # The game may be reading it at this moment.
            if attempt == 4:
                raise
            time.sleep(0.2)


def write_plan_slots(addons_root: Path, plan_lua: str) -> int:
    """Puts the newest plan in every slot, since any of them may be the next one loaded."""
    count = 0
    for slot in range(1, PLAN_SLOTS + 1):
        name = f'FTPlan{slot:02d}'
        folder = Path(addons_root) / name
        folder.mkdir(parents=True, exist_ok=True)
        toc = folder / f'{name}.toc'
        if not toc.exists():
            toc.write_text(
                f'## Interface: {INTERFACE_VERSION}\n## Title: Forever Trader Plan {slot:02d}\n'
                '## Notes: Buy limits written by the Forever Trader app\n## LoadOnDemand: 1\n'
                '## Dependencies: ForeverTraderBridge\n## Group: ForeverTraderBridge\nPlan.lua\n', encoding='utf-8', newline='\n')
        _write_atomic(folder / 'Plan.lua', plan_lua)
        count += 1
    return count


def store_plan(db, plan: dict) -> int:
    """Keeps a plan as a new revision only if the orders the game would use changed."""
    digest = plan_digest(plan)
    row = db.execute('SELECT rev, digest FROM plan_runs ORDER BY rev DESC LIMIT 1').fetchone()
    if row and row[1] == digest:
        return row[0]
    rev = (row[0] if row else 0) + 1
    with db:
        db.execute('INSERT INTO plan_runs VALUES(?,?,?,?)', (rev, plan['now'], digest, json.dumps(plan['summary'])))
        db.executemany('INSERT INTO plan_calls VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', [
            (rev, plan['now'], c.item_id, c.action, c.ask, c.limit, c.target, c.fair, c.max_units, c.margin,
             min(c.ev_day, 1e6), c.fill_hours, c.sold_per_hour, c.grade, c.why) for c in plan['calls']])
        db.execute('DELETE FROM plan_calls WHERE ts < ?', (plan['now'] - 2 * HISTORY_DAYS * quant.DAY,))
    return rev


def format_copper(value) -> str:
    from .auctionator import fmt_copper
    return fmt_copper(int(value))


def render_report(plan: dict, rev: int, link: dict | None = None) -> str:
    summary, scoring = plan['summary'], plan['scoring']
    lines = [
        f'# Forever market report, plan rev {rev}',
        f'Generated {time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(plan["now"]))}. '
        f'Wallet {format_copper(plan["cash"])}, reserve {format_copper(plan["reserve"])}, free {format_copper(plan["free_cash"])}.',
        '',
        '## Market',
        f'{summary["state"]}: {summary["index_change_pct"]:+.2f}% across {summary["liquid_items"]} liquid items over '
        f'{summary["hours_observed"]}h ({summary["rising"]} up, {summary["falling"]} down). '
        f'Estimated sales {format_copper(summary["turnover_copper_per_hour"])} per hour.',
        '',
        '## How past calls did',
    ]
    if scoring.get('scored'):
        lines.append(f'{scoring["won"]} won, {scoring["lost"]} lost of {scoring["filled"]} filled, from {scoring["calls"]} '
                     f'orders. Hit rate {100 * scoring["hit_rate"]:.0f}%, net {scoring["net_per_unit"]:+.0f}c per unit across scored calls.')
    else:
        lines.append(f'{scoring.get("calls", 0)} orders so far; none has played out yet. Until some do, these limits are unproven.')
    if plan.get('notes_summary'):
        lines += ['', '## Analyst notes in force', plan['notes_summary']]
    if link:
        lines += ['', '## Data link', json.dumps(link)]
    lines += ['', '## Orders', '| Action | Item | Ask | Pay up to | List at | Max units | Now | Sold/h | Book | Net | Hours | Grade |',
              '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |']
    for c in plan['calls']:
        if c.action in ('VENDOR', 'BUY NOW', 'LIMIT'):
            lines.append(f'| {c.action} | {c.name} | {c.ask} | {c.limit} | {c.target} | {c.max_units} | {c.now_units} | '
                         f'{c.sold_per_hour:.0f} | {c.units} | {100 * c.margin:.0f}% | {c.fill_hours:.1f} | {c.grade} |')
    avoid = [c for c in plan['calls'] if c.action == 'AVOID']
    if avoid:
        lines += ['', '## Avoid'] + [f'- {c.name}: {c.why}' for c in avoid[:30]]
    return '\n'.join(lines) + '\n'


def plan_to_json(plan: dict, rev: int) -> str:
    rows = [{k: getattr(c, k) for k in ('item_id', 'name', 'action', 'ask', 'limit', 'target', 'fair', 'max_units', 'now_units',
                                        'now_cost', 'margin', 'fill_hours', 'fill_chance', 'sold_per_hour', 'units', 'grade', 'why')}
            for c in plan['calls']]
    return json.dumps({'rev': rev, 'now': plan['now'], 'cash': plan['cash'], 'reserve': plan['reserve'], 'summary': plan['summary'],
                       'scoring': plan['scoring'], 'calls': rows}, indent=1)


def run_analysis(db, data_dir: Path, cash: int, reserve: int, spec: float = 0.25, addons_root: Path | None = None,
                 now: int | None = None, link: dict | None = None) -> dict | None:
    """One full pass. Returns None until there is a listing to work from."""
    now = int(time.time()) if now is None else now
    ensure_schema(db)
    views = load_views(db, now)
    if not views:
        return None
    notes = load_notes(data_dir, now)
    risk = (notes or {}).get('risk') or {}
    tightened = risk.get('spec_fraction')
    if isinstance(tightened, (int, float)) and 0 < tightened < spec:
        spec = float(tightened)
    plan = quant.build_plan(views, cash, reserve, now, notes=notes, scoring=score_past_calls(db, now))
    rev = store_plan(db, plan)
    out = Path(data_dir) / 'analysis'
    out.mkdir(parents=True, exist_ok=True)
    report = render_report(plan, rev, link)
    _write_atomic(out / 'strategy_report.md', report)
    _write_atomic(out / 'plan.json', plan_to_json(plan, rev))
    slots = 0
    if addons_root is not None and Path(addons_root).is_dir():
        slots = write_plan_slots(addons_root, render_plan_lua(plan, rev, spec))
    md.prune(db, now)
    return {'plan': plan, 'rev': rev, 'report': report, 'slots_written': slots, 'notes_active': notes is not None}
