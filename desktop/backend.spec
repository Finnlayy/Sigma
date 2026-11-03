# Build on Ubuntu 22.04 (glibc baseline), not on the user's machine.
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules, collect_data_files

root = Path(SPECPATH).parent
packages = ['uvicorn', 'websockets', 'netron', 'onnxruntime', 'playwright', 'pyarrow', 'duckdb', 'bs4', 'websocket', 'requests']
datas = [(str(root / name), name) for name in ('config', 'models', 'dist')]
datas += [(str(root / 'vendor/tradingview-scraper'), 'vendor/tradingview-scraper')]
datas += collect_data_files('app', includes=['**/*.json', '**/*.yaml'])
binaries, hiddenimports = [], collect_submodules('app') + collect_submodules('sigma')
for package in packages:
    data, binary, hidden = collect_all(package)
    datas += data
    binaries += binary
    hiddenimports += hidden
# Vendor modules are lazy-imported from the bundled source tree.
a = Analysis([str(root / 'desktop/backend.py')], pathex=[str(root)],
             datas=datas, binaries=binaries, hiddenimports=hiddenimports,
             excludes=['ccxt', 'pytest', 'fakeredis', 'tkinter', 'matplotlib', 'IPython'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='sigma-backend', console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='sigma-backend')
