"""The market data store and the engine that turns it into limit orders."""
import json
import sqlite3
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from trader import marketdata as md
from trader import quant, strategy
from trader.autopilot import write_export
from trader.bridge import parse_bridge
from trader.market import MarketStore

LINEN, BOLT, HATCHET = 2589, 2996, 2073
T0 = 1793870000


def new_db():
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE names(item_id TEXT PRIMARY KEY, name TEXT)')
    strategy.ensure_schema(db)
    md.ingest_items(db, {
        LINEN: dict(name='Linen Cloth', quality=1, level=5, **{'class': 'Trade Goods'}, subclass='Cloth', stack=20, vendor=13),
        BOLT: dict(name='Bolt', quality=1, level=5, **{'class': 'Trade Goods'}, subclass='Cloth', stack=20, vendor=40),
        HATCHET: dict(name='Hatchet', quality=2, level=5, **{'class': 'Weapon'}, subclass='Axe', stack=1, vendor=900),
    })
    return db


class FlowEstimate(unittest.TestCase):
    def test_stackables_sell_from_the_front_only(self):
        flow = md.estimate_flow({10: 5, 12: 5, 20: 5}, {12: 2, 20: 5}, {}, True)
        self.assertEqual((flow['sold_units'], flow['sold_value']), (8, 5 * 10 + 3 * 12))

    def test_units_behind_an_untouched_level_were_not_sales(self):
        flow = md.estimate_flow({10: 5, 12: 5, 20: 5}, {10: 5, 12: 5}, {}, True)
        self.assertEqual(flow['sold_units'], 0)
        self.assertEqual(flow['cancelled_units'], 5)

    def test_a_seller_moving_a_stack_is_not_a_sale(self):
        flow = md.estimate_flow({10: 20, 12: 5}, {9: 20, 12: 5}, {}, True)
        self.assertEqual(flow['sold_units'], 0)
        self.assertEqual(flow['relisted_units'], 20)
        self.assertEqual(flow['new_units'], 0)

    def test_about_to_expire_units_count_as_expired(self):
        flow = md.estimate_flow({10: 5, 12: 5}, {12: 5}, {10: 5}, True)
        self.assertEqual((flow['expired_units'], flow['sold_units']), (5, 0))

    def test_gear_can_sell_anywhere_near_the_low(self):
        flow = md.estimate_flow({100: 1, 140: 1, 900: 1}, {100: 1}, {}, False)
        self.assertEqual(flow['sold_units'], 1)
        self.assertEqual(flow['cancelled_units'], 1)

    def test_new_listings_are_counted(self):
        self.assertEqual(md.estimate_flow({10: 5}, {10: 5, 11: 7}, {}, True)['new_units'], 7)


class Snapshots(unittest.TestCase):
    def test_flows_are_written_between_two_listings(self):
        db = new_db()
        md.ingest_snapshot(db, T0, {LINEN: [(20, 200, 3), (20, 240, 3)]}, True, 'test')
        md.ingest_snapshot(db, T0 + 900, {LINEN: [(20, 240, 3)]}, True, 'test')
        row = db.execute('SELECT sold_units, sold_value FROM flows WHERE item_id=?', (LINEN,)).fetchone()
        self.assertEqual(row, (20, 200))

    def test_the_same_listing_captured_twice_is_not_a_market_move(self):
        db = new_db()
        rows = {LINEN: [(20, 200, 3)]}
        self.assertTrue(md.ingest_snapshot(db, T0, rows, True, 'link'))
        self.assertFalse(md.ingest_snapshot(db, T0 + 30, rows, True, 'ahledger'))
        self.assertEqual(db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0], 1)

    def test_a_complete_copy_replaces_a_partial_one(self):
        db = new_db()
        md.ingest_snapshot(db, T0, {LINEN: [(20, 200, 3)]}, False, 'link')
        self.assertTrue(md.ingest_snapshot(db, T0 + 10, {LINEN: [(20, 200, 3)], BOLT: [(1, 50, 3)]}, True, 'bridge'))
        self.assertEqual(db.execute('SELECT COUNT(*), MAX(items) FROM snapshots').fetchone(), (1, 2))

    def test_items_missing_from_a_partial_listing_are_not_sold_out(self):
        db = new_db()
        md.ingest_snapshot(db, T0, {LINEN: [(20, 200, 3)], BOLT: [(20, 1000, 3)]}, True, 'test')
        md.ingest_snapshot(db, T0 + 900, {LINEN: [(20, 200, 3)]}, False, 'test')
        self.assertIsNone(db.execute('SELECT 1 FROM flows WHERE item_id=?', (BOLT,)).fetchone())

    def test_an_item_gone_from_a_complete_listing_did_leave(self):
        db = new_db()
        md.ingest_snapshot(db, T0, {LINEN: [(20, 200, 3)], BOLT: [(20, 1000, 3)]}, True, 'test')
        md.ingest_snapshot(db, T0 + 900, {LINEN: [(20, 200, 3)]}, True, 'test')
        self.assertEqual(db.execute('SELECT sold_units FROM flows WHERE item_id=?', (BOLT,)).fetchone()[0], 20)

    def test_listings_too_far_apart_give_no_flow_rate(self):
        db = new_db()
        md.ingest_snapshot(db, T0, {LINEN: [(20, 200, 3)]}, True, 'test')
        md.ingest_snapshot(db, T0 + 7 * 3600, {}, True, 'test')
        md.ingest_snapshot(db, T0 + 7 * 3600, {BOLT: [(1, 50, 3)]}, True, 'test')
        self.assertEqual(db.execute('SELECT COUNT(*) FROM flows').fetchone()[0], 0)

    def test_robust_price_ignores_one_cheap_unit(self):
        self.assertEqual(md.robust_price({5: 1, 20: 1000}), 20)
        self.assertEqual(md.robust_price({5: 50, 20: 1000}), 5)
        self.assertEqual(md.robust_price({700: 1}), 700)


class SavedFileParsers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_aux_items_use_position_as_id_and_survive_a_hash_in_the_name(self):
        path = self.dir / 'auxForever.lua'
        path.write_text('aux = {\n["items"] = {\nnil,\n"Worn Axe#|cnIQ1:|Hitem:2::::|h[Worn Axe]|h|r#1#2#1#Weapon#Axes#INVTYPE_WEAPON#1#135274#7",\n'
                        'nil,\n"Cog #5#|cnIQ1:|Hitem:4::::|h[Cog #5]|h|r#1#5#1#Misc#Junk##20#1#3",\n},\n}\n', encoding='utf-8')
        items = md.parse_aux_items(path)
        self.assertEqual(items[2]['vendor'], 7)
        self.assertEqual(items[2]['stack'], 1)
        self.assertEqual((items[4]['stack'], items[4]['vendor'], items[4]['quality']), (20, 3, 1))
        self.assertNotIn(1, items)

    def test_ahledger_scans(self):
        path = self.dir / 'AHledger.lua'
        path.write_text('AHledgerDB = {\n["scans"] = {\n{\n["ts"] = 100,\n["region"] = "eu",\n["full"] = true,\n'
                        '["data"] = "2589:20:380:0:2;2589:5:0:40:3;9:1:99999999999999:0:4",\n["realm"] = "R",\n},\n},\n}\n', encoding='utf-8')
        scans = md.parse_ahledger_scans(path)
        self.assertEqual(scans, [(100, {2589: [(20, 380, 2)], 9: [(1, 99999999999999, 4)]})])

    def test_bridge_file_with_megabyte_strings_parses_fast(self):
        rows = ';'.join(f'{i}|20.380.3,5.100.2' for i in range(1, 60000))
        bars = ';'.join(f'{i},1,2,1,2,5,1,1' for i in range(1, 20000))
        path = self.dir / 'ForeverTraderBridge.lua'
        path.write_text('ForeverTraderBridgeDB = {\n["version"] = 1,\n["market"] = "PvP",\n["books"] = {\n},\n'
                        f'["snapshots"] = {{\n{{\n["ts"] = 100,\n["rows"] = 5,\n["data"] = "{rows}",\n}},\n}},\n'
                        f'["bars"] = {{\n{{\n["t"] = 300,\n["d"] = "{bars}",\n}},\n}},\n'
                        '["log"] = {\n"a \\"quoted\\" line",\n},\n}\n', encoding='utf-8')
        start = time.time()
        payload = parse_bridge(path)
        self.assertLess(time.time() - start, 2.0)
        self.assertEqual(payload['snapshots'][0][0], 100)
        self.assertEqual(md.parse_bridge_snapshot(payload['snapshots'][0][1])[7], [(20, 380, 3), (5, 100, 2)])
        self.assertEqual(md.parse_bridge_bars(payload['bars'][0][1])[0], (1, 1, 2, 1, 2, 5, 1, 1))
        self.assertEqual(payload['log'], ['a "quoted" line'])


def view(levels, stack=20, vendor=0, quality=1, sold_per_hour=60.0, hours=2.0, price=None, new_per_hour=0.0, item=LINEN):
    """An item with `hours` of tape at a steady sales rate and a steady price."""
    ref = price or levels[0][0]
    times = [T0 - int(3600 * hours) + i * 900 for i in range(int(hours * 4) + 1)]
    flows = [(times[i], times[i + 1], int(sold_per_hour / 4), int(sold_per_hour / 4) * ref, int(new_per_hour / 4), 0)
             for i in range(len(times) - 1)]
    return quant.ItemView(item, 'Item', stack, vendor, quality, sorted(levels), times, [ref] * len(times), flows)


class Engine(unittest.TestCase):
    def test_below_vendor_is_always_first_and_needs_no_history(self):
        v = quant.ItemView(LINEN, 'Linen', 20, 13, 1, [(10, 40), (12, 25), (15, 90)], [T0], [10], [])
        c = quant.evaluate_item(v, T0, 5000)
        self.assertEqual((c.action, c.now_units, c.now_cost, c.limit), ('VENDOR', 65, 700, 12))

    def test_gray_items_are_left_alone(self):
        v = quant.ItemView(LINEN, 'Junk', 20, 13, 0, [(10, 40)], [T0], [10], [])
        self.assertIsNone(quant.evaluate_item(v, T0, 5000))

    def test_gear_is_vendor_only(self):
        self.assertIsNone(quant.evaluate_item(view([(1000, 1)], stack=1, vendor=100), T0, 5000))

    def test_a_thin_market_is_not_traded(self):
        c = quant.evaluate_item(view([(50, 100)], sold_per_hour=0.5), T0, 5000)
        self.assertEqual(c.action, 'WATCH')

    def test_limit_leaves_margin_after_the_cut(self):
        c = quant.evaluate_item(view([(23, 5000)], sold_per_hour=200), T0, 5000)
        self.assertEqual(c.action, 'LIMIT')
        self.assertLess(c.limit, c.ask)
        net = c.target * 0.95 - c.limit
        self.assertGreater(net / c.limit, quant.GRADES['C']['margin'] - 0.05)

    def test_a_price_under_the_limit_is_buy_now_and_sized_to_cash(self):
        c = quant.evaluate_item(view([(10, 50), (23, 5000)], sold_per_hour=200, price=23), T0, 2000)
        self.assertEqual(c.action, 'BUY NOW')
        self.assertLessEqual(c.now_cost, 2000 * quant.GRADES['C']['share'])
        self.assertGreater(c.now_units, 0)
        self.assertEqual(c.grade, 'C')

    def test_supply_outrunning_sales_on_a_falling_price_is_avoided(self):
        v = view([(20, 5000)], sold_per_hour=30, new_per_hour=300, hours=8)
        v.robust = [60 - i for i in range(len(v.robust))]
        c = quant.evaluate_item(v, T0, 5000)
        self.assertEqual(c.action, 'AVOID')

    def test_evidence_grades(self):
        long_tape = view([(23, 5000)], sold_per_hour=200, hours=30)
        self.assertEqual(quant.evaluate_item(long_tape, T0, 5000).grade, 'B')
        longer = view([(23, 5000)], sold_per_hour=200, hours=80)
        self.assertEqual(quant.evaluate_item(longer, T0, 5000).grade, 'B')
        self.assertEqual(quant.evaluate_item(longer, T0, 5000, calls_scored=6, hit_rate=0.7).grade, 'A')
        self.assertEqual(quant.evaluate_item(longer, T0, 5000, calls_scored=6, hit_rate=0.3).grade, 'B')

    def test_more_evidence_allows_a_thinner_margin(self):
        short = quant.evaluate_item(view([(100, 5000)], sold_per_hour=200, hours=2), T0, 50000)
        long = quant.evaluate_item(view([(100, 5000)], sold_per_hour=200, hours=30), T0, 50000)
        self.assertGreater(long.limit, short.limit)

    def test_allocation_never_spends_more_than_free_cash(self):
        calls = []
        for item in range(5):
            c = quant.evaluate_item(view([(10, 500), (23, 5000)], sold_per_hour=2000, price=23, item=item), T0, 2000)
            calls.append(c)
        calls = quant.allocate(calls, 2000)
        self.assertLessEqual(sum(c.now_cost for c in calls if c.action == 'BUY NOW'), 2000)


class CallScoring(unittest.TestCase):
    CALL = {'item_id': LINEN, 'ts': T0, 'limit': 17, 'target': 23}

    def score(self, series, now=T0 + 5 * quant.DAY):
        return quant.evaluate_calls([self.CALL], {LINEN: series}, now)['total']

    def test_a_limit_never_touched_is_unfilled_not_lost(self):
        total = self.score([(T0 + 100, 20), (T0 + 200, 21)])
        self.assertEqual((total['filled'], total['won'], total['lost']), (0, 0, 0))
        self.assertIsNone(total['hit_rate'])

    def test_filled_then_recovered_is_a_win(self):
        total = self.score([(T0 + 100, 17), (T0 + 5000, 24)])
        self.assertEqual((total['filled'], total['won'], total['lost']), (1, 1, 0))
        self.assertEqual(total['net_per_unit'], 21 - 17)
        self.assertEqual(total['hit_rate'], 1.0)

    def test_filled_and_never_recovering_is_a_loss_at_the_last_price(self):
        total = self.score([(T0 + 100, 17), (T0 + 5000, 15)])
        self.assertEqual(total['lost'], 1)
        self.assertEqual(total['net_per_unit'], 14 - 17)

    def test_not_yet_decided_stays_open(self):
        total = self.score([(T0 + 100, 17), (T0 + 200, 18)], now=T0 + 3600)
        self.assertEqual((total['won'], total['lost'], total['open']), (0, 0, 1))


class AnalystNotes(unittest.TestCase):
    def setUp(self):
        self.call = quant.Call(LINEN, 'Linen', 'BUY NOW', 15, 17, 23, 23, 40, 30, 450, .2, 1, 1, .9, 60, 5000, 'C', 'base')

    def test_avoid_removes_an_order(self):
        quant.apply_notes([self.call], {'items': {str(LINEN): {'action': 'avoid', 'reason': 'guild is dumping'}}})
        self.assertEqual(self.call.action, 'AVOID')
        self.assertIn('guild is dumping', self.call.why)

    def test_a_cap_tightens_the_limit(self):
        quant.apply_notes([self.call], {'items': {str(LINEN): {'max_limit': 12}}})
        self.assertEqual((self.call.limit, self.call.action), (12, 'LIMIT'))

    def test_notes_can_never_raise_a_limit_or_size(self):
        quant.apply_notes([self.call], {'items': {str(LINEN): {'max_limit': 99, 'max_units': 9999}}})
        self.assertEqual((self.call.limit, self.call.max_units), (17, 40))

    def test_nonsense_is_ignored(self):
        quant.apply_notes([self.call], {'items': {str(LINEN): 'buy everything'}})
        self.assertEqual(self.call.action, 'BUY NOW')


class FullPass(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = new_db()
        md.ingest_snapshot(self.db, T0, {LINEN: [(20, 460, 3)] * 300}, True, 'test')
        md.ingest_snapshot(self.db, T0 + 900, {LINEN: [(20, 460, 3)] * 250}, True, 'test')
        md.ingest_snapshot(self.db, T0 + 1800, {LINEN: [(20, 460, 3)] * 200}, True, 'test')

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def run_it(self, **kw):
        return strategy.run_analysis(self.db, self.dir, 5000, 500, 0.25, now=T0 + 1800, **kw)

    def test_no_listing_means_no_plan(self):
        db = new_db()
        self.addCleanup(db.close)
        self.assertIsNone(strategy.run_analysis(db, self.dir, 5000, 500, now=T0))

    def test_report_and_plan_are_written(self):
        result = self.run_it()
        self.assertEqual(result['rev'], 1)
        report = (self.dir / 'analysis' / 'strategy_report.md').read_text(encoding='utf-8')
        self.assertIn('Linen Cloth', report)
        plan = json.loads((self.dir / 'analysis' / 'plan.json').read_text(encoding='utf-8'))
        self.assertEqual(plan['calls'][0]['item_id'], LINEN)

    def test_an_unchanged_plan_keeps_its_revision(self):
        self.assertEqual(self.run_it()['rev'], 1)
        self.assertEqual(self.run_it()['rev'], 1)

    def test_plan_slots_are_written_for_the_game(self):
        addons = self.dir / 'AddOns'
        addons.mkdir()
        result = self.run_it(addons_root=addons)
        self.assertEqual(result['slots_written'], strategy.PLAN_SLOTS)
        toc = (addons / 'FTPlan24' / 'FTPlan24.toc').read_text(encoding='utf-8')
        self.assertIn('## LoadOnDemand: 1', toc)
        self.assertIn(f'## Interface: {strategy.INTERFACE_VERSION}', toc)
        self.assertTrue((addons / 'FTPlan01' / 'Plan.lua').read_text(encoding='utf-8').startswith('ForeverTrader_ReceivePlan({rev=1,'))

    def test_notes_apply_until_they_expire(self):
        folder = self.dir / 'analyst'
        folder.mkdir()
        path = strategy.notes_path(self.dir)
        path.write_text(json.dumps({'summary': 'hold linen', 'expires_at': T0 + 3600,
                                    'items': {str(LINEN): {'action': 'avoid', 'reason': 'test'}}}), encoding='utf-8')
        result = self.run_it()
        self.assertTrue(result['notes_active'])
        self.assertEqual(result['plan']['notes_summary'], 'hold linen')
        self.assertEqual([c.action for c in result['plan']['calls'] if c.item_id == LINEN], ['AVOID'])
        path.write_text(json.dumps({'expires_at': T0, 'items': {str(LINEN): {'action': 'avoid'}}}), encoding='utf-8')
        self.assertFalse(self.run_it()['notes_active'])

    def test_a_broken_notes_file_changes_nothing(self):
        folder = self.dir / 'analyst'
        folder.mkdir()
        strategy.notes_path(self.dir).write_text('{ not json', encoding='utf-8')
        self.assertFalse(self.run_it()['notes_active'])


class Exports(unittest.TestCase):
    def test_export_holds_the_report_and_old_zips_are_pruned(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            with MarketStore(data) as store:
                strategy.ensure_schema(store.db)
                md.ingest_items(store.db, {LINEN: dict(name='Linen Cloth', quality=1, level=5, **{'class': 'T'}, subclass='C', stack=20, vendor=13)})
                md.ingest_snapshot(store.db, T0, {LINEN: [(20, 460, 3)] * 5}, True, 'test')
                md.ingest_snapshot(store.db, T0 + 900, {LINEN: [(20, 460, 3)] * 4}, True, 'test')
                strategy.run_analysis(store.db, data, 5000, 500, now=T0 + 900)
                path = write_export(store, data, {'frames': 5}, now=T0)
                with zipfile.ZipFile(path) as z:
                    names = set(z.namelist())
                    summary = json.loads(z.read('summary.json'))
                self.assertLessEqual({'strategy_report.md', 'plan.json', 'flows_recent.csv', 'latest_listing.csv', 'bars_24h.csv', 'summary.json'}, names)
                self.assertEqual(summary['link'], {'frames': 5})
                self.assertFalse(list((data / 'exports').glob('*.tmp')))
                self.assertEqual(json.loads((data / 'exports' / 'latest.json').read_text())['file'], path.name)
                for i in range(45):
                    write_export(store, data, now=T0 + i + 1)
                self.assertEqual(len(list((data / 'exports').glob('forever-market-*.zip'))), 40)


if __name__ == '__main__':
    unittest.main()
