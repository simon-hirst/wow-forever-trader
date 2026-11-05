from contextlib import ExitStack
import pathlib, tempfile, unittest, zipfile, json, sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from trader.bridge import parse_bridge
from trader.market import MarketStore
from trader.source_update_helper import validate_archive,install,is_allowed

FIXTURE=r'''ForeverTraderBridgeDB = {
  ["version"] = 1,
  ["market"] = "PvP",
  ["books"] = {},
  ["activity_seq"] = 4,
  ["activity"] = {
    { ["uid"]="1",["ts"]=1793854000,["character"]="Test-PvP",["kind"]="AH_BUY",["item_id"]=2304,["qty"]=3,["total"]=31,["status"]="confirmed",["note"]="Unique debit" },
    { ["uid"]="2",["ts"]=1793854010,["character"]="Test-PvP",["kind"]="VENDOR_SELL",["item_id"]=2304,["qty"]=3,["total"]=45,["status"]="confirmed",["note"]="Vendor matched" },
    { ["uid"]="3",["ts"]=1793854020,["character"]="Test-PvP",["kind"]="AH_BUY",["item_id"]=774,["qty"]=0,["total"]=0,["status"]="review",["note"]="Unknown qty" },
    { ["uid"]="4",["ts"]=1793854030,["character"]="Test-PvP",["kind"]="VENDOR_MONEY",["item_id"]=0,["qty"]=0,["total"]=42,["status"]="review",["note"]="Unknown sale" },
  },
  ["wallets"] = {
    ["Test-PvP"]={["character"]="Test-PvP",["copper"]=5477,["ts"]=1793854030},
  },
}'''

class TestAutoActivity(unittest.TestCase):
    def test_receipt_dedup_verified_costs_and_wallet(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as resources:
            path=pathlib.Path(directory)
            fixture=path/'ForeverTraderBridge.lua';fixture.write_text(FIXTURE)
            parsed=parse_bridge(fixture)
            self.assertEqual(len(parsed['activity']),4)
            s=resources.enter_context(MarketStore(path/'data'))
            self.assertEqual(s.ingest_activity(parsed)['confirmed'],2)
            self.assertEqual(s.ingest_activity(parsed)['new_events'],0)
            ledger=s.ledger()
            self.assertEqual(ledger['spent'],31)
            self.assertEqual(ledger['sales_net'],45)
            self.assertEqual(ledger['vendor_realized'],14)
            self.assertEqual(ledger['untracked_sell_qty'],0)
            self.assertEqual(s.latest_wallet()[1],5477)
            self.assertEqual(s.review_count(),2)
            self.assertEqual(len(s.transactions()),2)
            s.confirm_activity('3','774',1,20)
            self.assertEqual(s.review_count(),1)
            self.assertEqual(s.ledger()['holding_cost'],20)
            self.assertEqual(len(s.transactions()),3)
            with self.assertRaises(ValueError):s.confirm_activity('3','774',1,20)
            with self.assertRaises(ValueError):s.confirm_activity('4','774',1,20)
            output=path/'out.zip';s.export_bundle(output)
            with zipfile.ZipFile(output) as z:
                self.assertIn('automatic_activity.csv',z.namelist())
                self.assertIn('wallet_checkpoints.csv',z.namelist())
                summary=json.loads(z.read('summary.json'))
                self.assertEqual(summary['automatic_events_unverified'],1)
                self.assertEqual(summary['latest_saved_wallet']['copper'],5477)
            s.close()
            again=resources.enter_context(MarketStore(path/'data'))
            self.assertEqual(again.ledger()['vendor_realized'],14)
            self.assertEqual(again.ingest_activity(parsed)['confirmed'],0)
            self.assertEqual(again.ledger()['realized'],14)
            again.close()

    def test_source_updates_allow_only_known_project_files(self):
        self.assertFalse(is_allowed('../WTF/Account/secret.lua'))
        self.assertFalse(is_allowed('WTF/Account/secret.lua'))
        self.assertFalse(is_allowed('trader\\evil.py'))
        with tempfile.TemporaryDirectory() as td, ExitStack() as resources:
            p=pathlib.Path(td);z=p/'safe.zip'
            with zipfile.ZipFile(z,'w') as arch:
                arch.writestr('trader/version.py','VERSION=1')
                arch.writestr('START_WINDOWS.cmd','echo hi')
            validate_archive(z);install(z,p/'target')
            self.assertTrue((p/'target/trader/version.py').exists())
            with zipfile.ZipFile(z,'w') as arch:arch.writestr('WTF/personal.txt','bad')
            with self.assertRaises(ValueError):validate_archive(z)

if __name__=='__main__':unittest.main()
