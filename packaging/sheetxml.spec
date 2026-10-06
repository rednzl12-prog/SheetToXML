# PyInstaller spec (run through packaging/build.py): one onedir folder with two exes sharing _internal/
#   SheetXML.exe      windowed: double-click starts the local server + browser
#   SheetXML-cli.exe  console: command line / drag-and-drop conversion
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
DEV = {"launcher.cs"}
mods = [p.stem for p in ROOT.glob("*.py") if p.stem != "app" and not p.stem.startswith(("test_", "bench"))]
datas = [(str(ROOT / "static"), "static"), (str(ROOT / "schemas"), "schemas")]
datas += [(str(p), "resources") for p in (ROOT / "resources").iterdir() if p.is_file() and p.name not in DEV]

a = Analysis([str(ROOT / "app.py")], pathex=[str(ROOT)], datas=datas,
             hiddenimports=mods + collect_submodules("google.genai", filter=lambda n: ".tests" not in n),
             excludes=["tkinter", "bench", "mcp", "setuptools", "pkg_resources", "IPython"])  # genai: optional MCP/notebook extras
pyz = PYZ(a.pure)
icon = str(ROOT / "resources" / "icon.ico")
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="SheetXML", console=False, icon=icon, upx=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="SheetXML-cli", console=True, icon=icon, upx=False)
COLLECT(gui, cli, a.binaries, a.datas, name="SheetXML", upx=False)
