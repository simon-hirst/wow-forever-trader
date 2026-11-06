"""Snapshot store, signals, local trade ledger and export bundles."""
from __future__ import annotations
import sqlite3, json, statistics, time, csv, io, zipfile, hashlib
from pathlib import Path
from .auctionator import fmt_copper
from .seed_names import SEED_NAMES
from .orderbook import assess
from . import marketdata, performance

class MarketStore:
    def __init__(self, directory: Path):
        self.directory=Path(directory); self.directory.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.directory/'market.sqlite3',timeout=20)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS scans (id INTEGER PRIMARY KEY, observed_at INTEGER NOT NULL,
                market TEXT NOT NULL, hash TEXT NOT NULL UNIQUE, item_count INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS observations (scan_id INTEGER NOT NULL,item_id TEXT NOT NULL,
                price INTEGER NOT NULL, qty INTEGER NOT NULL, PRIMARY KEY (scan_id,item_id));
            CREATE INDEX IF NOT EXISTS idx_observation_item ON observations(item_id);
            CREATE TABLE IF NOT EXISTS names (item_id TEXT PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, time INTEGER NOT NULL,
                side TEXT NOT NULL,item_id TEXT NOT NULL,qty INTEGER NOT NULL,unit INTEGER NOT NULL,
                fee INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS watchlist (item_id TEXT PRIMARY KEY, target_copper INTEGER NOT NULL, notes TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS orderbooks (market TEXT NOT NULL, item_key TEXT NOT NULL, item_id TEXT NOT NULL,
                name TEXT NOT NULL, kind TEXT NOT NULL, observed_at INTEGER NOT NULL, full INTEGER NOT NULL,
                vendor INTEGER NOT NULL, total_quantity INTEGER NOT NULL, levels_json TEXT NOT NULL,
                PRIMARY KEY (market,item_key));
            CREATE TABLE IF NOT EXISTS orderbook_history (market TEXT NOT NULL,item_key TEXT NOT NULL,
                observed_at INTEGER NOT NULL,levels_json TEXT NOT NULL,
                PRIMARY KEY(market,item_key,observed_at));
        ''');self.db.commit()
        # SQLite additive migration for previous versions. An AH sale loses its 5% cut;
        # an NPC vendor sale does not. Existing journal entries were AH sales.
        columns={row[1] for row in self.db.execute('PRAGMA table_info(trades)')}
        if 'channel' not in columns:
            self.db.execute("ALTER TABLE trades ADD COLUMN channel TEXT NOT NULL DEFAULT 'AH'")
        # Separate raw automatic evidence from accepted trades.
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS automatic_events (
                uid TEXT PRIMARY KEY,character TEXT NOT NULL,time INTEGER NOT NULL,
                kind TEXT NOT NULL,item_id TEXT NOT NULL,qty INTEGER NOT NULL,
                total INTEGER NOT NULL,status TEXT NOT NULL,note TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS wallets (
                character TEXT PRIMARY KEY,copper INTEGER NOT NULL,
                seen_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS wallet_changes (
                uid TEXT PRIMARY KEY,character TEXT NOT NULL,ts INTEGER NOT NULL,
                copper INTEGER NOT NULL,delta INTEGER NOT NULL,context TEXT NOT NULL);
        """)
        if 'event_uid' not in columns:
            self.db.execute('ALTER TABLE trades ADD COLUMN event_uid TEXT')
        self.db.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_auto_uid ON trades(event_uid)')
        self.db.executemany('INSERT OR IGNORE INTO names (item_id,name) VALUES (?,?)', SEED_NAMES.items())
        self.db.commit()
        marketdata.ensure_schema(self.db)
        performance.ensure_schema(self.db)
        self._repair_duplicate_scan_timestamps()

    def close(self):
        """Release SQLite file handles (required before deleting a database on Windows)."""
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

    def _repair_duplicate_scan_timestamps(self):
        """Keep the latest revision of each independent Auctionator full-scan time.

        Earlier releases counted multiple saved-file writes with the same
        TimeOfLastBrowseScan as independent observations. Preserve newer data;
        leave trades, watchlists and detailed order books untouched.
        """
        with self.db:
            duplicates = self.db.execute('''SELECT market,observed_at,MAX(id)
                FROM scans GROUP BY market,observed_at HAVING COUNT(*)>1''').fetchall()
            for market, observed, keep_id in duplicates:
                old_ids = [row[0] for row in self.db.execute('''SELECT id FROM scans
                    WHERE market=? AND observed_at=? AND id<>?''',
                    (market, observed, keep_id))]
                for old_id in old_ids:
                    self.db.execute('DELETE FROM observations WHERE scan_id=?', (old_id,))
                    self.db.execute('DELETE FROM scans WHERE id=?', (old_id,))
            self.db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS idx_scans_market_time
                ON scans(market, observed_at)''')

    def upsert_names(self,names):
        self.db.executemany('INSERT INTO names(item_id,name) VALUES(?,?) ON CONFLICT(item_id) DO UPDATE SET name=excluded.name',names.items())
        self.db.commit()
    def ingest(self,snapshot,hash_value):
        # Ignore edits to UI configuration / posted auction history if the market scan
        # itself has not changed: otherwise we would manufacture fake observations.
        fingerprint=hashlib.sha256(json.dumps({
            'market':snapshot['market'],'scan_time':snapshot['scan_time'],
            'records':sorted((k,v['price'],v['quantity']) for k,v in snapshot['items'].items())
        },separators=(',',':')).encode('utf-8')).hexdigest()
        if self.db.execute('SELECT id FROM scans WHERE hash=?',(fingerprint,)).fetchone():return False
        # The price database can change without a new Full scan. When the game
        # reports the same scan timestamp, refresh that single observation
        # rather than manufacturing another independent market snapshot.
        existing=self.db.execute('SELECT id FROM scans WHERE market=? AND observed_at=?',
            (snapshot['market'],snapshot['scan_time'])).fetchone()
        with self.db:
            if existing:
                scan_id=existing[0]
                self.db.execute('UPDATE scans SET hash=?,item_count=? WHERE id=?',
                    (fingerprint,snapshot['count'],scan_id))
                self.db.execute('DELETE FROM observations WHERE scan_id=?',(scan_id,))
            else:
                cur=self.db.execute('''INSERT INTO scans(observed_at,market,hash,item_count)
                    VALUES(?,?,?,?)''',
                    (snapshot['scan_time'],snapshot['market'],fingerprint,snapshot['count']))
                scan_id=cur.lastrowid
            self.db.executemany('INSERT INTO observations VALUES(?,?,?,?)',[
                (scan_id,item['id'],item['price'],item['quantity']) for item in snapshot['items'].values()])
        return True
    def overview(self):
        return self.db.execute('SELECT id,observed_at,market,item_count FROM scans ORDER BY id DESC LIMIT 1').fetchone()
    def count_scans(self): return self.db.execute('SELECT COUNT(*) FROM scans').fetchone()[0]
    def set_pref(self,key,value):
        self.db.execute('INSERT INTO preferences(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(value))); self.db.commit()
    def get_pref(self,key,default=''):
        v=self.db.execute('SELECT value FROM preferences WHERE key=?',(key,)).fetchone();return v[0] if v else default
    def watchlist(self):
        return dict((item_id, (price, note)) for item_id,price,note in self.db.execute(
            'SELECT item_id,target_copper,notes FROM watchlist'))
    def set_watch(self,item_id,target_copper,notes=''):
        if not str(item_id).strip() or int(target_copper)<0:raise ValueError('Invalid watch target')
        self.db.execute('INSERT INTO watchlist (item_id,target_copper,notes) VALUES(?,?,?) '
             'ON CONFLICT(item_id) DO UPDATE SET target_copper=excluded.target_copper,notes=excluded.notes',
             (str(item_id),int(target_copper),str(notes)))
        self.db.commit()
    def remove_watch(self,item_id):
        self.db.execute('DELETE FROM watchlist WHERE item_id=?',(str(item_id),));self.db.commit()
    def set_name(self,item_id,name):
        if not str(name).strip():raise ValueError('Name is required')
        self.upsert_names({str(item_id):str(name).strip()})
    def ingest_books(self, payload):
        """Upsert real per-price depth, rejecting older writes and unchanged records."""
        market=payload.get('market','PvP');changed=0
        for key,book in payload.get('books',{}).items():
            observed=int(book['observed_at']);levels=json.dumps(book['levels'],separators=(',',':'))
            prior=self.db.execute('SELECT observed_at,levels_json,full FROM orderbooks WHERE market=? AND item_key=?',
                                  (market,key)).fetchone()
            if prior and observed<prior[0]:continue
            if prior and observed==prior[0] and prior[1]==levels and prior[2]==int(book['full']):continue
            self.db.execute("""INSERT INTO orderbooks(market,item_key,item_id,name,kind,observed_at,full,vendor,total_quantity,levels_json)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(market,item_key) DO UPDATE SET
                item_id=excluded.item_id,name=excluded.name,kind=excluded.kind,
                observed_at=excluded.observed_at,full=excluded.full,vendor=excluded.vendor,
                total_quantity=excluded.total_quantity,levels_json=excluded.levels_json""",
                (market,key,book['item_id'],book['name'],book['kind'],observed,int(book['full']),
                 book.get('vendor_copper',0),book.get('total_quantity',0),levels))
            self.db.execute('INSERT OR IGNORE INTO orderbook_history VALUES(?,?,?,?)',(market,key,observed,levels))
            if not book['name'].startswith('Item #'):
                self.db.execute('INSERT OR IGNORE INTO names VALUES(?,?)',(book['item_id'],book['name']))
            changed+=1
        self.db.commit();return changed

    def order_book_rows(self,cash=4338,reserve=500,now=None):
        latest=self.overview();market=latest[2] if latest else 'PvP'
        rows=[]
        for item_key,item_id,name,kind,observed,full,vendor,total,levels,known_name in self.db.execute("""
            SELECT b.item_key,b.item_id,b.name,b.kind,b.observed_at,b.full,b.vendor,
                b.total_quantity,b.levels_json,n.name
            FROM orderbooks b LEFT JOIN names n ON n.item_id=b.item_id
            WHERE b.market=?""",(market,)):
            resolved_name=known_name if name.startswith('Item #') and known_name else name
            book={'key':item_key,'item_id':item_id,'name':resolved_name,'kind':kind,
                  'observed_at':observed,'full':bool(full),'vendor_copper':vendor,
                  'total_quantity':total,'levels':json.loads(levels)}
            signal=assess(book,cash,reserve,now=now)
            rows.append({**book,**signal})
        priority={'VENDOR':0,'VENDOR CHECK':1,'INSPECT':2,'CAUTION':3,'REVIEW':4,'SKIP':5,'PARTIAL':6,'STALE':7}
        return sorted(rows,key=lambda r:(priority.get(r['status'],9),-r['score'],r['entry_cost'] or 99999999))

    def market_rows(self, cash=4338, reserve=1500):
        """Returns candidate *watch signals*, not guaranteed buyable listings."""
        latest=self.overview()
        if not latest:return []
        scan_id=latest[0]
        raw=self.db.execute('''SELECT o.item_id,COALESCE(n.name,'Item #'||o.item_id),o.price,o.qty
            FROM observations o LEFT JOIN names n ON n.item_id=o.item_id WHERE o.scan_id=?''',(scan_id,)).fetchall()
        previous={}
        old=self.db.execute('SELECT id FROM scans WHERE id<? AND market=? ORDER BY id DESC LIMIT 1',(scan_id,latest[2])).fetchone()
        if old:
            previous={item_id:(prev_price,prev_qty) for item_id,prev_price,prev_qty in self.db.execute('SELECT item_id,price,qty FROM observations WHERE scan_id=?',(old[0],)).fetchall()}
        rows=[]
        watching=self.watchlist()
        for item_id,name,price,qty in raw:
            history=[v[0] for v in self.db.execute('''SELECT o.price FROM observations o
                 JOIN scans s ON s.id=o.scan_id WHERE o.item_id=? AND o.scan_id<? AND s.market=?
                 ORDER BY o.scan_id DESC LIMIT 30''',(item_id,scan_id,latest[2])).fetchall()]
            median=round(statistics.median(history)) if history else None
            discount=(100.0*(median-price)/median) if median and median>0 else None
            prev_record=previous.get(item_id)
            prev,prev_qty=prev_record if prev_record else (None,None)
            change=(100.0*(price-prev)/prev) if prev and prev>0 else None
            affordable=price<=max(0,cash-reserve)
            # No invented profit when no independent market history / spread.
            eligible=(discount is not None and len(history)>=2 and discount>=20 and affordable and qty>0)
            rows.append({'id':item_id,'name':name,'price':price,'qty':qty,'history':len(history),
                         'median':median,'discount':discount,'change':change,'previous_price':prev,'previous_qty':prev_qty,
                         'watch_target':watching.get(item_id,(None,''))[0],
                         'watch_alert':(item_id in watching and price<=watching[item_id][0]),
                         'affordable':affordable,'signal':('RECHECK' if eligible else 'BASELINE' if median is None else 'WATCH'),
                         'potential_net':round(median*.95-price) if eligible else None})
        return rows
    def market_movers(self, cash=4338, reserve=500, minimum_abs_change=20):
        """Price movements from the latest TWO snapshots, never evidence of trades.
        The quantities are daily peak observations, not a count of available buyouts.
        """
        return sorted((r for r in self.market_rows(cash,reserve)
                       if r['previous_price'] is not None and r['change'] is not None
                       and abs(r['change']) >= minimum_abs_change),
                      key=lambda r:(abs(r['change']),r['price']),reverse=True)
    def log_trade(self,side,item_id,qty,price,fee=0,note='',channel='AH'):
        side=str(side).upper(); channel=str(channel).upper()
        if side not in ('BUY','SELL'):raise ValueError('side must be BUY or SELL')
        if channel not in ('AH','VENDOR'):raise ValueError('channel must be AH or VENDOR')
        if not str(item_id).strip() or int(qty)<=0 or int(price)<0 or int(fee)<0:
            raise ValueError('Invalid trade values')
        with self.db:
            self.db.execute("INSERT INTO trades(time,side,item_id,qty,unit,fee,note,channel) VALUES(?,?,?,?,?,?,?,?)",
                (int(time.time()),side,str(item_id),int(qty),int(price),int(fee),str(note),channel))

    def log_vendor_flip(self,item_id,qty,buy_unit,vendor_unit,note=''):
        """Record an ALREADY executed AH purchase and immediate NPC sale atomically.

        Never buys/sells items in game. Only use when both transactions actually
        happened, and don't separately record the same BUY or SELL.
        """
        if not str(item_id).strip() or int(qty)<=0 or int(buy_unit)<0 or int(vendor_unit)<=int(buy_unit):
            raise ValueError('Vendor flip requires actual positive spread and quantity')
        t=int(time.time()); note=str(note or 'Recorded completed vendor flip')
        with self.db:
            self.db.execute("INSERT INTO trades(time,side,item_id,qty,unit,fee,note,channel) VALUES(?,?,?,?,?,?,?,?)",
                (t,'BUY',str(item_id),int(qty),int(buy_unit),0,note,'AH'))
            self.db.execute("INSERT INTO trades(time,side,item_id,qty,unit,fee,note,channel) VALUES(?,?,?,?,?,?,?,?)",
                (t,'SELL',str(item_id),int(qty),int(vendor_unit),0,note,'VENDOR'))

    def ingest_activity(self, payload):
        """Deduplicate receipts; only confirmed events contribute to profit."""
        new_events=0;confirmed=0;wallets_updated=0;wallet_changes=0
        with self.db:
            for row in payload.get('activity',[]):
                uid=row['uid']
                if self.db.execute('SELECT 1 FROM automatic_events WHERE uid=?',(uid,)).fetchone():
                    continue
                self.db.execute('''INSERT INTO automatic_events
                    (uid,character,time,kind,item_id,qty,total,status,note)
                    VALUES(?,?,?,?,?,?,?,?,?)''',
                    (uid,row['character'],row['ts'],row['kind'],row['item_id'],row['qty'],
                     row['total'],row['status'],row['note']))
                new_events+=1
                if row['status']=='confirmed' and row['kind'] in ('AH_BUY','VENDOR_SELL'):
                    self._apply_confirmed_auto(row)
                    confirmed+=1
            for change in payload.get('wallet_changes',[]):
                cursor=self.db.execute('''INSERT OR IGNORE INTO wallet_changes
                    (uid,character,ts,copper,delta,context) VALUES(?,?,?,?,?,?)''',
                    (change['uid'],change['character'],change['ts'],
                     change['copper'],change['delta'],change['context']))
                wallet_changes+=cursor.rowcount
            for wallet in payload.get('wallets',[]):
                prior=self.db.execute('SELECT seen_at,copper FROM wallets WHERE character=?',
                                      (wallet['character'],)).fetchone()
                if prior is None or wallet['ts']>prior[0] or (wallet['ts']==prior[0] and wallet['copper']!=prior[1]):
                    self.db.execute('''INSERT INTO wallets(character,copper,seen_at) VALUES(?,?,?)
                        ON CONFLICT(character) DO UPDATE SET
                        copper=excluded.copper,seen_at=excluded.seen_at''',
                        (wallet['character'],wallet['copper'],wallet['ts']))
                    wallets_updated+=1
        return dict(new_events=new_events,confirmed=confirmed,wallets=wallets_updated,wallet_changes=wallet_changes)

    def _apply_confirmed_auto(self,row):
        kind=row['kind'];side='BUY' if kind=='AH_BUY' else 'SELL'
        channel='AH' if side=='BUY' else 'VENDOR'
        qty=int(row['qty']);total=int(row['total'])
        if qty<=0 or total<=0:raise ValueError('Auto receipt requires positive qty and amount')
        unit,remainder=divmod(total,qty)
        if side=='SELL' and remainder:
            raise ValueError('Vendor receipt amount was not divisible by quantity')
        self.db.execute('''INSERT OR IGNORE INTO trades
            (time,side,item_id,qty,unit,fee,note,channel,event_uid)
            VALUES(?,?,?,?,?,?,?,?,?)''',
            (int(row['ts']),side,str(row['item_id']),qty,unit,remainder,
             '[AUTO] '+str(row['note']),channel,str(row['uid'])))

    def automatic_events(self,limit=250):
        return self.db.execute('''SELECT e.uid,e.time,e.character,e.kind,
            COALESCE(n.name,'Item #'||e.item_id),e.qty,e.total,e.status,e.note
            FROM automatic_events e LEFT JOIN names n ON n.item_id=e.item_id
            ORDER BY CAST(e.uid AS INTEGER) DESC LIMIT ?''',(limit,)).fetchall()

    def review_count(self):
        return self.db.execute("SELECT COUNT(*) FROM automatic_events WHERE status='review'").fetchone()[0]

    def confirm_activity(self,uid,item_id,qty,total):
        """Explicitly verify a previously ambiguous purchase/sale."""
        row=self.db.execute('SELECT character,time,kind,status,note FROM automatic_events WHERE uid=?',
                            (str(uid),)).fetchone()
        if not row or row[3]!='review' or row[2] not in ('AH_BUY','VENDOR_SELL'):
            raise ValueError('Select an unconfirmed AH purchase or NPC sale')
        item_id=str(item_id);qty=int(qty);total=int(total)
        if not item_id.isdigit() or int(item_id)<=0 or qty<=0 or total<=0 or total>10**10:
            raise ValueError('Verified receipt requires item ID, quantity and total')
        if row[2]=='VENDOR_SELL' and total%qty:
            raise ValueError('Vendor sale needs exact unit payout')
        with self.db:
            self.db.execute('''UPDATE automatic_events SET status='confirmed',item_id=?,qty=?,total=?,
                note=note||' [USER VERIFIED]' WHERE uid=?''',(item_id,qty,total,str(uid)))
            self._apply_confirmed_auto(dict(uid=str(uid),ts=row[1],kind=row[2],
                item_id=item_id,qty=qty,total=total,note=row[4]+' [USER VERIFIED]'))

    def ingest_snipe_metrics(self,payload):
        """Idempotent live-link and SavedVariables import; never implies a purchase."""
        count=0
        for raw in payload.get('snipe_metrics',[]):
            count+=performance.ingest(self.db,raw)
        return count

    def sniper_performance(self):
        return performance.summary(self.db)

    def recent_vendor_sales(self,limit=200):
        return performance.vendor_sales(self.db, limit)

    def recent_snipe_attempts(self,limit=300):
        return performance.attempts(self.db,limit)

    def latest_wallet(self):
        return self.db.execute('SELECT character,copper,seen_at FROM wallets ORDER BY seen_at DESC LIMIT 1').fetchone()

    def recent_wallet_changes(self,limit=250):
        return self.db.execute('''SELECT uid,character,ts,copper,delta,context
            FROM wallet_changes ORDER BY CAST(uid AS INTEGER) DESC LIMIT ?''',(limit,)).fetchall()

    def transactions(self):
        return self.db.execute("SELECT t.id,t.time,t.side,t.item_id,COALESCE(n.name,'Item #'||t.item_id), t.qty,t.unit,t.fee,t.note,t.channel FROM trades t LEFT JOIN names n ON n.item_id=t.item_id ORDER BY t.id DESC").fetchall()

    def ledger(self):
        """FIFO cost accounting. Vendor SELL: full price; AH SELL: 5% commission.

        Buy fees are allocated over sold units. Unmatched sales do not generate
        fabricated realised profit. Estimates rely on accurate manual entries.
        """
        buys={};realized=0;vendor_realized=0;buycost=0;sellproceeds=0;unmatched=0;vendor_sold=0
        for _,_,side,item_id,_,qty,price,fee,_,channel in reversed(self.transactions()):
            if side=='BUY':
                buys.setdefault(item_id,[]).append([qty,price,fee]);buycost+=qty*price+fee
                continue
            amount=qty;knowncost=0;matched=0
            for lot in buys.get(item_id,[]):
                if amount==0:break
                if lot[0]<=0:continue
                take=min(amount,lot[0]);amount-=take;matched+=take
                allocated_fee=lot[2] if take==lot[0] else (lot[2]*take//lot[0])
                knowncost+=take*lot[1]+allocated_fee
                lot[2]-=allocated_fee;lot[0]-=take
            unmatched+=amount
            gross=qty*price
            earned= gross if channel=='VENDOR' else round(gross*.95)
            net=earned-fee; sellproceeds+=net
            if matched:
                matched_revenue=(matched*price if channel=='VENDOR' else round(matched*price*.95))
                profit=matched_revenue-knowncost-(fee if amount==0 else 0)
                realized+=profit
                if channel=='VENDOR':vendor_realized+=profit;vendor_sold+=matched
        holding_qty=sum(q for lots in buys.values() for q,_,_ in lots)
        holding_cost=sum(q*p+f for lots in buys.values() for q,p,f in lots)
        return {'realized':realized,'vendor_realized':vendor_realized,'vendor_units_sold':vendor_sold,
                'holdings_qty':holding_qty,'holding_cost':holding_cost,
                'spent':buycost,'sales_net':sellproceeds,'untracked_sell_qty':unmatched}

    def export_bundle(self, destination: Path, account_details=False, analysis_dir: Path|None=None, link_status: dict|None=None):
        latest=self.overview(); rows=self.market_rows()
        latest_iso=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(latest[1])) if latest else None
        wallet=self.latest_wallet()
        summary={'exported_at':int(time.time()),'market':latest[2] if latest else None,
            'latest_scan_utc':latest_iso,'scans':self.count_scans(),
            'starting_cash_copper':int(self.get_pref('starting_cash','4338')),
            'reserve_copper':int(self.get_pref('reserve','1500')),
            'latest_saved_wallet':dict(zip(['character','copper','seen_at'],wallet)) if wallet else None,
            'automatic_events_count':self.db.execute('SELECT COUNT(*) FROM automatic_events').fetchone()[0],
            'automatic_events_unverified':self.review_count(),
            'wallet_change_events_count':self.db.execute('SELECT COUNT(*) FROM wallet_changes').fetchone()[0],
            'sniper_performance':self.sniper_performance(),
             'market_item_count':len(rows),'orderbooks_recorded':len(self.order_book_rows()),'market_movers_over_20pct':sum(1 for r in rows if r['change'] is not None and abs(r['change'])>=20),'ledger':self.ledger(),
            'watchlist': [{'item_id':item,'target_copper':target,'notes':notes} for item,(target,notes) in self.watchlist().items()],
            'caveats':['Prices are auction-house minimum observed buyout, not confirmed filled trades.',
                      'Single scan cannot establish fair value or liquidity.',
                      'History from this PC is not proof an auction sold.',
                      'Auto-recorded receipts only enter the profit ledger when supported by matching evidence or user verification.',
                      'Previously completed flips are NOT reconstructed; wallet includes other money changes.',
                      'Sniper outcomes represent addon-mediated purchase attempts, not all auction-house listings or other traders.',
                      'Verified vendor P&L matches confirmed AH buys and vendor sells by character/item; unverified or unmatched sales do not count.',
                      'Profit per active hour is an estimate based on observed attempts, not an in-game session timer.',
                      'Vendor sales have no AH commission; ambiguous events never silently affect the ledger.',
                      'SavedVariable writes happen on /reload/logout; file monitoring is not continuous API polling.',
                      'Order-book price gaps are hypothetical offers, not confirmed buyer demand or realised returns.',
                      'flows_recent.csv estimates sales from full listings: stackables sell cheapest-first, so units gone from the front of the book count as sold. It is an estimate, not a trade record.'],
            'link':link_status or {},
            'snapshots':self.db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]}
        lines=io.StringIO();writer=csv.DictWriter(lines,fieldnames=['id','name','price','qty','history','median','discount','change','previous_price','previous_qty','affordable','signal','potential_net','watch_target','watch_alert']);writer.writeheader();writer.writerows(rows)
        history=io.StringIO();w=csv.writer(history);w.writerow(['scan_id','scan_time','market','item_id','price_copper','quantity'])
        for row in self.db.execute('''SELECT s.id,s.observed_at,s.market,o.item_id,o.price,o.qty FROM observations o JOIN scans s ON o.scan_id=s.id ORDER BY s.id'''):w.writerow(row)
        transactions=io.StringIO(); w=csv.writer(transactions);w.writerow(['id','time','side','item_id','name','quantity','price_copper','extra_fee_copper','note','channel']);w.writerows(self.transactions())
        movers=io.StringIO();mw=csv.writer(movers)
        mw.writerow(['item_id','name','prior_min_ask_copper','latest_min_ask_copper','change_percent',
                     'prior_peak_observed_qty','latest_peak_observed_qty','affordable','historical_snapshots'])
        for r in sorted((x for x in rows if x['change'] is not None), key=lambda x:x['change']):
            mw.writerow([r['id'],r['name'],r['previous_price'],r['price'],round(r['change'],2),
                         r['previous_qty'],r['qty'],r['affordable'],r['history']+1])
        orderbooks=io.StringIO();ob=csv.writer(orderbooks)
        ob.writerow(['item_key','item_id','name','kind','observed_at','full','min_ask_copper',
                     'front_units','next_ask_copper','next_units','entry_cost_copper','hypothetical_net_copper',
                     'hypothetical_roi_percent','status','reason','levels_price_quantity_json'])
        for b in self.order_book_rows(int(self.get_pref('starting_cash','4338')),
                                      int(self.get_pref('reserve','500'))):
            ob.writerow([b['key'],b['item_id'],b['name'],b['kind'],b['observed_at'],b['full'],
                        b['buy_price'],b['front_qty'],b['next_price'],b['next_qty'],
                        b['entry_cost'],b['gross_theoretical'],b['roi'],b['status'],b['reason'],json.dumps(b['levels'])])
        automatic=io.StringIO();aw=csv.writer(automatic)
        aw.writerow(['event_uid','time','character','kind','item_name','quantity','total_copper','status','reason'])
        aw.writerows(self.automatic_events(limit=2000))
        sniper_csv=io.StringIO();sp=csv.writer(sniper_csv)
        sp.writerow(['attempt_uid','timestamp_utc','character','item_id','item_name','outcome','quantity',
                     'cost_copper','estimated_profit_copper','early_page','page_to_ready_ms',
                     'page_to_finish_ms','offer_to_ready_ms','offer_to_finish_ms','reason','source','possible_purchase_receipt_near_attempt'])
        sp.writerows(self.recent_snipe_attempts(limit=100000))
        vendor_sales_csv=io.StringIO();vs=csv.writer(vendor_sales_csv)
        vs.writerow(['receipt_uid','time_utc','character','item','quantity',
                    'proceeds_copper','matched_purchase_units','matched_profit_copper','status'])
        vs.writerows(self.recent_vendor_sales(limit=100000))
        wallets_csv=io.StringIO();ww=csv.writer(wallets_csv)
        ww.writerow(['character','wallet_copper','last_saved_at_utc'])
        ww.writerows(self.db.execute('SELECT character,copper,seen_at FROM wallets ORDER BY seen_at DESC'))
        changes_csv=io.StringIO();cw=csv.writer(changes_csv)
        cw.writerow(['event_uid','character','timestamp_utc','balance_copper','change_copper','context'])
        for row in self.db.execute('''SELECT uid,character,ts,copper,delta,context FROM wallet_changes
                                      ORDER BY CAST(uid AS INTEGER)'''):
            cw.writerow(row)
        now=int(time.time())
        flows_csv=io.StringIO();fw=csv.writer(flows_csv)
        fw.writerow(['ts_from','ts_to','item_id','name','sold_units','sold_value_copper','new_units','relisted_units','expired_units','cancelled_units'])
        fw.writerows(self.db.execute('''SELECT f.ts_from,f.ts_to,f.item_id,COALESCE(i.name,'Item #'||f.item_id),f.sold_units,f.sold_value,
            f.new_units,f.relisted_units,f.expired_units,f.cancelled_units FROM flows f LEFT JOIN items i ON i.item_id=f.item_id
            WHERE f.ts_to>=? ORDER BY f.ts_to''',(now-3*86400,)))
        snapshots_csv=io.StringIO();sw=csv.writer(snapshots_csv)
        sw.writerow(['ts','item_id','name','units','listings','min_unit','robust_ask','median_ask','vendor','stack'])
        sw.writerows(self.db.execute('''SELECT s.ts,s.item_id,COALESCE(i.name,'Item #'||s.item_id),s.units,s.listings,s.min_unit,s.robust,s.median,
            COALESCE(i.vendor,0),COALESCE(i.stack,1) FROM snapshot_items s LEFT JOIN items i ON i.item_id=s.item_id
            WHERE s.ts=(SELECT MAX(ts) FROM snapshots) ORDER BY s.units DESC'''))
        bars_csv=io.StringIO();bw=csv.writer(bars_csv)
        bw.writerow(['item_id','bar_start','open','high','low','close','quantity','units_out','units_in'])
        bw.writerows(self.db.execute('SELECT item_id,ts,o,h,l,c,q,out_units,in_units FROM bars WHERE ts>=? ORDER BY ts,item_id',(now-86400,)))
        with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('flows_recent.csv',flows_csv.getvalue())
            z.writestr('latest_listing.csv',snapshots_csv.getvalue())
            z.writestr('bars_24h.csv',bars_csv.getvalue())
            if analysis_dir:
                for name in ('strategy_report.md','plan.json'):
                    path=Path(analysis_dir)/name
                    if path.is_file():z.write(path,name)
            z.writestr('wallet_changes.csv',changes_csv.getvalue())
            z.writestr('automatic_activity.csv',automatic.getvalue())
            z.writestr('sniper_attempts.csv',sniper_csv.getvalue())
            z.writestr('npc_vendor_sales.csv',vendor_sales_csv.getvalue())
            z.writestr('wallet_checkpoints.csv',wallets_csv.getvalue())
            z.writestr('order_books.csv',orderbooks.getvalue())
            z.writestr('market_movers.csv',movers.getvalue())
            z.writestr('summary.json',json.dumps(summary,indent=2))
            z.writestr('market_latest.csv',lines.getvalue())
            z.writestr('market_history.csv',history.getvalue())
            z.writestr('manual_trades.csv',transactions.getvalue())
            z.writestr('README_FOR_ANALYST.txt', 'Targets are user-set watch alerts, NOT executable offers. Observed daily maximum quantity is not sell-through, and an intraday minimum can be stale. Do not infer actual transactions from scan minima.\n')
            z.writestr('PROMPT.txt','Start with strategy_report.md and plan.json: the engine has already proposed limit orders; check them against flows_recent.csv and latest_listing.csv. Analyse this WoW Forever Horde EU PvP AH data. Starting cash is 43s 38c. Find cautious but high-upside manual trading opportunities from the attached snapshot/history. Distinguish observed ask prices from completed trades and DO NOT invent live order books, volume or profit. Where order_books.csv exists, use only its measured depth and freshness, and do not treat next price as an actual sale. Calculate break-even resale after 5% AH fee and deposits; provide exact maximum buy prices and which listings I should live-check first. I am happy with aggressive speculation but never recommend spending on invalid/incomplete data.\n')
        return destination
