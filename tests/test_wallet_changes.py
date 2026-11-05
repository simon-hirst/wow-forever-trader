from contextlib import ExitStack
import json, pathlib, tempfile, unittest, zipfile
from trader.bridge import parse_bridge
from trader.market import MarketStore
from trader.source_update_helper import is_allowed

FIXTURE='''ForeverTraderBridgeDB = {
  version=1, market="PvP", books={}, activity={},
  wallets={ ["Hero-PvP"]={ character="Hero-PvP",copper=5497,ts=1793863000 } },
  wallet_changes={
    {uid="1",character="Hero-PvP",ts=1793862990,copper=5611,delta=134,context="MERCHANT"},
    {uid="2",character="Hero-PvP",ts=1793863000,copper=5497,delta=-114,context="AH"},
    {uid="3",character="Hero-PvP",ts=1793863001,copper=-1,delta=20,context="AH"},
  }
}'''

class TestWalletAudit(unittest.TestCase):
    def test_import_wallet_delta_history_and_dedupe(self):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as resources:
            base=pathlib.Path(temp);f=base/'ForeverTraderBridge.lua';f.write_text(FIXTURE)
            result=parse_bridge(f)
            self.assertEqual(len(result['wallet_changes']),2)
            self.assertEqual([r['delta'] for r in result['wallet_changes']],[134,-114])
            store=resources.enter_context(MarketStore(base/'db'))
            self.assertEqual(store.ingest_activity(result)['wallet_changes'],2)
            self.assertEqual(store.ingest_activity(result)['wallet_changes'],0)
            self.assertEqual(store.latest_wallet()[1],5497)
            self.assertEqual(store.recent_wallet_changes(1)[0][4],-114)
            z=base/'out.zip';store.export_bundle(z)
            with zipfile.ZipFile(z) as archive:
                self.assertEqual(json.loads(archive.read('summary.json'))['wallet_change_events_count'],2)
                history=archive.read('wallet_changes.csv').decode()
                self.assertIn('-114,AH',history)
                self.assertIn('134,MERCHANT',history)

    def test_quick_snipe_assets_in_safe_update(self):
        base=pathlib.Path(__file__).resolve().parents[1]
        code=(base/'tools/aux_sniper_quick_ui.lua').read_text()
        self.assertIn('FOREVER_TRADER_QUICK_SNIPE_BEGIN',code)
        self.assertIn('FOREVER_TRADER_QUICK_SNIPE_END',code)
        self.assertIn('buy_bar.primary_click()',code)
        self.assertIn('listing:SetSelectedRecord(deal)',code)
        self.assertNotIn('C_AuctionHouse.PlaceBid',code)
        self.assertTrue(is_allowed('INSTALL_QUICK_SNIPE_WINDOWS.ps1'))
        self.assertTrue(is_allowed('tools/aux_sniper_quick_ui.lua'))

if __name__=='__main__':unittest.main()
