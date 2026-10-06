"""Optional offline OMR engine: run an installed Audiveris in batch mode and read its MusicXML export.

Command line per the Audiveris handbook, https://audiveris.github.io/audiveris/_pages/guides/advanced/cli/ :
    audiveris [OPTIONS] [--] [INPUT_FILES]
    audiveris -batch -transcribe -export -sheets 1 4-5 -output /path/to/output input.pdf
'-sheets' takes space-separated ids or X-Y ranges, so '--' must separate it from the input path.
"""

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, List, Optional

from lxml import etree

import abcxml
import llm

INPUT_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif"}
INSTALL_HINT = ("Install Audiveris 5.x from https://github.com/Audiveris/audiveris/releases, "
                "or set the AUDIVERIS environment variable to its Audiveris.exe / Audiveris.bat.")


def find() -> Optional[Path]:
    """The Audiveris launcher: env AUDIVERIS (file or install folder), PATH, then the usual install folders."""
    places: List[Path] = []
    env = os.environ.get("AUDIVERIS", "").strip().strip('"')
    if env:
        places += [Path(env), Path(env) / "Audiveris.exe", Path(env) / "bin" / "Audiveris.bat"]
    places += [Path(p) for p in (shutil.which("Audiveris"), shutil.which("audiveris")) if p]
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                 os.environ.get("LOCALAPPDATA") and os.path.join(os.environ["LOCALAPPDATA"], "Programs")):
        if base:
            places += [Path(base) / "Audiveris" / "Audiveris.exe", Path(base) / "Audiveris" / "bin" / "Audiveris.bat"]
    return next((p for p in places if p.is_file()), None)


def sheets_args(page_range: str) -> List[str]:
    """'1-3, 5' -> ['-sheets', '1-3', '5']; blank -> [] (every sheet)."""
    ids = [re.sub(r"\s+", "", p) for p in (page_range or "").split(",") if re.fullmatch(r"\s*\d+\s*(?:-\s*\d+\s*)?", p)]
    return ["-sheets", *ids] if ids else []


def command(exe: Path, src: Path, out: Path, page_range: str = "") -> List[str]:
    return [str(exe), "-batch", "-transcribe", "-export", "-output", str(out), *sheets_args(page_range), "--", str(src)]


def _kill(proc: subprocess.Popen):
    """Kill the launcher and its Java child (a .bat launcher leaves java.exe running on a plain kill)."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    if proc.poll() is None:
        proc.kill()
    proc.wait()


def _movement(path: Path) -> int:
    m = re.search(r"\.mvt(\d+)\.", path.name)
    return int(m.group(1)) if m else 0


CLEF_NAMES = {("G", "2"): "treble", ("F", "4"): "bass", ("C", "3"): "alto"}


def layout(data: bytes) -> dict:
    """Where Audiveris broke the first part into systems and pages, as bar indices (bar 0 starts both),
    plus its first clef: {"systemStarts": [...], "pageStarts": [...], "clef": "treble" | "bass" | "alto" | ""}."""
    root = etree.fromstring(abcxml._mxl_root(data) if data[:2] == b"PK" else data, abcxml._XML_PARSER)
    part = root.find("part")
    systems, pages = [0], [0]
    for i, m in enumerate(part.findall("measure") if part is not None else []):
        page = m.find("print[@new-page='yes']") is not None
        if i and (page or m.find("print[@new-system='yes']") is not None):
            systems.append(i)
            pages += [i] if page else []
    c = root.find(".//attributes/clef")
    clef = CLEF_NAMES.get(((c.findtext("sign") or "").strip(), (c.findtext("line") or "").strip())) if c is not None else None
    return {"systemStarts": systems, "pageStarts": pages, "clef": clef or ""}


def collect(out: Path) -> abcxml.Score:
    """Parse every exported movement under `out` (book.mxl or book.mvt1.mxl, book.mvt2.mxl, ...) and join them.
    score.info records Audiveris's layout (see layout()); a new movement also starts a system."""
    files = sorted((p for p in out.rglob("*") if p.suffix.lower() in (".mxl", ".musicxml", ".xml") and p.is_file()),
                   key=lambda p: (_movement(p), str(p)))
    if not files:
        raise RuntimeError("Audiveris finished but exported no MusicXML (no staff recognised?).")
    scores, info, n = [], {"systemStarts": [], "pageStarts": [], "clef": ""}, 0
    for p in files:
        data = p.read_bytes()
        scores.append(abcxml.parse_musicxml(data))
        lay = layout(data)
        info["systemStarts"] += [n + i for i in lay["systemStarts"]]
        info["pageStarts"] += [n + i for i in lay["pageStarts"]][bool(n):]     # a movement need not start a page
        info["clef"] = info["clef"] or lay["clef"]
        n += len(scores[-1].bars)
    sc = concat(scores)
    sc.info.update(info)
    return sc


def concat(scores: List[abcxml.Score]) -> abcxml.Score:
    """Append movements in order; a key, meter or tempo that differs from the running one is marked on the joining bar."""
    sc = scores[0]
    key, meter, tempo = sc.key, sc.meter, sc.tempo
    for b in sc.bars:
        key, meter, tempo = b.key or key, b.meter or meter, b.tempo or tempo
    for s in scores[1:]:
        if not s.bars:
            continue
        first = s.bars[0]
        first.key = first.key or (s.key if s.key != key else None)
        first.meter = first.meter or (s.meter if s.meter != meter else None)
        first.tempo = first.tempo or (s.tempo if s.tempo and s.tempo != tempo else None)
        for b in s.bars:
            key, meter, tempo = b.key or key, b.meter or meter, b.tempo or tempo
        sc.bars += s.bars
        sc.warnings += s.warnings
    return sc


def transcribe(data: bytes, filename: str, page_range: str = "",
               progress: Optional[Callable[[str, Optional[float]], None]] = None,
               cancel: Optional[Callable[[], bool]] = None, timeout: float = 900) -> abcxml.Score:
    """PDF / image bytes -> Score via Audiveris. RuntimeError when it is missing or fails; LLMError 'cancelled' on cancel."""
    exe = find()
    if exe is None:
        raise RuntimeError("Audiveris was not found. " + INSTALL_HINT)
    say = progress or (lambda msg, frac: None)
    suffix = Path(filename or "").suffix.lower()
    if suffix not in INPUT_SUFFIXES:
        suffix = ".pdf" if data[:5] == b"%PDF-" else ".png"
    with tempfile.TemporaryDirectory(prefix="sheetxml-audiveris-") as tmp:
        src, out, log = Path(tmp) / ("score" + suffix), Path(tmp) / "out", Path(tmp) / "audiveris.log"
        src.write_bytes(data)
        out.mkdir()
        say("Audiveris: recognising the score (offline, this takes a while)", None)
        with open(log, "wb") as fh:
            proc = subprocess.Popen(command(exe, src, out, page_range), stdout=fh, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, cwd=tmp,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            start = time.monotonic()
            while proc.poll() is None:
                if cancel and cancel():
                    _kill(proc)
                    raise llm.LLMError("Cancelled", "cancelled")
                if time.monotonic() - start > timeout:
                    _kill(proc)
                    raise RuntimeError(f"Audiveris took longer than {timeout:.0f} s and was stopped.")
                time.sleep(0.5)
        tail = log.read_text(encoding="utf-8", errors="replace")[-800:].strip()
        if proc.returncode != 0:
            raise RuntimeError(f"Audiveris failed (exit code {proc.returncode}). Last output:\n{tail}")
        try:
            score = collect(out)
        except RuntimeError as e:
            raise RuntimeError(f"{e} Last output:\n{tail}") from None
    say(f"Audiveris: read {len(score.bars)} bars", 1.0)
    return score


if __name__ == "__main__":
    import io
    import zipfile
    from fractions import Fraction

    exe = Path(r"C:\Audiveris\Audiveris.exe")
    assert command(exe, Path("in.pdf"), Path("out"), "1-3, 5") == \
        [str(exe), "-batch", "-transcribe", "-export", "-output", "out", "-sheets", "1-3", "5", "--", "in.pdf"]
    assert sheets_args("") == [] and sheets_args("x") == [] and sheets_args(" 2 - 4 ") == ["-sheets", "2-4"]

    # collect(): two movements as Audiveris names them, compressed and plain, joined in movement order
    sample = (Path(__file__).with_name("resources") / "music-xml-example.xml").read_bytes()
    a, _ = abcxml.to_musicxml(abcxml.parse_abc("M:3/4\nK:G\nG6|B6|]"), tab=False)
    with tempfile.TemporaryDirectory() as tmp:
        book = Path(tmp) / "score"
        book.mkdir()
        with zipfile.ZipFile(book / "score.mvt1.mxl", "w") as z:
            z.writestr("META-INF/container.xml",
                       "<container><rootfiles><rootfile full-path='score.mvt1.xml'/></rootfiles></container>")
            z.writestr("score.mvt1.xml", sample)
        (book / "score.mvt2.musicxml").write_text(a, encoding="utf-8")
        s = collect(Path(tmp))
    assert len(s.bars) == 23 and s.title == "Maid Behind the Bar", len(s.bars)
    assert s.info["systemStarts"][-1] == 21 and s.info["pageStarts"] == [0] and s.info["clef"] == "treble", s.info
    xml = (b"<score-partwise><part id='P1'><measure><attributes><clef><sign>F</sign><line>4</line></clef></attributes>"
           b"</measure><measure/><measure><print new-system='yes'/></measure><measure><print new-page='yes'/></measure>"
           b"</part></score-partwise>")
    assert layout(xml) == {"systemStarts": [0, 2, 3], "pageStarts": [0, 3], "clef": "bass"}, layout(xml)
    assert s.bars[21].key == (1, "major") and s.bars[21].meter == (3, 4, "") and s.bars[21].total == Fraction(3, 4)

    found = find()
    print("Audiveris launcher:", found or "not installed (transcribe() untested here)")
    if found is None:
        try:
            transcribe(b"%PDF-1.4", "x.pdf")
        except RuntimeError as e:
            assert "not found" in str(e)
    print("audiveris self-check ok")
