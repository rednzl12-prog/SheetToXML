"""Exact, AI-free reader for born-digital PDFs (Sibelius, Finale, Dorico, MuseScore exports).

Such PDFs draw music as text in a music font (noteheads, rests, clefs, accidentals ...) plus
vector paths (staff lines, stems, beams, barlines, ties). Reading those back gives exact
pitches and rhythms with no OCR and no AI. Scans and photos have no music-font glyphs, so
transcribe() returns None and the caller falls back to another engine.
"""

import ctypes
import re
import statistics
import sys
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

import pypdfium2 as pdfium
import pypdfium2.raw as R

from abcxml import Bar, Ev, Pitch, Score, key_alters
from omr import parse_page_range

F = Fraction
# Legacy byte codes shared by Sonata, Opus (Sibelius), Maestro (Finale), Petrucci, Engraver ...
_LEGACY_CODES = {
    38: ("clef", "G"), 63: ("clef", "F"), 66: ("clef", "C"),
    207: ("head", F(1, 4)), 250: ("head", F(1, 2)), 119: ("head", F(1)),
    183: ("rest", F(1)), 238: ("rest", F(1, 2)), 206: ("rest", F(1, 4)), 228: ("rest", F(1, 8)),
    197: ("rest", F(1, 16)), 168: ("rest", F(1, 32)), 244: ("rest", F(1, 64)),
    # ponytail: 32nd+ flag glyphs not mapped (rare unbeamed), add their codes when a score needs them
    106: ("flag", 1), 74: ("flag", 1), 114: ("flag", 2), 82: ("flag", 2),
    35: ("acc", 1), 98: ("acc", -1), 110: ("acc", 0), 220: ("acc", 2), 186: ("acc", -2),
    99: ("time", "common"), 67: ("time", "cut"), **{48 + i: ("digit", i) for i in range(10)},
}
# the same bytes as different producers' text extraction reports them (MacRoman, WinAnsi, symbol PUA)
LEGACY = {k: v for c, v in _LEGACY_CODES.items()
          for k in {bytes([c]).decode("mac_roman"), bytes([c]).decode("cp1252", "replace"), chr(0xF000 + c)}}
LEGACY_FONT = re.compile(r"opus|sonata|maestro|petrucci|engraver|jazz|reprise|broadway|golden|november|"
                         r"tranquility|toccata|helsinki|kousaku|norfolk|ash", re.I)
# ponytail: SMuFL table (MuseScore 4, Dorico, Finale 27) is untested here, verify on a real export
SMUFL = {0xE050: ("clef", "G"), 0xE052: ("clef", "G8"), 0xE062: ("clef", "F"), 0xE064: ("clef", "F8"),
         0xE05C: ("clef", "C"), 0xE0A2: ("head", F(1)), 0xE0A3: ("head", F(1, 2)), 0xE0A4: ("head", F(1, 4)),
         **{0xE4E3 + i: ("rest", F(1, 2 ** i)) for i in range(8)},
         **{0xE240 + i: ("flag", i // 2 + 1) for i in range(10)},
         0xE260: ("acc", -1), 0xE261: ("acc", 0), 0xE262: ("acc", 1), 0xE263: ("acc", 2), 0xE264: ("acc", -2),
         **{0xE080 + i: ("digit", i) for i in range(10)}, 0xE08A: ("time", "common"), 0xE08B: ("time", "cut")}
SMUFL = {chr(k): v for k, v in SMUFL.items()}
CLEF_REF = {"G": 32, "G8": 25, "F": 24, "F8": 17, "C": 28}   # diatonic index (octave*7 + step) of the clef's line
STEPS = "CDEFGAB"


@dataclass
class Glyph:
    kind: str                    # head / rest / flag / acc / clef / digit / time / dot / text
    val: object
    x: float                     # origin (PDF points, y up)
    y: float
    l: float
    r: float
    b: float
    t: float
    font: str = ""
    pos: int = 0                 # heads: staff position, half spaces above the bottom line
    dots: int = 0                # heads/rests: augmentation dots

    @property
    def cx(self) -> float:
        return (self.l + self.r) / 2

    @property
    def cy(self) -> float:
        return (self.b + self.t) / 2


@dataclass
class VLine:
    x: float
    lo: float
    hi: float
    w: float


@dataclass
class Staff:
    lines: List[float]           # y of the 5 lines, top first
    x0: float
    x1: float
    top_of_system: bool = True
    items: Dict[str, list] = field(default_factory=lambda: {k: [] for k in ("g", "h", "v", "beam", "curve", "text")})

    @property
    def sp(self) -> float:
        return (self.lines[0] - self.lines[-1]) / 4

    @property
    def top(self) -> float:
        return self.lines[0]

    @property
    def bot(self) -> float:
        return self.lines[-1]


# ------------------------------------------------------------------ extraction

def _chars(page) -> Tuple[List[Glyph], List[Glyph]]:
    """Music-font glyphs and plain text characters on a page."""
    tp = page.get_textpage()
    music, text = [], []
    buf, flags = ctypes.create_string_buffer(256), ctypes.c_int()
    x, y = ctypes.c_double(), ctypes.c_double()
    l, r, b, t = (ctypes.c_double() for _ in range(4))
    for i in range(tp.count_chars()):
        u = R.FPDFText_GetUnicode(tp.raw, i)
        if u <= 32 or R.FPDFText_IsGenerated(tp.raw, i) == 1:
            continue
        R.FPDFText_GetFontInfo(tp.raw, i, buf, 256, ctypes.byref(flags))
        font = buf.value.decode("latin-1")
        R.FPDFText_GetCharOrigin(tp.raw, i, x, y)
        R.FPDFText_GetCharBox(tp.raw, i, l, r, b, t)
        ch = chr(u)
        legacy = LEGACY_FONT.search(font) and "text" not in font.lower()    # "Opus Text" = dynamics etc.
        hit = SMUFL.get(ch) or (LEGACY.get(ch, ("dot", None)) if legacy else None)   # unknown: sized later
        g = Glyph(*(hit or ("text", ch)), x.value, y.value, l.value, r.value, b.value, t.value, font)
        (music if hit else text).append(g)
    return music, text


def _paths(page) -> Tuple[list, List[VLine], list, list]:
    """Horizontal lines (y, x0, x1), vertical lines, filled quads (beams) and curves, page coordinates."""
    hl, vl, quads, curves = [], [], [], []
    seg_pt_x, seg_pt_y = ctypes.c_float(), ctypes.c_float()
    fill, stroke, width = ctypes.c_int(), ctypes.c_int(), ctypes.c_float()

    def walk(obj, mats):
        kind = R.FPDFPageObj_GetType(obj)
        m = R.FS_MATRIX()
        R.FPDFPageObj_GetMatrix(obj, m)
        mats = [(m.a, m.b, m.c, m.d, m.e, m.f)] + mats
        if kind == R.FPDF_PAGEOBJ_FORM:
            for i in range(R.FPDFFormObj_CountObjects(obj)):
                walk(R.FPDFFormObj_GetObject(obj, i), mats)
            return
        if kind != R.FPDF_PAGEOBJ_PATH:
            return

        def tf(px, py):
            for a, b, c, d, e, f in mats:
                px, py = a * px + c * py + e, b * px + d * py + f
            return px, py
        R.FPDFPath_GetDrawMode(obj, ctypes.byref(fill), ctypes.byref(stroke))
        R.FPDFPageObj_GetStrokeWidth(obj, ctypes.byref(width))
        scale = 1.0
        for a, b, c, d, _, _ in mats:
            scale *= abs(a * d - b * c) ** 0.5
        w = width.value * scale
        subs, cur = [], None
        for s in range(R.FPDFPath_CountSegments(obj)):
            seg = R.FPDFPath_GetPathSegment(obj, s)
            R.FPDFPathSegment_GetPoint(seg, ctypes.byref(seg_pt_x), ctypes.byref(seg_pt_y))
            typ = R.FPDFPathSegment_GetType(seg)
            if typ == R.FPDF_SEGMENT_MOVETO or cur is None:
                cur = [[], False]
                subs.append(cur)
            cur[0].append(tf(seg_pt_x.value, seg_pt_y.value))
            cur[1] |= typ == R.FPDF_SEGMENT_BEZIERTO
        for pts, curved in subs:
            if curved:
                curves.append(pts)
                continue
            if len(pts) > 2 and pts[-1] == pts[0]:
                pts = pts[:-1]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            W, H = max(xs) - min(xs), max(ys) - min(ys)
            if len(pts) == 2 and stroke.value:
                if H < 0.1 and W > 0:
                    hl.append(((ys[0] + ys[1]) / 2, min(xs), max(xs)))
                elif W < 0.1 and H > 0:
                    vl.append(VLine((xs[0] + xs[1]) / 2, min(ys), max(ys), w))
            elif len(pts) == 4 and fill.value:
                if H < 1.2 and W > 4 * H and _axis_rect(pts):
                    hl.append(((min(ys) + max(ys)) / 2, min(xs), max(xs)))
                elif W < 1.5 and H > 4 * W and _axis_rect(pts):
                    vl.append(VLine((min(xs) + max(xs)) / 2, min(ys), max(ys), W))
                else:
                    quads.append(pts)

    for i in range(R.FPDFPage_CountObjects(page.raw)):
        walk(R.FPDFPage_GetObject(page.raw, i), [])
    return hl, vl, quads, curves


def _axis_rect(pts) -> bool:
    xs = sorted({round(p[0], 1) for p in pts})
    ys = sorted({round(p[1], 1) for p in pts})
    return len(xs) <= 2 and len(ys) <= 2


# ------------------------------------------------------------------ staves

def _staves(hl, vl) -> List[Staff]:
    """5-line staves from long horizontal lines (collinear pieces merged), top of the page first."""
    hl = sorted(hl, key=lambda h: (round(h[0], 1), h[1]))
    merged: List[list] = []
    for y, a, b in hl:
        if merged and abs(merged[-1][0] - y) < 0.15 and a <= merged[-1][2] + 1:
            merged[-1][2] = max(merged[-1][2], b)
        else:
            merged.append([y, a, b])
    long = sorted((h for h in merged if h[2] - h[1] > 50), key=lambda h: -h[0])
    staves: List[Staff] = []
    used = set()
    for i, (y, a, b) in enumerate(long):
        if i in used:
            continue
        run = [i]
        for j in range(i + 1, len(long)):
            yj, aj, bj = long[j]
            if j in used or abs(aj - a) > 3 or abs(bj - b) > 3:
                continue
            gap = long[run[-1]][0] - yj
            if gap < 0.3:
                used.add(j)                  # duplicate line
                continue
            first = long[run[0]][0] - long[run[1]][0] if len(run) > 1 else gap
            if gap > 20 or abs(gap - first) > 0.12 * first:
                break
            run.append(j)
        used.update(run)
        if len(run) == 5:                    # 4/6-line TAB staves and stray lines are ignored
            staves.append(Staff([long[k][0] for k in run], min(long[k][1] for k in run), max(long[k][2] for k in run)))
    staves.sort(key=lambda s: -s.top)
    for a, b in zip(staves, staves[1:]):     # a vertical crossing the gap joins two staves into one system
        if any(v.lo <= b.top + 0.5 and v.hi >= a.bot - 0.5 and a.x0 - 5 <= v.x <= a.x1 + 5 for v in vl):
            b.top_of_system = False
    return staves


def _assign(staves: List[Staff], key: str, items, xy):
    for it in items:
        x, y = xy(it)
        best, dist = None, 1e9
        for s in staves:
            if not s.x0 - 3 * s.sp <= x <= s.x1 + 3 * s.sp:
                continue
            d = max(0.0, y - s.top, s.bot - y)
            if d < dist and d <= 8 * s.sp:
                best, dist = s, d
        if best:
            best.items[key].append(it)


def _quad_span(q, x) -> Optional[Tuple[float, float]]:
    """Vertical extent of a polygon at abscissa x (clamped into its x range)."""
    xs = [p[0] for p in q]
    x = min(max(x, min(xs)), max(xs))
    ys = []
    for (x1, y1), (x2, y2) in zip(q, q[1:] + q[:1]):
        if min(x1, x2) <= x <= max(x1, x2):
            ys += [y1, y2] if x1 == x2 else [y1 + (y2 - y1) * (x - x1) / (x2 - x1)]
    return (min(ys), max(ys)) if ys else None


# ------------------------------------------------------------------ one staff -> tokens

@dataclass
class Chord:
    heads: List[Glyph]
    stem: Optional[VLine] = None
    up: bool = True
    beams: int = 0
    flag: int = 0
    dots: int = 0
    beam_ids: set = field(default_factory=set)
    tie: bool = False
    accs: Dict[int, int] = field(default_factory=dict)    # head index -> printed alter
    tuplet: Optional[int] = None

    @property
    def x(self) -> float:
        return min(h.l for h in self.heads)


def _staff_tokens(st: Staff, head_w: Dict[F, float]) -> List[tuple]:
    """Sorted (x, kind, data) tokens: clef, key, time, bar, chord, rest."""
    sp, items = st.sp, st.items
    g = items["g"]
    for gl in g:                                   # unknown music glyphs: only tiny ones are dots
        if gl.kind == "dot" and not (gl.r - gl.l <= 0.6 * sp and gl.t - gl.b <= 0.6 * sp):
            gl.kind = "other"
    heads = [h for h in g if h.kind == "head" and h.r - h.l >= 0.8 * head_w[h.val]]   # smaller = grace/cue
    for h in heads:
        h.pos = round((h.y - st.bot) / (sp / 2))
    rests = [h for h in g if h.kind == "rest"]
    dots = [h for h in g if h.kind == "dot"]
    tokens: List[tuple] = []

    # barlines: verticals spanning the staff whose ends sit on staff edges and touch no notehead
    def touches_head(v: VLine) -> bool:
        return any(abs(v.x - h.r) < 1.2 or abs(v.x - h.l) < 1.2 for h in heads
                   if v.lo - 0.6 * sp <= h.y <= v.hi + 0.6 * sp)
    bars, stems = [], []
    for v in items["v"]:
        if v.lo <= st.bot + 0.3 * sp and v.hi >= st.top - 0.3 * sp and not touches_head(v) \
                and v.x > st.x0 + 2 * sp:
            bars.append(v)
        elif v.hi - v.lo > 1.5 * sp:
            stems.append(v)
    bars.sort(key=lambda v: v.x)
    thin = min((v.w for v in bars), default=1)
    groups: List[List[VLine]] = []
    for v in bars:
        if groups and v.x - groups[-1][-1].x < 1.2 * sp:
            groups[-1].append(v)
        else:
            groups.append([v])
    for grp in groups:
        x0, x1 = grp[0].x, grp[-1].x
        rep = [d for d in dots if st.bot + sp <= d.cy <= st.bot + 3 * sp and x0 - 1.5 * sp < d.cx < x1 + 1.5 * sp]
        for d in rep:
            d.kind = "used"
        end_rep = any(d.cx < x0 for d in rep)
        start_rep = any(d.cx > x1 for d in rep)
        thick = any(v.w > 1.8 * thin for v in grp)
        kind = "repeat" if end_rep else "" if start_rep else "final" if thick and len(grp) > 1 else             "double" if len(grp) > 1 else ""
        tokens.append((x1 if start_rep and not end_rep else x0, "bar", (kind, start_rep)))

    # volta brackets: a horizontal line above the staff with "1." / "2." under its left end
    for y, a, b in items["h"]:
        if not (st.top + sp < y < st.top + 6 * sp and b - a > 2 * sp):
            continue
        label = "".join(t.val for t in sorted(items["text"], key=lambda t: t.l)
                        if a <= t.cx <= a + 4 * sp and y - 2.5 * sp < t.cy < y)
        nums = ", ".join(re.findall(r"\d+", label))
        if nums:
            hook = any(abs(v.x - b) < 1 and v.hi > y - 0.5 and v.lo < y - sp for v in items["v"])
            tokens.append((a + 0.1, "volta", nums))
            tokens.append((b - sp, "volta_end", (nums, "stop" if hook else "discontinue")))

    # chords: noteheads grouped by the stem they touch; stemless heads by x
    chords: List[Chord] = []
    by_stem: Dict[int, Chord] = {}
    for h in sorted(heads, key=lambda h: h.l):
        best, bd = None, 1.0
        for si, s in enumerate(stems):
            if s.lo - 0.6 * sp <= h.y <= s.hi + 0.6 * sp:
                d = min(abs(s.x - h.r), abs(s.x - h.l))
                if d < bd:
                    best, bd = si, d
        if best is None:
            near = next((c for c in chords if c.stem is None and abs(c.heads[0].cx - h.cx) < 0.5 * sp), None)
            if near:
                near.heads.append(h)
            else:
                chords.append(Chord([h]))
        elif best in by_stem:
            by_stem[best].heads.append(h)
        else:
            by_stem[best] = c = Chord([h], stems[best])
            chords.append(c)
    for c in chords:
        if c.stem is None:
            continue
        s = c.stem
        c.up = statistics.mean(h.y for h in c.heads) < (s.lo + s.hi) / 2
        for bi, q in enumerate(items["beam"]):
            xs = [p[0] for p in q]
            if min(xs) - 0.8 <= s.x <= max(xs) + 0.8:
                span = _quad_span(q, s.x)
                if span and span[0] <= s.hi + 0.3 and span[1] >= s.lo - 0.3:
                    c.beams += 1
                    c.beam_ids.add(bi)
        tip = s.hi if c.up else s.lo
        for f in g:
            if f.kind == "flag" and abs(f.l - s.x) < 1.5 and f.b - sp <= tip <= f.t + sp:
                c.flag = max(c.flag, f.val)

    # augmentation dots right of a head/rest; staccato dots sit over/under a head and are skipped
    def over_head(d: Glyph) -> bool:
        return any(h.l - 0.5 <= d.cx <= h.r + 0.5 for h in heads)
    owners = [(c, h) for c in chords for h in c.heads] + [(r, r) for r in rests]
    for d in dots:
        if d.kind != "dot" or over_head(d):
            continue
        best, bd = None, 2.5 * sp
        for o, box in owners:
            dx = d.cx - box.r
            if 0 < dx < bd and abs(d.cy - (box.y if o is not box else box.cy)) <= 0.8 * sp:
                best, bd = (o, box), dx
        if best:
            o, box = best
            n = 1 + sum(1 for e in dots if e is not d and e.kind == "dot" and 0 < e.cx - d.cx < 1.2 * sp
                        and abs(e.cy - d.cy) < 0.3 * sp)
            o.dots = max(o.dots, n)

    # accidentals: printed before a head at the same height, else part of a key signature
    accs = sorted((a for a in g if a.kind == "acc"), key=lambda a: a.x)
    loose = []
    for a in accs:
        best, bd = None, 1.3 * sp
        for c in chords:
            for i, h in enumerate(c.heads):
                gap = h.l - a.r
                if -0.5 < gap < bd and abs(h.y - a.y) < 0.3 * sp:
                    best, bd = (c, i), gap
        if best:
            best[0].accs[best[1]] = a.val
        else:
            loose.append(a)
    clusters: List[List[Glyph]] = []
    for a in loose:
        if clusters and a.l - clusters[-1][-1].r < 1.5 * sp:
            clusters[-1].append(a)
        else:
            clusters.append([a])
    for cl in clusters:
        sharps, flats = sum(a.val == 1 for a in cl), sum(a.val == -1 for a in cl)
        tokens.append((cl[0].x, "key", sharps or -flats))

    # ties: a curve from one head to a head at the same height (or off the system end)
    for pts in items["curve"]:
        (xl, yl), (xr, yr) = min(pts), max(pts)
        if abs(yl - yr) > 0.6 * sp or xr - xl < sp:
            continue
        start = [c for c in chords for h in c.heads if xl - 1.5 * sp <= h.cx <= xl + 0.8 * sp and abs(h.y - yl) < 1.3 * sp]
        if start:
            start[0].tie = True

    # clefs and time signatures
    for c in (gl for gl in g if gl.kind == "clef"):
        tokens.append((c.x, "clef", (CLEF_REF[c.val], round((c.y - st.bot) / (sp / 2)))))
    mid = (st.top + st.bot) / 2
    digits = sorted((d for d in g if d.kind == "digit" and st.bot - 0.5 * sp < d.cy < st.top + 0.5 * sp), key=lambda d: d.l)
    while digits:                                  # one column of stacked digits per time signature
        col = [d for d in digits if d.l <= digits[0].r + 0.3 * sp]
        col = [d for d in digits if d.l <= max(c.r for c in col) + 0.3 * sp]
        digits = [d for d in digits if d not in col]
        num = "".join(str(d.val) for d in col if d.y > mid)
        den = "".join(str(d.val) for d in col if d.y <= mid)
        if num and den and int(den):
            tokens.append((col[0].l, "time", (int(num), int(den), "")))
    for t in (gl for gl in g if gl.kind == "time"):
        tokens.append((t.l, "time", (4, 4, "common") if t.val == "common" else (2, 2, "cut")))

    # tuplet numerals (non-bold digits by a beam group), applied later only if the bar needs them
    # ponytail: beamed tuplets whose note count equals the numeral only; unbeamed/bracketed ones need bracket spans
    for t in items["text"] + [d for d in g if d.kind == "digit"]:
        if str(t.val) in "23456789" and "bold" not in t.font.lower()                 and (t.cy > st.top + 0.5 * sp or t.cy < st.bot - 0.5 * sp):
            beamed = [c for c in chords if c.beam_ids]
            near = min(beamed, key=lambda c: abs(c.x - t.cx), default=None)
            if near and abs(near.x - t.cx) < 4 * sp and t.x > st.x0 + 8 * sp:
                grp = [c for c in beamed if c.beam_ids & near.beam_ids]
                if len(grp) == int(t.val):
                    for c in grp:
                        c.tuplet = int(t.val)

    for c in chords:
        tokens.append((c.x, "chord", c))
    for r in rests:
        tokens.append((r.l, "rest", r))
    order = {"volta_end": 0, "clef": 1, "key": 2, "time": 3, "bar": 4, "volta": 5, "rest": 6, "chord": 6}
    tokens.sort(key=lambda t: (t[0], order[t[1]]))
    return tokens


# ------------------------------------------------------------------ assembly

def _chord_dur(c: Chord) -> F:
    head = c.heads[0].val
    d = head / 2 ** (c.beams or c.flag) if head == F(1, 4) else head
    return d * (2 - F(1, 2 ** c.dots))


def _title(text: List[Glyph], st: Staff, width: float) -> Tuple[str, str]:
    """Largest text line above the first staff = title; a right-aligned line there = composer."""
    lines: Dict[int, List[Glyph]] = {}
    for t in text:
        if t.b > st.top + 4 * st.sp:
            lines.setdefault(round(t.y / 3), []).append(t)
    if not lines:
        return "", ""
    rows = list(lines.values())

    def s(row):
        row.sort(key=lambda t: t.l)
        out = row[0].val
        for a, b in zip(row, row[1:]):
            out += (" " if b.l - a.r > 0.25 * (b.t - b.b) else "") + b.val
        return out.strip()
    big = max(rows, key=lambda r: max(t.t - t.b for t in r))
    comp = next((r for r in rows if r is not big and max(t.r for t in r) > 0.8 * width
                 and min(t.l for t in r) > 0.5 * width), None)
    return s(big), s(comp) if comp else ""


def transcribe(data: bytes, page_range: str = "") -> Optional[Score]:
    """Score from a born-digital PDF's music-font glyphs and vector paths, or None if it has none."""
    if data[:5] != b"%PDF-":
        return None
    pdf = pdfium.PdfDocument(data)
    tokens: List[Tuple[int, float, str, object]] = []
    staff_no, multi, any_heads, title = 0, False, False, ("", "")
    pages = parse_page_range(page_range, len(pdf))
    for pi in pages:
        page = pdf[pi]
        music, text = _chars(page)
        if not any(g.kind == "head" for g in music):
            continue
        any_heads = True
        hl, vl, quads, curves = _paths(page)
        staves = _staves(hl, vl)
        if not staves:
            continue
        _assign(staves, "g", music, lambda g: (g.cx, g.y if g.kind in ("head", "acc", "clef") else g.cy))
        _assign(staves, "v", vl, lambda v: (v.x, (v.lo + v.hi) / 2))
        _assign(staves, "h", hl, lambda h: ((h[1] + h[2]) / 2, h[0]))
        _assign(staves, "beam", quads, lambda q: (sum(p[0] for p in q) / 4, sum(p[1] for p in q) / 4))
        _assign(staves, "curve", curves, lambda c: (sum(p[0] for p in c) / len(c), sum(p[1] for p in c) / len(c)))
        _assign(staves, "text", text, lambda t: (t.cx, t.cy))
        head_w: Dict[F, float] = {}
        for v in (F(1, 4), F(1, 2), F(1)):
            ws = [g.r - g.l for g in music if g.kind == "head" and g.val == v]
            head_w[v] = statistics.median(ws) if ws else 0
        if pi == 0:
            title = _title(text, staves[0], page.get_width())
        for st in staves:
            if not st.top_of_system:
                multi = True
                continue
            for x, kind, val in _staff_tokens(st, head_w):
                tokens.append((staff_no, x, kind, val))
            tokens.append((staff_no, 1e9, "end", None))
            staff_no += 1
    if not any_heads:
        return None
    sc = _build(tokens)
    sc.title, sc.composer = title
    if multi:
        sc.warnings.append("Multi-staff systems: only the top staff of each system was read.")
    return sc


def _build(tokens) -> Score:
    sc = Score()
    clef = (CLEF_REF["G"], 2)
    key, meter = 0, (4, 4, "")
    keys, meters = [], []
    cur: List[tuple] = []          # (Ev, chord or None)
    left_repeat, ending, ending_stop = False, "", None
    tie_alter: Dict[Tuple[str, int], int] = {}
    acc: Dict[Tuple[str, int], int] = {}

    def close(right: str = ""):
        nonlocal cur, left_repeat, acc, ending, ending_stop
        if not cur:
            if right and sc.bars and not sc.bars[-1].right:
                sc.bars[-1].right = right
            return
        b = Bar(events=[e for e, _ in cur], expected=F(meter[0], meter[1]), right=right)
        if left_repeat:
            b.left, left_repeat = "repeat", False
        b.ending, b.ending_stop, ending, ending_stop = ending, ending_stop, "", None
        if len(b.events) == 1 and not b.events[0].pitches and b.events[0].dur == 1:
            b.events[0].dur, b.events[0].whole_bar = b.expected, True
        _fix_tuplets(b, cur)
        sc.bars.append(b)
        keys.append(key)
        meters.append(meter)
        cur, acc = [], {}

    for staff, x, kind, val in tokens:
        if kind == "clef":
            clef = val
        elif kind == "key" and not cur:
            key = val
        elif kind == "time" and not cur:
            meter = val
        elif kind == "bar":
            close(val[0])
            left_repeat = left_repeat or val[1]
        elif kind == "volta":
            ending = val
        elif kind == "volta_end":
            if cur:
                ending_stop = val
            elif sc.bars:
                sc.bars[-1].ending_stop = val
        elif kind == "end":
            close()
        elif kind == "rest":
            cur.append((Ev(dur=val.val * (2 - F(1, 2 ** val.dots))), None))
            tie_alter = {}
        elif kind == "chord":
            alters = key_alters(key)
            pitches = []
            for i, h in sorted(enumerate(val.heads), key=lambda ih: ih[1].pos):
                d = clef[0] + h.pos - clef[1]
                step, octave = STEPS[d % 7], d // 7
                k = (step, octave)
                if i in val.accs:
                    alter, show = val.accs[i], True
                    acc[k] = alter
                else:
                    alter, show = tie_alter.get(k, acc.get(k, alters.get(step, 0))), False
                pitches.append(Pitch(step, alter, octave, show))
            ev = Ev(dur=_chord_dur(val), pitches=pitches, tie=val.tie)
            tie_alter = {(p.step, p.octave): p.alter for p in pitches} if val.tie else {}
            cur.append((ev, val))
    close()

    sc.key, sc.meter = (keys[0] if keys else 0, "major"), meters[0] if meters else (4, 4, "")
    for i, b in enumerate(sc.bars):
        if i and keys[i] != keys[i - 1]:
            b.key = (keys[i], "major")
        if i and meters[i] != meters[i - 1]:
            b.meter = meters[i]
    evs = [e for b in sc.bars for e in b.events]
    for a, b in zip(evs, evs[1:]):              # a tie needs a shared pitch in the next event
        if a.tie and not any(p.same(q) for p in a.pitches for q in b.pitches):
            a.tie = False
    if evs and evs[-1].tie:
        evs[-1].tie = False
    return sc


def _fix_tuplets(b: Bar, cur):
    """Apply tuplet numerals seen in this bar only when they make its length add up."""
    tup = [(e, c) for e, c in cur if c is not None and c.tuplet]
    if not tup or b.total <= b.expected:
        return
    saved = [(e.dur, e.tuplet, e.tpos) for e, _ in tup]
    i = 0
    while i < len(tup):
        n = tup[i][1].tuplet
        normal = 2 if n == 3 else 4 if n < 8 else 8
        run = tup[i:i + n]
        for j, (e, _) in enumerate(run):
            e.dur *= F(normal, n)
            e.tuplet, e.tpos = (n, normal), "start" if j == 0 else "stop" if j == len(run) - 1 else ""
        i += n
    if b.total != b.expected:
        for (e, _), (d, t, p) in zip(tup, saved):
            e.dur, e.tuplet, e.tpos = d, t, p


if __name__ == "__main__":
    import time
    from abcxml import score_to_abc
    path = sys.argv[1]
    t0 = time.time()
    with open(path, "rb") as fh:
        score = transcribe(fh.read(), sys.argv[2] if len(sys.argv) > 2 else "")
    if score is None:
        sys.exit("no music-font glyphs: not a born-digital score PDF")
    print(score_to_abc(score))
    print(f"% {len(score.bars)} bars, problems {score.problems()}, warnings {score.warnings}, "
          f"{time.time() - t0:.2f}s", file=sys.stderr)
    if "Pachelbel_Canon" in path:
        assert score.key == (2, "major") and score.meter[:2] == (4, 4), (score.key, score.meter)
        assert not score.problems() and len(score.bars) > 50, (len(score.bars), score.problems())
