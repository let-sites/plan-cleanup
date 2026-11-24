# PyInstaller build file: `pyinstaller plancleanup.spec`
from PyInstaller.utils.hooks import collect_all
datas, binaries, hidden = [], [], []
for pkg in ('rapidocr_onnxruntime', 'onnxruntime', 'ezdxf', 'pymupdf'):
    d, b, h = collect_all(pkg); datas += d; binaries += b; hidden += h

datas += [('assets/icon.png', 'assets'), ('assets/chevron.svg', 'assets')]
import sys
ICON = 'assets/icon.icns' if sys.platform == 'darwin' else 'assets/icon.ico'
a = Analysis(['run_plancleanup.py'], pathex=['.'], datas=datas, binaries=binaries, hiddenimports=hidden,
             excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.Qt3DCore', 'tkinter'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='PlanCleanup', console=False, icon=ICON)
coll = COLLECT(exe, a.binaries, a.datas, name='PlanCleanup')
app = BUNDLE(coll, name='PlanCleanup.app', bundle_identifier='com.letdw.plancleanup', icon=ICON)
