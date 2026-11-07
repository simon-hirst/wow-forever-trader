"""Win11 Tk desktop UI. Built using stdlib for reliable single-EXE packaging."""
from __future__ import annotations
import hashlib, json, os, queue, sys, threading, time, webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from .auctionator import parse_auctionator, parse_aux_names, fmt_copper, ParseError
from .market import MarketStore
from .bridge import parse_bridge
from .updater import get_latest,download_update,install_on_exit,download_source,install_source_on_exit
from . import VERSION
from .addon_sync import sync_installed_bridge, addons_root
from . import marketdata
from .autopilot import Autopilot, exports_dir
from .link import LinkReceiver, set_dpi_aware
from .overlay import ConfirmOverlay
from .phone_alerts import WalletCrossing, generate_topic, validate_topic, threshold_copper, publish_ntfy

BG='#0e1522';PANEL='#172336';PANEL2='#1d2c41';FG='#e7edf7';MUTED='#91a2bb';ACCENT='#51a9ff';GREEN='#52d1a6';ORANGE='#fbbf66';RED='#fa7185'

def user_data_dir():
    parent=os.getenv('APPDATA') or str(Path.home()/'.config')
    return Path(parent)/'ForeverTrader'

def installed_wow_root():
    """Where the Battle.net installer says WoW is. It can be on any drive."""
    if os.name!='nt':return None
    import winreg
    for key_path in (r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\World of Warcraft',
                     r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\World of Warcraft'):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,key_path) as key:
                return Path(winreg.QueryValueEx(key,'InstallLocation')[0])
        except OSError:
            continue
    return None

def find_suggested_path():
    roots=[Path(os.getenv('PROGRAMFILES(X86)','C:/Program Files (x86)'))/'World of Warcraft',
           Path(os.getenv('PROGRAMFILES','C:/Program Files'))/'World of Warcraft',
           Path(os.getenv('USERPROFILE',str(Path.home())))/'Games'/'World of Warcraft',
           Path('C:/World of Warcraft'),Path('D:/World of Warcraft'),Path('D:/Games/World of Warcraft'),
           Path('E:/World of Warcraft')]
    registered=installed_wow_root()
    if registered:roots.insert(0,registered)
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
        self.overlay_var=tk.BooleanVar(value=self.store.get_pref('windows_button','1')=='1')
        self.auto_export_var=tk.BooleanVar(value=self.store.get_pref('auto_export','1')=='1')
        self.link_state=None;self.link_state_at=0.0;self.last_market_files={}
        self.phone_enabled=self.store.get_pref('phone_alert_enabled','0')=='1'
        self.phone_threshold_silver=self.store.get_pref('phone_alert_silver','30')
        self.phone_topic=self.store.get_pref('phone_alert_topic','')
        self.phone_alarm=WalletCrossing()
        self.phone_threshold=threshold_copper(self.phone_threshold_silver)
        self.phone_enabled_var=tk.BooleanVar(value=self.phone_enabled)
        self.phone_silver_var=tk.StringVar(value=self.phone_threshold_silver)
        self.phone_topic_var=tk.StringVar(value=self.phone_topic)
        self.phone_status_var=tk.StringVar(value='Disabled' if not self.phone_enabled else 'Waiting for live wallet data')
        self._style();self._build()
        if not self.source_var.get():
            candidate=find_suggested_path()
            if candidate:self.source_var.set(str(candidate));self._save_settings()
        self.overlay=ConfirmOverlay(self,status=self.status_var.set)
        self.overlay.enabled=self.overlay_var.get()
        self.context_lock=threading.Lock();self.analysis_context={}
        self._refresh_analysis_context()
        self.link=LinkReceiver();self.link.start()
        self.autopilot=Autopilot(user_data_dir(),self.link.frames,self._analysis_context);self.autopilot.start()
        self.after(100,self.refresh);self.after(4000,self._poll);self.after(50,self._poll_link)
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
        st.configure('TNotebook.Tab',padding=(9,8),background=PANEL,foreground=MUTED,font=('Segoe UI Semibold',9))
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
        ttk.Label(left,text='EU · Horde · PvP  |  Automatic market analysis  |  v'+VERSION,style='Hint.TLabel').pack(anchor='w',pady=(3,0))
        ttk.Button(top,text='Export ZIP',style='Accent.TButton',command=self.export).pack(side='right',padx=(8,0))
        ttk.Button(top,text='Refresh file',command=self.refresh).pack(side='right',padx=(8,0))
        ttk.Button(top,text='Check updates',command=self.check_updates).pack(side='right',padx=(8,0))
        cards=ttk.Frame(outer);cards.pack(fill='x',pady=(24,20))
        self.card_vars={}
        for key,label in [('cash','Wallet'),('profit','Realised profit'),('orders','Plan orders'),('scan','Last scan'),('link','Data link')]:
            card=ttk.Frame(cards,style='Card.TFrame',padding=(17,14));card.pack(side='left',fill='both',expand=True,padx=(0,9))
            ttk.Label(card,text=label,style='CardName.TLabel').pack(anchor='w')
            v=tk.StringVar(value='—');self.card_vars[key]=v
            ttk.Label(card,textvariable=v,style='CardValue.TLabel').pack(anchor='w',pady=(7,0))
        self.tabs=ttk.Notebook(outer);self.tabs.pack(fill='both',expand=True)
        self.strategy_tab=ttk.Frame(self.tabs)
        self.performance_tab=ttk.Frame(self.tabs)
        self.market_tab=ttk.Frame(self.tabs);self.signal_tab=ttk.Frame(self.tabs)
        self.movers_tab=ttk.Frame(self.tabs)
        self.book_tab=ttk.Frame(self.tabs)
        self.vendor_tab=ttk.Frame(self.tabs)
        self.activity_tab=ttk.Frame(self.tabs)
        self.watch_tab=ttk.Frame(self.tabs)
        self.trades_tab=ttk.Frame(self.tabs);self.settings_tab=ttk.Frame(self.tabs)
        self.calc_tab=ttk.Frame(self.tabs)
        self.help_tab=ttk.Frame(self.tabs)
        for frame,label in [(self.strategy_tab,'STRATEGY'),(self.performance_tab,'PERFORMANCE'),(self.market_tab,'MARKET'),(self.book_tab,'ORDER BOOK'),(self.vendor_tab,'VENDOR'),(self.movers_tab,'MOVERS'),(self.signal_tab,'SIGNALS'),(self.watch_tab,'WATCHLIST'),
                             (self.activity_tab,'ACTIVITY'),(self.trades_tab,'TRADES'),(self.calc_tab,'CALC'),(self.settings_tab,'SETTINGS'),(self.help_tab,'HELP')]:
            self.tabs.add(frame,text=label)
        self._strategy_tab();self._performance_tab();self._market_tab();self._book_tab();self._vendor_tab();self._movers_tab();self._signal_tab();self._watch_tab();self._trades_tab();self._activity_tab();self._calc_tab();self._settings_tab();self._help_tab()
        status=ttk.Frame(outer);status.pack(fill='x',pady=(12,0))
        ttk.Label(status,textvariable=self.status_var,style='Hint.TLabel').pack(side='left')
        self.link_detail_var=tk.StringVar(value='Link: waiting for the auction house')
        ttk.Label(status,textvariable=self.link_detail_var,style='Hint.TLabel').pack(side='right')
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
    def _strategy_tab(self):
        head=ttk.Frame(self.strategy_tab,padding=(0,14,0,4));head.pack(fill='x')
        self.plan_header_var=tk.StringVar(value='Waiting for a full listing. Open the auction house, or AHledger or the Bridge will supply one after a /reload.')
        ttk.Label(head,textvariable=self.plan_header_var,wraplength=880).pack(side='left',anchor='w')
        ttk.Button(head,text='Open exports folder',command=self.open_exports).pack(side='right')
        ttk.Button(head,text='Analyse now',command=lambda:self.autopilot.request_analysis()).pack(side='right',padx=8)
        self.plan_notes_var=tk.StringVar(value='')
        ttk.Label(self.strategy_tab,textvariable=self.plan_notes_var,style='Hint.TLabel',wraplength=1200).pack(anchor='w',pady=(0,8))
        cols={'action':'ACTION','name':'ITEM','ask':'ASK','limit':'PAY UP TO','target':'LIST AT','units':'MAX UNITS','sold':'SOLD/H','book':'FOR SALE','net':'NET','hours':'HOURS','grade':'GRADE','why':'WHY'}
        self.plan_tree=self._make_tree(self.strategy_tab,cols,{'action':80,'name':205,'ask':70,'limit':80,'target':70,'units':80,'sold':65,'book':75,'net':55,'hours':60,'grade':55,'why':420})
        self.plan_tree.column('action',anchor='w');self.plan_tree.column('why',anchor='w',stretch=True);self.plan_tree.column('grade',anchor='center')
        self.plan_tree.tag_configure('avoid',foreground=RED)
        self.plan_tree.tag_configure('muted',foreground=MUTED)
        ttk.Label(self.strategy_tab,text='Each order is a limit: when something lists at or under PAY UP TO, the Confirm button appears. Grade C means under a day of history, so margins are higher and sizes smaller. Grades rise as calls prove out.',
                  style='Hint.TLabel',wraplength=1200).pack(anchor='w',pady=(8,0))

    def _show_plan(self,result):
        plan=result['plan'];summary=plan['summary'];scoring=plan['scoring']
        self.plan_result=result
        scored=(f"Past calls: {scoring['won']} won, {scoring['lost']} lost." if scoring.get('scored')
                else f"Past calls: {scoring.get('calls',0)} placed, none played out yet.")
        self.plan_header_var.set(f"{summary['state']}: {summary['index_change_pct']:+.2f}% over {summary['hours_observed']}h, "
            f"{summary['rising']} up, {summary['falling']} down, about {fmt_copper(summary['turnover_copper_per_hour'])} sold per hour.   {scored}   "
            f"Free cash {fmt_copper(plan['free_cash'])}.   Plan rev {result['rev']}.")
        self.plan_notes_var.set(('Analyst notes: '+plan['notes_summary']) if plan.get('notes_summary') else '')
        self.plan_tree.delete(*self.plan_tree.get_children())
        orders=0
        for c in plan['calls']:
            if c.action in ('VENDOR','BUY NOW','LIMIT'):orders+=1
            elif c.action!='AVOID':continue
            tag=('signal',) if c.action in ('VENDOR','BUY NOW') else ('avoid',) if c.action=='AVOID' else ()
            self.plan_tree.insert('',tk.END,values=(c.action,c.name,fmt_copper(c.ask),fmt_copper(c.limit) if c.limit else '—',
                fmt_copper(c.target) if c.target else '—',c.max_units or '—',f'{c.sold_per_hour:.0f}',c.units,
                f'{100*c.margin:.0f}%' if c.margin else '—',f'{c.fill_hours:.1f}' if c.fill_hours else '—',c.grade,c.why),tags=tag)
        self.card_vars['orders'].set(str(orders))

    def open_exports(self):
        folder=exports_dir(user_data_dir());folder.mkdir(parents=True,exist_ok=True)
        if os.name=='nt':os.startfile(folder)
        else:self.status_var.set(str(folder))

    def _refresh_analysis_context(self):
        """Runs on the UI thread; the worker reads this copy, since Tk is not thread-safe."""
        try:cash=int(self.store.get_pref('starting_cash','4338'));reserve=int(self.store.get_pref('reserve','500'))
        except ValueError:cash,reserve=4338,500
        wallet=self.store.latest_wallet()
        if wallet:cash=wallet[1]
        state=self.link_state
        if state and time.time()-self.link_state_at<600:cash=state['money']
        link=getattr(self,'link',None)
        with self.context_lock:
            self.analysis_context=dict(cash=cash,reserve=reserve,spec=0.25,addons_root=addons_root(self.source_var.get()),
                auto_export=self.auto_export_var.get(),link=link.stats.as_dict() if link else None)

    def _analysis_context(self):
        with self.context_lock:
            return dict(self.analysis_context)

    def _item_name(self,item_id):
        row=self.store.db.execute('SELECT name FROM names WHERE item_id=?',(str(item_id),)).fetchone()
        return row[0] if row else f'Item #{item_id}'

    def _poll_link(self):
        """Fast UI tick: the newest addon state, plus results from the worker."""
        try:
            newest=None
            while True:
                try:newest=self.link.states.get_nowait()
                except queue.Empty:break
                self._observe_wallet(newest)
            if newest:
                self.link_state,self.link_state_at=newest,time.time()
                self.overlay.update(newest,self._item_name(newest['item_id']) if newest['item_id'] else '',fmt_copper)
            while True:
                try:self._show_plan(self.autopilot.results.get_nowait())
                except queue.Empty:break
            while True:
                try:self.status_var.set(self.autopilot.messages.get_nowait())
                except queue.Empty:break
            changed=False
            while True:
                try:
                    self.autopilot.live_changes.get_nowait()
                    changed=True
                except queue.Empty:
                    break
            if changed:
                self._update_performance()
                self._update_activity()
                self._update_ledger()
                self._update_cards()
            self._update_link_status()
        except Exception as exc:
            self.status_var.set('Link error: '+str(exc))
        self.after(50,self._poll_link)

    def _observe_wallet(self, state):
        """Only live addon frames count; saved wallet snapshots may be days old."""
        if not self.phone_enabled or not self.phone_topic:
            return
        if self.phone_alarm.observe(state.get('money'), self.phone_threshold):
            money=state['money']
            self._send_phone_alert('Wallet threshold reached',
                f'Your WoW wallet reached {fmt_copper(money)} (target {self.phone_threshold_silver}s).')

    def _send_phone_alert(self, title, message):
        topic=self.phone_topic_var.get().strip() if title=='Forever Trader test' else self.phone_topic
        try:validate_topic(topic)
        except ValueError as exc:
            self.phone_status_var.set(str(exc))
            return
        self.phone_status_var.set('Sending ntfy message...')
        def worker():
            try:
                publish_ntfy(topic,message,title=title)
                status='Sent to ntfy. Check your subscribed phone topic.'
            except Exception as exc:
                # The HTTP error can contain a private topic URL; don't display
                # it to a UI log or an exported diagnostics bundle.
                status=f'ntfy send failed ({type(exc).__name__}); check internet/topic'
            try:self.after(0,lambda:self.phone_status_var.set(status))
            except RuntimeError:pass
        threading.Thread(target=worker,name='ft-phone-alert',daemon=True).start()

    def _generate_phone_topic(self):
        topic=generate_topic()
        self.phone_topic_var.set(topic)
        self.clipboard_clear()
        self.clipboard_append(topic)
        self.phone_status_var.set('Private topic copied. Subscribe in ntfy on your phone, then enable and save.')

    def _save_phone_alerts(self):
        try:
            threshold=threshold_copper(self.phone_silver_var.get())
            topic=self.phone_topic_var.get().strip()
            if topic:validate_topic(topic)
            if self.phone_enabled_var.get() and not topic:
                raise ValueError('Generate a private ntfy topic before enabling phone alerts.')
        except ValueError as exc:
            messagebox.showerror('Phone alert settings',str(exc))
            return
        self.phone_enabled=self.phone_enabled_var.get()
        self.phone_threshold=threshold
        self.phone_threshold_silver=self.phone_silver_var.get().strip()
        self.phone_topic=topic
        self.phone_alarm.reset()
        for key,value in [('phone_alert_enabled','1' if self.phone_enabled else '0'),
                          ('phone_alert_silver',self.phone_threshold_silver),
                          ('phone_alert_topic',self.phone_topic)]:
            self.store.set_pref(key,value)
        self.phone_status_var.set(
            'Enabled; waiting for a below-to-above crossing from the live addon.' if self.phone_enabled
            else 'Disabled. Changes saved.')

    def _test_phone_alert(self):
        try:validate_topic(self.phone_topic_var.get().strip())
        except ValueError as exc:
            messagebox.showerror('Phone alert settings',str(exc))
            return
        self._send_phone_alert('Forever Trader test',
                               'Phone alerts are connected. You will be notified on a wallet threshold crossing.')

    def _update_link_status(self):
        stats=self.link.stats
        age=time.time()-stats.last_frame_at if stats.last_frame_at else None
        state=self.link_state
        if age is not None and age<3:
            card='Live'
            bits=[f'Link live ({stats.frames} frames, {stats.bad} unreadable, {stats.gaps} gaps)']
            if state:
                bits.append(f"{state['rounds']} rounds")
                if state['queue']:bits.append(f"{state['queue']} queued")
        elif not stats.window_found:
            card='No WoW';bits=['Link: WoW is not running']
        else:
            card='Waiting';bits=['Link: waiting for the auction house, with the top of the game window visible']
        if card!=self.card_vars['link'].get():self.card_vars['link'].set(card)
        text=' · '.join(bits)
        if text!=self.link_detail_var.get():self.link_detail_var.set(text)

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
    def _performance_tab(self):
        """Outcome telemetry (fast live link) and audited vendor receipts."""
        outer=self.performance_tab
        header=ttk.Frame(outer,padding=(0,14,0,10));header.pack(fill='x')
        ttk.Label(header,text='SNIPER PERFORMANCE',style='Title.TLabel').pack(side='left')
        self.perf_note=tk.StringVar(value='Waiting for Sniper telemetry')
        ttk.Label(header,textvariable=self.perf_note,style='Hint.TLabel').pack(side='right')
        ttk.Label(outer,text='Cumulative matched AH-buy and NPC-sale receipts determine vendor profit. '
                  'Sniper estimates do not count. CHECK RECEIPT marks a possible purchase '
                  'near an otherwise unsuccessful attempt: inspect it rather than assuming a loss.',
                  style='Hint.TLabel',wraplength=1090).pack(anchor='w',pady=(0,12))
        metrics=ttk.Frame(outer);metrics.pack(fill='x',pady=(0,14))
        self.perf_vars={}
        labels=[('vendor','VERIFIED VENDOR P&L'),('rate','EST. GOLD / ACTIVE HOUR'),
                ('attempts','BOUGHT / MISSED'),('capture','KNOWN-OUTCOME WIN RATE'),
                ('latency','MEDIAN PAGE → PURCHASE'),('review','RECEIPTS NEEDING REVIEW')]
        for key,label in labels:
            tile=ttk.Frame(metrics,style='Card.TFrame',padding=(10,10))
            tile.pack(side='left',fill='x',expand=True,padx=(0,7))
            ttk.Label(tile,text=label,style='CardName.TLabel').pack(anchor='w')
            v=tk.StringVar(value='—');self.perf_vars[key]=v
            ttk.Label(tile,textvariable=v,style='CardValue.TLabel').pack(anchor='w',pady=(4,0))
        ttk.Label(outer,text='RECENT SNIPER ACTIVITY  ·  green = bought  ·  red = unavailable / legacy lost',
                  style='Hint.TLabel').pack(anchor='w',pady=(0,5))
        self.perf_chart=tk.Canvas(outer,height=102,bg=PANEL,highlightthickness=0)
        self.perf_chart.pack(fill='x',pady=(0,12))
        cols={'time':'UTC','item':'ITEM','outcome':'RESULT','source':'SCAN','qty':'QTY',
              'cost':'SPEND','est':'EST. MARGIN','page':'PAGE → FINISH','offer':'OFFER → FINISH'}
        self.perf_tree=self._make_tree(outer,cols,
            {'time':130,'item':240,'outcome':100,'source':72,'qty':55,
             'cost':90,'est':98,'page':115,'offer':120})
        self.perf_tree.tag_configure('bought',foreground=GREEN)
        self.perf_tree.tag_configure('lost',foreground=RED)
        ttk.Label(outer,text='NPC VENDOR SALES  ·  green profit = matched purchase receipt  ·  ? = cost unknown',
                  style='Hint.TLabel').pack(anchor='w',pady=(13,5))
        sale_cols={'time':'UTC','item':'ITEM','qty':'SOLD','proceeds':'VENDOR PAID',
                   'matched':'MATCHED UNITS','profit':'VERIFIED PROFIT','status':'ACCOUNTING'}
        self.perf_sales_tree=self._make_tree(outer,sale_cols,
            {'time':132,'item':240,'qty':62,'proceeds':105,'matched':110,
             'profit':115,'status':125})
        self.perf_sales_tree.tag_configure('matched',foreground=GREEN)
        self.perf_sales_tree.tag_configure('review',foreground=ORANGE)
        ttk.Label(outer,text='Recorded attempts are deduplicated between the live data link and SavedVariables. '
                  'Timeouts and failed live checks are now UNCERTAIN, not competitive misses. Older LOST '
                  'rows may also be ambiguous. Vendor receipts may need /reload. '
                  'Hourly rates use observed attempts, not complete playing time.',
                  style='Hint.TLabel',wraplength=1080).pack(anchor='w',pady=(9,0))

    def _draw_performance_chart(self,rows):
        canvas=self.perf_chart
        canvas.delete('all')
        width=max(680,canvas.winfo_width())
        left,right,top,bottom=20,width-15,14,85
        canvas.create_line(left,bottom,right,bottom,fill=MUTED)
        # Twelve five-minute bins, ending in the current five-minute bucket.
        now=int(time.time());end=now//300*300+300;start=end-12*300
        buckets=[[0,0] for _ in range(12)]
        for uid,ts,char,item,name,outcome,*_ in rows:
            i=(ts-start)//300
            if 0<=i<12 and outcome in ('BOUGHT','LOST','UNAVAILABLE'):
                buckets[i][0 if outcome=='BOUGHT' else 1]+=1
        peak=max([1]+[a+b for a,b in buckets])
        cell=(right-left)/12
        for i,(bought,lost) in enumerate(buckets):
            x=left+i*cell+cell*0.2;w=cell*0.6
            h1=(bought/peak)*(bottom-top)
            h2=(lost/peak)*(bottom-top)
            if bought:canvas.create_rectangle(x,bottom-h1,x+w,bottom,fill=GREEN,outline='')
            if lost:canvas.create_rectangle(x,bottom-h1-h2,x+w,bottom-h1,fill=RED,outline='')
            if i%2==0:
                lab=time.strftime('%H:%M',time.gmtime(start+i*300))
                canvas.create_text(x+w/2,bottom+10,text=lab,fill=MUTED,font=('Segoe UI',8))
        if sum(sum(b) for b in buckets)==0:
            canvas.create_text(width/2,top+27,text='No recent Sniper outcomes yet',
                               fill=MUTED,font=('Segoe UI',11))
        canvas.create_text(right,top,text='Past 60 minutes',fill=MUTED,
                           anchor='ne',font=('Segoe UI',9))

    def _update_performance(self):
        perf=self.store.sniper_performance()
        vendor=perf['confirmed_vendor']
        rate=perf['profit_per_active_hour']
        self.perf_vars['vendor'].set(fmt_copper(vendor['realised']))
        self.perf_vars['rate'].set(fmt_copper(round(rate))+'/h' if rate is not None else '—')
        self.perf_vars['attempts'].set(f"{perf['bought']} / {perf['missed']}")
        capture=perf['capture_rate']
        self.perf_vars['capture'].set(f'{capture:.0%}' if capture is not None else '—')
        latency=perf['median_page_to_buy_ms']
        self.perf_vars['latency'].set(f'{latency/1000:.2f}s' if latency is not None else '—')
        self.perf_vars['review'].set(str(self.store.review_count()))
        self.perf_note.set(
            f"{perf['attempts']} attempts · {perf['uncertain']} uncertain · "
            f"{perf['early_buys']} early buys · "
            f"{vendor['matched_qty']} historical matched sale units · "
            f"{vendor['unmatched_qty']} unmatched")
        rows=self.store.recent_snipe_attempts(limit=1000)
        self.perf_tree.delete(*self.perf_tree.get_children())
        for uid,ts,char,item,name,outcome,qty,cost,estimate,early,page_ready,page_finish,offer_ready,offer_finish,reason,source,receipt_hint in rows[:350]:
            page=f'{page_finish/1000:.2f}s' if page_finish and early else '—'
            offer=f'{offer_finish/1000:.2f}s' if offer_finish else '—'
            displayed='CHECK RECEIPT' if receipt_hint else outcome
            self.perf_tree.insert('',tk.END,iid=uid,
                values=(time.strftime('%d %b %H:%M:%S',time.gmtime(ts)),name,displayed,
                        source.upper(),qty,fmt_copper(cost) if cost else '—',
                        fmt_copper(estimate) if estimate else '—',page,offer),
                tags=('bought',) if outcome=='BOUGHT' else ('lost',) if outcome in ('LOST','UNAVAILABLE') else ())
        self._draw_performance_chart(rows)
        self.perf_sales_tree.delete(*self.perf_sales_tree.get_children())
        for uid,ts,char,name,qty,total,matched,profit,status in self.store.recent_vendor_sales(limit=200):
            self.perf_sales_tree.insert('',tk.END,iid=str(uid),
                values=(time.strftime('%d %b %H:%M:%S',time.gmtime(ts)),
                        name,qty,fmt_copper(total) if total else '—',
                        matched if status not in ('REVIEW','NO BUY RECORD') else '—',
                        fmt_copper(profit) if profit is not None else '?',
                        status),
                tags=('matched',) if status=='MATCHED' else
                     ('review',) if status in ('REVIEW','NO BUY RECORD') else ())

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
        settings_book=ttk.Notebook(self.settings_tab);settings_book.pack(fill='both',expand=True,pady=(8,0))
        frame=ttk.Frame(settings_book,padding=20);settings_book.add(frame,text='GENERAL')
        phone=ttk.Frame(settings_book,padding=20);settings_book.add(phone,text='PHONE ALERTS')
        ttk.Label(phone,text='Wallet threshold push notification',font=('Segoe UI Semibold',13)).pack(anchor='w',pady=(0,12))
        ttk.Label(phone,text='Uses the LIVE money counter from the WoW addon. No new auction-house requests.',
                  style='Hint.TLabel',wraplength=850).pack(anchor='w',pady=(0,12))
        ttk.Checkbutton(phone,text='Notify my phone when my wallet rises to the threshold',
                        variable=self.phone_enabled_var).pack(anchor='w',pady=(0,12))
        self._setting(phone,'Target silver (30 = 30s; 1g = 100s)',self.phone_silver_var)
        self._setting(phone,'Private ntfy.sh topic',self.phone_topic_var)
        buttons=ttk.Frame(phone);buttons.pack(anchor='w',pady=(10,12))
        ttk.Button(buttons,text='Generate private topic + copy',command=self._generate_phone_topic).pack(side='left')
        ttk.Button(buttons,text='Send test to phone',command=self._test_phone_alert).pack(side='left',padx=(10,0))
        ttk.Button(phone,text='Save phone alert settings',style='Accent.TButton',command=self._save_phone_alerts).pack(anchor='w',pady=(6,12))
        ttk.Label(phone,textvariable=self.phone_status_var,style='Hint.TLabel',wraplength=840).pack(anchor='w',pady=(0,10))
        ttk.Label(phone,text='Setup: install ntfy on Android, subscribe to the generated topic, then enable and save. '
                  'In WoW, use /ft walletstream on to receive balance updates outside the auction house too. '
                  'An alert fires once on a crossing from below to the target and rearms below it. '
                  'The app must be running with the WoW data strip visible.',
                  style='Hint.TLabel',wraplength=870).pack(anchor='w',pady=(0,10))
        ttk.Label(phone,text='ntfy.sh is a third-party public service. Anyone who knows your topic can read messages. '
                  'Keep your random topic private; notifications include your WoW wallet balance. '
                  'Topic and preferences remain on your PC.',style='Hint.TLabel',wraplength=870).pack(anchor='w')
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
        ttk.Checkbutton(frame,text='Windows Confirm button when the cursor is outside WoW (one click sends one key press)',
                        variable=self.overlay_var).pack(anchor='w',pady=(4,0))
        ttk.Checkbutton(frame,text='Write an export ZIP every 15 minutes when there is new data',
                        variable=self.auto_export_var).pack(anchor='w',pady=(4,0))
        ttk.Button(frame,text='Save settings and scan',style='Accent.TButton',command=self.apply_settings).pack(anchor='w',pady=18)
        ttk.Label(frame,text='Example: 43s 38c = 4338 copper, reserve 5s = 500 copper.',style='Hint.TLabel').pack(anchor='w')
        ttk.Label(frame,text='Updates are checked on startup and roughly every 20 minutes; installation requires confirmation.',style='Hint.TLabel').pack(anchor='w',pady=4)
        ttk.Label(frame,text='Market exports stay on your PC. Optional phone alerts send wallet notifications to ntfy.sh.',style='Hint.TLabel').pack(anchor='w')
    def _help_tab(self):
        frame=ttk.Frame(self.help_tab,padding=20);frame.pack(fill='both',expand=True)
        help_text='''HOW IT RUNS\n\nStart this app, then open the auction house in WoW. That is all.\nThe addon opens auxForever's Sniper and starts it, records the market and sends it here over the\ncolour strip at the top of the game window. Keep that strip visible. No /reload is needed.\nThe STRATEGY tab reruns as data arrives. An export ZIP is written every 15 minutes when there is\nnew data, and a Claude Code chat open on this PC can review them (type /ft-review).\n\nTHE CONFIRM BUTTON\n\nA deal puts a Confirm button at your cursor. One press buys every unit of a stackable item under\nthe limit. Gear takes one press per auction, because the game requires it. A mouse-wheel notch\nover the button counts as a press too. Right-click skips the deal.\nBelow-vendor deals may use all your cash except the reserve. Other deals get a quarter of free\ncash at most, and the plan's unit cap.\nIf your cursor is outside WoW, a Windows button appears instead. Each click sends one key press.\n\nIn game: /ft diag shows what is working. /ft for all commands.\n\nOLDER SIGNALS\n\nBASELINE: first observation; do not treat it as a deal.\nWATCH: has price history but not a sufficiently strong discount.\nRECHECK: current minimum at least 20% below prior scan median and fits bankroll.\n\nYou can rename unidentified IDs in MARKET. Price targets alert on a NEW snapshot.\nEvery listed price is an observed minimum, not a live executable order.\nORDER BOOK shows actual price tiers from searches you performed, but never transactions.\nINSPECT requires fresh, complete commodity results, thin cheapest tier, and bankroll fit.\nPrices are scenarios, not fair values. Individual stack auctions require manual evaluation.\nAuctionator does not reveal completed sales. This app cannot know actual sell-through.\nMultiple scans on the same day may be merged by Auctionator's own daily buckets,\nbut the app separately saves snapshots after every changed SavedVariables write.\n\nTRADING\n\nRecord manual buys and sells to track holdings and estimated after-fee realised P&L.\nAlways double-check the posted stack sizes and deposit before buying/relisting.\nThe default faction AH sales fee assumption is 5%.\nNever buy solely from the app's signal: check the listing in WoW.\n\nUPDATES\n\nThe app checks GitHub Releases for a newer version and asks before installing it.\n'''
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
        self.store.set_pref('windows_button','1' if self.overlay_var.get() else '0')
        self.store.set_pref('auto_export','1' if self.auto_export_var.get() else '0')
        if hasattr(self,'overlay'):self.overlay.enabled=self.overlay_var.get()
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
    def _refresh_market_files(self):
        """Vendor prices from aux, and full listings AHledger saved. Only what changed is read."""
        path_text=self.source_var.get().strip()
        if not path_text:return
        folder=Path(path_text).parent
        sources=[('auxForever.lua',Path(self.aux_var.get().strip()) if self.aux_var.get().strip() else folder/'auxForever.lua'),
                 ('AHledger.lua',folder/'AHledger.lua')]
        for name,path in sources:
            try:stat=path.stat()
            except OSError:continue
            sig=(stat.st_mtime_ns,stat.st_size)
            if self.last_market_files.get(name)==sig:continue
            self.last_market_files[name]=sig
            if name=='auxForever.lua':
                marketdata.ingest_items(self.store.db,marketdata.parse_aux_items(path))
            else:
                added=0
                for ts,items in sorted(marketdata.parse_ahledger_scans(path),key=lambda s:s[0]):
                    added+=marketdata.ingest_snapshot(self.store.db,ts,items,True,'ahledger')
                if added:self.autopilot.request_analysis()

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
            attempts=self.store.ingest_snipe_metrics(payload)
            for bucket,data in payload.get('bars',[]):
                marketdata.ingest_bars(self.store.db,bucket,marketdata.parse_bridge_bars(data))
            for ts,data in payload.get('snapshots',[]):
                if marketdata.ingest_snapshot(self.store.db,ts,marketdata.parse_bridge_snapshot(data),True,'bridge'):
                    self.autopilot.request_analysis()
            self.last_bridge_sig=sig
            if imported or attempts or activity['new_events'] or activity['wallets'] or activity['wallet_changes']:
                self.status_var.set(f'Bridge: {imported} books, {attempts} Sniper outcomes, {activity["new_events"]} events '
                    f'({activity["confirmed"]} confirmed), {activity["wallets"]} wallet updates, '
                    f'{activity["wallet_changes"]} wallet changes; /reload to sync')
            return imported+attempts+activity['new_events']+activity['wallets']+activity['wallet_changes']
        except (OSError,ParseError,ValueError) as e:
            if not silent:self.status_var.set('Bridge import failed: '+str(e))
            return 0

    def refresh(self,silent=False):
        self._refresh_analysis_context()
        try:self._refresh_market_files()
        except (OSError,ValueError) as exc:self.status_var.set('Could not read market files: '+str(exc))
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
        if self.link_state and time.time()-self.link_state_at<600:cash=self.link_state['money']
        self.card_vars['cash'].set(fmt_copper(cash))
        self.card_vars['profit'].set(fmt_copper(ledger['realized']))
        self.card_vars['scan'].set(time.strftime('%d %b %H:%M UTC',time.gmtime(latest[1])) if latest else '—')
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
        self._update_ledger();self._update_activity();self._update_cards();self._update_performance()
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
        recent=self.store.recent_wallet_changes(limit=1)
        movement=(f' · Latest wallet change {recent[0][4]:+d}c ({recent[0][5]})' if recent else '')
        self.activity_var.set(f'{len(rows)} recent events · {self.store.review_count()} need review'+extra+movement)

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
        dest=filedialog.asksaveasfilename(title='Export ZIP',defaultextension='.zip',initialfile=initial,filetypes=[('ZIP files','*.zip')])
        if not dest:return
        try:self.store.export_bundle(Path(dest),analysis_dir=user_data_dir()/'analysis',link_status=self.link.stats.as_dict());self.status_var.set('Export ready: '+dest)
        except Exception as e:messagebox.showerror('Export error',str(e));return
        messagebox.showinfo('Export ready','Export written:\n'+dest)
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
        try:
            self.autopilot.stop();self.link.stop()
            self.autopilot.join(timeout=2);self.link.join(timeout=2)
            self.store.close()
        finally: self.destroy()


def main():
    set_dpi_aware()
    app=App();app.mainloop()
