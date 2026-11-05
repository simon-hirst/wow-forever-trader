"""Build the versioned source-release archive used by Python launch auto-update."""
from __future__ import annotations
import argparse, pathlib, zipfile, hashlib
ROOT=pathlib.Path(__file__).resolve().parents[1]
ALLOW_DIRS=('trader','tests','wow_addon','scripts','.github')
# Installed copies check an update against the allowlist they already have, so a new
# top-level path here would make every older copy refuse the release.
ALLOW_FILES=('app.py','README.md','START_WINDOWS.cmd','START_SILENT_WINDOWS.cmd',
             'BUILD_WINDOWS_EXE.cmd','INSTALL_ADDON_WINDOWS.cmd',
             'PUBLISH_RELEASE.ps1','SETUP_GITHUB_WINDOWS.ps1','NEXT_SESSION.md','.gitignore')

def build(out):
    out=pathlib.Path(out)
    files=[ROOT/name for name in ALLOW_FILES if (ROOT/name).is_file()]
    for d in ALLOW_DIRS:
        files.extend(p for p in (ROOT/d).rglob('*') if p.is_file() and
                     '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo'))
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(files):z.write(p,p.relative_to(ROOT).as_posix())
    digest=hashlib.sha256(out.read_bytes()).hexdigest()
    out.with_suffix(out.suffix+'.sha256').write_text(f'{digest}  {out.name}\n')
    return out

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('out',nargs='?',default='ForeverTrader-source.zip')
    args=parser.parse_args();print(build(ROOT/args.out))
