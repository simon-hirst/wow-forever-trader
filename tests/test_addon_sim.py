"""The addon's Lua, run in a simulated client. The purchase tests use the real auxForever
when it is installed on this machine, so they cover the real buy bar and Sniper."""
import random
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.wow_sim import HAVE_LUA, WowSim, find_aux
from trader import link as L
from trader import marketdata as md
from trader import quant, strategy

LINEN, BOLT, HATCHET, CLOAK = 2589, 2996, 2073, 4000

ITEMS = {
    LINEN: dict(name='Linen Cloth', vendor=13, stack=20, quality=1),
    BOLT: dict(name='Bolt of Linen Cloth', vendor=40, stack=20, quality=1),
    HATCHET: dict(name='Dwarven Hatchet', vendor=900, stack=1, quality=2),
    CLOAK: dict(name='Plain Cloak', vendor=0, stack=20, quality=1),
}


def frame_to_bgra(sim, ftype, seq, payload: bytes, noise=0, seed=1) -> bytes:
    """What the strip looks like on screen for one frame, through the real Lua encoder."""
    rng = random.Random(seed)
    link = sim.bridge_ns.Link
    lua_payload = sim.lua.eval('function(t) local s = {}; for i = 1, #t do s[i] = string.char(t[i]) end; return table.concat(s) end')(
        sim.lua.table_from(list(payload)))
    values = link.ToBlocks(link.BuildFrame(ftype, seq, lua_payload))

    def channel(level):
        value = round(255 * level / 7)
        if noise:
            value = min(255, max(0, value + rng.randint(-noise, noise)))
        return value

    row = bytearray()
    blocks = [(i, i, i) for i in range(L.RAMP)]
    for i in range(1, L.DATA_BLOCKS + 1):
        v = values[i]
        blocks.append((v >> 6, (v >> 3) & 7, v & 7))
    for r, g, b in blocks:
        row += bytes((channel(b), channel(g), channel(r), 255)) * L.BLOCK_PX
    return bytes(row) * L.BLOCK_PX


@unittest.skipUnless(HAVE_LUA, 'needs lupa')
class SniperSpeedAndPriority(unittest.TestCase):
    """Priority ordering never alters browse data or skips normal candidates."""

    def setUp(self):
        self.sim = WowSim()
        self.sim.set_items(ITEMS)
        self.sim.load_bridge()
        self.sim.login()
        self.aux = self.sim.bridge_ns.Aux
        self.key = self.sim.lua.eval('function(itemKey) return tostring(itemKey.itemID) end')

    def _rows(self, entries):
        rows = self.sim.lua.table()
        for i, (id_, price, quantity) in enumerate(entries, 1):
            rows[i] = self.sim.lua.table(
                itemKey=self.sim.lua.table(itemID=id_),
                minPrice=price, totalQuantity=quantity)
        return rows

    def test_gone_cooldown_backs_off_without_reopening_on_quantity_churn(self):
        deal = self.sim.lua.table(deal_key='8178:0', unit_buyout_price=100,
                                  ft_browse_price=100, ft_browse_qty=1)
        self.assertEqual(self.aux.ReportUnavailable(deal), 25)
        self.assertTrue(self.aux.IsGoneCooldown('8178:0', 100, 1))
        self.assertTrue(self.aux.IsGoneCooldown('8178:0', 100, 1200))
        self.sim.run(46)
        self.assertFalse(self.aux.IsGoneCooldown('8178:0', 100, 1))
        self.assertEqual(self.aux.ReportUnavailable(deal), 60)
        self.assertTrue(self.aux.IsGoneCooldown('8178:0', 100, 1))
        self.sim.run(121)
        self.assertFalse(self.aux.IsGoneCooldown('8178:0', 100, 1))
        self.assertEqual(self.aux.ReportUnavailable(deal), 180)
        self.assertTrue(self.aux.IsGoneCooldown('8178:0', 100, 2))
        self.assertEqual(self.aux.ReportUnavailable(deal), 180)
        self.assertFalse(self.aux.IsGoneCooldown('8178:0', 90, 1))

    def test_unavailable_gear_quarantines_other_enchantments_at_same_price(self):
        # Real export: 100+ failed 1s Training Sword attempts across enchants.
        # Gear lacks unit_buyout_price, so use buyout_price if page data is absent.
        deal = self.sim.lua.table(
            deal_key='8178:10:14306:0', item_id=8178, buyout_price=100)
        self.assertEqual(self.aux.ReportUnavailable(deal), 25)
        self.assertTrue(self.aux.IsGoneCooldown('8178:10:14321:0', 100, 2000))
        self.assertTrue(self.aux.IsGoneCooldown('8178:10:14306:0', 100, 1999))
        self.assertFalse(self.aux.IsGoneCooldown('8178:10:14321:0', 99, 2000))
        self.assertFalse(self.aux.IsGoneCooldown('2073:0', 100, 2000))
        self.assertEqual(self.aux.ItemGoneStats(), 1)
        self.sim.run(46)
        self.assertFalse(self.aux.IsGoneCooldown('8178:10:14321:0', 100, 2000))

    def test_recent_early_rechecks_do_not_reset_on_quantity_churn(self):
        self.aux.NoteEarlyCheck('8178:0', 100, False, 1)
        self.assertTrue(self.aux.IsRecentEarly('8178:0', 100, 20))
        self.assertFalse(self.aux.IsRecentEarly('8178:0', 99, 20))
        self.assertFalse(self.aux.IsRecentEarly('8178:0', 100, 37))
        # A checked bid-only listing gets a longer rest; quantity is intentionally
        # not part of its fingerprint.
        self.aux.NoteEarlyCheck('8178:0', 100, True, 40)
        self.assertTrue(self.aux.IsRecentEarly('8178:0', 100, 159))
        self.assertFalse(self.aux.IsRecentEarly('8178:0', 100, 160))

    def test_purchase_clears_same_price_caches_without_changing_other_items(self):
        self.aux.NoteEarlyCheck('8178:0', 100, True, 2)
        self.aux.NoteEarlyCheck('5071:0', 100, True, 2)
        deal = self.sim.lua.table(deal_key='8178:0', item_id=8178, buyout_price=100)
        self.aux.ReportUnavailable(deal)
        self.assertTrue(self.aux.IsGoneCooldown('8178:0', 100, 1))
        self.aux.OnPurchased(deal)
        self.assertFalse(self.aux.IsRecentEarly('8178:0', 100, 3))
        self.assertFalse(self.aux.IsGoneCooldown('8178:0', 100, 1))
        self.assertTrue(self.aux.IsRecentEarly('5071:0', 100, 3))

    def test_deadcache_kill_switch_immediately_reenables_stale_candidates(self):
        deal=self.sim.lua.table(deal_key='8178:0',unit_buyout_price=100,
                                ft_browse_price=100,ft_browse_qty=1)
        self.assertEqual(self.aux.ReportUnavailable(deal),25)
        self.assertTrue(self.aux.IsGoneCooldown('8178:0',100,1))
        self.sim.g.SlashCmdList.FOREVERTRADER('deadcache off')
        self.assertFalse(self.aux.DeadCacheOn())
        self.assertEqual(self.aux.GoneStats(),0)
        self.assertFalse(self.aux.IsGoneCooldown('8178:0',100,1))
        self.assertEqual(self.aux.ReportUnavailable(deal),0)
        self.sim.g.SlashCmdList.FOREVERTRADER('deadcache on')
        self.assertTrue(self.aux.DeadCacheOn())
        self.assertEqual(self.aux.ReportUnavailable(deal),25)
        self.assertIn('deadcache on',self.aux.PreviewStats())

    def test_gone_cooldown_is_scoped_to_item_key_not_every_other_item(self):
        deal=self.sim.lua.table(deal_key='8178:0',unit_buyout_price=100,
                                ft_browse_price=100,ft_browse_qty=1)
        self.aux.ReportUnavailable(deal)
        self.assertFalse(self.aux.IsGoneCooldown('5071:0',100,1))
        self.assertEqual(self.aux.GoneStats(),1)
        self.assertGreaterEqual(self.aux.stats.goneReports,1)

    def test_vendor_savings_receive_priority_without_modifying_browse_results(self):
        rows = self._rows([
            (CLOAK, 30, 8), (LINEN, 10, 6),
            (BOLT, 35, 2), (HATCHET, 650, 1), (LINEN, 18, 2)])
        ordered, count = self.aux.RankVendorCandidates(
            rows, self.sim.lua.table(), self.key, 1000)
        self.assertEqual(count, 3)
        self.assertEqual([ordered[i].itemKey.itemID for i in range(1, 6)],
                         [HATCHET, LINEN, BOLT, CLOAK, LINEN])
        self.assertEqual(rows[1].itemKey.itemID, CLOAK)  # original remains unchanged

    def test_unchanged_or_unaffordable_items_do_not_jump_the_queue(self):
        rows = self._rows([(HATCHET, 650, 1), (LINEN, 10, 2), (BOLT, 30, 4)])
        known = self.sim.lua.table_from({str(LINEN): 10})
        ordered, count = self.aux.RankVendorCandidates(rows, known, self.key, 100)
        self.assertEqual(count, 1)
        self.assertEqual([ordered[i].itemKey.itemID for i in range(1, 4)],
                         [BOLT, HATCHET, LINEN])

    def test_gear_signatures_are_distinct_and_idempotent(self):
        records = self.sim.lua.table_from([
            self.sim.lua.table(auction_id=501, name='Sturdy Quarterstaff of Power',
                               commodity=False, search_signature='4566:600:one'),
            self.sim.lua.table(auction_id=502, name='Sturdy Quarterstaff of Magic',
                               commodity=False, search_signature='4566:600:one')])
        self.aux.UniqueGearSignatures(records)
        a, b = records[1].search_signature, records[2].search_signature
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith('4566:600:one'))
        self.aux.UniqueGearSignatures(records)
        self.assertEqual((records[1].search_signature, records[2].search_signature), (a, b))

    def test_speed_modes_are_explicit_and_reject_unknown_names(self):
        self.assertEqual(self.aux.Speed(), 'normal')
        self.assertTrue(self.aux.SetSpeed('normal'))
        self.assertEqual(self.aux.Speed(), 'normal')
        self.assertTrue(self.aux.SetSpeed('safe'))
        self.assertFalse(self.aux.SetSpeed('turbo'))
        self.assertEqual(self.aux.Speed(), 'safe')
        self.assertIn('browse not returned yet', self.aux.StatsLine())


@unittest.skipUnless(HAVE_LUA, 'needs lupa')
class LinkEncoding(unittest.TestCase):
    def setUp(self):
        self.sim = WowSim()
        self.sim.load_bridge()
        self.sim.login()

    def test_every_frame_survives_the_trip_to_python(self):
        decoder = L.StripDecoder()
        for seq, payload in enumerate((b'', b'x', bytes(range(256))[:L.MAX_PAYLOAD], bytes([255]) * L.MAX_PAYLOAD)):
            frame, ramp = decoder.decode(frame_to_bgra(self.sim, 2, seq, payload))
            self.assertTrue(ramp)
            self.assertEqual((frame.type, frame.seq, frame.payload), (2, seq, payload))

    def test_colour_error_is_tolerated(self):
        payload = bytes(random.Random(3).randrange(256) for _ in range(200))
        frame, _ = L.StripDecoder().decode(frame_to_bgra(self.sim, 1, 9, payload, noise=6, seed=4))
        self.assertEqual(frame.payload, payload)

    def test_a_shifted_colour_response_is_calibrated_out(self):
        """If the game's gamma moves every level, the ramp in the same frame moves too."""
        payload = b'hello from the auction house'
        raw = bytearray(frame_to_bgra(self.sim, 3, 5, payload))
        for i in range(0, len(raw), 4):
            for c in range(3):
                raw[i + c] = int(255 * (raw[i + c] / 255) ** 1.25)
        frame, ramp = L.StripDecoder().decode(bytes(raw))
        self.assertTrue(ramp)
        self.assertEqual(frame.payload, payload)

    def test_a_torn_frame_is_rejected(self):
        a = frame_to_bgra(self.sim, 1, 1, b'a' * 200)
        b = frame_to_bgra(self.sim, 1, 2, b'b' * 200)
        row = L.STRIP_WIDTH * 4
        torn = a[:row] + (a[row:row + row // 2] + b[row + row // 2:row * 2]) + a[row * 2:]
        frame, ramp = L.StripDecoder().decode(torn)
        self.assertTrue(ramp)
        self.assertIsNone(frame)

    def test_something_other_than_the_strip_is_ignored(self):
        frame, ramp = L.StripDecoder().decode(bytes([40, 60, 80, 255]) * (L.STRIP_WIDTH * L.STRIP_HEIGHT))
        self.assertIsNone(frame)
        self.assertFalse(ramp)

    def test_varints_survive_absurd_prices(self):
        t = self.sim.lua.table()
        self.sim.bridge_ns.Link.PutVarint(t, 99999999999999)
        self.assertEqual(L.read_varint(bytes(t.values()), 0)[0], 99999999999999)

    def test_test_burst_matches_what_the_app_expects(self):
        payload = L.expected_test_payload(2, 3)
        self.assertEqual(len(payload), L.MAX_PAYLOAD)
        self.assertEqual(L.read_varint(payload, 0)[0], 2)

    def test_state_frame_fields(self):
        ns = self.sim.bridge_ns
        state = L.parse_state(self.sim.lua.eval('function(ns) return ns.BuildState() end')(ns).encode('latin-1'))
        self.assertEqual(state['money'], self.sim.money())
        self.assertEqual(state['phase_name'], 'IDLE')
        self.assertFalse(state['ah_open'])
        self.assertEqual((state['reason'], state['percent']), ('', 0))

    def test_an_old_twelve_field_state_frame_still_parses(self):
        payload = bytes([1, 0, 5, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        self.assertEqual(L.parse_state(payload)['reason'], '')


def frames_of_type(sim, ftype):
    """Drains the addon's send queue through the strip and decodes it, as the app would."""
    out = []
    decoder = L.StripDecoder()
    captured = {}
    link = sim.bridge_ns.Link
    original = sim.lua.eval("""function(link, sink)
        local build = link.BuildFrame
        link.BuildFrame = function(t, seq, payload) sink(t, seq, payload); return build(t, seq, payload) end
    end""")

    def sink(t, seq, payload):
        if t == ftype:
            out.append(payload.encode('latin-1') if isinstance(payload, str) else bytes(payload))
    original(link, sink)
    link.Open()
    sim.run(0.3)
    sim.run_until(lambda: link.QueueLength() == 0, timeout=300, dt=0.05)
    sim.run(0.2)
    return out


@unittest.skipUnless(HAVE_LUA, 'needs lupa')
class MarketRecording(unittest.TestCase):
    def setUp(self):
        self.sim = WowSim()
        self.sim.set_items(ITEMS)
        self.sim.load_bridge()
        self.sim.login()
        self.ns = self.sim.bridge_ns

    def browse(self, rows):
        make = self.sim.lua.eval("""function(py_rows, n)
            local out = {}
            for i = 1, n do
                local r = py_rows[i]
                out[i] = {itemKey = {itemID = r[1], itemLevel = 0, itemSuffix = r[4] or 0}, minPrice = r[2], totalQuantity = r[3]}
            end
            return out
        end""")
        table = self.sim.lua.table_from([self.sim.lua.table_from(list(r)) for r in rows])
        self.ns.Market.OnBrowse(make(table, len(rows)))

    def test_bars_track_price_range_and_flows(self):
        self.browse([(LINEN, 20, 100), (BOLT, 80, 10)])
        self.sim.run(30)
        self.browse([(LINEN, 22, 90), (BOLT, 80, 10)])
        self.sim.run(30)
        self.browse([(LINEN, 18, 130), (BOLT, 80, 10)])
        self.ns.Market.CloseBar()
        rows = {}
        for payload in frames_of_type(self.sim, L.TYPE_BARS):
            _, parsed = L.parse_bars(payload)
            for row in parsed:
                rows[row[0]] = row[1:]
        # open, high, low, close, qty, units out, units in
        self.assertEqual(rows[LINEN], (20, 22, 18, 18, 130, 10, 40))
        self.assertEqual(rows[BOLT], (80, 80, 80, 80, 10, 0, 0))
        saved = self.sim.lua.eval('ForeverTraderBridgeDB')
        self.assertIn(f'{LINEN},20,22,18,18,130,10,40', saved.bars[1].d)

    def test_a_vanished_item_counts_as_units_out(self):
        self.browse([(LINEN, 20, 100), (BOLT, 80, 10)])
        self.sim.run(30)
        self.browse([(LINEN, 20, 100)])
        self.ns.Market.CloseBar()
        rows = {}
        for payload in frames_of_type(self.sim, L.TYPE_BARS):
            for row in L.parse_bars(payload)[1]:
                rows[row[0]] = row[1:]
        self.assertEqual(rows[BOLT][4:6], (0, 10))

    def test_a_second_unchanged_bar_sends_nothing_for_that_item(self):
        self.browse([(LINEN, 20, 100)])
        self.ns.Market.CloseBar()
        frames_of_type(self.sim, L.TYPE_BARS)
        self.sim.run(400)
        self.browse([(LINEN, 20, 100)])
        self.ns.Market.CloseBar()
        saved = self.sim.lua.eval('ForeverTraderBridgeDB')
        self.assertEqual(len(list(saved.bars.values())), 1)

    def test_full_listing_round_trip(self):
        rng = random.Random(11)
        rows = []
        for _ in range(3000):
            rows.append(dict(id=rng.choice((LINEN, BOLT, HATCHET, 777, 888)), count=rng.choice((1, 5, 20)),
                             buyout=rng.randint(10, 500000), band=rng.randint(1, 4)))
        rows.append(dict(id=999, count=1, buyout=99999999999999, band=4))
        rows.append(dict(id=998, count=1, buyout=0, band=4))  # bid-only: not buyable now
        listing = self.sim.lua.table()
        for i, row in enumerate(rows, 1):
            listing[i] = self.sim.lua.table(**row)
        self.sim.g['__ah'].listing = listing
        self.sim.g['__fire']('REPLICATE_ITEM_LIST_UPDATE')
        self.ns.Market.OnReplicateUpdate()
        self.sim.run_until(lambda: not self.ns.Market.IsReading() and self.ns.Market.lastListingRows > 0, timeout=60)
        chunks = frames_of_type(self.sim, L.TYPE_L3)
        assembler = L.SnapshotAssembler()
        for chunk in chunks:
            assembler.add_chunk(chunk, 0)
        ts = self.ns.Market.lastListingTs
        end = bytearray()
        for value in (ts, len(chunks), len(rows) - 1):
            while value >= 128:
                end.append(value % 128 + 128)
                value //= 128
            end.append(value)
        snapshot = assembler.add_end(bytes(end), 0)[0]
        self.assertTrue(snapshot['complete'])
        expected = {}
        for row in rows:
            if row['buyout'] > 0:
                expected.setdefault(row['id'], []).append((row['count'], row['buyout'], row['band']))
        self.assertEqual({k: sorted(v) for k, v in snapshot['items'].items()}, {k: sorted(v) for k, v in expected.items()})
        # The saved-file fallback carries the same data.
        saved = self.sim.lua.eval('ForeverTraderBridgeDB').snapshots[1].data
        self.assertEqual({k: sorted(v) for k, v in md.parse_bridge_snapshot(saved).items()}, {k: sorted(v) for k, v in expected.items()})

    def test_one_lost_chunk_leaves_a_partial_listing(self):
        assembler = L.SnapshotAssembler()
        # ts 100, chunk 0: item 3 (count 1, buyout 50), then item 7 (count 5, buyout 90)
        assembler.add_chunk(bytes([100, 0, 3, 8, 50, 0, 7, 40, 90, 0]), 0)
        snapshot = assembler.add_end(bytes([100, 2, 2]), 1)[0]  # said two chunks, got one
        self.assertFalse(snapshot['complete'])
        # Item 7 may run on into the lost chunk, so only item 3 is known.
        self.assertEqual(snapshot['items'], {3: [(1, 50, 0)]})

    def test_items_either_side_of_a_lost_chunk_are_unknown(self):
        assembler = L.SnapshotAssembler()
        assembler.add_chunk(bytes([100, 0, 3, 8, 50, 0, 7, 40, 90, 0]), 0)
        # chunk 1 (lost) would carry more of item 7; chunk 2 starts with item 7's last rows
        assembler.add_chunk(bytes([100, 2, 7, 16, 95, 0, 9, 8, 60, 0]), 0)
        snapshot = assembler.add_end(bytes([100, 3, 9]), 1)[0]
        self.assertFalse(snapshot['complete'])
        self.assertEqual(snapshot['items'], {3: [(1, 50, 0)], 9: [(1, 60, 0)]})

    def test_a_complete_listing_keeps_items_that_span_chunks(self):
        assembler = L.SnapshotAssembler()
        assembler.add_chunk(bytes([100, 0, 7, 40, 90, 0]), 0)
        assembler.add_chunk(bytes([100, 1, 7, 16, 95, 0]), 0)
        snapshot = assembler.add_end(bytes([100, 2, 2]), 1)[0]
        self.assertTrue(snapshot['complete'])
        self.assertEqual(sorted(snapshot['items'][7]), [(2, 95, 0), (5, 90, 0)])


@unittest.skipUnless(HAVE_LUA, 'needs lupa')
class PlanFile(unittest.TestCase):
    def test_game_reads_what_the_app_writes(self):
        sim = WowSim()
        sim.load_bridge()
        plan = {'now': int(sim.g['GetServerTime']()), 'reserve': 500, 'calls': [
            quant.Call(LINEN, 'Linen Cloth', 'LIMIT', 23, 17, 22, 23, 40, 0, 0, .1, .1, 1, .9, 60, 5000, 'C', ''),
            quant.Call(BOLT, 'Bolt', 'WATCH', 90, 0, 0, 90, 0, 0, 0, 0, 0, 0, 0, 1, 10, 'C', '')]}
        sim.g['__planFile'] = strategy.render_plan_lua(plan, 7, 0.2)
        sim.login()  # the first slot loads at login
        self.assertEqual(sim.bridge_ns.Plan.rev, 7)
        entry = sim.bridge_ns.Plan.For(LINEN)
        self.assertEqual((entry.l, entry.q, entry.t), (17, 40, 22))
        self.assertIsNone(sim.bridge_ns.Plan.For(BOLT))
        self.assertEqual((sim.bridge_ns.Plan.reserve, sim.bridge_ns.Plan.spec), (500, 0.2))

    def test_an_older_plan_never_replaces_a_newer_one(self):
        sim = WowSim()
        sim.load_bridge()
        sim.login()
        receive = sim.g['ForeverTrader_ReceivePlan']
        make = lambda rev, limit: sim.lua.table(rev=rev, ts=sim.g['GetServerTime'](), items=sim.lua.table_from({LINEN: sim.lua.table(l=limit)}))
        receive(make(5, 17))
        receive(make(4, 99))
        self.assertEqual(sim.bridge_ns.Plan.For(LINEN).l, 17)

    def test_a_plan_a_day_old_stops_applying(self):
        sim = WowSim()
        sim.load_bridge()
        sim.login()
        sim.g['ForeverTrader_ReceivePlan'](sim.lua.table(rev=1, ts=sim.g['GetServerTime']() - 90000,
                                                          items=sim.lua.table_from({LINEN: sim.lua.table(l=17)})))
        self.assertIsNone(sim.bridge_ns.Plan.For(LINEN))


@unittest.skipUnless(HAVE_LUA, 'needs lupa')
class Sizing(unittest.TestCase):
    def setUp(self):
        self.sim = WowSim()
        self.sim.load_bridge()
        self.size = self.sim.bridge_ns.Snipe.Size

    def tiers(self, *pairs):
        return self.sim.lua.table_from([self.sim.lua.table(commodity_unit_price=p, count=q) for p, q in pairs])

    def deal(self, reason='vendor', vendor=13, tiers=None, **extra):
        return self.sim.lua.table(deal_reason=reason, deal_vendor=vendor, commodity=True, deal_tiers=tiers, **extra)

    def test_below_vendor_buys_everything_affordable(self):
        n, cost, profit = self.size(self.deal(tiers=self.tiers((10, 40), (12, 25))), 100000, 500, 0.25, None)
        self.assertEqual((n, cost, profit), (65, 700, 145))

    def test_reserve_is_never_spent(self):
        n, cost, _ = self.size(self.deal(tiers=self.tiers((10, 40), (12, 25))), 700, 500, 0.25, None)
        self.assertEqual((n, cost), (20, 200))

    def test_wallet_at_or_under_reserve_buys_nothing(self):
        self.assertEqual(self.size(self.deal(tiers=self.tiers((10, 40))), 500, 500, 0.25, None), (0, 0, 0))

    def test_cheapest_first_stops_at_the_budget(self):
        n, cost, _ = self.size(self.deal(tiers=self.tiers((10, 10), (12, 100))), 1000 + 500, 500, 0.25, None)
        self.assertEqual((n, cost), (10 + 75, 100 + 900))

    def test_a_bet_gets_only_its_share_of_free_cash(self):
        plan = self.sim.lua.table(l=15, q=0, t=20, f=20)
        n, cost, _ = self.size(self.deal('plan', 0, self.tiers((10, 1000))), 10500, 500, 0.25, plan)
        self.assertEqual((n, cost), (250, 2500))

    def test_a_bet_respects_the_plan_unit_cap(self):
        plan = self.sim.lua.table(l=15, q=30, t=20, f=20)
        n, _, _ = self.size(self.deal('plan', 0, self.tiers((10, 1000))), 100000, 500, 0.25, plan)
        self.assertEqual(n, 30)

    def test_units_already_bought_count_against_the_cap(self):
        plan = self.sim.lua.table(l=15, q=30, t=20, f=20)
        deal = self.deal('plan', 0, self.tiers((10, 1000)))
        self.assertEqual(self.size(deal, 100000, 500, 0.25, plan, 25)[0], 5)
        self.assertEqual(self.size(deal, 100000, 500, 0.25, plan, 30), (0, 0, 0))

    def test_below_vendor_ignores_what_was_bought_before(self):
        plan = self.sim.lua.table(l=15, q=30, t=20, f=20)
        n, _, _ = self.size(self.deal(tiers=self.tiers((10, 40))), 100000, 500, 0.25, plan, 30)
        self.assertEqual(n, 40)

    def test_the_button_says_why_it_offers_a_deal(self):
        reason = self.sim.bridge_ns.Snipe.Reason
        self.assertEqual(reason(self.deal('vendor', 15), None), 'below vendor price (vendor pays 15c)')
        usual = self.sim.lua.table(deal_reason='usual', deal_percent=36, deal_usual=120)
        self.assertEqual(reason(usual, None), '36% of usual price (usual 1s 20c)')
        bet = self.sim.lua.table(deal_reason='plan', deal_percent=63)
        plan = self.sim.lua.table(l=9, q=47, t=12, f=19)
        self.assertEqual(reason(bet, plan), 'resale bet: list at 12c (63% of fair)')

    def test_gear_must_fit_the_budget_whole(self):
        gear = self.sim.lua.table(deal_reason='vendor', deal_vendor=900, buyout_price=700, count=1, deal_profit=200)
        self.assertEqual(self.size(gear, 1200, 500, 0.25, None), (1, 700, 200))
        self.assertEqual(self.size(gear, 1199, 500, 0.25, None), (0, 0, 0))


@unittest.skipUnless(HAVE_LUA, 'needs lupa')
class LinkReceiptPriority(unittest.TestCase):
    def setUp(self):
        self.sim=WowSim()
        self.sim.set_items(ITEMS)
        self.sim.load_bridge()
        self.sim.login()
        self.link=self.sim.bridge_ns.Link
        self.sim.bridge_ns.Settings().link=True
        self.link.Open()

    def test_sale_receipt_jumps_ahead_of_market_snapshot_backlog(self):
        prior=self.link.QueueLength()
        for index in range(30):
            self.assertTrue(self.link.Enqueue(self.link.TYPE.L3, 'market chunk '+str(index)))
        self.assertTrue(self.link.Enqueue(self.link.TYPE.LOG,
            'FTREC1|1|1793863080|Mage-PvP|VENDOR_SELL|2589|2|26|confirmed'))
        self.assertEqual(self.link.UrgentQueueLength(),1)
        self.assertEqual(self.link.QueueLength(),prior+31)
        for _ in range(4):self.link.Tick(0.2)
        self.assertEqual(self.link.urgentSent,1)
        self.assertEqual(self.link.UrgentQueueLength(),0)
        self.assertGreater(self.link.QueueLength(),0)

    def test_vendor_closing_does_not_discard_pending_critical_receipt(self):
        self.assertTrue(self.link.Enqueue(self.link.TYPE.LOG,
            'FTREC1|2|1793863080|Mage-PvP|VENDOR_SELL|2589|1|13|confirmed'))
        self.link.Close()
        for _ in range(4):self.link.Tick(0.2)
        self.assertEqual(self.link.urgentSent,1)
        self.assertEqual(self.link.droppedUrgent,0)

@unittest.skipUnless(HAVE_LUA and find_aux(), 'needs lupa and an installed auxForever')
class ConfirmWithRealAux(unittest.TestCase):
    def setUp(self):
        self.sim = WowSim()
        self.sim.set_items(ITEMS)
        self.sim.load_aux()
        self.sim.load_bridge()
        self.sim.login()
        self.sim.lua.execute('aux.account.sniper_profit = 1')
        self.ns = self.sim.bridge_ns

    def start(self):
        self.sim.open_auction_house()

    def wait_ready(self, timeout=30):
        self.assertTrue(self.sim.run_until(lambda: self.sim.phase_is('READY'), timeout=timeout), f'never ready: phase {self.sim.phase}')
        self.sim.run(0.3)  # past the anti-misclick delay

    def test_sniper_purchase_journals_once_with_latency_and_no_autobuy(self):
        self.sim.set_commodity(LINEN, [(10, 5)])
        self.start()
        self.wait_ready()
        self.assertEqual(self.sim.bought(), [])
        self.sim.press()
        self.assertTrue(self.sim.run_until(lambda: self.sim.bought(), timeout=5))
        self.assertTrue(self.sim.run_until(
            lambda: self.sim.lua.eval('ForeverTraderBridgeDB.snipe_metrics') is not None,
            timeout=3))
        self.sim.run(2)
        db=self.sim.lua.eval('ForeverTraderBridgeDB')
        rows=list(db.snipe_metrics.values())
        self.assertEqual(len(rows),1)
        row=rows[0]
        self.assertEqual(row.outcome,'BOUGHT')
        self.assertEqual(row.item_id,LINEN)
        self.assertEqual(row.reason,'vendor')
        self.assertGreater(row.offer_finish_ms,0)
        self.assertTrue(row.uid.endswith(':1'))
        self.assertTrue(any('FTPM1|' in line for line in db.log.values()))
        self.assertEqual(len(self.sim.bought()),1)

    def test_incremental_buy_records_page_to_purchase(self):
        self._set_paged_market()
        self.start()
        self.wait_ready(timeout=15)
        self.assertFalse(self.sim.g['C_AuctionHouse'].HasFullBrowseResults())
        self.sim.press()
        self.assertTrue(self.sim.run_until(lambda: self.sim.bought(), timeout=5))
        self.sim.run(1)
        rows=list(self.sim.lua.eval('ForeverTraderBridgeDB.snipe_metrics').values())
        self.assertEqual(len(rows),1)
        self.assertTrue(rows[0].early)
        self.assertEqual(rows[0].source,'early')
        self.assertGreater(rows[0].page_ready_ms,0)
        self.assertGreater(rows[0].page_finish_ms,rows[0].page_ready_ms)


    def test_unanswered_browse_stops_sniper_with_pages_disabled(self):
        # Regression for WOW51900319 session: experimental pages were off,
        # but aux started new whole-AH scans after every 20s timeout.
        self.ns.Settings().pages = False
        self.sim.g['__ah'].dropBrowseAnswers = True
        self.start()
        self.assertTrue(self.sim.run_until(
            lambda: self.ns.Aux.stats.safetyStops >= 1, timeout=30))
        self.assertFalse(self.ns.Aux.IsRunning())
        self.assertEqual(self.ns.Aux.stats.browseFailures, 1)
        self.assertIn('safety stops 1', self.ns.Aux.FaultStats())
        first_browses = self.sim.ah_log().count('browse')
        self.assertEqual(first_browses, 1)
        self.sim.run(12)
        self.assertEqual(self.sim.ah_log().count('browse'), first_browses)

    def test_successful_browse_never_trips_safety_stop(self):
        self.ns.Settings().pages = False
        self.sim.set_commodity(LINEN, [(10, 5)])
        self.start()
        self.assertTrue(self.sim.run_until(
            lambda: self.ns.Aux.stats.browses > 0, timeout=10))
        self.assertEqual(self.ns.Aux.stats.safetyStops, 0)
        self.assertTrue(self.ns.Aux.IsRunning())

    def test_unavailable_listing_is_recorded_as_unavailable_without_buying(self):
        self.sim.set_commodity(LINEN, [(10, 5)])
        self.start()
        self.wait_ready()
        self.sim.set_commodity(LINEN, [(50, 5)])
        self.sim.press()
        self.assertTrue(self.sim.run_until(
            lambda: self.sim.phase_is('LOST'), timeout=15))
        self.sim.run(0.1)
        rows=list(self.sim.lua.eval('ForeverTraderBridgeDB.snipe_metrics').values())
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0].outcome,'UNAVAILABLE')
        self.assertEqual(self.sim.bought(), [])
        self.assertGreaterEqual(self.ns.Aux.stats.goneReports,1)

    def test_browse_is_counted_even_when_quick_buy_interrupts_the_round(self):
        self.sim.set_commodity(LINEN, [(10, 5)])
        self.start()
        self.wait_ready()
        self.assertGreaterEqual(self.ns.Aux.stats.browses, 1)
        # A browse must be visible even if the scan finishes or is interrupted.
        self.assertIn('browses', self.ns.Aux.StatsLine())

    def test_sniper_listing_keeps_same_price_gear_variants_separate(self):
        self.start()
        self.sim.run(1)
        listing = self.ns.Aux.env.listing
        make = self.sim.lua.eval("""function()
            local a = {item_key='4566:0', item_id=4566, auction_id=11,
                name='Sturdy Quarterstaff of Power', link='item:4566',
                search_signature='4566:600:identical',
                count=1, quality=1, buyout_price=600, unit_buyout_price=600,
                bid_price=0, unit_bid_price=0, time_left=2,
                deal_found=time(), deal_profit=100, deal_vendor=700, deal_reason='vendor'}
            local b = {item_key='4566:0', item_id=4566, auction_id=12,
                name='Sturdy Quarterstaff of Magic', link='item:4566',
                search_signature='4566:600:identical',
                count=1, quality=1, buyout_price=600, unit_buyout_price=600,
                bid_price=0, unit_bid_price=0, time_left=2,
                deal_found=time(), deal_profit=100, deal_vendor=700, deal_reason='vendor'}
            return a, b
        end""")
        power, magic = make()
        listing.SetDatabase(listing, self.sim.lua.table_from([power, magic]))
        listing.SetSelectedRecord(listing, magic)
        selection = listing.GetSelection(listing)
        self.assertTrue(selection is not None)
        self.assertTrue(self.sim.lua.eval('function(a,b) return a == b end')(selection.record, magic))

    def test_vendor_only_skips_usual_deals_before_individual_verification(self):
        self.start()
        self.sim.run(1)  # the adapter attaches 0.3s after AH opens
        judge = self.ns.Aux.env.judge
        self.ns.Settings().bets = False
        self.assertIsNone(judge(10, 100, 0, 3, 60, 1))
        self.assertEqual(judge(10, 100, 13, 3, 60, 1)[0], 'vendor')
        self.ns.Settings().bets = True
        self.assertEqual(judge(10, 100, 0, 3, 60, 1)[0], 'usual')

    def test_vendor_only_skips_unaffordable_browse_minimum(self):
        self.start()
        self.sim.run(1)  # wait for the scheduled adapter attachment
        self.ns.Settings().bets = False
        self.ns.Settings().reserve = 99800   # only 2s free
        judge = self.ns.Aux.env.judge
        self.assertEqual(judge(100, None, 150, 0, 60, 1)[0], 'vendor')
        self.assertIsNone(judge(250, None, 300, 0, 60, 1))

    def _set_paged_market(self):
        """Three priced items, with a profitable gear bargain on page one."""
        self.sim.g['__ah'].browsePageSize = 1
        self.sim.g['__ah'].browseLatency = 2.0
        self.sim.set_gear(HATCHET, [(501, 500)])  # vendor 900, +400c
        self.sim.set_commodity(CLOAK, [(800, 2)])  # vendor 0
        self.sim.set_commodity(BOLT, [(1000, 3)])  # vendor 40

    def test_small_vendor_margin_is_checked_early_when_aux_threshold_allows(self):
        # Old hard-coded 25c minimum silently excluded most of the player's
        # 5-24c vendor flips. The page scanner must honour auxForever's 5c.
        self.sim.g['__ah'].browsePageSize = 1
        self.sim.g['__ah'].browseLatency = 1.5
        self.sim.set_commodity(BOLT, [(30, 3)])  # vendor 40, 10c profit
        self.sim.set_commodity(CLOAK, [(300, 1)])
        self.sim.lua.execute('aux.account.sniper_profit = 5')
        self.start()
        self.wait_ready(timeout=15)
        self.assertFalse(self.sim.g['C_AuctionHouse'].HasFullBrowseResults())
        self.assertGreaterEqual(self.ns.Aux.stats.earlyChecks, 1)
        self.assertEqual(self.ns.Aux.stats.earlyDeals, 1)
        self.assertEqual(self.ns.Aux.stats.earlyMisses, 0)
        self.assertGreater(self.ns.Aux.stats.lastEarlyFirst, 0)
        self.assertIn('min 5c', self.ns.Aux.PreviewStats())
        self.assertEqual(self.sim.bought(), [])

    def test_early_check_respects_higher_user_minimum(self):
        self.sim.g['__ah'].browsePageSize = 1
        self.sim.g['__ah'].browseLatency = 0.5
        self.sim.set_commodity(BOLT, [(30, 3)])  # 10c vendor margin
        self.sim.set_commodity(CLOAK, [(300, 1)])
        self.sim.lua.execute('aux.account.sniper_profit = 15')
        self.start()
        self.assertTrue(self.sim.run_until(
            lambda: self.ns.Market.rounds > 0, timeout=15))
        self.assertEqual(self.ns.Aux.stats.earlyChecks, 0)
        self.assertIn('min 15c', self.ns.Aux.PreviewStats())

    def test_early_vendor_deal_before_full_browse_is_not_bought_unattended(self):
        self._set_paged_market()
        self.start()
        self.wait_ready(timeout=15)
        self.assertFalse(self.sim.g['C_AuctionHouse'].HasFullBrowseResults())
        self.assertGreaterEqual(self.ns.Aux.stats.earlyChecks, 1)
        self.assertEqual(self.ns.Aux.stats.earlyDeals, 1)
        self.assertEqual(self.sim.bought(), [])
        events = self.sim.ah_log()
        self.assertIn('search:' + str(HATCHET), events)
        self.assertLess(events.index('search:' + str(HATCHET)),
                        events.index('browse_more') if 'browse_more' in events else len(events))
        self.sim.press()
        self.assertTrue(self.sim.run_until(lambda: self.sim.bought(), timeout=5))
        self.assertEqual(len(self.sim.bought()), 1)
        self.assertEqual(self.sim.bought()[0]['id'], HATCHET)
        self.assertEqual(self.sim.violations(), [])

    def test_cached_only_early_results_are_not_offered_as_fresh_deals(self):
        self._set_paged_market()
        self.sim.g['__ah'].latency = 9.0
        self.start()
        self.assertTrue(self.sim.run_until(
            lambda: self.ns.Aux.stats.earlyCachedFallbacks >= 1, timeout=12))
        self.assertFalse(self.ns.Aux.PagesOn())
        self.assertIn('cached-only fallbacks 1', self.ns.Aux.PreviewStats())
        self.assertFalse(self.sim.phase_is('READY'))
        self.assertEqual(self.sim.bought(), [])

    def test_one_slow_early_verification_falls_back_to_normal_browse(self):
        self._set_paged_market()
        self.sim.g['__ah'].latency = 9.0
        # Force a real 9s event: aux's cached() can otherwise answer from
        # the old client cache after 1 second even while the server is slow.
        self.sim.lua.execute('C_AuctionHouse.HasSearchResults = function() return false end')
        self.start()
        self.assertTrue(self.sim.run_until(
            lambda: self.ns.Aux.stats.earlySlowFallbacks >= 1, timeout=20))
        self.assertFalse(self.ns.Aux.PagesOn())
        self.assertEqual(self.ns.Aux.stats.earlyChecks, 1)
        self.assertIn('slow fallbacks 1', self.ns.Aux.PreviewStats())
        self.assertEqual(self.sim.bought(), [])

    def test_browse_cache_invalidation_safely_disables_incremental_search(self):
        self._set_paged_market()
        self.sim.g['__ah'].invalidateBrowseOnSearch = True
        self.start()
        self.assertTrue(self.sim.run_until(
            lambda: not self.ns.Aux.PagesOn(), timeout=12))
        self.assertEqual(self.ns.Aux.stats.earlyChecks, 1)
        self.assertEqual(self.ns.Market.rounds, 0)
        self.assertEqual(self.sim.bought(), [])
        self.assertIn('fallback', self.ns.Aux.PreviewStats())

    def test_unchanged_dead_lead_is_not_researched_every_new_browse(self):
        self._set_paged_market()
        self.start()
        self.sim.run(0.75) # adapter attached, first page has not yet arrived
        key=self.ns.Aux.scan.item_key_string(self.sim.lua.table(itemID=HATCHET))
        dead=self.sim.lua.table(deal_key=key,unit_buyout_price=500,
                                ft_browse_price=500,ft_browse_qty=1)
        self.assertEqual(self.ns.Aux.ReportUnavailable(dead),25)
        self.assertTrue(self.sim.run_until(
            lambda: self.ns.Market.rounds>=1, timeout=15))
        self.assertEqual(self.ns.Aux.stats.earlyChecks,0)
        self.assertGreaterEqual(self.ns.Aux.stats.goneFilteredEarly,1)
        self.assertGreaterEqual(self.ns.Aux.stats.goneFilteredFull,1)
        self.assertNotIn('search:'+str(HATCHET),self.sim.ah_log())
        self.assertEqual(self.sim.bought(), [])
        self.assertFalse(self.sim.phase_is('READY'))
        # The ordinary market bar still includes this row despite the filtered
        # Sniper queue. Suppression is scoped to purchase selection, not data.
        self.assertEqual(self.ns.Aux.stats.rows,3)

    def test_disabling_incremental_pages_waits_for_entire_browse(self):
        self._set_paged_market()
        self.ns.Settings().pages = False
        self.start()
        self.wait_ready(timeout=25)
        self.assertTrue(self.sim.g['C_AuctionHouse'].HasFullBrowseResults())
        self.assertEqual(self.ns.Aux.stats.earlyChecks, 0)
        self.assertEqual(self.sim.bought(), [])

    def test_normal_pagination_finishes_when_no_early_bargains(self):
        self.sim.g['__ah'].browsePageSize = 1
        self.sim.g['__ah'].browseLatency = 0.5
        self.sim.set_commodity(CLOAK, [(800, 2)])
        self.sim.set_commodity(BOLT, [(1000, 3)])
        self.start()
        self.assertTrue(self.sim.run_until(
            lambda: self.ns.Market.rounds > 0, timeout=15))
        self.assertEqual(self.ns.Aux.stats.earlyChecks, 0)
        self.assertGreaterEqual(self.sim.ah_log().count('browse_more'), 1)
        self.assertEqual(self.sim.bought(), [])

    def test_opening_the_auction_house_starts_everything(self):
        self.start()
        self.sim.run(1)
        self.assertTrue(self.ns.Aux.IsReady())
        self.assertTrue(self.ns.Aux.IsRunning())
        self.assertTrue(self.ns.Aux.IsTabShown())
        self.assertTrue(self.ns.Link.IsActive())

    def test_nothing_is_ever_bought_without_a_press(self):
        self.sim.set_commodity(LINEN, [(10, 40)])
        self.start()
        self.wait_ready()
        self.sim.run(60)
        self.assertEqual(self.sim.bought(), [])
        self.assertFalse(any(e.startswith(('start', 'confirm', 'bid')) for e in self.sim.ah_log()))

    def test_one_press_buys_every_unit_under_vendor(self):
        self.sim.set_commodity(LINEN, [(10, 40), (12, 25), (13, 500), (20, 100)])
        self.start()
        self.wait_ready()
        before = self.sim.money()
        self.sim.press()
        self.assertTrue(self.sim.run_until(lambda: self.sim.bought(), timeout=5))
        self.assertEqual(self.sim.bought()[0]['n'], 65)
        self.assertEqual(before - self.sim.money(), 700)
        self.assertEqual(self.sim.violations(), [])
        self.assertTrue(self.sim.run_until(lambda: self.sim.phase_is('DONE'), timeout=3))

    def test_mouse_wheel_notch_counts_as_a_press(self):
        self.sim.set_commodity(LINEN, [(10, 5)])
        self.start()
        self.wait_ready()
        self.sim.wheel()
        self.assertTrue(self.sim.run_until(lambda: self.sim.bought(), timeout=5))
        self.assertEqual(self.sim.violations(), [])

    def test_the_relayed_key_buys_like_a_click(self):
        self.sim.set_commodity(LINEN, [(10, 5)])
        self.start()
        self.wait_ready()
        self.assertEqual(self.sim.g['GetBindingByKey']('NUMPADDIVIDE', True), 'CLICK ForeverTraderConfirmButton:LeftButton')
        self.sim.g['__press'](self.sim.lua.eval("function() __relay_key('NUMPADDIVIDE') end"))
        self.assertTrue(self.sim.run_until(lambda: self.sim.bought(), timeout=5))
        self.assertEqual(self.sim.violations(), [])
        log = list(self.sim.lua.eval('ForeverTraderBridgeDB').log.values())
        self.assertTrue(any('press (key) in READY' in line for line in log), log)

    def test_relay_test_only_logs_and_then_restores_the_key(self):
        self.sim.set_commodity(LINEN, [(10, 5)])
        self.start()
        self.wait_ready()
        self.sim.g.SlashCmdList.FOREVERTRADER('relaytest 5')
        for key in ('NUMPADDIVIDE', 'F12', 'NUMPAD5'):
            self.sim.g['__press'](self.sim.lua.eval(f"function() __relay_key('{key}') end"))
        self.sim.run(1)
        self.assertEqual(self.sim.bought(), [])
        log = list(self.sim.lua.eval('ForeverTraderBridgeDB').log.values())
        for key in ('NUMPADDIVIDE', 'F12', 'NUMPAD5'):
            self.assertTrue(any(f'relay test: {key} arrived' in line for line in log), key)
        self.sim.run(5)
        self.assertEqual(self.sim.g['GetBindingByKey']('NUMPADDIVIDE', True), 'CLICK ForeverTraderConfirmButton:LeftButton')
        self.assertIsNone(self.sim.g['GetBindingByKey']('F12', True))

    def receipts(self):
        db = self.sim.lua.eval('ForeverTraderBridgeDB')
        rows = db.activity.values() if db.activity else []
        return [(r.kind, r.item_id, r.qty, r.total, r.status) for r in rows if r.kind == 'AH_BUY']

    def test_each_purchase_gets_one_confirmed_receipt(self):
        # Live, every buy fired more than one purchase event and all 60 receipts went to REVIEW.
        self.sim.g['__ah'].noisyPurchaseEvents = True
        self.sim.set_commodity(LINEN, [(10, 3)])
        self.sim.set_commodity(BOLT, [(30, 2)])
        self.start()
        for expected in range(1, 3):
            self.wait_ready()
            self.sim.press()
            self.assertTrue(self.sim.run_until(lambda: len(self.sim.bought()) == expected, timeout=5))
        self.sim.run(3)  # receipts settle a moment after the last wallet change
        self.assertEqual(sorted(self.receipts()), sorted([
            ('AH_BUY', LINEN, 3, 30, 'confirmed'), ('AH_BUY', BOLT, 2, 60, 'confirmed')]))

    def test_a_purchase_amid_a_deposit_is_still_confirmed(self):
        self.sim.g['__ah'].noisyPurchaseEvents = True
        self.sim.set_commodity(LINEN, [(10, 3)])
        self.start()
        self.wait_ready()
        self.sim.g['__money'] = self.sim.g['__money'] - 40  # a deposit taken at the same moment
        self.sim.g['__fire']('PLAYER_MONEY')
        self.sim.press()
        self.assertTrue(self.sim.run_until(lambda: self.sim.bought(), timeout=5))
        self.sim.run(3)
        self.assertEqual(self.receipts(), [('AH_BUY', LINEN, 3, 30, 'confirmed')])

    def test_frantic_presses_do_not_double_buy(self):
        self.sim.set_commodity(LINEN, [(10, 40)])
        self.start()
        self.wait_ready()
        for _ in range(25):
            self.sim.press()
            self.sim.run(0.05)
        self.sim.run(3)
        self.assertEqual(len(self.sim.bought()), 1)
        self.assertEqual(sum(1 for e in self.sim.ah_log() if e.startswith('start')), 1)

    def test_a_press_too_soon_after_the_button_appears_does_nothing(self):
        self.sim.set_commodity(LINEN, [(10, 40)])
        self.start()
        self.assertTrue(self.sim.run_until(lambda: self.sim.phase_is('READY'), timeout=30, dt=0.01))
        self.sim.lua.eval('function(ns) ns.Snipe.state.armedAt = GetTime() + 5 end')(self.ns)
        self.sim.press()
        self.sim.run(1)
        self.assertEqual(self.sim.bought(), [])

    def test_when_confirm_also_needs_a_press_a_second_press_finishes_it(self):
        self.sim.g['__ah'].confirmNeedsHardware = True
        self.sim.set_commodity(LINEN, [(10, 40)])
        self.start()
        self.wait_ready()
        self.sim.press()
        self.sim.run(1)
        self.assertEqual(self.sim.bought(), [])
        self.assertFalse(self.ns.Settings().autoConfirm)
        # aux times the blocked attempt out; the engine then offers the deal again, for two presses.
        self.assertTrue(self.sim.run_until(lambda: self.sim.phase_is('LOST') or self.sim.phase_is('READY'), timeout=20))
        self.assertTrue(self.sim.run_until(lambda: self.sim.phase_is('READY'), timeout=30))
        self.assertEqual(self.ns.Aux.stats.uncertainHolds, 0)
        self.sim.run(0.3)
        self.sim.press()
        self.assertTrue(self.sim.run_until(lambda: self.sim.phase_is('CONFIRMING'), timeout=5))
        self.sim.press()
        self.assertTrue(self.sim.run_until(lambda: self.sim.bought(), timeout=5))
        self.assertEqual(len(self.sim.bought()), 1)

    def test_sniped_while_quoting_reoffers_what_is_left(self):
        self.sim.set_commodity(LINEN, [(10, 40), (12, 25)])
        self.start()
        self.wait_ready()
        self.sim.set_commodity(LINEN, [(12, 25)])  # someone else took the 10c tier
        self.sim.press()
        self.assertTrue(self.sim.run_until(lambda: self.sim.phase_is('READY') and self.ns.Snipe.state.n == 25, timeout=15))
        self.assertEqual(self.sim.bought(), [])
        self.sim.run(0.3)
        self.sim.press()
        self.assertTrue(self.sim.run_until(lambda: self.sim.bought(), timeout=5))
        self.assertEqual(self.sim.bought()[0]['n'], 25)

    def test_all_sold_out_is_reported_lost(self):
        self.sim.set_commodity(LINEN, [(10, 40)])
        self.start()
        self.wait_ready()
        self.sim.set_commodity(LINEN, [(50, 100)])
        self.sim.press()
        self.assertTrue(self.sim.run_until(lambda: self.sim.phase_is('LOST'), timeout=15))
        self.assertEqual(self.sim.bought(), [])

    def test_uncertain_purchase_holds_same_item_price_after_timeout(self):
        self.sim.g['__ah'].dropAnswers = True
        self.sim.set_commodity(LINEN, [(10, 5)])
        self.start()
        self.wait_ready()
        self.sim.press()
        self.assertTrue(self.sim.run_until(
            lambda: self.sim.phase_is('LOST'), timeout=20))
        self.assertEqual(self.ns.Aux.stats.uncertainHolds, 1)
        self.sim.run(8)
        self.assertFalse(self.sim.phase_is('READY'))
        self.assertFalse(self.sim.phase_is('PREPARE'))
        self.assertEqual(self.sim.bought(), [])

    def test_cached_only_selection_does_not_reach_ready_or_purchase(self):
        self.ns.Settings().pages = False
        self.sim.g['__ah'].browseLatency = 0.2
        self.sim.g['__ah'].latency = 9.0
        self.sim.set_gear(HATCHET, [(501, 500)])
        self.start()
        self.assertTrue(self.sim.run_until(
            lambda: self.ns.Aux.stats.cachedRefreshSkips >= 1, timeout=12))
        self.assertFalse(self.sim.phase_is('READY'))
        self.assertEqual(self.sim.bought(), [])
        self.assertIn('stale refresh skips 1', self.ns.Aux.FaultStats())

    def test_no_answer_never_buys_twice(self):
        self.sim.g['__ah'].dropAnswers = True
        self.sim.set_commodity(LINEN, [(10, 40)])
        self.start()
        self.wait_ready()
        self.sim.press()
        self.assertTrue(self.sim.run_until(lambda: self.sim.phase_is('LOST'), timeout=20))
        self.assertIn('check your mail', self.ns.Snipe.state.message)
        self.sim.run(5)
        self.assertEqual(sum(1 for e in self.sim.ah_log() if e.startswith('start')), 1)

    def test_reserve_limits_a_vendor_deal(self):
        self.sim.g['__money'] = 900
        self.sim.set_commodity(LINEN, [(10, 400)])
        self.start()
        self.wait_ready()
        self.assertEqual(self.ns.Snipe.state.n, 40)
        self.sim.press()
        self.assertTrue(self.sim.run_until(lambda: self.sim.bought(), timeout=5))
        self.assertEqual(self.sim.money(), 500)

    def test_gear_takes_one_press_per_auction(self):
        self.sim.set_gear(HATCHET, [(501, 500), (502, 500), (503, 700)])
        self.start()
        self.wait_ready()
        self.sim.press()
        self.assertTrue(self.sim.run_until(lambda: len(self.sim.bought()) == 1, timeout=5))
        self.assertEqual(self.sim.violations(), [])
        self.wait_ready(timeout=15)
        self.assertEqual(len(self.sim.bought()), 1)  # nothing further without another press
        self.sim.press()
        self.assertTrue(self.sim.run_until(lambda: len(self.sim.bought()) == 2, timeout=5))
        self.wait_ready(timeout=15)
        self.sim.press()
        self.assertTrue(self.sim.run_until(lambda: len(self.sim.bought()) == 3, timeout=5))
        self.assertEqual(self.sim.money(), 100000 - 1700)
        self.sim.run(5)
        self.assertEqual(self.sim.bought()[-1]['auction'], 503)

    def test_right_click_skips_and_scanning_resumes(self):
        self.sim.set_commodity(LINEN, [(10, 40)])
        self.start()
        self.wait_ready()
        rounds = self.ns.Market.rounds
        self.ns.Snipe.Skip()
        self.sim.run(15)
        self.assertTrue(self.sim.phase_is('IDLE'))
        self.assertGreater(self.ns.Market.rounds, rounds)
        self.assertEqual(self.sim.bought(), [])

    def test_an_ignored_deal_releases_the_scanner(self):
        self.sim.set_commodity(LINEN, [(10, 40)])
        self.start()
        self.wait_ready()
        rounds = self.ns.Market.rounds
        self.sim.run(16)
        self.assertTrue(self.sim.phase_is('IDLE'))
        self.sim.run(10)
        self.assertGreater(self.ns.Market.rounds, rounds)

    def test_plan_limit_makes_a_deal_with_a_capped_position(self):
        self.sim.set_commodity(CLOAK, [(15, 500), (30, 100)])
        self.start()
        self.sim.run(1)
        self.assertTrue(self.sim.phase_is('IDLE'))
        self.sim.g['ForeverTrader_ReceivePlan'](self.sim.lua.table(
            rev=3, ts=self.sim.g['GetServerTime'](), reserve=500,
            items=self.sim.lua.table_from({CLOAK: self.sim.lua.table(l=20, q=100, t=28, f=28, g='B')})))
        self.wait_ready()
        self.assertEqual(self.ns.Snipe.state.n, 100)  # plan cap, not the 500 for sale
        self.assertEqual(self.ns.Snipe.state.deal.deal_reason, 'plan')
        self.sim.press()
        self.assertTrue(self.sim.run_until(lambda: self.sim.bought(), timeout=5))
        self.assertEqual(self.sim.bought()[0], {'id': CLOAK, 'n': 100, 'total': 1500})

    def test_the_plan_cap_holds_across_purchases(self):
        self.sim.set_commodity(CLOAK, [(15, 500)])
        self.sim.g['ForeverTrader_ReceivePlan'](self.sim.lua.table(
            rev=3, ts=self.sim.g['GetServerTime'](), reserve=500,
            items=self.sim.lua.table_from({CLOAK: self.sim.lua.table(l=20, q=60, t=28, f=28, g='B')})))
        self.start()
        self.wait_ready()
        self.assertEqual(self.ns.Snipe.state.n, 60)
        self.sim.press()
        self.assertTrue(self.sim.run_until(lambda: self.sim.bought(), timeout=5))
        self.assertEqual(self.ns.Snipe.Held(CLOAK), 60)
        # 440 still sit under the limit, but the plan's 60 are bought.
        self.sim.run(30)
        self.assertNotEqual(self.sim.phase, self.ns.Snipe.PHASE.READY)
        self.assertEqual(len(self.sim.bought()), 1)
        saved = self.sim.lua.eval('ForeverTraderBridgeDB')
        self.assertEqual(saved.held[CLOAK].n, 60)

    def test_with_bets_off_only_below_vendor_deals_are_offered(self):
        self.sim.set_commodity(CLOAK, [(15, 500)])
        self.sim.set_commodity(LINEN, [(10, 40)])
        self.sim.g['ForeverTrader_ReceivePlan'](self.sim.lua.table(
            rev=3, ts=self.sim.g['GetServerTime'](), reserve=500,
            items=self.sim.lua.table_from({CLOAK: self.sim.lua.table(l=20, q=100, t=28, f=28, g='B')})))
        self.ns.Settings().bets = False
        self.start()
        self.wait_ready()
        self.assertEqual(self.ns.Snipe.state.deal.item_id, LINEN)
        self.sim.press()
        self.assertTrue(self.sim.run_until(lambda: self.sim.bought(), timeout=5))
        self.sim.run(30)
        self.assertEqual([row['id'] for row in self.sim.bought()], [LINEN])

    def test_plan_never_chases_a_price_above_its_limit(self):
        self.sim.set_commodity(CLOAK, [(25, 500)])
        self.sim.g['ForeverTrader_ReceivePlan'](self.sim.lua.table(
            rev=3, ts=self.sim.g['GetServerTime'](), items=self.sim.lua.table_from({CLOAK: self.sim.lua.table(l=20, q=100, t=28, f=28)})))
        self.start()
        self.sim.run(20)
        self.assertTrue(self.sim.phase_is('IDLE'))

    def test_recheck_after_a_buy_does_not_read_as_the_market_selling_out(self):
        self.sim.set_commodity(BOLT, [(30, 5)])
        self.sim.set_commodity(CLOAK, [(100, 50)])
        self.start()
        self.wait_ready()
        self.sim.press()
        self.assertTrue(self.sim.run_until(lambda: self.sim.bought(), timeout=5))
        self.sim.run(3)
        self.ns.Market.CloseBar()
        saved = self.sim.lua.eval('ForeverTraderBridgeDB')
        text = ';'.join(row.d for row in saved.bars.values())
        for row in text.split(';'):
            parts = row.split(',')
            if int(parts[0]) == CLOAK:
                self.assertEqual(parts[6], '0', f'cloak wrongly counted as sold: {row}')

    def test_full_listing_is_requested_every_15_minutes_while_open(self):
        self.sim.set_commodity(CLOAK, [(100, 50)])
        self.start()
        self.sim.run(100, dt=0.2)
        self.assertEqual(sum(1 for e in self.sim.ah_log() if e == 'replicate'), 1)
        self.sim.run(300, dt=0.2)
        self.assertEqual(sum(1 for e in self.sim.ah_log() if e == 'replicate'), 1)
        self.sim.run(700, dt=0.2)
        self.assertEqual(sum(1 for e in self.sim.ah_log() if e == 'replicate'), 2)

    def test_an_ignored_full_listing_request_is_retried_after_three_minutes(self):
        self.sim.set_commodity(CLOAK, [(100, 50)])
        now = self.sim.g['__now']
        self.sim.g['__ah'].replicatedAt = now - 600  # someone else used the server's allowance
        self.start()
        self.sim.run(100, dt=0.2)
        self.assertEqual(self.sim.ah_log().count('replicate ignored'), 1)
        self.sim.run(200, dt=0.2)
        self.assertEqual(self.sim.ah_log().count('replicate ignored'), 2)
        self.sim.run(200, dt=0.2)  # the allowance is back 15 minutes after it was used
        self.assertEqual(self.sim.ah_log().count('replicate'), 1)

    def test_an_aux_full_scan_attempt_does_not_delay_ours(self):
        # aux stamps replicate_time when it asks, even if the server refuses.
        self.sim.lua.execute('aux.account.replicate_time = GetServerTime()')
        self.sim.set_commodity(CLOAK, [(100, 50)])
        self.start()
        self.sim.run(100, dt=0.2)
        self.assertEqual(self.sim.ah_log().count('replicate'), 1)

    def test_nothing_runs_after_the_auction_house_closes(self):
        self.sim.set_commodity(LINEN, [(10, 40)])
        self.start()
        self.wait_ready()
        self.sim.g['__fire']('AUCTION_HOUSE_CLOSED')
        self.sim.run(2)
        self.assertTrue(self.sim.phase_is('IDLE'))
        self.assertFalse(self.sim.g['ForeverTraderConfirmButton'].IsShown(self.sim.g['ForeverTraderConfirmButton']))
        self.sim.press()
        self.sim.run(2)
        self.assertEqual(self.sim.bought(), [])


if __name__ == '__main__':
    unittest.main()
