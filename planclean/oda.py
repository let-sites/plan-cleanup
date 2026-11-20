"""DWG <-> DXF through the free ODA File Converter (installed separately)."""
from __future__ import annotations
import glob, os, shutil, subprocess, sys, tempfile
from pathlib import Path

DOWNLOAD_URL = "https://www.opendesign.com/guestfiles/oda_file_converter"

def find_converter(configured=""):
    if configured and Path(configured).exists(): return configured
    cands = []
    if sys.platform.startswith('win'):
        for base in (os.environ.get('ProgramFiles', r'C:\Program Files'), os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')):
            cands += sorted(glob.glob(os.path.join(base, 'ODA', 'ODAFileConverter*', 'ODAFileConverter.exe')), reverse=True)
    elif sys.platform == 'darwin':
        cands += ['/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter']
    else:
        cands += [shutil.which('ODAFileConverter') or '', '/usr/bin/ODAFileConverter']
    for c in cands:
        if c and Path(c).exists(): return c
    return None

def _run(exe, src_dir, out_dir, version, fmt, pattern):
    # ODAFileConverter <in dir> <out dir> <version> <type> <recurse> <audit> [filter]
    args = [exe, str(src_dir), str(out_dir), version, fmt, '0', '1', pattern]
    kw = {}
    if sys.platform.startswith('win'): kw['creationflags'] = 0x08000000  # no console window
    subprocess.run(args, check=True, timeout=300, **kw)

def convert(src, dst, exe, version='ACAD2018'):
    """Convert src (.dwg/.dxf) to dst (.dxf/.dwg)."""
    src, dst = Path(src), Path(dst)
    fmt = 'DWG' if dst.suffix.lower() == '.dwg' else 'DXF'
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        tmp_in = Path(a) / ('in' + src.suffix.lower())
        shutil.copy(src, tmp_in)
        _run(exe, a, b, version, fmt, '*' + src.suffix.lower())
        made = list(Path(b).glob('*.' + fmt.lower()))
        if not made:
            raise RuntimeError('ODA File Converter did not produce a file')
        shutil.copy(made[0], dst)
    return dst

def to_dxf(path, exe):
    """Return a DXF path for any DWG/DXF (converting a DWG into a temp DXF)."""
    p = Path(path)
    if p.suffix.lower() == '.dxf': return str(p)
    if not exe: raise RuntimeError('Reading DWG needs the free ODA File Converter. Install it and set its location in Settings.')
    out = Path(tempfile.mkdtemp()) / (p.stem + '.dxf')
    return str(convert(p, out, exe))
