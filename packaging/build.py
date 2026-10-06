"""Release build: python -B packaging/build.py [--installer-only]

1. PyInstaller (packaging/sheetxml.spec) -> dist/SheetXML/ (SheetXML.exe windowed, SheetXML-cli.exe console)
2. smoke test: CLI on resources/Pachelbel_Canon.pdf -> valid MusicXML; server answers /api/config
3. csc (ships with Windows) -> uninstall.exe, then dist/SheetXML-Setup-<VERSION>.exe embedding
   app.zip + packaging/vendor/<Audiveris MSI> + license.txt
--installer-only reuses an existing dist/SheetXML (skips steps 1-2).
Smoke-test files go to the system temp folder (set TMP to redirect).
"""

import importlib.metadata as md
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "packaging"
BUILD, DIST = ROOT / "build", ROOT / "dist"
APP = DIST / "SheetXML"
MSI = PKG / "vendor" / "Audiveris-5.11.0-windows-x86_64.msi"
MSI_SIZE = 85062430
MSI_URL = "https://github.com/Audiveris/audiveris/releases/tag/5.11.0"
CSC = Path(os.environ.get("WINDIR", r"C:\Windows")) / r"Microsoft.NET\Framework64\v4.0.30319\csc.exe"


def run(cmd, **kw):
    print(">", " ".join(map(str, cmd)))
    subprocess.run(cmd, check=True, **kw)


def mb(p: Path) -> str:
    n = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size
    return f"{n / 1e6:.1f} MB"


def pyinstaller():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        run([sys.executable, "-m", "pip", "install", "pyinstaller"])
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", DIST,
         "--workpath", BUILD / "pyinstaller", PKG / "sheetxml.spec"])


def smoke_test():
    """Runs the frozen exes with SHEETXML_DATA_DIR in a temp dir; removes anything they wrote into dist."""
    from lxml import etree
    before = {p for p in APP.rglob("*")}
    tmp = Path(tempfile.mkdtemp(prefix="sheetxml-smoke-"))
    env = dict(os.environ, SHEETXML_DATA_DIR=str(tmp / "data"), PYTHONIOENCODING="utf-8")
    out = tmp / "canon.musicxml"
    try:
        run([APP / "SheetXML-cli.exe", ROOT / "resources" / "Pachelbel_Canon.pdf", "-o", out], env=env, timeout=300)
        root = etree.parse(str(out)).getroot()
        bars = len(root.findall("part/measure"))
        assert root.tag == "score-partwise" and bars > 50, (root.tag, bars)
        print(f"smoke: CLI ok, {bars} measures")
        with socket.socket() as s:  # a free port, so a running SheetXML cannot answer for the frozen one
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        srv =subprocess.Popen([APP / "SheetXML.exe", "--port", str(port), "--no-browser"], env=env)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            for _ in range(120):
                try:
                    body = opener.open(f"http://127.0.0.1:{port}/api/config", timeout=2).read()
                    print(f"smoke: server ok, /api/config {len(body)} bytes")
                    break
                except OSError:
                    assert srv.poll() is None, f"server exited with {srv.returncode}"
                    time.sleep(0.5)
            else:
                raise AssertionError("server did not answer in 60 s")
        finally:
            srv.kill()
            srv.wait()
        data = tmp / "data"
        print("smoke: data dir", "used" if data.exists() and any(data.iterdir()) else
              "NOT used (app.py does not honour SHEETXML_DATA_DIR yet)")
    finally:
        stray = sorted({p for p in APP.rglob("*")} - before, key=lambda p: -len(p.parts))
        for p in stray:  # e.g. logs/pid written next to the code when DATA_DIR is not implemented
            print("smoke: removing stray", p.relative_to(APP))
            shutil.rmtree(p) if p.is_dir() else p.unlink()
        shutil.rmtree(tmp, ignore_errors=True)


def notices() -> str:
    """Hand-written notices + the distribution and declared license of every module PyInstaller bundled."""
    toc = (BUILD / "pyinstaller" / "sheetxml" / "PYZ-00.toc").read_text(encoding="utf-8")
    mods = set(re.findall(r"\('([\w.]+)',\s*'[^']*',\s*'PYMODULE'\)", toc))
    owners = md.packages_distributions()
    dists = set()
    for top in {m.split(".")[0] for m in mods}:
        cands = owners.get(top, [])
        if len(cands) > 1:  # namespace package (google.*): keep the distributions that ship a bundled module
            paths = {m.replace(".", "/") + s for m in mods for s in (".py", "/__init__.py")}
            cands = [c for c in cands if paths & {f.as_posix() for f in md.distribution(c).files or []}]
        dists.update(cands)
    lines = []
    for name in dists:
        d = md.distribution(name)
        m = d.metadata
        lic = m.get("License-Expression") or ""
        if not lic and (m.get("License") or "") and len(m["License"]) < 60 and "\n" not in m["License"]:
            lic = m["License"]
        lic = lic or ", ".join(c.split("::")[-1].strip() for c in m.get_all("Classifier") or [] if c.startswith("License ::"))
        lines.append(f"  {m['Name']} {d.version}: {lic or 'see package'}")
    text = (PKG / "THIRD-PARTY-NOTICES.txt").read_text(encoding="utf-8")
    return text + "\n".join(sorted(lines, key=str.lower)) + "\n"


def csc(out: Path, resources=()):
    if not CSC.is_file():
        sys.exit(f"C# compiler not found: {CSC}")
    info = BUILD / "assemblyinfo.cs"
    info.write_text(
        "using System.Reflection;\n"
        '[assembly: AssemblyTitle("SheetXML Setup")]\n[assembly: AssemblyProduct("SheetXML")]\n'
        '[assembly: AssemblyCompany("rednzl12-prog")]\n'
        f'[assembly: AssemblyVersion("{VERSION}.0")]\n[assembly: AssemblyFileVersion("{VERSION}.0")]\n'
        f'[assembly: AssemblyInformationalVersion("{VERSION}")]\n', encoding="utf-8")
    run([CSC, "/nologo", "/target:winexe", "/optimize+", f"/out:{out}", f"/win32icon:{ROOT / 'resources' / 'icon.ico'}",
         "/r:System.IO.Compression.dll", "/r:Microsoft.CSharp.dll",
         *[f"/resource:{path},{name}" for path, name in resources], PKG / "installer.cs", info])


def main():
    BUILD.mkdir(exist_ok=True)
    if "--installer-only" not in sys.argv:
        pyinstaller()
        smoke_test()
    if not (APP / "SheetXML.exe").is_file():
        sys.exit(f"{APP} missing: run without --installer-only first")
    if not MSI.is_file() or MSI.stat().st_size != MSI_SIZE:
        sys.exit(f"{MSI} missing or wrong size (want {MSI_SIZE} bytes); download it from {MSI_URL}")

    csc(APP / "uninstall.exe")
    shutil.copy(ROOT / "LICENSE", APP / "LICENSE.txt")
    lic = notices()
    (APP / "THIRD-PARTY-NOTICES.txt").write_text(lic, encoding="utf-8")
    (BUILD / "license.txt").write_text((ROOT / "LICENSE").read_text(encoding="utf-8") + "\n\n" + lic, encoding="utf-8")

    payload = BUILD / "app.zip"
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(APP.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(APP).as_posix())
    setup = DIST / f"SheetXML-Setup-{VERSION}.exe"
    csc(setup, [(payload, "app.zip"), (MSI, MSI.name), (BUILD / "license.txt", "license.txt")])

    print(f"\nprogram folder  {APP}  {mb(APP)}")
    print(f"app.zip         {mb(payload)}")
    print(f"Audiveris MSI   {mb(MSI)}")
    print(f"installer       {setup}  {mb(setup)}")


if __name__ == "__main__":
    main()
