"""Win11 Tk desktop UI. Built using stdlib for reliable single-EXE packaging."""
from __future__ import annotations
import hashlib, json, os, sys, threading, time, webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from .auctionator import parse_auctionator, parse_aux_names, fmt_copper, ParseError
from .market import MarketStore
from .bridge import parse_bridge
from .updater import get_latest,download_update,install_on_exit,download_source,install_source_on_exit
from . import VERSION
from .addon_sync import sync_installed_bridge

BG='#0e1522';PANEL='#172336';PANEL2='#1d2c41';FG='#e7edf7';MUTED='#91a2bb';ACCENT='#51a9ff';GREEN='#52d1a6';ORANGE='#fbbf66';RED='#fa7185'

def user_data_dir():
    parent=os.getenv('APPDATA') or str(Path.home()/'.config')
    return Path(parent)/'ForeverTrader'

def find_suggested_path():
    roots=[Path(os.getenv('PROGRAMFILES(X86)','C:/Program Files (x86)'))/'World of Warcraft',
           Path(os.getenv('PROGRAMFILES','C:/Program Files'))/'World of Warcraft',
           Path(os.getenv('USERPROFILE',str(Path.home())))/'Games'/'World of Warcraft',
           Path('C:/World of Warcraft'),Path('D:/World of Warcraft'),Path('D:/Games/World of Warcraft'),
           Path('E:/World of Warcraft')]
    for root in roots:
        base=root/'_forever_'/'WTF'/'Account'
        if base.exists():
            for path in base.glob('*/SavedVariables/Auctionator.lua'):
                if path.is_file():return path
    return None

class App(tk.Tk):
    def __init__(self):
        super().__init__();self.title('Forever Trader  •  WoW Forever Auction House')
        self.geometry('1280x810');self.minsize(900,630);self.configure(bg=BG)
        self.store=MarketStore(user_data_dir());self.last_file_sig=None;self.last_bridge_sig=None;self.latest_update=None;self.skipped_update=None
        self.protocol('WM_DELETE_WINDOW', self._close)
        self.sort_col='price';self.sort_reverse=False;self.market_rows=[]
        self.source_var=tk.StringVar(value=self.store.get_pref('auctionator_path'))
        self.aux_var=tk.StringVar(value=self.store.get_pref('aux_path'))
        self.bridge_var=tk.StringVar(value=self.store.get_pref('bridge_path'))
        self.repo_var=tk.StringVar(value=self.store.get_pref('repo','simon-hirst/wow-forever-trader'))
        self.sync_bridge_var=tk.BooleanVar(value=self.store.get_pref('sync_bridge','0')=='1')
        self.cash_var=tk.StringVar(value=self.store.get_pref('starting_cash','4338'))
        self.reserve_var=tk.StringVar(value=self.store.get_pref('reserve','500'))
        self.search_var=tk.StringVar();self.status_var=tk.StringVar(value='Ready • select Auctionator.lua and optional Bridge data')
        self.filter_var=tk.StringVar(value='All items')
        self._style();self._build()
        if not self.source_var.get():
            candidate=find_suggested_path()
            if candidate:self.source_var.set(str(candidate));self._save_settings()
        self.after(100,self.refresh);self.after(4000,self._poll)
        if self.sync_bridge_var.get():self.after(800,self._sync_bridge_silently)
        self.after(10000,self._async_update_check)
    def _style(self):
        self.option_add('*Font','{Segoe UI} 10');self.option_add('*TCombobox*Listbox.background',PANEL2)
        st=ttk.Style(self);st.theme_use('clam')
        st.configure('.',background=BG,foreground=FG,fieldbackground=PANEL2,insertcolor=FG)
        st.configure('TFrame',background=BG)
        st.configure('Card.TFrame',background=PANEL)
        st.configure('TLabel',background=BG,foreground=FG)
        st.configure('Hint.TLabel',background=BG,foreground=MUTED)
        st.configure('Title.TLabel',background=BG,foreground=FG,font=('Segoe UI Semibold',20))
        st.configure('CardName.TLabel',background=PANEL,foreground=MUTED,font=('Segoe UI',10))
        st.configure('CardValue.TLabel',background=PANEL,foreground=FG,font=('Segoe UI Semibold',19))
        st.configure('TButton',padding=(12,8),background=PANEL2,foreground=FG,borderwidth=0)
        st.map('TButton',background=[('active','#315174'),('pressed','#274b71')])
        st.configure('Accent.TButton',background='#1868a8',foreground='white')
        st.map('Accent.TButton',background=[('active','#2986d1')])
        st.configure('TEntry',fieldbackground=PANEL2,foreground=FG,padding=7)
        st.configure('TCombobox',fieldbackground=PANEL2,foreground=FG,background=PANEL2)
        st.configure('TNotebook',background=BG,borderwidth=0)
        st.configure('TNotebook.Tab',padding=(22,10),background=PANEL,foreground=MUTED,font=('Segoe UI Semibold',10))
        st.map('TNotebook.Tab',background=[('selected',PANEL2)],foreground=[('selected',FG)])
        st.configure('Treeview',background=PANEL,foreground=FG,fieldbackground=PANEL,rowheight=30,borderwidth=0,font=('Segoe UI',10))
        st.configure('Treeview.Heading',background=PANEL2,foreground=FG,borderwidth=0,relief='flat',font=('Segoe UI Semibold',10))
        st.map('Treeview',background=[('selected','#20588a')],foreground=[('selected','white')])
        st.configure('TScrollbar',troughcolor=BG,background=PANEL2,arrowcolor=MUTED)
    def _build(self):
        outer=ttk.Frame(self,padding=(25,20));outer.pack(fill='both',expand=True)
        top=ttk.Frame(outer);top.pack(fill='x')
        left=ttk.Frame(top);left.pack(side='left',fill='x',expand=True)
        ttk.Label(left,text='FOREVER / MARKET TERMINAL',style='Title.TLabel').pack(anchor='w')
        ttk.Label(left,text='EU · Horde · PvP  |  Manual trading intelligence  |  v'+VERSION,style='Hint.TLabel').pack(anchor='w',pady=(3,0))
        ttk.Button(top,text='Export for ChatGPT',style='Accent.TButton',command=self.export).pack(side='right',padx=(8,0))
        ttk.Button(top,text='Refresh file',command=self.refresh).pack(side='right',padx=(8,0))
        ttk.Button(top,text='Check updates',command=self.check_updates).pack(side='right',padx=(8,0))
        cards=ttk.Frame(outer);cards.pack(fill='x',pady=(24,20))
        self.card_vars={}
        for key,label in [('cash','Last saved wallet'),('profit','Realised profit'),('count','Items priced'),('scan','Last scan'),('signals','Discount / price alerts')]:
            card=ttk.Frame(cards,style='Card.TFrame',padding=(17,14));card.pack(side='left',fill='both',expand=True,padx=(0,9))
            ttk.Label(card,text=label,style='CardName.TLabel').pack(anchor='w')
            v=tk.StringVar(value='—');self.card_vars[key]=v
            ttk.Label(card,textvariable=v,style='CardValue.TLabel').pack(anchor='w',pady=(7,0))
        self.tabs=ttk.Notebook(outer);self.tabs.pack(fill='both',expand=True)
        self.market_tab=ttk.Frame(self.tabs);self.signal_tab=ttk.Frame(self.tabs)
        self.movers_tab=ttk.Frame(self.tabs)
        self.book_tab=ttk.Frame(self.tabs)
        self.vendor_tab=ttk.Frame(self.tabs)
        self.activity_tab=ttk.Frame(self.tabs)
        self.watch_tab=ttk.Frame(self.tabs)
        self.trades_tab=ttk.Frame(self.tabs);self.settings_tab=ttk.Frame(self.tabs)
        self.calc_tab=ttk.Frame(self.tabs)
        self.help_tab=ttk.Frame(self.tabs)
        for frame,label in [(self.market_tab,'MARKET'),(self.book_tab,'ORDER BOOK'),(self.vendor_tab,'VENDOR FLIPS'),(self.movers_tab,'MARKET MOVERS'),(self.signal_tab,'OPPORTUNITIES'),(self.watch_tab,'WATCHLIST'),
                             (self.activity_tab,'AUTO ACTIVITY'),(self.trades_tab,'TRADE JOURNAL'),(self.calc_tab,'FLIP CALCULATOR'),(self.settings_tab,'SETTINGS'),(self.help_tab,'HOW IT WORKS')]:
            self.tabs.add(frame,text=label)
        self._market_tab();self._book_tab();self._vendor_tab();self._movers_tab();self._signal_tab();self._watch_tab();self._trades_tab();self._activity_tab();self._calc_tab();self._settings_tab();self._help_tab()
        status=ttk.Frame(outer);status.pack(fill='x',pady=(12,0))
        ttk.Label(status,textvariable=self.status_var,style='Hint.TLabel').pack(side='left')
        ttk.Label(status,text='AH updates reach Windows after /reload • no automated trading',style='Hint.TLabel').pack(side='right')
        self.search_var.trace_add('write',lambda *_:self._update_tables())
    def _make_tree(self,parent,cols,widths):
        box=ttk.Frame(parent);box.pack(fill='both',expand=True)
        tree=ttk.Treeview(box,columns=tuple(cols),show='headings',selectmode='browse')
        for key,display in cols.items():
            tree.heading(key,text=display,command=lambda k=key:self._sort(k))
            tree.column(key,anchor='w' if key in ('name','signal','note','side') else 'e',width=widths.get(key,100),stretch=key=='name')
        y=ttk.Scrollbar(box,orient='vertical',command=tree.yview)
        tree.configure(yscrollcommand=y.set);y.pack(side='right',fill='y');tree.pack(side='left',fill='both',expand=True)
        tree.tag_configure('signal',background='#234435',foreground='#b9f2d3')
        tree.tag_configure('alternate',background='#1a293c')
        return tree
    def _market_tab(self):
        controls=ttk.Frame(self.market_tab,padding=(0,14,0,10));controls.pack(fill='x')
        ttk.Label(controls,text='Find item',style='Hint.TLabel').pack(side='left')
        ttk.Entry(controls,textvariable=self.search_var,width=30).pack(side='left',padx=9)
        ttk.Label(controls,text='Show',style='Hint.TLabel').pack(side='left',padx=(16,8))
        ttk.Combobox(controls,textvariable=self.filter_var,values=['All items','Affordable only','Materials only','Watched only'],state='readonly',width=17).pack(side='left')
        self.filter_var.trace_add('write',lambda *_:self._update_tables())
        ttk.Label(controls,text='Minimum observed ask • availability is a daily peak',style='Hint.TLabel').pack(side='right')
        cols={'name':'ITEM','price':'LOWEST ASK','qty':'PEAK OBS. QTY','median':'HIST. MEDIAN','change':'CHANGE','signal':'SIGNAL'}
        self.market_tree=self._make_tree(self.market_tab,cols,{'name':270,'price':112,'qty':90,'median':130,'change':105,'signal':105})
        foot=ttk.Frame(self.market_tab,padding=(0,10));foot.pack(fill='x')
        ttk.Button(foot,text='Record selected BUY',command=lambda:self.trade_dialog('BUY')).pack(side='left')
        ttk.Button(foot,text='Set price alert',command=self.add_watch).pack(side='left',padx=8)
        ttk.Button(foot,text='Rename item',command=self.rename_item).pack(side='left')
        ttk.Button(foot,text='Record selected SELL',command=lambda:self.trade_dialog('SELL')).pack(side='left',padx=10)
        ttk.Label(foot,text='Live-check the auction house before buying. Check stack sizes.',style='Hint.TLabel').pack(side='right')
    def _book_tab(self):
        head=ttk.Frame(self.book_tab,padding=(0,13,0,10));head.pack(fill='x')
        ttk.Label(head,text='Measured detail-search depth • recorded by ForeverTraderBridge; /reload to save.',
                  style='Hint.TLabel').pack(side='left')
        ttk.Button(head,text='How to capture',command=self.bridge_help).pack(side='right')
        cols={'name':'ITEM','status':'SIGNAL','entry':'CHEAPEST','qty':'FRONT QTY',
              'next':'NEXT ASK','cost':'CLEAR COST','net':'HYP. NET','age':'AGE'}
        self.book_tree=self._make_tree(self.book_tab,cols,
              {'name':230,'status':99,'entry':98,'qty':90,'next':104,'cost':103,'net':97,'age':75})
        self.book_tree.bind('<Double-1>',self.show_book)
        foot=ttk.Frame(self.book_tab,padding=(0,9,0,0));foot.pack(fill='x')
        ttk.Button(foot,text='Inspect selected depth',command=self.show_book).pack(side='left')
        ttk.Label(foot,text='INSPECT means check live, not buy. No sale volumes or guaranteed profit.',
                  style='Hint.TLabel').pack(side='right')

    def _vendor_tab(self):
        bar=ttk.Frame(self.vendor_tab,padding=(0,14,0,9));bar.pack(fill='x')
        ttk.Label(bar,text='Below-NPC-vendor opportunities from captured search depth; verify listings live.',
                  style='Hint.TLabel').pack(side='left')
        ttk.Button(bar,text='Log completed NPC flip',command=self.vendor_flip_dialog).pack(side='right')
        cols={'name':'ITEM','kind':'SOURCE','buy':'BUY UNIT/LOT','vendor':'NPC PAYS','profit':'POTENTIAL','status':'STATUS'}
        self.vendor_tree=self._make_tree(self.vendor_tab,cols,
             {'name':235,'kind':95,'buy':115,'vendor':115,'profit':118,'status':150})
        ttk.Label(self.vendor_tab,text='Vendor sales do NOT pay the AH 5% fee. Prices can change; verify stack quantities and NPC payout. No automatic buying.',
                  style='Hint.TLabel',wraplength=980).pack(anchor='w',pady=9)

    def vendor_flip_dialog(self):
        """Convenience journal entry for a complete, already-executed NPC flip."""
        item_id=simpledialog.askstring('Completed vendor flip',
                    'Item ID (only if the AH BUY and NPC SALE are both complete):',parent=self)
        if not item_id:return
        qty=simpledialog.askinteger('Completed vendor flip','Number of items actually sold to NPC:',minvalue=1,parent=self)
        if qty is None:return
        buy=simpledialog.askinteger('Completed vendor flip','Actual AH purchase cost per item in COPPER:',minvalue=0,parent=self)
        if buy is None:return
        vendor=simpledialog.askinteger('Completed vendor flip','Actual NPC vendor payout per item in COPPER:',minvalue=0,parent=self)
        if vendor is None:return
        if vendor<=buy:
            messagebox.showwarning('Not a vendor arbitrage',
                'Vendor unit price must exceed the purchase cost. Record each transaction individually if it was a loss.')
            return
        if not messagebox.askyesno('Confirm completed trades',
            f'Log ALREADY COMPLETED BUY + NPC SELL for {qty} x Item #{item_id}?\n'
            f'Bought: {fmt_copper(buy)} each. NPC paid: {fmt_copper(vendor)} each.\n'
            f'Actual profit: {fmt_copper((vendor-buy)*qty)}.\n\n'
            'Do not use if you already recorded either transaction; no in-game action will occur.'):
            return
        self.store.log_vendor_flip(item_id,qty,buy,vendor)
        self._update_tables();self.tabs.select(self.trades_tab)

    def bridge_help(self):
        messagebox.showinfo('Record price levels in WoW',
            'Copy the bundled ForeverTraderBridge folder into _forever_\\Interface\\AddOns\\. '
            'Enable it, restart WoW, search for an item in auxForever, then type /reload. '
            'Point Settings to ForeverTraderBridge.lua in SavedVariables. '
            'The addon only listens for price results; it never searches or buys for you.')

    def show_book(self,event=None):
        sel=self.book_tree.selection()
        if not sel:return
        row=next((r for r in self.book_rows if r['key']==sel[0]),None)
        if not row:return
        depth='\n'.join(f'{p}c × {q} '+('units' if row['kind']=='commodity' else 'auctions')
                      for p,q in row['levels'][:35])
        messagebox.showinfo(row['name'],
            f"{row['status']}: {row['reason']}\n\n"
            f"Recorded: {time.strftime('%d %b %H:%M:%S UTC',time.gmtime(row['observed_at']))}\n"
            f"Full result: {'yes' if row['full'] else 'no'}; source: {row['kind']}\n"
            f"Price to verify: {fmt_copper(row['buy_price'])}\n"
            f"To clear cheapest tier: {fmt_copper(row['entry_cost'])}\n"
            f"Hypothetical net after 5% cut, BEFORE listing deposits: "
            f"{fmt_copper(row['gross_theoretical']) if row['gross_theoretical'] is not None else 'n/a'}\n"
            'NEXT ASK IS NOT PROOF OF SALES. Check live listings before any trade.\n\n'+depth)

    def _movers_tab(self):
        banner=ttk.Frame(self.movers_tab,padding=(0,14,0,9));banner.pack(fill='x')
        ttk.Label(banner,text='Min-ask changes between two saved scans (NOT sales or fair value).',style='Hint.TLabel').pack(side='left')
        self.movers_filter=tk.StringVar(value='Drops >20%')
        ttk.Combobox(banner,textvariable=self.movers_filter,
                     values=['Drops >20%','Rises >20%','All changes'],state='readonly',width=19).pack(side='right')
        self.movers_filter.trace_add('write',lambda *_:self._update_tables())
        cols={'name':'ITEM','old':'OLD MIN','price':'NEW MIN','change':'CHANGE','oldqty':'OLD PEAK QTY','qty':'NEW PEAK QTY','status':'STATUS'}
        self.movers_tree=self._make_tree(self.movers_tab,cols,
            {'name':245,'old':107,'price':107,'change':90,'oldqty':105,'qty':105,'status':120})
        ttk.Label(self.movers_tab,text='An observed price fall can mean oversupply, bad prior pricing, or a real opportunity. Open the item live and inspect price depth before trading.',
                  style='Hint.TLabel',wraplength=1050).pack(anchor='w',pady=(9,0))

    def _signal_tab(self):
        ttk.Label(self.signal_tab,text='Candidate discounts require at least two older price snapshots; current asks may have changed.',
                  style='Hint.TLabel').pack(anchor='w',pady=(15,10))
        cols={'name':'ITEM','price':'CURRENT','median':'PAST MEDIAN','discount':'DISCOUNT','potential':'THEORETICAL NET / UNIT','signal':'ACTION'}
        self.signal_tree=self._make_tree(self.signal_tab,cols,{'name':280,'price':100,'median':120,'discount':100,'potential':180,'signal':100})
        ttk.Label(self.signal_tab,text='RECHECK = price ≥20% below prior median; excludes deposits and assumes a buyer exists. Never an automatic BUY order.',style='Hint.TLabel').pack(anchor='w',pady=8)
    def _trades_tab(self):
        banner=ttk.Frame(self.trades_tab,padding=(0,15,0,10));banner.pack(fill='x')
        self.ledger_var=tk.StringVar(value='No recorded trades')
        ttk.Label(banner,textvariable=self.ledger_var).pack(side='left')
        ttk.Button(banner,text='Record BUY',command=lambda:self.trade_dialog('BUY')).pack(side='right')
        ttk.Button(banner,text='Record AH SELL',command=lambda:self.trade_dialog('SELL')).pack(side='right',padx=7)
        ttk.Button(banner,text='Record NPC SELL',command=lambda:self.trade_dialog('SELL','VENDOR')).pack(side='right',padx=7)
        cols={'time':'UTC TIME','side':'SIDE','name':'ITEM','qty':'QTY','price':'UNIT PRICE','fee':'EXTRA FEES','venue':'VENUE','note':'NOTES'}
        self.trade_tree=self._make_tree(self.trades_tab,cols,{'time':150,'side':65,'name':210,'qty':55,'price':100,'fee':95,'venue':75,'note':210})
    def _activity_tab(self):
        bar=ttk.Frame(self.activity_tab,padding=(0,14,0,10));bar.pack(fill='x')
        self.activity_var=tk.StringVar(value='Waiting for the first saved WoW activity snapshot')
        ttk.Label(bar,textvariable=self.activity_var,style='Hint.TLabel').pack(side='left')
        ttk.Button(bar,text='Verify selected receipt',command=self.verify_activity).pack(side='right')
        cols={'time':'UTC TIME','char':'CHARACTER','kind':'EVENT','name':'ITEM','qty':'QTY',
              'total':'TOTAL','status':'CONFIDENCE','reason':'DETAILS'}
        self.activity_tree=self._make_tree(self.activity_tab,cols,
            {'time':144,'char':130,'kind':140,'name':180,'qty':55,'total':106,'status':105,'reason':360})
        ttk.Label(self.activity_tab,text='CONFIRMED events enter the profit ledger once. REVIEW events do not count until you explicitly verify them. '
                  'Saved-variable events reach Windows only after /reload or logout. AH/mail sales are not auto-attributed.',
                  style='Hint.TLabel',wraplength=1060).pack(anchor='w',pady=9)

    def verify_activity(self):
        selected=self.activity_tree.selection()
        if not selected:return
        uid=selected[0]
        row=next((r for r in self.store.automatic_events() if r[0]==uid),None)
        if not row or row[7]!='review':
            messagebox.showinfo('Already confirmed','Select an unconfirmed AH_BUY or VENDOR_SELL event.')
            return
        if row[3] not in ('AH_BUY','VENDOR_SELL'):
            messagebox.showinfo('Needs manual reconciliation',
                'This is an unattributed wallet change. Record the actual item trades in Trade Journal instead.')
            return
        item_id=simpledialog.askstring('Verify receipt','Actual item ID:',parent=self)
        if not item_id:return
        qty=simpledialog.askinteger('Verify receipt','Actual item quantity:',initialvalue=max(1,row[5]),minvalue=1,parent=self)
        if qty is None:return
        total=simpledialog.askinteger('Verify receipt','Actual total copper paid / received:',
                  initialvalue=max(1,row[6]),minvalue=1,parent=self)
        if total is None:return
        if not messagebox.askyesno('Confirm in-game evidence',
            'Only verify this event if you personally checked your WoW transaction. '
            'Incorrect entries will produce incorrect profit. Continue?'):
            return
        try:self.store.confirm_activity(uid,item_id,qty,total)
        except (ValueError,TypeError) as e:messagebox.showerror('Cannot verify',str(e));return
        self._update_tables()

    def _watch_tab(self):
        banner=ttk.Frame(self.watch_tab,padding=(0,14));banner.pack(fill='x')
        ttk.Label(banner,text='Personal price thresholds • checked after WoW writes a new SavedVariables snapshot, not live.',style='Hint.TLabel').pack(side='left')
        ttk.Button(banner,text='Remove selected',command=self.remove_watch).pack(side='right')
        ttk.Button(banner,text='Add / change target',command=self.add_watch).pack(side='right',padx=8)
        cols={'name':'ITEM','target':'ALERT AT OR BELOW','observed':'LAST SCANNED','status':'STATUS','notes':'NOTES'}
        self.watch_tree=self._make_tree(self.watch_tab,cols,{'name':275,'target':170,'observed':145,'status':135,'notes':240})

    def selected_item_id(self):
        for tree in (self.market_tree,self.watch_tree):
            selected=tree.selection()
            if selected:return selected[0]
        return None

    def add_watch(self):
        initial=self.selected_item_id() or ''
        item_id=simpledialog.askstring('Watchlist','Item ID (select a Market row to prefill)',initialvalue=initial,parent=self)
        if not item_id:return
        current=self.store.watchlist().get(item_id,(None,''))
        market=next((r for r in self.market_rows if r['id']==item_id),None)
        initial_target=current[0] if current[0] is not None else (market['price'] if market else 100)
        target=simpledialog.askinteger('Alert target','Alert when a new scanned minimum is at or below this many COPPER:',
                                         initialvalue=initial_target,minvalue=0,parent=self)
        if target is None:return
        notes=simpledialog.askstring('Watch notes','Optional strategy or note',initialvalue=current[1],parent=self)
        if notes is None:return
        self.store.set_watch(item_id,target,notes)
        self._update_tables()
        self.tabs.select(self.watch_tab)
    def remove_watch(self):
        selection=self.watch_tree.selection()
        if not selection:return
        self.store.remove_watch(selection[0]);self._update_tables()
    def rename_item(self):
        selected=self.market_tree.selection()
        if not selected:return
        item_id=selected[0]
        previous=next((r['name'] for r in self.market_rows if r['id']==item_id),'')
        name=simpledialog.askstring('Item display name',f'Name for item ID {item_id}',
                                    initialvalue=previous if not previous.startswith('Item #') else '',parent=self)
        if name:
            self.store.set_name(item_id,name)
            self._update_tables()

    def _setting(self,parent,label,var,button=None,action=None):
        row=ttk.Frame(parent);row.pack(fill='x',pady=6)
        ttk.Label(row,text=label,width=31).pack(side='left')
        ttk.Entry(row,textvariable=var).pack(side='left',fill='x',expand=True,padx=(8,6))
        if button:ttk.Button(row,text=button,command=action).pack(side='left')
    def _calc_tab(self):
        frame=ttk.Frame(self.calc_tab,padding=22);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='Flip profit calculator',font=('Segoe UI Semibold',14)).pack(anchor='w',pady=(0,6))
        ttk.Label(frame,text='Enter prices in copper. 1 silver = 100 copper, 1 gold = 10,000 copper.',style='Hint.TLabel').pack(anchor='w',pady=(0,18))
        self.calc_buy=tk.StringVar(value='100')
        self.calc_sell=tk.StringVar(value='180')
        self.calc_qty=tk.StringVar(value='1')
        self.calc_fee=tk.StringVar(value='0')
        self.calc_target=tk.StringVar(value='40')
        self._setting(frame,'Buy price per item (copper)',self.calc_buy)
        self._setting(frame,'Resale price per item (copper)',self.calc_sell)
        self._setting(frame,'Quantity',self.calc_qty)
        self._setting(frame,'Expected lost deposits (copper)',self.calc_fee)
        self._setting(frame,'Desired ROI % on cost',self.calc_target)
        ttk.Button(frame,text='Calculate profit',style='Accent.TButton',command=self.calculate_flip).pack(anchor='w',pady=15)
        self.calc_result=tk.StringVar(value='Press Calculate profit to estimate your net proceeds.')
        ttk.Label(frame,textvariable=self.calc_result,wraplength=750,font=('Segoe UI Semibold',12)).pack(anchor='w',pady=(10,10))
        ttk.Label(frame,text='This assumes everything sells at the entered resale price. It does not estimate sale probability, demand or your actual AH deposit.',style='Hint.TLabel',wraplength=820).pack(anchor='w')
    def calculate_flip(self):
        try:
            buy=int(self.calc_buy.get());sell=int(self.calc_sell.get());qty=int(self.calc_qty.get());fee=int(self.calc_fee.get());target=float(self.calc_target.get())
            if min(buy,sell,fee)<0 or qty<=0 or target<0:raise ValueError('Prices and quantities must be nonnegative; quantity must be positive')
            if qty>100000:raise ValueError('Quantity too large')
        except ValueError as e:
            messagebox.showerror('Invalid calculator input',str(e));return
        import math
        gross=qty*sell
        cut=round(gross*0.05)
        net=gross-cut-qty*buy-fee
        break_even=math.ceil((buy*qty+fee)/(qty*.95))
        maximum_buy=math.floor((qty*sell*.95-fee)/(qty*(1+target/100)))
        roi=100*net/(buy*qty) if buy>0 else None
        self.calc_result.set(f'Projected net: {fmt_copper(net)}  |  ROI: {roi:.1f}%  |  Break-even sell: {fmt_copper(break_even)} each  |  Max buy for {target:g}% ROI: {fmt_copper(maximum_buy)} each' if roi is not None else f'Projected net: {fmt_copper(net)}  |  Break-even sell: {fmt_copper(break_even)} each  |  Max buy: {fmt_copper(maximum_buy)} each')

    def _settings_tab(self):
        frame=ttk.Frame(self.settings_tab,padding=20);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='SavedVariables input',font=('Segoe UI Semibold',13)).pack(anchor='w',pady=(0,10))
        self._setting(frame,'Auctionator.lua file',self.source_var,'Browse',self.choose_auctionator)
        self._setting(frame,'auxForever.lua (optional names)',self.aux_var,'Browse',self.choose_aux)
        self._setting(frame,'ForeverTraderBridge.lua (depth)',self.bridge_var,'Browse',self.choose_bridge)
        ttk.Separator(frame).pack(fill='x',pady=15)
        ttk.Label(frame,text='Trading & release settings',font=('Segoe UI Semibold',13)).pack(anchor='w',pady=(0,10))
        self._setting(frame,'Initial bankroll (copper)',self.cash_var)
        self._setting(frame,'Reserved copper',self.reserve_var)
        self._setting(frame,'GitHub updates (owner/repo)',self.repo_var)
        ttk.Checkbutton(frame,text='Keep existing in-game Forever Trader Bridge synced to app updates',
                        variable=self.sync_bridge_var).pack(anchor='w',pady=(7,0))
        ttk.Button(frame,text='Save settings and scan',style='Accent.TButton',command=self.apply_settings).pack(anchor='w',pady=18)
        ttk.Label(frame,text='Example: 43s 38c = 4338 copper, reserve 5s = 500 copper.',style='Hint.TLabel').pack(anchor='w')
        ttk.Label(frame,text='Updates are checked on startup and roughly every 20 minutes; installation requires confirmation.',style='Hint.TLabel').pack(anchor='w',pady=4)
        ttk.Label(frame,text='No login credentials or auction-house data are uploaded automatically.',style='Hint.TLabel').pack(anchor='w')
    def _help_tab(self):
        frame=ttk.Frame(self.help_tab,padding=20);frame.pack(fill='both',expand=True)
        help_text='''GETTING STARTED\n\n1. In WoW Forever, open the Auction House → Auctionator → Full Scan.\n2. Install bundled wow_addon/ForeverTraderBridge into _forever_/Interface/AddOns.\n   Search items normally in auxForever; the bridge records detail-query price tiers.\n   Type /reload (or log out) to write both addons' data to disk.\n3. In Settings, select _forever_\\WTF\\Account\\<account>\\SavedVariables\\Auctionator.lua.\n4. In Settings, select ForeverTraderBridge.lua from that same SavedVariables folder.\n   Optional: select auxForever.lua to resolve additional item names.\n5. The desktop app automatically detects later saved-file changes every 4 seconds.\n   Use the WATCHLIST tab for a saved-snapshot price threshold and local audible alert.\n6. When you want my analysis, click Export for ChatGPT and upload the ZIP in our chat.\n\nUNDERSTAND THE SIGNALS\n\nBASELINE: first observation; do not treat it as a deal.\nWATCH: has price history but not a sufficiently strong discount.\nRECHECK: current minimum at least 20% below prior scan median and fits bankroll.\n\nYou can rename unidentified IDs in MARKET. Price targets alert on a NEW snapshot.\nEvery listed price is an observed minimum, not a live executable order.\nORDER BOOK shows actual price tiers from searches you performed, but never transactions.\nINSPECT requires fresh, complete commodity results, thin cheapest tier, and bankroll fit.\nPrices are scenarios, not fair values. Individual stack auctions require manual evaluation.\nAuctionator does not reveal completed sales. This app cannot know actual sell-through.\nMultiple scans on the same day may be merged by Auctionator's own daily buckets,\nbut the app separately saves snapshots after every changed SavedVariables write.\n\nTRADING\n\nRecord manual buys and sells to track holdings and estimated after-fee realised P&L.\nAlways double-check the posted stack sizes and deposit before buying/relisting.\nThe default faction AH sales fee assumption is 5%.\nNever buy solely from the app's signal: check the listing in WoW.\n\nUPDATES\n\nPublish tagged Windows builds in your configured GitHub repository.\nThe EXE checks GitHub Releases for a newer stable version and can install it\nafter your confirmation. Source launches can't self-replace.\nNo updates are pushed automatically by ChatGPT; new releases must be published.\n'''
        t=tk.Text(frame,bg=PANEL,fg=FG,insertbackground=FG,relief='flat',borderwidth=0,wrap='word',font=('Consolas',10),padx=16,pady=15)
        t.insert('1.0',help_text);t.configure(state='disabled');t.pack(fill='both',expand=True)
    def choose_auctionator(self):
        f=filedialog.askopenfilename(title='Select Auctionator.lua',filetypes=[('Lua SavedVariables','*.lua'),('All files','*.*')]);
        if f:self.source_var.set(f);self.apply_settings()
    def choose_bridge(self):
        f=filedialog.askopenfilename(title='Select ForeverTraderBridge.lua',filetypes=[('Lua SavedVariables','*.lua'),('All files','*.*')]);
        if f:self.bridge_var.set(f);self.apply_settings()
    def choose_aux(self):
        f=filedialog.askopenfilename(title='Select auxForever.lua',filetypes=[('Lua SavedVariables','*.lua'),('All files','*.*')]);
        if f:self.aux_var.set(f);self.apply_settings()
    def _save_settings(self):
        self.store.set_pref('sync_bridge','1' if self.sync_bridge_var.get() else '0')
        for key,var in [('auctionator_path',self.source_var),('aux_path',self.aux_var),('bridge_path',self.bridge_var),('repo',self.repo_var),
                        ('starting_cash',self.cash_var),('reserve',self.reserve_var)]:self.store.set_pref(key,var.get())
    def apply_settings(self):
        try:
            cash=int(self.cash_var.get()); reserve=int(self.reserve_var.get())
            if cash<=0 or reserve<0 or reserve>cash: raise ValueError('Reserve must be between zero and starting cash')
        except ValueError as e:messagebox.showerror('Invalid settings',str(e));return
        self._save_settings();self.last_file_sig=None;self.last_bridge_sig=None;self.refresh()
        if self.sync_bridge_var.get():self._sync_bridge_silently()
    def _sync_bridge_silently(self):
        try:
            outcome=sync_installed_bridge(self.source_var.get())
            if 'Updated 0' not in outcome:self.status_var.set(outcome)
        except OSError as exc:
            self.status_var.set('Could not sync in-game Bridge: '+str(exc))

    def _poll(self):
        try:self.refresh(silent=True)
        except Exception as exc:self.status_var.set('Monitor error: '+str(exc))
        self.after(4000,self._poll)
    def _refresh_bridge(self,silent=False):
        source=self.bridge_var.get().strip()
        if not source and self.source_var.get().strip():
            source=str(Path(self.source_var.get().strip()).with_name('ForeverTraderBridge.lua'))
        if not source:return 0
        path=Path(source)
        if not path.is_file():return 0
        try:
            stat=path.stat();sig=(stat.st_mtime_ns,stat.st_size)
            if sig==self.last_bridge_sig:return 0
            payload=parse_bridge(path)
            imported=self.store.ingest_books(payload)
            activity=self.store.ingest_activity(payload)
            self.last_bridge_sig=sig
            if imported or activity['new_events'] or activity['wallets']:
                self.status_var.set(f'Bridge: {imported} books, {activity["new_events"]} events '
                    f'({activity["confirmed"]} confirmed), {activity["wallets"]} wallet updates; /reload to sync')
            return imported+activity['new_events']+activity['wallets']
        except (OSError,ParseError,ValueError) as e:
            if not silent:self.status_var.set('Bridge import failed: '+str(e))
            return 0

    def refresh(self,silent=False):
        bridge_imported=self._refresh_bridge(silent)
        path_text=self.source_var.get().strip()
        if not path_text:
            if not silent:self.status_var.set('Choose Auctionator.lua under Settings to begin')
            self._update_tables();return
        path=Path(path_text)
        if not path.exists():
            if not silent:self.status_var.set('SavedVariables not found — check the path in Settings')
            self._update_tables();return
        stat=path.stat();sig=(stat.st_mtime_ns,stat.st_size)
        if sig==self.last_file_sig:
            if not silent and not bridge_imported:self.status_var.set('No changed scan; search with auxForever then /reload for new depth')
            # Recompute freshness even when files do not change: stale books must not remain green.
            self._update_tables()
            return
        try:
            snap=parse_auctionator(path)
            sha=hashlib.sha256(path.read_bytes()).hexdigest()
            previous_scan_count=self.store.count_scans()
            added=self.store.ingest(snap,sha)
            aux=Path(self.aux_var.get()) if self.aux_var.get() else path.with_name('auxForever.lua')
            if aux.exists():self.store.upsert_names(parse_aux_names(aux))
            self.last_file_sig=sig
            if added:
                description=('New distinct AH scan archived' if self.store.count_scans()>previous_scan_count
                             else 'Updated prices (same AH scan time)')
            else: description='Already imported'
            msg=description+f' • {snap["count"]} items • market {snap["market"]}'
            if added:
                watches=self.store.watchlist()
                triggered=[item for item,data in snap['items'].items() if item in watches and data['price']<=watches[item][0]]
                if triggered:
                    msg+=f' • {len(triggered)} PRICE ALERT(S): verify live in WoW'
                    try:
                        if os.name=='nt':
                            import winsound
                            winsound.MessageBeep()
                    except Exception:pass
            self.status_var.set(msg)
        except (ParseError,OSError,ValueError) as e:self.status_var.set('Cannot import: '+str(e));return
        self._update_tables()
    def _sort(self,col):
        if self.sort_col==col:self.sort_reverse=not self.sort_reverse
        else:self.sort_col=col;self.sort_reverse=False
        self._update_tables()
    def _update_cards(self):
        latest=self.store.overview()
        starting=int(self.store.get_pref('starting_cash','4338'))
        ledger=self.store.ledger()
        wallet=self.store.latest_wallet()
        cash=wallet[1] if wallet else starting-ledger['spent']+ledger['sales_net']
        self.card_vars['cash'].set(fmt_copper(cash))
        self.card_vars['profit'].set(fmt_copper(ledger['realized']))
        self.card_vars['count'].set(str(latest[3]) if latest else '—')
        self.card_vars['scan'].set(time.strftime('%d %b %H:%M UTC',time.gmtime(latest[1])) if latest else '—')
        self.card_vars['signals'].set(str(sum(1 for r in self.market_rows if r['signal']=='RECHECK' or r['watch_alert'])))
    def _update_tables(self):
        try:cash=int(self.store.get_pref('starting_cash','4338'));reserve=int(self.store.get_pref('reserve','500'))
        except ValueError:cash=4338;reserve=500
        ledger=self.store.ledger()
        wallet=self.store.latest_wallet()
        available_cash=max(0,wallet[1] if wallet else cash-ledger['spent']+ledger['sales_net'])
        self.market_rows=self.store.market_rows(available_cash,reserve);q=self.search_var.get().lower().strip()
        filtered=[r for r in self.market_rows if q in r['name'].lower() or q in r['id']]
        if self.filter_var.get()=='Affordable only':filtered=[r for r in filtered if r['affordable']]
        if self.filter_var.get()=='Watched only':filtered=[r for r in filtered if r['watch_target'] is not None]
        if self.filter_var.get()=='Materials only':
            keywords=('cloth','leather','ore','bar','herb','silverleaf','peacebloom','copper','mageroyal','hide','stone','linen','wool','silk','iron','malachite','tigerseye','meat','fish','thread')
            filtered=[r for r in filtered if any(k in r['name'].lower() for k in keywords)]
        def key(r):
            v=r.get(self.sort_col)
            return (v is None,v.lower() if isinstance(v,str) else v if v is not None else -1)
        filtered.sort(key=key,reverse=self.sort_reverse)
        self.market_tree.delete(*self.market_tree.get_children())
        for i,r in enumerate(filtered):
            ch='—' if r['change'] is None else f'{r["change"]:+.1f}%'
            row=(r['name'],fmt_copper(r['price']),r['qty'],fmt_copper(r['median']) if r['median'] else '—',ch,('PRICE ALERT' if r['watch_alert'] else r['signal']))
            self.market_tree.insert('',tk.END,iid=r['id'],values=row,tags=('signal',) if r['signal']=='RECHECK' or r['watch_alert'] else ('alternate',) if i%2 else ())
        self.movers_tree.delete(*self.movers_tree.get_children())
        mover_mode=self.movers_filter.get()
        movers=(r for r in self.market_rows if r['change'] is not None and
                (mover_mode=='All changes' and abs(r['change'])>=0.01 or
                 mover_mode=='Drops >20%' and r['change']<=-20 or
                 mover_mode=='Rises >20%' and r['change']>=20))
        movers=sorted(movers,key=lambda r:abs(r['change']),reverse=True)
        for r in movers:
            status=('INSPECT LIVE' if r['affordable'] else 'OVER BUDGET')
            if r['name'].startswith('Item #'):status='NAME UNKNOWN'
            self.movers_tree.insert('',tk.END,iid=r['id'],
                values=(r['name'],fmt_copper(r['previous_price']),fmt_copper(r['price']),
                        f'{r["change"]:+.1f}%',r['previous_qty'],r['qty'],status),
                tags=('signal',) if status=='INSPECT LIVE' and r['change']<0 else ())
        self.signal_tree.delete(*self.signal_tree.get_children())
        signals=sorted((r for r in self.market_rows if r['signal']=='RECHECK'),key=lambda r:r['discount'] or 0,reverse=True)
        for r in signals:
            self.signal_tree.insert('',tk.END,iid=r['id'],values=(r['name'],fmt_copper(r['price']),fmt_copper(r['median']),f'{r["discount"]:.1f}%',fmt_copper(r['potential_net']), 'VERIFY LIVE'),tags=('signal',))
        self.watch_tree.delete(*self.watch_tree.get_children())
        observed={r['id']:r for r in self.market_rows}
        for item_id,(target,notes) in sorted(self.store.watchlist().items()):
            row=observed.get(item_id)
            self.watch_tree.insert('',tk.END,iid=item_id,values=(row['name'] if row else 'Item #'+item_id,
                   fmt_copper(target),fmt_copper(row['price']) if row else '—',
                   'VERIFY LIVE' if row and row['watch_alert'] else 'Waiting',notes),
                   tags=('signal',) if row and row['watch_alert'] else ())
        self.book_rows=self.store.order_book_rows(available_cash,reserve)
        self.book_tree.delete(*self.book_tree.get_children())
        self.vendor_tree.delete(*self.vendor_tree.get_children())
        now=int(time.time())
        for b in self.book_rows:
            age=now-b['observed_at']
            age_str=f'{age//60}m' if age<3600 else f'{age//3600}h'
            self.book_tree.insert('',tk.END,iid=b['key'],values=(b['name'],b['status'],
                    fmt_copper(b['buy_price']) if b['buy_price'] is not None else '—',
                    b['front_qty'],fmt_copper(b['next_price']) if b['next_price'] is not None else '—',
                    fmt_copper(b['entry_cost']) if b['entry_cost'] is not None else '—',
                    fmt_copper(b['gross_theoretical']) if b['gross_theoretical'] is not None else '—',age_str),
                    tags=('signal',) if b['status'] in ('VENDOR','VENDOR CHECK','INSPECT') else ())
            if b['status'] in ('VENDOR','VENDOR CHECK'):
                self.vendor_tree.insert('',tk.END,iid=b['key'],values=(b['name'],b['kind'],
                    fmt_copper(b['buy_price']),fmt_copper(b.get('vendor_copper') or 0),
                    fmt_copper(b['gross_theoretical']) if b['gross_theoretical'] is not None else '—',
                    b['status']),tags=('signal',))
        self._update_ledger();self._update_activity();self._update_cards()
    def _update_activity(self):
        self.activity_tree.delete(*self.activity_tree.get_children())
        rows=self.store.automatic_events()
        for uid,ts,char,kind,name,qty,total,status,note in rows:
            self.activity_tree.insert('',tk.END,iid=str(uid),values=(
                time.strftime('%d %b %H:%M:%S',time.gmtime(ts)),char,kind,name,qty,
                fmt_copper(total),status.upper(),note),tags=('signal',) if status=='confirmed' else ())
        wallet=self.store.latest_wallet()
        extra=(f' · Last wallet: {fmt_copper(wallet[1])} ({wallet[0]}; '
               f'{time.strftime("%H:%M UTC",time.gmtime(wallet[2]))})') if wallet else ''
        self.activity_var.set(f'{len(rows)} recent events · {self.store.review_count()} need review'+extra)

    def _update_ledger(self):
        self.trade_tree.delete(*self.trade_tree.get_children())
        for id,ts,side,item_id,name,qty,price,fee,note,channel in self.store.transactions():
            self.trade_tree.insert('',tk.END,iid=str(id),values=(time.strftime('%Y-%m-%d %H:%M',time.gmtime(ts)),side,name,qty,fmt_copper(price),fmt_copper(fee),channel,note))
        l=self.store.ledger()
        note=f"Realised P&L: {fmt_copper(l['realized'])}  ·  NPC vendor profit: {fmt_copper(l['vendor_realized'])}  ·  Holdings: {l['holdings_qty']} items ({fmt_copper(l['holding_cost'])} cost)"
        if l['untracked_sell_qty']:note+='  ·  Some sells lack recorded buys'
        self.ledger_var.set(note)
    def trade_dialog(self,side,channel='AH'):
        selected=self.market_tree.selection();selected_id=selected[0] if selected else ''
        item_id=simpledialog.askstring(f'{side} trade','Item ID (search/choose in Market to prefill)',initialvalue=selected_id,parent=self)
        if not item_id:return
        qty=simpledialog.askinteger(f'{side} trade','Quantity',minvalue=1,parent=self)
        if qty is None:return
        guess=next((r['price'] for r in self.market_rows if r['id']==item_id),0)
        price=simpledialog.askinteger(f'{side} trade','Unit price in COPPER (1s = 100c)',initialvalue=guess,minvalue=0,parent=self)
        if price is None:return
        fee=simpledialog.askinteger(f'{side} trade','Additional fees/deposits in COPPER (0 if none)',initialvalue=0,minvalue=0,parent=self)
        if fee is None:return
        note=simpledialog.askstring('Trade note','Optional note',parent=self) or ''
        if side=='BUY':
            cash=int(self.store.get_pref('starting_cash','4338'))
            ledger=self.store.ledger(); available=cash-ledger['spent']+ledger['sales_net']
            reserve=int(self.store.get_pref('reserve','500'))
            if qty*price+fee>max(0,available-reserve):
                if not messagebox.askyesno('Exceeds trading budget',
                   'This purchase would use more than your unreserved trading cash.\nAre you sure you want to record it?'):
                    return
        self.store.log_trade(side,item_id,qty,price,fee,note,channel=channel);self._update_tables()
        messagebox.showinfo('Recorded',f'{side} ({channel}): {qty} x {item_id} @ {fmt_copper(price)} each.\nThis has NOT placed a WoW order.')
    def export(self):
        initial='forever-market-'+time.strftime('%Y%m%d-%H%M%S')+'.zip'
        dest=filedialog.asksaveasfilename(title='Export for ChatGPT',defaultextension='.zip',initialfile=initial,filetypes=[('ZIP files','*.zip')])
        if not dest:return
        try:self.store.export_bundle(Path(dest));self.status_var.set('Export ready: '+dest)
        except Exception as e:messagebox.showerror('Export error',str(e));return
        messagebox.showinfo('Export ready','Upload this ZIP in our ChatGPT conversation for detailed trade analysis:\n'+dest)
    def _async_update_check(self):
        self.check_updates(silent=True)
        self.after(20*60*1000,self._async_update_check)
    def check_updates(self,silent=False):
        repo=self.repo_var.get().strip()
        if not repo:
            if not silent:messagebox.showinfo('Updates','Enter a GitHub owner/repo in Settings')
            return
        def worker():
            try:result=get_latest(repo)
            except Exception as e:
                if not silent:self.after(0,lambda:messagebox.showinfo('Update check',f'Could not check GitHub Releases:\n{e}\n\nPublish a release first.'))
                return
            self.after(0,lambda:self._offer_update(result,silent))
        threading.Thread(target=worker,daemon=True).start()
    def _offer_update(self,info,silent):
        if not info['new']:
            if not silent:messagebox.showinfo('Updates','Already on the latest published release (v'+VERSION+').')
            return
        if silent and self.skipped_update==info['version']:
            return
        kind='standalone EXE' if getattr(sys,'frozen',False) else 'Python source'
        if not messagebox.askyesno('New version available',
            f'Forever Trader {info["version"]} is available (installed: {VERSION}).\n'
            f'Update this {kind} installation now?\n\n'
            'The app will download a SHA-256 verified release and restart after replacing its files. '
            'Your trading database under AppData is preserved.'):
            self.skipped_update=info['version']
            return
        self.status_var.set('Downloading verified update…')
        def worker():
            try:
                stage=download_update(info) if getattr(sys,'frozen',False) else download_source(info)
                self.after(0,lambda:self._finish_update(stage))
            except Exception as e:
                self.after(0,lambda:messagebox.showerror('Update error',str(e)))
        threading.Thread(target=worker,daemon=True).start()
    def _finish_update(self,stage):
        try:
            if getattr(sys,'frozen',False):install_on_exit(stage)
            else:install_source_on_exit(stage,Path(__file__).resolve().parents[1])
        except Exception as e:messagebox.showerror('Update failed',str(e));return
        self._close()

    def _close(self):
        try: self.store.close()
        finally: self.destroy()


def main():
    app=App();app.mainloop()
