"""Small separate process used to install an explicitly approved source update.

This process runs after the desktop app closes; never install updates when an
untrusted archive, an unexpected path, or a missing checksum is detected.
"""
from __future__ import annotations
import os, pathlib, shutil, sys, time, zipfile

ALLOWED_ROOTS={'trader','tests','wow_addon','.github','scripts'}
ALLOWED_FILES={'app.py','README.md','START_WINDOWS.cmd','START_SILENT_WINDOWS.cmd',
               'BUILD_WINDOWS_EXE.cmd','INSTALL_ADDON_WINDOWS.cmd','PUBLISH_RELEASE.ps1',
               'SETUP_GITHUB_WINDOWS.ps1','.gitignore'}

def is_allowed(name):
    p=pathlib.PurePosixPath(name)
    if not name or name.startswith('/') or '\\' in name or '..' in p.parts or ':' in name:
        return False
    return (p.parts[0] in ALLOWED_ROOTS or name in ALLOWED_FILES)

def validate_archive(archive):
    with zipfile.ZipFile(archive) as z:
        entries=z.infolist()
        if len(entries)>1000:raise ValueError('Source update has too many members')
        if sum(x.file_size for x in entries)>100*1024*1024:
            raise ValueError('Source update too large')
        for info in entries:
            if not is_allowed(info.filename):
                raise ValueError('Unexpected source update path: '+info.filename)
            if info.is_dir():continue
            if info.file_size>10*1024*1024:raise ValueError('Oversized source file')
        if z.testzip() is not None:raise ValueError('Corrupted source archive')
    return True

def install(archive, destination):
    validate_archive(archive)
    destination=pathlib.Path(destination).resolve()
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            if info.is_dir():continue
            target=destination.joinpath(*pathlib.PurePosixPath(info.filename).parts)
            target.parent.mkdir(parents=True,exist_ok=True)
            tmp=target.with_name(target.name+'.new')
            with z.open(info) as src,open(tmp,'wb') as out:shutil.copyfileobj(src,out)
            os.replace(tmp,target)
    return destination

if __name__=='__main__':
    # Called by a separate Python process, after the GUI process has exited.
    archive,destination,pid=sys.argv[1],sys.argv[2],int(sys.argv[3])
    if os.name=='nt':
        import ctypes
        kernel=ctypes.windll.kernel32
        handle=kernel.OpenProcess(0x00100000,False,pid) # SYNCHRONIZE
        if handle:
            kernel.WaitForSingleObject(handle,90_000)
            kernel.CloseHandle(handle)
        else:time.sleep(2)
    else:time.sleep(2)
    try:
        install(archive,destination)
        import subprocess
        starter=pathlib.Path(destination)/'START_WINDOWS.cmd'
        if os.name=='nt':subprocess.Popen(['cmd.exe','/c',str(starter)],cwd=str(destination),creationflags=0x08000000)
    except Exception as exc:
        pathlib.Path(destination,'UPDATE_ERROR.txt').write_text(str(exc),encoding='utf-8')
        raise
