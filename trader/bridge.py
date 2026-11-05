"""Read only the ForeverTraderBridge SavedVariables table. Never execute Lua.

Disk writes only occur on /reload or logout, so this is the fallback path for data
the live link missed. This parser accepts Lua table literals.
"""
from __future__ import annotations
import re
from pathlib import Path
from .auctionator import ParseError

_NAME_KEY=re.compile(rb'([A-Za-z_][A-Za-z_0-9]*)\s*=')
_NUMBER=re.compile(rb'-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?')

class LuaTableReader:
    def __init__(self, data: bytes):
        self.data=data; self.i=0; self.depth=0
    def skip(self):
        d=self.data; n=len(d)
        while self.i<n:
            if d[self.i] in b' \r\n\t': self.i+=1
            elif d[self.i:self.i+2]==b'--':
                end=d.find(b'\n',self.i)
                self.i=n if end<0 else end+1
            else: return
    def char(self, value):
        self.skip()
        if self.data[self.i:self.i+1]!=value:raise ParseError('Unexpected token at offset '+str(self.i))
        self.i+=1
    def quoted(self):
        self.char(b'"'); buf=bytearray(); d=self.data
        escapes={ord('n'):10,ord('r'):13,ord('t'):9,ord('a'):7,ord('b'):8,ord('f'):12,ord('v'):11}
        while self.i<len(d):
            # Long plain runs copy in one go: market snapshots are megabyte strings.
            quote=d.find(b'"',self.i)
            if quote<0:break
            slash=d.find(b'\\',self.i,quote)
            stop=slash if slash>=0 else quote
            buf+=d[self.i:stop];self.i=stop
            c=d[self.i];self.i+=1
            if c==34:return buf.decode('utf-8','replace')
            if self.i>=len(d):break
            c=d[self.i];self.i+=1
            if 48<=c<=57:
                digits=[c]
                while self.i<len(d) and len(digits)<3 and 48<=d[self.i]<=57:
                    digits.append(d[self.i]);self.i+=1
                v=int(bytes(digits));
                if v>255:raise ParseError('Invalid Lua byte escape')
                buf.append(v)
            else:buf.append(escapes.get(c,c))
        raise ParseError('Unterminated Lua string')
    def value(self):
        self.skip(); d=self.data
        if self.depth>25:raise ParseError('Lua nesting too deep')
        if d[self.i:self.i+1]==b'{':
            self.i+=1;self.depth+=1; result={}; auto=1
            while True:
                self.skip()
                if self.i>=len(d):raise ParseError('Unterminated Lua table')
                if d[self.i:self.i+1]==b'}':self.i+=1;break
                if d[self.i:self.i+1]==b'[':
                    self.i+=1; key=self.value();self.char(b']');self.char(b'=');value=self.value()
                else:
                    m=_NAME_KEY.match(d,self.i)
                    if m:
                        key=m.group(1).decode();self.i=m.end();value=self.value()
                    else:
                        key=auto;auto+=1;value=self.value()
                result[key]=value
                self.skip()
                if d[self.i:self.i+1] in (b',',b';'):self.i+=1
                elif d[self.i:self.i+1]!=b'}':raise ParseError('Expected table delimiter at '+str(self.i))
            self.depth-=1;return result
        if d[self.i:self.i+1]==b'"':return self.quoted()
        m=_NUMBER.match(d,self.i)
        if m:
            self.i=m.end();s=m.group().decode();return float(s) if ('.' in s or 'e' in s or 'E' in s) else int(s)
        for lit,val in ((b'true',True),(b'false',False),(b'nil',None)):
            if d[self.i:self.i+len(lit)]==lit:
                self.i+=len(lit);return val
        raise ParseError('Unsupported Lua literal at '+str(self.i))

def parse_bridge(path: Path) -> dict:
    data=Path(path).read_bytes()
    if len(data)>48*1024*1024: raise ParseError('Bridge SavedVariables file is unexpectedly large')
    m=re.search(rb'\bForeverTraderBridgeDB\s*=\s*',data)
    if not m:raise ParseError('ForeverTraderBridgeDB not found')
    reader=LuaTableReader(data);reader.i=m.end();table=reader.value()
    if not isinstance(table,dict) or table.get('version')!=1:raise ParseError('Unknown Bridge data version')
    books={}
    for key,raw in table.get('books',{}).items():
        if not isinstance(raw,dict):continue
        try:
            item_id=int(raw.get('item_id'))
            observed=int(raw.get('ts'))
            if not 1<=item_id<=10_000_000 or observed<=0:continue
            kind=raw.get('kind','')
            if kind not in ('commodity','item'):continue
            name=str(raw.get('name') or f'Item #{item_id}')[:160]
            levels=[]
            src=raw.get('levels',{})
            if not isinstance(src,dict):continue
            for _,val in sorted(src.items(),key=lambda kv:int(kv[0]) if str(kv[0]).isdigit() else 999999):
                if not isinstance(val,dict):continue
                p=int(val.get('p',0));q=int(val.get('q',0))
                if 0<p<=10**9 and 0<q<=10**7:levels.append((p,q))
            if not levels:continue
            group={}
            for p,q in levels:group[p]=group.get(p,0)+q
            normalized=sorted(group.items())
            books[str(key)]={'key':str(key),'item_id':str(item_id),'name':name,'kind':kind,
                'observed_at':observed,'full':bool(raw.get('full',False)),
                'levels':normalized,'vendor_copper':max(0,int(raw.get('vendor',0) or 0)),
                'total_quantity':max(0,int(raw.get('total',0) or 0))}
        except (ValueError,TypeError,OverflowError):continue
    activity=[]
    for _, raw in sorted((table.get('activity') or {}).items(),
                         key=lambda pair: int(pair[0]) if str(pair[0]).isdigit() else 999999):
        if not isinstance(raw,dict):continue
        try:
            uid=str(raw.get('uid',''))
            character=str(raw.get('character',''))[:120]
            kind=str(raw.get('kind',''))
            status=str(raw.get('status','review'))
            if not uid.isdigit() or int(uid)<=0 or not character:continue
            if kind not in ('AH_BUY','VENDOR_SELL','VENDOR_MONEY'):continue
            if status not in ('confirmed','review'):continue
            ts=int(raw.get('ts',0));item_id=int(raw.get('item_id',0))
            qty=int(raw.get('qty',0));total=int(raw.get('total',0))
            if not 0<ts<4102444800 or not 0<=item_id<=10_000_000:continue
            if not 0<=qty<=100000 or not 0<=total<=10_000_000_000:continue
            if status=='confirmed' and (item_id<=0 or qty<=0 or total<=0):continue
            activity.append(dict(uid=uid,character=character,kind=kind,status=status,
                                 item_id=str(item_id),qty=qty,total=total,ts=ts,
                                 note=str(raw.get('note',''))[:300]))
        except (TypeError,ValueError,OverflowError):continue
    wallets=[]
    for _,raw in (table.get('wallets') or {}).items():
        if not isinstance(raw,dict):continue
        try:
            character=str(raw.get('character',''))[:120]
            copper=int(raw.get('copper',-1));ts=int(raw.get('ts',0))
            if character and 0<=copper<=10**12 and 0<ts<4102444800:
                wallets.append(dict(character=character,copper=copper,ts=ts))
        except (TypeError,ValueError,OverflowError):continue
    changes=[]
    for _,raw in sorted((table.get('wallet_changes') or {}).items(),
                        key=lambda pair: int(pair[0]) if str(pair[0]).isdigit() else 999999):
        if not isinstance(raw,dict):continue
        try:
            uid=str(raw.get('uid',''))
            character=str(raw.get('character',''))[:120]
            ts=int(raw.get('ts',0));copper=int(raw.get('copper',-1))
            delta=int(raw.get('delta',0));context=str(raw.get('context','WORLD'))[:32]
            if not uid.isdigit() or int(uid)<=0 or not character:continue
            if not 0<ts<4102444800 or not 0<=copper<=10**12:continue
            if not -(10**12)<=delta<=10**12:continue
            if context not in ('AH','MERCHANT','WORLD'):context='WORLD'
            changes.append(dict(uid=uid,character=character,ts=ts,
                                copper=copper,delta=delta,context=context))
        except (TypeError,ValueError,OverflowError):continue
    # Completed Sniper attempts are immutable and re-import safely on /reload.
    from . import performance
    snipe_metrics=[]
    for _,raw in sorted((table.get('snipe_metrics') or {}).items(),
                        key=lambda pair: int(pair[0]) if str(pair[0]).isdigit() else 999999):
        if not isinstance(raw,dict):continue
        try:snipe_metrics.append(performance.normalise(raw))
        except (ValueError,TypeError,OverflowError):continue
    bars=[]
    for _,raw in (table.get('bars') or {}).items():
        if isinstance(raw,dict) and isinstance(raw.get('t'),int) and isinstance(raw.get('d'),str):
            bars.append((raw['t'],raw['d']))
    snapshots=[]
    for _,raw in (table.get('snapshots') or {}).items():
        if isinstance(raw,dict) and isinstance(raw.get('ts'),int) and isinstance(raw.get('data'),str):
            snapshots.append((raw['ts'],raw['data']))
    return {'books':books,'count':len(books),'market':str(table.get('market') or 'PvP'),
            'activity':activity,'wallets':wallets,'wallet_changes':changes,'snipe_metrics':snipe_metrics,'bars':bars,'snapshots':snapshots,
            'log':[str(v) for _,v in sorted((table.get('log') or {}).items()) if isinstance(v,str)]}


def parse_live_receipt(line: str) -> dict:
    """One previously confirmed in-game trade receipt delivered over the link.

    Do not accept REVIEW messages here; ambiguous evidence is only imported
    from SavedVariables and requires user review.
    """
    if not isinstance(line,str) or not line.startswith('FTREC1|'):
        raise ValueError('Not a live trade receipt')
    parts=line.rstrip('\r\n').split('|')
    if len(parts)!=9:raise ValueError('Incomplete live trade receipt')
    _,uid,ts,char,kind,item_id,qty,total,status=parts
    if not uid.isdigit() or int(uid)<=0 or len(uid)>32:raise ValueError('Invalid event UID')
    if not char or len(char)>120 or '\n' in char:raise ValueError('Invalid character')
    if kind not in ('AH_BUY','VENDOR_SELL') or status!='confirmed':
        raise ValueError('Unverified receipt cannot become an automatic trade')
    ts=int(ts);item_id=int(item_id);qty=int(qty);total=int(total)
    if not 1600000000<ts<4102444800 or not 0<item_id<=10_000_000 or not 0<qty<=100000 or not 0<total<=10**10:
        raise ValueError('Invalid trade receipt')
    if kind=='VENDOR_SELL' and total%qty:
        raise ValueError('Vendor receipt payout must divide evenly')
    return {'uid':uid,'ts':ts,'character':char,'kind':kind,'item_id':str(item_id),
            'qty':qty,'total':total,'status':'confirmed',
            'note':'Live-confirmed by in-game purchase/merchant evidence'}
