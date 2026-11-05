"""Opt-in GitHub Releases updater for PyInstaller Windows builds."""
from __future__ import annotations
import hashlib, json, os, re, subprocess, sys, tempfile, urllib.request, webbrowser, zipfile
from pathlib import Path
from . import VERSION

AGENT='ForeverTrader/'+VERSION

def get_latest(repo):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo):
        raise ValueError('Release repository must be owner/repository')
    url=f'https://api.github.com/repos/{repo}/releases/latest'
    request=urllib.request.Request(url,headers={'Accept':'application/vnd.github+json','User-Agent':AGENT})
    with urllib.request.urlopen(request,timeout=8) as response: info=json.load(response)
    if info.get('draft') or info.get('prerelease'): raise ValueError('No stable release')
    tag=info.get('tag_name','').lstrip('v')
    def vers(s):
        values=re.findall(r'\d+',s)
        return tuple(int(x) for x in (values[:3]+['0','0','0'])[:3])
    return {'new':vers(tag)>vers(VERSION),'version':tag,
            'release_url':info.get('html_url',''),'assets':info.get('assets',[])}

def _fetch_verified_assets(info, filename, size_limit):
    assets={a.get('name'):a.get('browser_download_url') for a in info['assets']}
    url=assets.get(filename);check=assets.get(filename+'.sha256')
    if not url or not check:raise ValueError('Release lacks '+filename+' or its checksum')
    for u in (url,check):
        if not u.startswith('https://github.com/') or '/releases/download/' not in u:
            raise ValueError('Unexpected update download host')
    def fetch(u,limit):
        req=urllib.request.Request(u,headers={'User-Agent':AGENT})
        with urllib.request.urlopen(req,timeout=60) as r:
            data=r.read(limit+1)
            if len(data)>limit:raise ValueError('Unexpectedly large update asset')
            return data
    expected=fetch(check,2048).decode('utf-8').strip().split()[0].lower()
    if not re.fullmatch('[a-f0-9]{64}',expected):raise ValueError('Bad checksum file')
    data=fetch(url,size_limit)
    if hashlib.sha256(data).hexdigest()!=expected:raise ValueError('SHA256 mismatch')
    return data

def download_source(info):
    """Stage a checked release archive for a running Python-source install."""
    from .source_update_helper import validate_archive
    data=_fetch_verified_assets(info,'ForeverTrader-source.zip',40*1024*1024)
    fd,filename=tempfile.mkstemp(prefix='forever_source_',suffix='.zip')
    with os.fdopen(fd,'wb') as f:f.write(data)
    try:validate_archive(filename)
    except Exception:
        os.unlink(filename);raise
    return Path(filename)

def install_source_on_exit(archive,source_dir):
    """Start a separate helper that replaces source only AFTER the app exits."""
    from .source_update_helper import validate_archive
    validate_archive(archive)
    helper=Path(__file__).with_name('source_update_helper.py')
    fd,tmp=tempfile.mkstemp(prefix='forever_update_helper_',suffix='.py')
    with os.fdopen(fd,'wb') as f:f.write(helper.read_bytes())
    subprocess.Popen([sys.executable,tmp,str(archive),str(source_dir),str(os.getpid())],
                     creationflags=0x08000000 if os.name=='nt' else 0)

def download_update(info):
    """Downloads but does not install; must be triggered by explicit user action."""
    if not getattr(sys,'frozen',False):
        raise ValueError('Use the GitHub release to update source-code builds')
    data=_fetch_verified_assets(info,'ForeverTrader.exe',200*1024*1024)
    target=Path(sys.executable)
    stage=target.with_name(target.stem+'.updating.exe')
    stage.write_bytes(data)
    return stage

def install_on_exit(stage):
    """Write local helper that waits for process exit, replaces executable, and relaunches."""
    target=Path(sys.executable)
    if not getattr(sys,'frozen',False):raise ValueError('Not running packaged binary')
    fd,filename=tempfile.mkstemp(prefix='forever_update_',suffix='.cmd');os.close(fd)
    script=f'''@echo off
setlocal
set "SRC={stage}"
set "DEST={target}"
set /a COUNT=0
:again
timeout /t 2 /nobreak >nul
move /Y "%SRC%" "%DEST%" >nul 2>nul
if not errorlevel 1 goto done
set /a COUNT+=1
if %COUNT% GEQ 30 exit /b 1
goto again
:done
start "" "%DEST%"
del "%~f0"
'''
    Path(filename).write_text(script,encoding='utf-8')
    subprocess.Popen(['cmd.exe','/c',filename],creationflags=0x08000000)
