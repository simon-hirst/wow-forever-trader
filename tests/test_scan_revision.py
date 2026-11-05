import pathlib, tempfile, unittest, sqlite3, sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from trader.market import MarketStore

T0=1793852451
T1=1793856850

def snapshot(when,price,count=1):
    return {'scan_time':when,'market':'PvP','count':count,
            'items':{'954':{'id':'954','price':price,'quantity':count}}}

class TestDistinctScans(unittest.TestCase):
    def test_same_timestamp_updates_price_without_manufacturing_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=MarketStore(pathlib.Path(tmp))
            self.assertTrue(store.ingest(snapshot(T0,599,24),'file1'))
            self.assertTrue(store.ingest(snapshot(T1,205,38),'file2'))
            self.assertTrue(store.ingest(snapshot(T1,180,51),'file3'))
            self.assertEqual(store.count_scans(),2)
            rows=store.market_rows()
            self.assertEqual(rows[0]['price'],180)
            self.assertEqual(rows[0]['previous_price'],599)
            self.assertEqual(rows[0]['history'],1)
            self.assertEqual(rows[0]['signal'],'WATCH')
            self.assertFalse(store.ingest(snapshot(T1,180,51),'file4'))
            store.db.close()

    def test_old_db_duplicate_scans_repaired_without_losing_trades_or_books(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=pathlib.Path(tmp)
            store=MarketStore(path)
            store.ingest(snapshot(T0,599,24),'file1')
            store.ingest(snapshot(T1,180,51),'file2')
            store.log_trade('BUY','954',1,180)
            store.set_watch('954',150)
            store.ingest_books({'market':'PvP','books':{'954':{
                'key':'954','item_id':'954','name':'Item #954','kind':'commodity','observed_at':T1,
                'full':True,'vendor_copper':0,'total_quantity':2,'levels':[(180,1),(200,1)]
            }}})
            # Simulate a v0.2.0 DB that saved two independent rows with identical timestamps.
            store.db.execute('DROP INDEX idx_scans_market_time')
            store.db.execute('''INSERT INTO scans (observed_at,market,hash,item_count)
                VALUES (?,?,?,?)''',(T1,'PvP','old-revision',1))
            idx=store.db.execute('SELECT MAX(id) FROM scans').fetchone()[0]
            store.db.execute('INSERT INTO observations VALUES(?,?,?,?)',(idx,'954',150,55))
            store.db.commit()
            store.db.close()
            reopened=MarketStore(path)
            self.assertEqual(reopened.count_scans(),2)
            self.assertEqual(reopened.market_rows()[0]['price'],150)
            self.assertEqual(reopened.transactions()[0][2],'BUY')
            self.assertEqual(reopened.watchlist()['954'][0],150)
            reopened.upsert_names({'954':'Scroll of Strength'})
            self.assertEqual(reopened.order_book_rows(now=T1)[0]['name'],'Scroll of Strength')
            self.assertEqual(len(reopened.order_book_rows(now=T1)),1)

if __name__=='__main__':unittest.main()
