"""Runs addon Lua inside a simulated client (tests/wow_stub.lua). Needs `pip install lupa`.

The integration tests also load the player's installed auxForever when it is on this
machine, so the Confirm button is tested against the real buy bar and Sniper code.
aux itself is third-party, so it is never copied into this repository.
"""
from __future__ import annotations
import os
import re
from pathlib import Path

try:
    from lupa import lua51
    HAVE_LUA = True
except ImportError:
    lua51 = None
    HAVE_LUA = False

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / 'wow_addon' / 'ForeverTraderBridge'
STUB = Path(__file__).with_name('wow_stub.lua')


def find_aux() -> Path | None:
    candidates = [os.environ.get('FT_AUX_DIR', ''),
                  '/mnt/f/wow/World of Warcraft/_forever_/Interface/AddOns/auxForever',
                  r'F:\wow\World of Warcraft\_forever_\Interface\AddOns\auxForever']
    for candidate in candidates:
        if candidate and (Path(candidate) / 'auxForever.toc').is_file():
            return Path(candidate)
    return None


def toc_files(folder: Path, toc_name: str) -> list[str]:
    out = []
    for line in (folder / toc_name).read_text(encoding='utf-8', errors='replace').splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            out.append(line.replace('\\', '/'))
    return out


class WowSim:
    def __init__(self):
        self.lua = lua51.LuaRuntime(unpack_returned_tuples=True, register_eval=False, encoding='latin-1')
        self.g = self.lua.globals()
        self._load_chunk = self.lua.eval(
            'function(code, name, addon, ns) local f, err = loadstring(code, name); if not f then error(err) end; return f(addon, ns) end')
        self.lua.execute(STUB.read_text(encoding='utf-8'))
        self.bridge_ns = None
        self.aux_loaded = False

    def load_addon(self, folder: Path, toc: str, addon: str):
        ns = self.lua.table()
        for filename in toc_files(folder, toc):
            path = folder / filename
            if path.suffix.lower() != '.lua':
                continue
            self._load_chunk(path.read_text(encoding='utf-8', errors='replace'), '@' + filename, addon, ns)
        return ns

    def load_bridge(self):
        self.bridge_ns = self.load_addon(BRIDGE, 'ForeverTraderBridge.toc', 'ForeverTraderBridge')
        return self.bridge_ns

    def load_aux(self, folder: Path | None = None):
        folder = folder or find_aux()
        self.load_addon(folder, 'auxForever.toc', 'auxForever')
        self.aux_loaded = True

    def set_items(self, items: dict[int, dict]):
        table = self.g['__items']
        for item_id, spec in items.items():
            entry = self.lua.table(id=item_id, **spec)
            table[item_id] = entry

    def set_commodity(self, item_id: int, tiers: list[tuple[int, int]]):
        lua_tiers = self.lua.table()
        for i, (price, qty) in enumerate(tiers, 1):
            lua_tiers[i] = self.lua.table(p=price, q=qty)
        self.g['__ah'].commodity[item_id] = lua_tiers

    def set_gear(self, item_id: int, auctions: list[tuple[int, int]]):
        lua_auctions = self.lua.table()
        for i, (auction_id, buyout) in enumerate(auctions, 1):
            lua_auctions[i] = self.lua.table(id=auction_id, buyout=buyout)
        self.g['__ah'].gear[item_id] = lua_auctions

    def login(self):
        self.g['__fire']('ADDON_LOADED', 'auxForever')
        self.g['__fire']('ADDON_LOADED', 'ForeverTraderBridge')
        self.g['__fire']('PLAYER_LOGIN')
        self.g['__fire']('PLAYER_ENTERING_WORLD')

    def open_auction_house(self):
        self.g['__fire']('AUCTION_HOUSE_SHOW')

    def run(self, seconds: float, dt: float = 0.05):
        for _ in range(max(1, int(seconds / dt))):
            self.g['__frame'](dt)

    def run_until(self, predicate, timeout: float = 30.0, dt: float = 0.05) -> bool:
        elapsed = 0.0
        while elapsed < timeout:
            if predicate():
                return True
            self.g['__frame'](dt)
            elapsed += dt
        return predicate()

    def press(self):
        """One real mouse click on the Confirm button."""
        button = self.g.ForeverTraderConfirmButton
        self.g['__press'](self.lua.eval('function(b) return function() b:Click("LeftButton") end end')(button))

    def wheel(self):
        button = self.g.ForeverTraderConfirmButton
        self.g['__press'](self.lua.eval('function(b) return function() b:GetScript("OnMouseWheel")(b, 1) end end')(button))

    @property
    def phase(self) -> int:
        return self.bridge_ns.Snipe.state.phase

    def phase_is(self, name: str) -> bool:
        return self.phase == self.bridge_ns.Snipe.PHASE[name]

    def ah_log(self) -> list[str]:
        return list(self.g['__ah_log'].values())

    def violations(self) -> list[str]:
        return list(self.g['__violations'].values())

    def bought(self) -> list[dict]:
        rows = self.g['__ah'].bought
        return [dict(row.items()) for row in rows.values()] if rows else []

    def prints(self) -> list[str]:
        return list(self.g['__prints'].values())

    def money(self) -> int:
        return self.g['__money']
