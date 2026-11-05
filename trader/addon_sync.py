"""Opt-in copy of the bundled Bridge to an already installed WoW Forever addon.

The app never writes to other addons or the WoW SavedVariables database.
"""
from __future__ import annotations
import hashlib, pathlib, shutil, sys

def packaged_addon():
    root=pathlib.Path(getattr(sys,'_MEIPASS',pathlib.Path(__file__).resolve().parents[1]))
    addon=root/'wow_addon'/'ForeverTraderBridge'
    return addon if (addon/'ForeverTraderBridge.toc').is_file() else None

def installed_addon(auctionator_path):
    if not auctionator_path:return None
    path=pathlib.Path(auctionator_path).resolve()
    # .../_forever_/WTF/Account/<account>/SavedVariables/Auctionator.lua
    if path.name.lower()!='auctionator.lua' or len(path.parents)<5:
        return None
    root=path.parents[4]
    if root.name.lower()!='_forever_':return None
    return root/'Interface'/'AddOns'/'ForeverTraderBridge'

def sync_installed_bridge(auctionator_path):
    """Only update an already installed Bridge. Never create new addon folders."""
    src=packaged_addon();dest=installed_addon(auctionator_path)
    if not src or not dest or not (dest/'ForeverTraderBridge.toc').is_file():
        return 'Bridge not previously installed; use INSTALL_ADDON_WINDOWS.cmd once'
    changed=0
    for filename in ('ForeverTraderBridge.toc','ForeverTraderBridge.lua'):
        incoming=src/filename;target=dest/filename
        raw=incoming.read_bytes()
        if target.exists() and hashlib.sha256(target.read_bytes()).digest()==hashlib.sha256(raw).digest():
            continue
        # Update only the two named, intended Bridge source files.
        shutil.copyfile(incoming,target)
        changed+=1
    return f'Updated {changed} Bridge file(s); restart or /reload WoW to use them'
