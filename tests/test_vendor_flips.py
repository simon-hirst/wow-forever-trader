from contextlib import ExitStack
import json, zipfile, tempfile, pathlib, sys, unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from trader.market import MarketStore

class TestVendorFlips(unittest.TestCase):
    def test_complete_vendor_flip_no_auction_house_cut(self):
        with tempfile.TemporaryDirectory() as d, ExitStack() as resources:
            store=resources.enter_context(MarketStore(pathlib.Path(d)))
            store.log_vendor_flip('2304',22,12,15)
            self.assertEqual(len(store.transactions()),2)
            self.assertEqual(store.transactions()[0][-1],'VENDOR')
            result=store.ledger()
            self.assertEqual(result['realized'],66)
            self.assertEqual(result['vendor_realized'],66)
            self.assertEqual(result['holdings_qty'],0)
            self.assertEqual(result['spent'],264)
            self.assertEqual(result['sales_net'],330)
            export=pathlib.Path(d)/'export.zip'
            store.export_bundle(export)
            with zipfile.ZipFile(export) as z:
                self.assertIn('VENDOR',z.read('manual_trades.csv').decode())
                summary=json.loads(z.read('summary.json'))
                self.assertEqual(summary['ledger']['vendor_realized'],66)
            store.close()
            reopened=resources.enter_context(MarketStore(pathlib.Path(d)))
            self.assertEqual(reopened.ledger()['vendor_realized'],66)

    def test_vendor_buy_then_separate_sell_and_ah_sale(self):
        with tempfile.TemporaryDirectory() as d, ExitStack() as resources:
            store=resources.enter_context(MarketStore(pathlib.Path(d)))
            store.log_trade('BUY','1',2,100,fee=3)
            store.log_trade('SELL','1',1,120,channel='VENDOR')
            self.assertEqual(store.ledger()['vendor_realized'],19)  # 120 - (100 + allocated fee 1)
            store.log_trade('SELL','1',1,200)
            result=store.ledger()
            self.assertEqual(result['realized'],107) # 19 + (190 - 102) = 107
            self.assertEqual(result['vendor_realized'],19)
            self.assertEqual(result['holdings_qty'],0)
            self.assertEqual(result['untracked_sell_qty'],0)

    def test_reject_duplicate_or_negative_vendor_flip(self):
        with tempfile.TemporaryDirectory() as d, ExitStack() as resources:
            store=resources.enter_context(MarketStore(pathlib.Path(d)))
            with self.assertRaises(ValueError):store.log_vendor_flip('1',1,30,30)
            self.assertFalse(store.transactions())

if __name__=='__main__':unittest.main()
