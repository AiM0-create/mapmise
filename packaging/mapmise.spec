# PyInstaller build of the desktop app: one folder with two programs sharing one Python + GDAL:
#   Mapmise      — double-click app (no console): starts the local app in the browser
#   mapmise-cli  — the same commands as `mapmise` for the terminal (also used by the release self-test)
# Build: pyinstaller packaging/mapmise.spec   (from the repository root)
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
ICON = {"win32": "packaging/icon.ico", "darwin": "packaging/icon.icns"}.get(sys.platform, "packaging/icon.png")
ICON = str(ROOT / ICON) if (ROOT / ICON).exists() else str(ROOT / "packaging/icon.png")  # PyInstaller converts PNG with Pillow

datas, binaries, hidden = [], [], []
for pkg in ("rasterio", "pyproj", "pyogrio", "shapely", "onnxruntime", "tokenizers"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hidden += h
datas += collect_data_files("mapmise")  # registry YAML, GUI files, the AI model
datas += collect_data_files("certifi")
datas += copy_metadata("mapmise")  # so mapmise.__version__ is right
hidden += collect_submodules("mapmise")

common = dict(pathex=[str(ROOT)], binaries=binaries, datas=datas, hiddenimports=hidden,
              excludes=["tkinter", "matplotlib", "IPython", "pytest", "PIL"], noarchive=False)
app_a = Analysis([str(ROOT / "packaging/app_launcher.py")], **common)
cli_a = Analysis([str(ROOT / "packaging/cli_launcher.py")], **common)

opts = [("X utf8", None, "OPTION")]  # UTF-8 everywhere, whatever the Windows locale
app_exe = EXE(PYZ(app_a.pure), app_a.scripts, opts, exclude_binaries=True, name="Mapmise", icon=ICON,
              console=False, upx=False)
cli_exe = EXE(PYZ(cli_a.pure), cli_a.scripts, opts, exclude_binaries=True, name="mapmise-cli", icon=ICON,
              console=True, upx=False)
coll = COLLECT(app_exe, app_a.binaries, app_a.datas, cli_exe, cli_a.binaries, cli_a.datas, name="Mapmise", upx=False)

if sys.platform == "darwin":
    app = BUNDLE(coll, name="Mapmise.app", icon=ICON, bundle_identifier="io.github.mapmise",
                 info_plist={"CFBundleShortVersionString": __import__("mapmise").__version__, "LSMinimumSystemVersion": "11.0",
                             "NSHighResolutionCapable": True})
