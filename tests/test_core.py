import tempfile, unittest, pathlib, sys, zipfile
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from trader.auctionator import parse_auctionator,parse_aux_names,fmt_copper,_Cbor,ParseError
from trader.market import MarketStore

class TestCore(unittest.TestCase):
    def test_money(self):
        self.assertEqual(fmt_copper(4338),'43s 38c')
        self.assertEqual(fmt_copper(2838),'28s 38c')
        self.assertEqual(fmt_copper(20000),'2g 00s 00c')
    def test_cbor_small(self):
        self.assertEqual(_Cbor(bytes.fromhex('a24161014162820304')).obj(),{b'a':1,b'b':[3,4]})
    def test_cbor_incomplete(self):
        with self.assertRaises(ParseError):_Cbor(b'\xa1').obj()
    def test_fixture_auctionator_parser(self):
        # Compact synthetic CBOR: item 765, m=10, daily qty=42, day high=10
        # {b'765': {b'a': {b'2500': 42}, b'h': {b'2500': 10}, b'm': 10}}
        cbor = bytes.fromhex('a143373635a34161a14432353030182a4168a144323530300a416d0a')
        escaped = ''.join(('\\%03d' % x) if x<32 or x==34 or x==92 else chr(x) for x in cbor)
        content = 'AUCTIONATOR_SAVEDVARS = { ["TimeOfLastBrowseScan"] = 1793852451, }\nAUCTIONATOR_PRICE_DATABASE = { ["__dbversion"] = 8, ["PvP"] = "' + escaped + '", }\n'
        with tempfile.TemporaryDirectory() as d:
            f=pathlib.Path(d)/'Auctionator.lua'
            f.write_bytes(content.encode('latin-1'))
            snapshot=parse_auctionator(f)
            self.assertEqual(snapshot['count'],1)
            self.assertEqual(snapshot['items']['765']['price'],10)
            self.assertEqual(snapshot['items']['765']['quantity'],42)

    def test_history_and_exports(self):
        with tempfile.TemporaryDirectory() as d:
            store=MarketStore(pathlib.Path(d)); item={'id':'2589','price':20,'quantity':22,'daily_high':{},'daily_low':{},'daily_quantity':{}}
            snap={'market':'PvP','scan_time':1793852451,'items':{'2589':item},'count':1}
            self.assertTrue(store.ingest(snap,'hash1'))
            self.assertFalse(store.ingest(snap,'hash1'))
            item2=dict(item,price=12)
            snap2=dict(snap,scan_time=1793852511,items={'2589':item2})
            self.assertTrue(store.ingest(snap2,'hash2'))
            self.assertEqual(store.market_rows()[0]['median'],20)
            self.assertEqual(store.market_rows()[0]['previous_price'],20)
            self.assertEqual(store.market_rows()[0]['change'],-40.0)
            self.assertEqual(store.market_movers()[0]['name'],'Linen Cloth')
            snap3=dict(snap,scan_time=1793852581,items={'2589':item2})
            store.ingest(snap3,'hash3')
            self.assertEqual(store.market_rows()[0]['signal'],'RECHECK')
            store.log_trade('BUY','2589',2,12)
            store.log_trade('SELL','2589',2,25)
            # 50 copper gross - 5% AH fee (~2c) - 24 cost => +24c
            self.assertEqual(store.ledger()['realized'],24)
            out=pathlib.Path(d)/'export.zip';store.export_bundle(out)
            self.assertTrue(zipfile.is_zipfile(out))
            with zipfile.ZipFile(out) as f:
                self.assertIn('market_latest.csv',f.namelist())
                self.assertIn('market_movers.csv',f.namelist())

if __name__=='__main__':unittest.main()

class TestWatchlist(unittest.TestCase):
    def test_watchlist_signal_and_export(self):
        with tempfile.TemporaryDirectory() as d:
            store=MarketStore(pathlib.Path(d))
            store.set_watch('2589',15,'Check linen cloth')
            item={'id':'2589','price':12,'quantity':200}
            snapshot={'market':'PvP','scan_time':1793852451,'items':{'2589':item},'count':1}
            self.assertTrue(store.ingest(snapshot,'scan1'))
            market=store.market_rows(4338,500)[0]
            self.assertEqual(market['name'],'Linen Cloth')
            self.assertTrue(market['watch_alert'])
            self.assertEqual(market['watch_target'],15)
            store.set_name('2589','Custom Linen')
            self.assertEqual(store.market_rows()[0]['name'],'Custom Linen')
            path=pathlib.Path(d)/'analysis.zip';store.export_bundle(path)
            with zipfile.ZipFile(path) as z:
                self.assertIn('"target_copper": 15',z.read('summary.json').decode('utf8'))
            store.remove_watch('2589')
            self.assertFalse(store.market_rows()[0]['watch_alert'])
