"""Decode Auctionator's SavedVariables price database (CBOR in a Lua string).

This is not a listing-level order book: it contains per-item minima, availability
summaries, and daily aggregates. Prices are in copper, not gold.
"""
from __future__ import annotations
from pathlib import Path
import re
import struct
from datetime import datetime, timezone

class ParseError(Exception):
    pass


def _lua_bytes(source: bytes) -> bytes:
    out = bytearray(); i = 0
    escapes = {ord('a'): 7, ord('b'): 8, ord('f'): 12, ord('n'): 10,
               ord('r'): 13, ord('t'): 9, ord('v'): 11}
    while i < len(source):
        c = source[i]; i += 1
        if c != 92:
            out.append(c); continue
        if i >= len(source): raise ParseError('Unfinished Lua escape')
        c = source[i]; i += 1
        if 48 <= c <= 57:
            digits = [c]
            while i < len(source) and len(digits) < 3 and 48 <= source[i] <= 57:
                digits.append(source[i]); i += 1
            value = int(bytes(digits), 10)
            if value > 255: raise ParseError('Invalid byte escape')
            out.append(value)
        elif c in (10, 13):
            if c == 13 and i < len(source) and source[i] == 10: i += 1
            elif c == 10 and i < len(source) and source[i] == 13: i += 1
        elif c == ord('z'):
            while i < len(source) and chr(source[i]).isspace(): i += 1
        else: out.append(escapes.get(c, c))
    return bytes(out)


class _Cbor:
    def __init__(self, data: bytes): self.data, self.pos = data, 0
    def read(self, size):
        if size > 100_000_000 or self.pos + size > len(self.data):
            raise ParseError('Incomplete/corrupt CBOR database')
        out = self.data[self.pos:self.pos+size]; self.pos += size; return out
    def argument(self, x):
        if x < 24: return x
        if x in (24, 25, 26, 27):
            return int.from_bytes(self.read({24:1,25:2,26:4,27:8}[x]), 'big')
        raise ParseError('Unsupported CBOR length '+str(x))
    def obj(self, depth=0):
        if depth > 20: raise ParseError('CBOR nesting too deep')
        b = self.read(1)[0]; kind, info = b>>5, b&31
        if kind == 7:
            if info == 20: return False
            if info == 21: return True
            if info in (22,23): return None
            if info == 25: return struct.unpack('>e',self.read(2))[0]
            if info == 26: return struct.unpack('>f',self.read(4))[0]
            if info == 27: return struct.unpack('>d',self.read(8))[0]
            raise ParseError('Unsupported CBOR special')
        n = self.argument(info)
        if kind == 0: return n
        if kind == 1: return -1-n
        if kind == 2: return self.read(n)
        if kind == 3: return self.read(n).decode('utf-8', 'replace')
        if kind == 4: return [self.obj(depth+1) for _ in range(n)]
        if kind == 5: return {self.obj(depth+1):self.obj(depth+1) for _ in range(n)}
        if kind == 6: return self.obj(depth+1)
        raise ParseError('Unsupported CBOR type '+str(kind))


def _text(v): return v.decode('utf-8', 'replace') if isinstance(v,bytes) else str(v)

def _map(v):
    if not isinstance(v,dict): return {}
    return {_text(k):val for k,val in v.items()}

def _market_string(data: bytes):
    loc = re.search(rb'AUCTIONATOR_PRICE_DATABASE\s*=\s*\{',data)
    if not loc: raise ParseError('AUCTIONATOR_PRICE_DATABASE not found')
    section = data[loc.end():]
    match = re.search(rb'\["([^"\r\n]+)"\]\s*=\s*"',section)
    if not match: raise ParseError('Market database is empty; perform a scan and /reload')
    realm = match.group(1).decode('utf-8','replace'); pos = match.end(); start = pos
    while pos < len(section):
        if section[pos] == 92:
            pos += 2; continue
        if section[pos] == 34: return realm,_lua_bytes(section[start:pos])
        pos += 1
    raise ParseError('Unterminated market price string')


def parse_auctionator(path: Path) -> dict:
    data = Path(path).read_bytes()
    market, payload = _market_string(data)
    decoder = _Cbor(payload)
    decoded = decoder.obj()
    if decoder.pos != len(payload): raise ParseError('Trailing bytes in market data')
    if not isinstance(decoded,dict): raise ParseError('Unexpected market data shape')
    tm = re.search(rb'\["TimeOfLastBrowseScan"\]\s*=\s*(\d+)',data)
    ts = int(tm.group(1)) if tm else int(Path(path).stat().st_mtime)
    items = {}
    for raw_id, raw_values in decoded.items():
        item_id = _text(raw_id)
        if item_id in ('version','__dbversion'): continue
        values = _map(raw_values)
        current = values.get('m')
        if not isinstance(current,(int,float)) or current < 1: continue
        days_high = _map(values.get('h'))
        days_low = _map(values.get('l'))
        days_available = _map(values.get('a'))
        try: available = max([int(v) for v in days_available.values()] or [0])
        except Exception: available = 0
        items[item_id] = {
            'id':item_id,'price':round(current),'quantity':available,
            'daily_high':{str(k):int(v) for k,v in days_high.items() if isinstance(v,(int,float))},
            'daily_low':{str(k):int(v) for k,v in days_low.items() if isinstance(v,(int,float))},
            'daily_quantity':{str(k):int(v) for k,v in days_available.items() if isinstance(v,(int,float))},
        }
    return {'market':market,'scan_time':ts,'scanned_at':datetime.fromtimestamp(ts,timezone.utc).isoformat(),
            'items':items, 'count':len(items),'format':'Auctionator CBOR dbversion 8'}


def _brace_section(data: bytes, label: bytes) -> bytes:
    m = re.search(rb'\["'+re.escape(label)+rb'"\]\s*=\s*\{',data)
    if not m: return b''
    depth=1; i=m.end(); start=i; quoted=False; escaped=False
    while i<len(data):
        c=data[i];i+=1
        if quoted:
            if escaped: escaped=False
            elif c==92: escaped=True
            elif c==34: quoted=False
        elif c==34: quoted=True
        elif c==123: depth+=1
        elif c==125:
            depth-=1
            if depth==0: return data[start:i-1]
    return b''


def parse_aux_names(path: Path) -> dict:
    if not path or not Path(path).exists(): return {}
    section=_brace_section(Path(path).read_bytes(),b'item_ids')
    names={}
    for m in re.finditer(rb'\["((?:\\.|[^"\\])+)"\]\s*=\s*(\d+)',section):
        try: names[str(int(m.group(2)))] = _lua_bytes(m.group(1)).decode('utf-8','replace').title()
        except (ValueError,UnicodeError): pass
    return names


def fmt_copper(value: int) -> str:
    sign='-' if value<0 else ''
    value=abs(round(value)); g,rem=divmod(value,10000); s,c=divmod(rem,100)
    return f'{sign}{g}g {s:02d}s {c:02d}c' if g else (f'{sign}{s}s {c:02d}c' if s else f'{sign}{c}c')
