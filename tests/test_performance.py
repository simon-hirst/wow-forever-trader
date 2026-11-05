"""Sniper performance: identity, verified receipts, latency and exports."""
from __future__ import annotations
import pathlib
import tempfile
import unittest
import zipfile
from contextlib import ExitStack

from trader import performance
from trader.market import MarketStore
from trader.bridge import parse_bridge
from trader.autopilot import Autopilot
from trader.link import Frame, TYPE_LOG

T0 = 1793863000

def metric(index=1, **kw):
    row=dict(uid=f'Mage-PvP:{index}', ts=T0+index*45,
        character='Mage-PvP', item_id=2589, outcome='BOUGHT',
        qty=2, cost=20, estimated_profit=6,
        early=1, page_ready_ms=500, page_finish_ms=800,
        offer_ready_ms=250, offer_finish_ms=400, reason='vendor', source='early')
    row.update(kw)
    return row

def add_receipt(db,uid,character,kind,item,qty,total,ts,confirmed=True):
    db.execute("""INSERT INTO automatic_events
        (uid,character,time,kind,item_id,qty,total,status,note)
        VALUES(?,?,?,?,?,?,?,?,?)""",
        (str(uid),character,ts,kind,str(item),qty,total,
         'confirmed' if confirmed else 'review','test'))

class TestPerformance(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=MarketStore(pathlib.Path(self.temp.name)/'data')
    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_live_log_parser_dedup_and_malformed_messages(self):
        r=metric()
        wire=performance.PREFIX+'|'.join(str(r[k]) for k in performance.FIELDS)
        decoded=performance.parse_log_line(wire)
        self.assertEqual(decoded['uid'],'Mage-PvP:1')
        self.assertEqual(decoded['page_finish_ms'],800)
        self.assertEqual(self.store.ingest_snipe_metrics({'snipe_metrics':[decoded]}),1)
        self.assertEqual(self.store.ingest_snipe_metrics({'snipe_metrics':[decoded]}),0)
        self.assertIsNone(performance.parse_log_line('ordinary addon log'))
        with self.assertRaises(ValueError):performance.parse_log_line(wire[:-15])
        with self.assertRaises(ValueError):performance.ingest(self.store.db,dict(r,outcome='MADE_UP'))
        self.assertEqual(self.store.sniper_performance()['bought'],1)

    def test_live_link_and_saved_fallback_have_same_uid(self):
        r=metric()
        wire=performance.PREFIX+'|'.join(str(r[k]) for k in performance.FIELDS)
        worker=Autopilot(pathlib.Path(self.temp.name),None,lambda:{})
        worker._handle_frame(self.store,Frame(type=TYPE_LOG,seq=0,payload=wire.encode()))
        self.assertEqual(self.store.sniper_performance()['bought'],1)
        source=pathlib.Path(self.temp.name)/'ForeverTraderBridge.lua'
        source.write_text('''ForeverTraderBridgeDB = {
            version=1, activity={}, books={}, snipe_metrics={
               {uid="Mage-PvP:1", ts=1793863045, character="Mage-PvP",
                item_id=2589,outcome="BOUGHT",qty=2,cost=20,
                estimated_profit=6,early=true,page_ready_ms=500,
                page_finish_ms=800,offer_ready_ms=250,offer_finish_ms=400,
                reason="vendor",source="early" },
            }
        }''')
        parsed=parse_bridge(source)
        self.assertEqual(len(parsed['snipe_metrics']),1)
        self.assertEqual(self.store.ingest_snipe_metrics(parsed),0)
        self.assertEqual(self.store.sniper_performance()['attempts'],1)

    def test_matched_vendor_profit_tracks_character_and_exact_copper(self):
        db=self.store.db
        add_receipt(db,1,'Mage-PvP','AH_BUY',2589,3,101,T0)
        add_receipt(db,2,'Mage-PvP','VENDOR_SELL',2589,2,100,T0+60)
        add_receipt(db,3,'Warrior-PvP','VENDOR_SELL',2589,1,50,T0+65)
        add_receipt(db,4,'Mage-PvP','VENDOR_SELL',2589,1,50,T0+120)
        db.commit()
        pnl=performance.confirmed_vendor_pnl(db)
        self.assertEqual(pnl['realised'],49)  # 150c sold - 101c bought
        self.assertEqual(pnl['matched_qty'],3)
        self.assertEqual(pnl['unmatched_qty'],1)
        self.assertEqual(pnl['open_units'],0)
        self.assertEqual(pnl['confirmed_buys'],1)

    def test_review_events_never_contribute_to_profit(self):
        db=self.store.db
        add_receipt(db,1,'Mage-PvP','AH_BUY',2589,2,20,T0)
        add_receipt(db,2,'Mage-PvP','VENDOR_SELL',2589,2,26,T0+90,False)
        db.commit()
        self.assertEqual(self.store.sniper_performance()['confirmed_vendor']['realised'],0)
        self.assertEqual(self.store.review_count(),1)

    def test_metrics_distinguish_bought_lost_skip_and_unverified_margin(self):
        self.store.ingest_snipe_metrics({'snipe_metrics':[
            metric(1),metric(2,outcome='LOST',qty=0,cost=0),
            metric(3,outcome='SKIPPED',qty=0,cost=0),
            metric(4,outcome='BOUGHT',early=0,page_finish_ms=0,source='full')
        ]})
        p=self.store.sniper_performance()
        self.assertEqual(p['attempts'],4)
        self.assertEqual(p['bought'],2)
        self.assertEqual(p['lost'],1)
        self.assertEqual(p['skipped'],1)
        self.assertAlmostEqual(p['capture_rate'],2/3)
        self.assertEqual(p['median_page_to_buy_ms'],800)
        self.assertEqual(p['confirmed_vendor']['realised'],0)

    def test_active_rate_does_not_use_profit_from_before_telemetry(self):
        db=self.store.db
        # Previously confirmed activity predates the new attempt clock.
        add_receipt(db,1,'Mage-PvP','AH_BUY',2589,1,10,T0-86400)
        add_receipt(db,2,'Mage-PvP','VENDOR_SELL',2589,1,13,T0-86300)
        # A second, *tracked* purchase and sale count towards the rate.
        add_receipt(db,3,'Mage-PvP','AH_BUY',2589,1,10,T0+120)
        add_receipt(db,4,'Mage-PvP','VENDOR_SELL',2589,1,14,T0+690)
        db.commit()
        attempts=[metric(i,ts=T0+i*90) for i in range(1,10)]
        self.store.ingest_snipe_metrics({'snipe_metrics':attempts})
        stats=self.store.sniper_performance()
        self.assertEqual(stats['confirmed_vendor']['realised'],7)
        self.assertEqual(stats['active_seconds'],720)
        self.assertEqual(stats['profit_per_active_hour'],20.0) # 4c over 12 minutes
        self.assertEqual(self.store.sniper_performance()['bought'],9)

    def test_export_has_attempt_csv_and_audited_summary(self):
        self.store.ingest_snipe_metrics({'snipe_metrics':[metric()]})
        path=pathlib.Path(self.temp.name)/'export.zip'
        self.store.export_bundle(path)
        with zipfile.ZipFile(path) as z:
            self.assertIn('sniper_attempts.csv',z.namelist())
            self.assertIn('Mage-PvP:1',z.read('sniper_attempts.csv').decode())
            self.assertIn('sniper_performance',z.read('summary.json').decode())

if __name__=='__main__':
    unittest.main()
