from contextlib import ExitStack
import io, sys, json, time, zipfile, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from trader.bridge import LuaTableReader, parse_bridge
from trader.auctionator import ParseError
from trader.orderbook import assess
from trader.market import MarketStore

NOW=1793856000
class TestBridge(unittest.TestCase):
    def test_parser_realistic_savedvariables(self):
        lua=b'''ForeverTraderBridgeDB = {\n ["version"] = 1, ["market"] = "PvP", ["books"] = {
          ["2841"] = { ["key"] = "2841", ["item_id"] = 2841, ["name"] = "Bronze Bar",
           ["kind"] = "commodity", ["ts"] = 1793856000, ["full"] = true,
           ["vendor"] = 50, ["total"] = 274, ["levels"] = {
             { ["p"] = 245, ["q"] = 12 }, { ["p"] = 300, ["q"] = 24 },
             { ["p"] = 333, ["q"] = 51 },
            }, },
          ["4360"] = { item_id=4360, name="Test\\\"Item", kind="item",ts=1793856000,full=false,levels={{p=100,q=1}} },
     } }
'''
        with tempfile.TemporaryDirectory() as d, ExitStack() as resources:
            path=Path(d)/'ForeverTraderBridge.lua';path.write_bytes(lua)
            parsed=parse_bridge(path)
        self.assertEqual(parsed['count'],2)
        self.assertEqual(parsed['books']['2841']['levels'],[(245,12),(300,24),(333,51)])
        self.assertEqual(parsed['books']['4360']['name'],'Test"Item')
    def test_reject_invalid_lua(self):
        with tempfile.TemporaryDirectory() as d, ExitStack() as resources:
            path=Path(d)/'bad.lua';path.write_text('ForeverTraderBridgeDB = { version = 1, books = {abc = (require "os") }}')
            with self.assertRaises(ParseError):parse_bridge(path)
    def test_real_screenshots_should_all_skip(self):
        cases={
          'Bronze Bar':[(245,12),(300,24),(310,15)],
          'Copper Bolts':[(76,74),(77,108),(88,12)],
          'Rough Dynamite':[(95,40),(100,48),(150,26)],
          'Scroll of Strength':[(205,10),(399,1),(400,4)]}
        for name,levels in cases.items():
            row={'name':name,'kind':'commodity','levels':levels,'full':True,'observed_at':NOW}
            self.assertEqual(assess(row,4338,500,now=NOW)['status'],'SKIP',name)
    def test_small_opportunity_and_vendor(self):
        thin={'kind':'commodity','full':True,'observed_at':NOW,'levels':[(100,2),(240,2),(300,8)]}
        r=assess(thin,4338,500,now=NOW)
        self.assertEqual(r['status'],'INSPECT')
        self.assertEqual(r['entry_cost'],200)
        self.assertEqual(r['target'],239)
        self.assertGreater(r['gross_theoretical'],0)
        self.assertEqual(assess(thin,4338,500,now=NOW+601)['status'],'STALE')
        thin['full']=False
        self.assertEqual(assess(thin,4338,500,now=NOW)['status'],'PARTIAL')
        vendor={'kind':'commodity','full':True,'observed_at':NOW,'vendor_copper':15,'levels':[(12,22),(20,40)]}
        self.assertEqual(assess(vendor,4338,500,now=NOW)['status'],'VENDOR')
        vendor['kind']='item'
        self.assertEqual(assess(vendor,4338,500,now=NOW)['status'],'VENDOR CHECK')
    def test_ingest_exports_and_persists(self):
        with tempfile.TemporaryDirectory() as d, ExitStack() as resources:
            store=resources.enter_context(MarketStore(Path(d)))
            book={'key':'2841','item_id':'2841','name':'Bronze Bar','kind':'commodity',
                'observed_at':NOW,'full':True,'vendor_copper':1,'total_quantity':20,'levels':[(100,2),(250,4)]}
            payload={'market':'PvP','books':{'2841':book}}
            self.assertEqual(store.ingest_books(payload),1)
            self.assertEqual(store.ingest_books(payload),0)
            r=store.order_book_rows(now=NOW)[0]
            self.assertEqual(r['status'],'INSPECT')
            older=dict(book,observed_at=NOW-100,levels=[(30,100)])
            self.assertEqual(store.ingest_books({'market':'PvP','books':{'2841':older}}),0)
            out=Path(d)/'analysis.zip';store.export_bundle(out)
            with zipfile.ZipFile(out) as z:
                self.assertIn('order_books.csv',z.namelist())
                self.assertIn('Bronze Bar',z.read('order_books.csv').decode())
                self.assertEqual(json.loads(z.read('summary.json'))['orderbooks_recorded'],1)
            store.close()
            reopened=resources.enter_context(MarketStore(Path(d)))
            self.assertEqual(len(reopened.order_book_rows(now=NOW)),1)

if __name__=='__main__':unittest.main()
