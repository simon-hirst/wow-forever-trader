"""The market engine: what each item is worth, how fast it really sells, and at what
price buying it pays once the auction house's cut and the time to resell are counted.

The output is a book of limit orders: for each worthwhile item, the highest price to
pay and how many units. Nothing here assumes a price will revert just because it did
once. Thin evidence earns a higher required margin and a smaller position, and past
calls are scored against what the market later did (see evaluate_calls).
"""
from __future__ import annotations
import math
import statistics
from dataclasses import dataclass, field

AH_CUT = 0.05
HOUR = 3600
DAY = 86400

# Required net margin and largest share of free cash per position, by evidence grade.
GRADES = {
    'A': {'margin': 0.12, 'share': 0.25},
    'B': {'margin': 0.18, 'share': 0.18},
    'C': {'margin': 0.25, 'share': 0.10},
}
GRADE_A_HOURS, GRADE_A_FLOWS, GRADE_A_CALLS, GRADE_A_HIT_RATE = 72, 50, 5, 0.5
GRADE_B_HOURS, GRADE_B_FLOWS = 24, 10

FLOW_HALF_LIFE_HOURS = 12
FAIR_HALF_LIFE_HOURS = 48
MIN_UNITS_PER_DAY = 24
MIN_NET_COPPER = 2
LIQUIDITY_HORIZON_HOURS = 6
FRONT_SHARE = 0.5           # we capture about half the flow while at the front of the book
AHEAD_HOURS = 1.0           # list where about an hour of sales sits ahead of us
FILL_WINDOW_HOURS = 48
FAIR_TARGET_CAP = 1.15
SALVAGE_DISCOUNT = 0.75
DEPOSIT_RATE_12H = 0.15     # of vendor price, lost only if the listing expires unsold
FALLING_SUPPLY_RATIO = 2.0
MAX_PLAN_ROWS = 150


@dataclass
class ItemView:
    item_id: int
    name: str
    stack: int
    vendor: int
    quality: int
    levels: list[tuple[int, int]]          # (unit price, quantity), cheapest first
    times: list[int] = field(default_factory=list)
    robust: list[int] = field(default_factory=list)
    flows: list[tuple] = field(default_factory=list)  # (ts_from, ts_to, sold, sold_value, new, relisted)


@dataclass
class Metrics:
    ask: int
    units: int
    ref: int
    fair: int
    sigma_day: float
    trend_day: float
    sold_per_hour: float
    new_per_hour: float
    value_per_hour: float
    hours: float
    flow_count: int
    spread_pct: float


@dataclass
class Call:
    item_id: int
    name: str
    action: str
    ask: int
    limit: int
    target: int
    fair: int
    max_units: int
    now_units: int
    now_cost: int
    margin: float
    ev_day: float
    fill_hours: float
    fill_chance: float
    sold_per_hour: float
    units: int
    grade: str
    why: str


def weighted_median(values: list[float], weights: list[float]) -> float:
    pairs = sorted(zip(values, weights))
    half = sum(weights) / 2
    cumulative = 0.0
    for value, weight in pairs:
        cumulative += weight
        if cumulative >= half:
            return value
    return pairs[-1][0]


def compute_metrics(view: ItemView, now: int) -> Metrics | None:
    if not view.levels or not view.robust:
        return None
    ask = view.levels[0][0]
    units = sum(q for _, q in view.levels)
    ref = view.robust[-1]
    decay = math.log(2) / (FAIR_HALF_LIFE_HOURS * HOUR)
    weights = [math.exp(-decay * max(0, now - t)) for t in view.times]
    fair = weighted_median([float(v) for v in view.robust], weights)

    sigma_day = trend_day = 0.0
    if len(view.robust) >= 3:
        rets = []
        for i in range(1, len(view.robust)):
            dt = view.times[i] - view.times[i - 1]
            if dt > 0 and view.robust[i] > 0 and view.robust[i - 1] > 0:
                rets.append(math.log(view.robust[i] / view.robust[i - 1]) / math.sqrt(dt / DAY))
        if rets:
            sigma_day = 1.4826 * statistics.median(abs(r) for r in rets)
        span = view.times[-1] - view.times[0]
        if span >= 6 * HOUR and view.robust[0] > 0:
            trend_day = math.log(view.robust[-1] / view.robust[0]) / (span / DAY)

    sold = new = value = weight_hours = hours = 0.0
    flow_decay = math.log(2) / (FLOW_HALF_LIFE_HOURS * HOUR)
    for ts_from, ts_to, sold_units, sold_value, new_units, _relisted in view.flows:
        span_hours = (ts_to - ts_from) / HOUR
        if span_hours <= 0:
            continue
        w = math.exp(-flow_decay * max(0, now - ts_to))
        sold += w * sold_units
        value += w * sold_value
        new += w * new_units
        weight_hours += w * span_hours
        hours += span_hours
    rate = (lambda total: total / weight_hours) if weight_hours > 0 else (lambda _total: 0.0)
    # A few minutes of tape cannot show a rate, so short samples are pulled towards zero.
    confidence = hours / (hours + 1.0)
    spread = 0.0
    if len(view.levels) > 1 and ask > 0:
        spread = 100 * (view.levels[1][0] - ask) / ask
    return Metrics(ask=ask, units=units, ref=ref, fair=int(round(fair)), sigma_day=sigma_day, trend_day=trend_day,
                   sold_per_hour=rate(sold) * confidence, new_per_hour=rate(new) * confidence,
                   value_per_hour=rate(value) * confidence, hours=(view.times[-1] - view.times[0]) / HOUR,
                   flow_count=len(view.flows), spread_pct=spread)


def grade_for(m: Metrics, calls_scored: int, hit_rate: float | None) -> str:
    if m.hours >= GRADE_A_HOURS and m.flow_count >= GRADE_A_FLOWS and calls_scored >= GRADE_A_CALLS \
            and hit_rate is not None and hit_rate >= GRADE_A_HIT_RATE:
        return 'A'
    if m.hours >= GRADE_B_HOURS and m.flow_count >= GRADE_B_FLOWS:
        return 'B'
    return 'C'


def deposit_per_unit(vendor: int) -> int:
    return max(1, math.ceil(vendor * DEPOSIT_RATE_12H)) if vendor > 0 else 1


def exit_plan(view: ItemView, m: Metrics, buy_units: int) -> tuple[int, float]:
    """Where our units would list after buying the front of the book: (price, units ahead)."""
    ahead_allowed = m.sold_per_hour * AHEAD_HOURS
    remaining = buy_units
    ahead = 0.0
    wall = None
    for price, qty in view.levels:
        take = min(qty, remaining)
        remaining -= take
        left = qty - take
        if left <= 0:
            continue
        if ahead + left > ahead_allowed:
            wall = price
            break
        ahead += left
    if wall is None:
        wall = view.levels[-1][0]
    target = max(1, wall - 1)
    if m.fair > 0:
        target = min(target, math.ceil(m.fair * FAIR_TARGET_CAP))
    return target, ahead


def revenue_per_unit(target: int, ahead: float, units: int, m: Metrics, vendor: int) -> tuple[float, float, float]:
    """Expected money back per unit after the cut: (revenue, chance of filling, hours)."""
    flow = max(m.sold_per_hour * FRONT_SHARE, 1e-6)
    hours = (ahead + units) / flow
    chance = 1 - math.exp(-FILL_WINDOW_HOURS / max(hours, 1e-6))
    salvage = max(vendor, math.floor(target * SALVAGE_DISCOUNT * (1 - AH_CUT)))
    revenue = chance * target * (1 - AH_CUT) + (1 - chance) * (salvage - deposit_per_unit(vendor))
    return revenue, chance, hours


def units_at_or_under(levels, limit: int) -> tuple[int, int]:
    units = cost = 0
    for price, qty in levels:
        if price > limit:
            break
        units += qty
        cost += price * qty
    return units, cost


def evaluate_item(view: ItemView, now: int, free_cash: int, calls_scored: int = 0, hit_rate: float | None = None) -> Call | None:
    m = compute_metrics(view, now)
    if m is None or view.quality == 0:
        return None
    grade = grade_for(m, calls_scored, hit_rate)
    rules = GRADES[grade]

    def call(action, limit=0, target=0, max_units=0, now_units=0, now_cost=0, margin=0.0, ev_day=0.0, fill_hours=0.0,
             fill_chance=0.0, why=''):
        return Call(view.item_id, view.name, action, m.ask, limit, target, m.fair, max_units, now_units, now_cost, margin,
                    ev_day, fill_hours, fill_chance, m.sold_per_hour, m.units, grade, why)

    if view.vendor > 0 and m.ask < view.vendor:
        units, cost = units_at_or_under(view.levels, view.vendor - 1)
        net = sum((view.vendor - p) * q for p, q in view.levels if p < view.vendor)
        return call('VENDOR', limit=view.vendor - 1, target=view.vendor, max_units=units, now_units=units, now_cost=cost,
                    margin=net / cost if cost else 0.0, ev_day=9e9, why=f'NPC pays {view.vendor}c: {units} units, {net}c profit')

    if view.stack <= 1:
        return None  # gear resale depends on stats and buyers, not a tape we can read
    per_day = m.sold_per_hour * 24
    if per_day < MIN_UNITS_PER_DAY:
        return call('WATCH', why=f'Too thin to trade: about {per_day:.0f} sold per day')
    if m.new_per_hour > FALLING_SUPPLY_RATIO * max(m.sold_per_hour, 1e-6) and m.trend_day < 0:
        return call('AVOID', why=f'Supply arriving {m.new_per_hour / max(m.sold_per_hour, 1e-6):.1f}x faster than it sells while the price falls')

    affordable = free_cash // max(m.ask, 1)
    size = max(1, min(int(m.sold_per_hour * FRONT_SHARE * LIQUIDITY_HORIZON_HOURS),
                      int(free_cash * rules['share']) // max(m.ask, 1), affordable))
    target, ahead = exit_plan(view, m, size)
    revenue, chance, hours = revenue_per_unit(target, ahead, size, m, view.vendor)
    limit = min(math.floor(revenue / (1 + rules['margin'])), target - 1)
    limit = min(limit, m.fair)
    if view.vendor > 0:
        limit = max(limit, view.vendor - 1)
    if limit < 1 or math.floor(target * (1 - AH_CUT)) - limit < MIN_NET_COPPER:
        return call('WATCH', target=target, why=f'No room after the cut: fair {m.fair}c, resale {target}c')

    now_units, now_cost = units_at_or_under(view.levels, limit)
    now_units = min(now_units, size)
    if now_units > 0:
        now_cost = 0
        left = now_units
        for price, qty in view.levels:
            take = min(left, qty)
            now_cost += take * price
            left -= take
            if not left:
                break
    entry = now_cost / now_units if now_units else limit
    margin = (revenue - entry) / entry if entry else 0.0
    ev_day = margin * 24 / (hours + 4)
    text = (f'Sells about {m.sold_per_hour:.0f}/h. Pay up to {limit}c, list {target}c, '
            f'about {hours:.0f}h to clear {size}. {margin * 100:.0f}% net expected')
    action = 'BUY NOW' if now_units > 0 else 'LIMIT'
    return call(action, limit=limit, target=target, max_units=size, now_units=now_units, now_cost=now_cost, margin=margin,
                ev_day=ev_day, fill_hours=hours, fill_chance=chance, why=text)


def allocate(calls: list[Call], free_cash: int) -> list[Call]:
    """Best first, limited by cash. Only orders executable now use money up: a resting
    limit may never fill, and the addon re-checks the wallet when one does."""
    order = {'VENDOR': 0, 'BUY NOW': 1, 'LIMIT': 2, 'WATCH': 3, 'AVOID': 4}
    calls.sort(key=lambda c: (order.get(c.action, 9), -c.ev_day, -c.sold_per_hour))
    left = free_cash
    for c in calls:
        if c.action == 'BUY NOW':
            if c.now_cost > left:
                scale = left / c.now_cost if c.now_cost else 0
                c.now_units = int(c.now_units * scale)
                c.now_cost = int(c.now_cost * scale)
                if c.now_units <= 0:
                    c.action = 'LIMIT'
                    continue
            left -= c.now_cost
    return calls


def market_summary(views: list[ItemView], metrics: dict[int, Metrics]) -> dict:
    """Is the whole market moving? Worth knowing before reading any single item."""
    changes = []
    for view in views:
        m = metrics.get(view.item_id)
        if m and m.sold_per_hour * 24 >= MIN_UNITS_PER_DAY and len(view.robust) >= 2 and view.robust[0] > 0 and view.robust[-1] > 0:
            changes.append(math.log(view.robust[-1] / view.robust[0]))
    index = statistics.median(changes) if changes else 0.0
    up = sum(1 for c in changes if c > 0.005)
    down = sum(1 for c in changes if c < -0.005)
    hours = max((m.hours for m in metrics.values()), default=0.0)
    turnover = sum(m.value_per_hour for m in metrics.values())
    state = 'Too early to call'
    if hours >= 6 and changes:
        if index > 0.02 and up > 1.5 * down:
            state = 'Broad rise'
        elif index < -0.02 and down > 1.5 * up:
            state = 'Broad fall'
        else:
            state = 'Mixed'
    return {'state': state, 'index_change_pct': round(100 * (math.exp(index) - 1), 2), 'rising': up, 'falling': down,
            'liquid_items': len(changes), 'hours_observed': round(hours, 1), 'turnover_copper_per_hour': int(turnover)}


def evaluate_calls(history: list[dict], lows: dict[int, list[tuple[int, int]]], now: int,
                   fill_window: int = DAY, exit_window: int = 2 * DAY) -> dict:
    """Score earlier limit orders against what the market then did.

    history: {'item_id','ts','limit','target'}, one per item per day (the first call).
    lows: item -> [(ts, lowest ask)], oldest first.
    A call that never touched its limit is not a loss, only unfilled. One that filled
    wins if the ask later reached the target, the price we said we could resell at.
    """
    per_item: dict[int, dict] = {}
    total = {'calls': 0, 'filled': 0, 'won': 0, 'lost': 0, 'open': 0, 'net_per_unit': 0.0}
    for c in history:
        series = lows.get(c['item_id'], [])
        fill_at = next((t for t, price in series if c['ts'] <= t <= c['ts'] + fill_window and price <= c['limit']), None)
        total['calls'] += 1
        entry = per_item.setdefault(c['item_id'], {'calls': 0, 'filled': 0, 'won': 0, 'lost': 0})
        entry['calls'] += 1
        if fill_at is None:
            if now < c['ts'] + fill_window:
                total['open'] += 1
            continue
        total['filled'] += 1
        entry['filled'] += 1
        after = [price for t, price in series if fill_at < t <= fill_at + exit_window]
        if after and max(after) >= c['target']:
            total['won'] += 1
            entry['won'] += 1
            total['net_per_unit'] += math.floor(c['target'] * (1 - AH_CUT)) - c['limit']
        elif now >= fill_at + exit_window:
            total['lost'] += 1
            entry['lost'] += 1
            last = after[-1] if after else c['limit']
            total['net_per_unit'] += math.floor(last * (1 - AH_CUT)) - c['limit']
        else:
            total['open'] += 1
    scored = total['won'] + total['lost']
    total['hit_rate'] = total['won'] / scored if scored else None
    total['scored'] = scored
    return {'total': total, 'items': per_item}


def apply_notes(calls: list[Call], notes: dict | None) -> list[Call]:
    """Merge the analyst's per-item instructions. They can only tighten: notes may drop
    an item, cap its limit or cut its size, but never raise a limit the numbers set."""
    items = (notes or {}).get('items') or {}
    for c in calls:
        note = items.get(str(c.item_id))
        if not isinstance(note, dict):
            continue
        reason = str(note.get('reason', ''))[:160]
        if note.get('action') == 'avoid' and c.action in ('BUY NOW', 'LIMIT'):
            c.action = 'AVOID'
            c.why = f'Analyst: {reason}' if reason else 'Analyst says avoid'
            continue
        cap = note.get('max_limit')
        if isinstance(cap, (int, float)) and 0 < cap < c.limit and c.action in ('BUY NOW', 'LIMIT'):
            c.limit = int(cap)
            if c.ask > c.limit:
                c.action, c.now_units, c.now_cost = 'LIMIT', 0, 0
        units = note.get('max_units')
        if isinstance(units, (int, float)) and 0 < units < c.max_units:
            c.max_units = int(units)
            if c.now_units > c.max_units:
                c.now_cost = int(c.now_cost * c.max_units / c.now_units)
                c.now_units = c.max_units
        if reason:
            c.why = f'{c.why}. Analyst: {reason}'
    return calls


def build_plan(views: list[ItemView], cash: int, reserve: int, now: int, notes: dict | None = None,
               scoring: dict | None = None) -> dict:
    free_cash = max(0, cash - reserve)
    scoring = scoring or {'total': {'scored': 0, 'hit_rate': None}, 'items': {}}
    total = scoring['total']
    metrics, calls = {}, []
    for view in views:
        m = compute_metrics(view, now)
        if m is None:
            continue
        metrics[view.item_id] = m
        call = evaluate_item(view, now, free_cash, total.get('scored', 0), total.get('hit_rate'))
        if call:
            calls.append(call)
    calls = apply_notes(allocate(calls, free_cash), notes)
    return {'now': now, 'cash': cash, 'reserve': reserve, 'free_cash': free_cash, 'calls': calls,
            'metrics': metrics, 'summary': market_summary(views, metrics), 'scoring': total,
            'notes_summary': str((notes or {}).get('summary', ''))[:600]}


def plan_items_for_game(plan: dict) -> dict[int, dict]:
    """The limits the in-game Sniper works from. Vendor deals need no entry: aux finds them."""
    entries = {}
    for c in plan['calls']:
        if c.action in ('BUY NOW', 'LIMIT') and c.limit > 0 and len(entries) < MAX_PLAN_ROWS:
            entries[c.item_id] = {'l': c.limit, 'q': c.max_units, 't': c.target, 'f': c.fair, 'g': c.grade}
    return entries
