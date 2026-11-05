"""Keeps an installed copy of the bundled Bridge addon up to date.

Only ever writes inside this app's own addon folders; other addons and the WoW
SavedVariables database are left alone.
"""
from __future__ import annotations
import hashlib, pathlib, shutil, sys

def packaged_addon():
    root=pathlib.Path(getattr(sys,'_MEIPASS',pathlib.Path(__file__).resolve().parents[1]))
    addon=root/'wow_addon'/'ForeverTraderBridge'
    return addon if (addon/'ForeverTraderBridge.toc').is_file() else None

def game_root(auctionator_path):
    """.../_forever_/WTF/Account/<account>/SavedVariables/Auctionator.lua -> .../_forever_"""
    if not auctionator_path:return None
    path=pathlib.Path(auctionator_path).resolve()
    if path.name.lower()!='auctionator.lua' or len(path.parents)<5:
        return None
    root=path.parents[4]
    return root if root.name.lower()=='_forever_' else None

def addons_root(auctionator_path):
    root=game_root(auctionator_path)
    return root/'Interface'/'AddOns' if root else None

def installed_addon(auctionator_path):
    root=addons_root(auctionator_path)
    return root/'ForeverTraderBridge' if root else None

def sync_installed_bridge(auctionator_path):
    """Only update an already installed Bridge; never install one unasked."""
    src=packaged_addon();dest=installed_addon(auctionator_path)
    if not src or not dest or not (dest/'ForeverTraderBridge.toc').is_file():
        return 'Bridge not previously installed; use INSTALL_ADDON_WINDOWS.cmd once'
    changed=0
    for incoming in sorted(p for p in src.iterdir() if p.is_file()):
        target=dest/incoming.name
        raw=incoming.read_bytes()
        if target.exists() and hashlib.sha256(target.read_bytes()).digest()==hashlib.sha256(raw).digest():
            continue
        shutil.copyfile(incoming,target)
        changed+=1
    return f'Updated {changed} Bridge file(s); restart WoW to use them'
