"""Sheet music (PDF / image) -> ABC -> MusicXML.

Pages are rendered, cropped into staff systems (a model reads one system far more
reliably than a whole page), transcribed to ABC by a vision LLM, checked bar by bar
against the time signature, repaired where they don't add up, then turned into MusicXML
by abcxml.
"""

import io
import re
import statistics
import warnings
from dataclasses import dataclass
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

import pypdfium2 as pdfium
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

warnings.filterwarnings("ignore", category=DeprecationWarning)   # Pillow's getdata() notice

TARGET_SPACING = 22          # px between staff lines after normalisation
MAX_CROP_WIDTH = 3000
PDF_MAX_SIDE = 3400          # px for the longer side of a rendered page


# ------------------------------------------------------------------ loading

def parse_page_range(spec: str, total: int) -> List[int]:
    """'1-3,5' -> [0, 1, 2, 4]; blank or unusable -> every page."""
    picked: List[int] = []
    for part in (spec or "").split(","):
        m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+))?\s*", part)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
            picked += [i - 1 for i in range(max(1, lo), min(total, hi) + 1)]
    return sorted(set(picked)) or list(range(total))


def load_pages(data: bytes, page_range: str = "") -> List[Image.Image]:
    """Greyscale page images from a PDF or an image file."""
    if data[:5] == b"%PDF-":
        pdf = pdfium.PdfDocument(data)
        pages = []
        for i in parse_page_range(page_range, len(pdf)):
            page = pdf[i]
            w, h = page.get_size()
            pages.append(page.render(scale=min(300 / 72, PDF_MAX_SIDE / max(w, h))).to_pil().convert("L"))
        return pages
    img = Image.open(io.BytesIO(data))
    frames = []
    for i in range(min(getattr(img, "n_frames", 1), 50)):
        img.seek(i)
        f = ImageOps.exif_transpose(img.copy())
        if f.mode in ("RGBA", "LA", "P"):
            f = f.convert("RGBA")
            bg = Image.new("RGBA", f.size, "white")
            f = Image.alpha_composite(bg, f)
        f = f.convert("L")
        if max(f.size) > 5000:
            f.thumbnail((5000, 5000), Image.LANCZOS)
        frames.append(f)
    pick = parse_page_range(page_range, len(frames))
    return [frames[i] for i in pick]


# ------------------------------------------------------------------ staff detection

def _otsu(hist: List[int]) -> int:
    total, sum_all = sum(hist), sum(i * h for i, h in enumerate(hist))
    wb = sb = 0
    best = (0.0, 127)
    for t in range(256):
        wb += hist[t]
        if not wb:
            continue
        wf = total - wb
        if not wf:
            break
        sb += t * hist[t]
        var = wb * wf * (sb / wb - (sum_all - sb) / wf) ** 2
        if var > best[0]:
            best = (var, t)
    return best[1]


def _binarize(img: Image.Image) -> Image.Image:
    g = ImageOps.autocontrast(img, cutoff=1)
    t = min(_otsu(g.histogram()) + 40, 200)       # staff lines are often light grey in JPEGs
    return g.point(lambda v: 255 if v <= t else 0)


def _row_ink(bw: Image.Image) -> List[float]:
    return list(bw.resize((1, bw.height), Image.BOX).getdata())


def _long_runs(bw: Image.Image, width: int) -> Image.Image:
    """Keep only ink that sits in horizontal runs of at least `width` px (staff lines, not beams)."""
    out, step = bw, 1
    while step * 2 <= width:
        out = ImageChops.darker(out, ImageChops.offset(out, -step, 0))
        step *= 2
    return out


def _vfill(bw: Image.Image, k: int) -> Image.Image:
    """Fill blank pixels with ink within k px both above and below them (the hole of a half-note head;
    a staff space, 1 spacing tall, stays open for k < spacing / 2)."""
    up = down = bw
    for d in range(1, k + 1):
        up = ImageChops.lighter(up, ImageChops.offset(bw, 0, d))
        down = ImageChops.lighter(down, ImageChops.offset(bw, 0, -d))
    return ImageChops.lighter(bw, ImageChops.darker(up, down))


def deskew(img: Image.Image) -> Image.Image:
    """Straighten phone photos / crooked scans so staff lines are horizontal: the angle that makes the
    row-ink profile spikiest, 0.5 degree steps on a small copy, then 0.05 steps on a larger one."""
    def spiky(small: Image.Image, angle: float) -> float:
        prof = _row_ink(_binarize(small.rotate(angle, resample=Image.BILINEAR, fillcolor=255)))
        mean = sum(prof) / len(prof)
        return sum((v - mean) ** 2 for v in prof)

    small = img.copy()
    small.thumbnail((700, 900))
    coarse = max(range(-30, 31, 5), key=lambda t: (spiky(small, t / 10), -abs(t))) / 10
    small = img.copy()
    small.thumbnail((1600, 2000))
    scores = {t: spiky(small, t / 100) for t in range(round(coarse * 100) - 50, round(coarse * 100) + 51, 5)}
    best = max(scores, key=lambda t: (scores[t], -abs(t)))
    if abs(best) >= 10 and scores[best] > scores.get(0, spiky(small, 0)) * 1.02:
        return img.rotate(best / 100, resample=Image.BICUBIC, fillcolor=255)
    return img


def _line_centers(prof: List[float]) -> List[float]:
    thr = max(prof) * 0.30
    centers, run = [], []
    for r, v in enumerate(prof):
        if v >= thr:
            if run and r - run[-1] > 1:
                centers.append(sum(run) / len(run))
                run = []
            run.append(r)
    if run:
        centers.append(sum(run) / len(run))
    return centers


def _staves(centers: List[float]) -> List[List[float]]:
    """Runs of 4-6 equally spaced lines."""
    out, i, n = [], 0, len(centers)
    while i < n - 3:
        grp, sp, j = [centers[i]], None, i + 1
        while j < n:
            gap = centers[j] - grp[-1]
            if sp is None:
                if 3 <= gap <= 60:
                    sp = gap
                else:
                    break
            elif abs(gap - sp) > max(2.0, 0.25 * sp):
                break
            grp.append(centers[j])
            sp = (grp[-1] - grp[0]) / (len(grp) - 1)
            j += 1
        if 4 <= len(grp) <= 6:
            out.append(grp)
            i = j
        else:
            i += 1
    return out


@dataclass
class System:
    box: Tuple[int, int, int, int]       # left, top, right, bottom on the (deskewed) page
    spacing: float                       # staff-line spacing in page pixels
    staves: int                          # notation staves inside the box


def find_systems(img: Image.Image, first_page: bool = True) -> Tuple[Image.Image, List[System]]:
    """Locate staff systems. Returns the deskewed page and the systems top to bottom (maybe none)."""
    img = deskew(img)
    bw = _binarize(img)
    prof = _row_ink(bw)
    if max(prof) == 0:
        return img, []
    lines = _row_ink(_long_runs(bw, max(16, img.width // 20)))
    staves = _staves(_line_centers(lines)) if max(lines) > 0 else []
    five = [s for s in staves if len(s) == 5] or staves
    if not five:
        return img, []
    sp = statistics.median((s[-1] - s[0]) / (len(s) - 1) for s in five)
    notation = [s for s in staves if abs((s[-1] - s[0]) / (len(s) - 1) - sp) <= 0.2 * sp]
    notation.sort(key=lambda s: s[0])
    kept: List[List[float]] = []
    for s in notation:                    # drop noise "staves" overlapping a better one
        if kept and s[0] < kept[-1][-1]:
            if len(s) > len(kept[-1]):
                kept[-1] = s
            continue
        kept.append(s)
    if not kept:
        return img, []

    # group notation staves into systems (grand staff: small gaps inside, bigger between systems)
    gaps = [b[0] - a[-1] for a, b in zip(kept, kept[1:])]
    groups = [[kept[0]]]
    if gaps and max(gaps) > 1.6 * min(gaps) and min(gaps) < 8 * sp:
        limit = (max(gaps) + min(gaps)) / 2
        for g, s in zip(gaps, kept[1:]):
            groups.append([s]) if g > limit else groups[-1].append(s)
    else:
        groups = [[s] for s in kept]

    others = [s for s in staves if s not in kept]            # tab staves etc. travel with the system above
    W, H = img.size
    blocks = []
    for gi, grp in enumerate(groups):
        top, bottom = grp[0][0], grp[-1][-1]
        nxt = groups[gi + 1][0][0] if gi + 1 < len(groups) else H
        for o in others:
            if bottom <= o[0] < nxt:
                bottom = max(bottom, o[-1])
        blocks.append((top, bottom, len(grp)))

    def valley(lo: float, hi: float) -> int:
        """Blank-most row in the middle of the gap between two blocks."""
        a, b = int(lo + 0.3 * (hi - lo)), int(lo + 0.7 * (hi - lo)) + 1
        if b <= a:
            return int((lo + hi) / 2)
        return min(range(a, b), key=lambda r: (prof[min(r, H - 1)], abs(r - (lo + hi) / 2)))

    systems = []
    for i, (top, bottom, n) in enumerate(blocks):
        up = valley(blocks[i - 1][1], top) if i else (0 if first_page else max(0, int(top - 8 * sp)))
        down = valley(bottom, blocks[i + 1][0]) if i + 1 < len(blocks) else min(H, int(bottom + 8 * sp))
        edge = bw.crop((0, int(top) - 1, W, int(top) + 2)).getbbox()
        left, right = (edge[0], edge[2]) if edge else (0, W)
        left, right = max(0, int(left - 4 * sp)), min(W, int(right + 4 * sp))
        systems.append(System((left, max(0, up), right, min(H, down)), sp, n))
    return img, systems


def measures(img: Image.Image, system: System) -> Tuple[List[Tuple[int, int]], bool]:
    """Measure x-spans (page coordinates) of a system from its barlines, and whether the count looks reliable.

    A barline is a column inked from the top staff line to the bottom one, thin at every space
    centre (no notehead) and blank just beyond both ends (stems run on into a notehead or beam).
    # ponytail: barlines must cross the whole system (grand staff with broken barlines -> unreliable), a per-staff vote if needed
    """
    left, top, right, bottom = system.box
    bw = _binarize(img.crop(system.box))
    W, H = bw.size
    staves = _staves(_line_centers(_row_ink(_long_runs(bw, max(16, W // 20)))))
    sp = system.spacing
    staves = [s for s in staves if len(s) == 5 and abs((s[-1] - s[0]) / 4 - sp) <= 0.2 * sp]
    if not staves:
        return [], False
    y0, y1 = round(staves[0][0]), round(staves[-1][-1])
    band = bw.crop((0, y0, W, y1 + 1)).resize((W, 1), Image.BOX)
    full = [x for x, v in enumerate(band.getdata()) if v >= 235]
    px = bw.load()
    lines = bw.crop((0, y0 - 1, W, y0 + 2)).getbbox()                 # where the staff lines run
    x_start, x_end = (lines[0], lines[2]) if lines else (0, W)

    def ink(x: int, y: int) -> bool:
        return 0 <= x < W and 0 <= y < H and px[x, y] > 0

    shut = _vfill(bw, round(0.35 * sp)).load()          # hollow heads filled, so they count as attached

    def run(x: int, y: int) -> int:
        a = b = x
        while a > 0 and shut[a - 1, y]:
            a -= 1
        while b < W - 1 and shut[b + 1, y]:
            b += 1
        return b - a + 1

    on_line = {round(c) + d for s in staves for c in s for d in (-2, -1, 0, 1, 2)}
    between = [y for y in range(y0, y1) if y not in on_line]
    dots = {y for s in staves for y in range(round(s[1]), round(s[3]))} - on_line   # repeat dots: the middle spaces
    far, wide = max(5, round(0.45 * sp)), round(sp)

    def attached(x: int, y_edge: int, step: int) -> int:
        """Longest horizontal run in the ink joined to the column just past an end line, when that ink reaches
        0.45 spaces out (else 0): the head or beam of a stem, or a measure number touching a barline. The flood
        starts on the first row clear of the staff line, so even a 1 px gap on a blurred scan keeps a number apart."""
        d = 1                                # first row clear of the staff line here (blurred lines run 4+ px thick)
        while d < 0.4 * sp and sum(ink(x + i, y_edge + d * step) for i in range(-wide, wide + 1)) >= 1.4 * wide:
            d += 1
        start = y_edge + d * step
        lo, hi = sorted((start, y_edge + (round(0.9 * sp)) * step))
        stack = [(x + d, start) for d in (-1, 0, 1) if ink(x + d, start)]
        seen = set(stack)
        reached = False
        while stack:
            cx, cy = stack.pop()
            reached = reached or abs(cy - y_edge) >= far
            for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                if abs(nx - x) <= wide and lo <= ny <= hi and (nx, ny) not in seen and ink(nx, ny):
                    seen.add((nx, ny))
                    stack.append((nx, ny))
        if not reached:
            return 0
        best = run = 0                       # longest horizontal run: heads and beams are solid, digits are strokes
        for p in sorted(seen, key=lambda q: (q[1], q[0])):
            run = run + 1 if (p[0] - 1, p[1]) in seen else 1
            best = max(best, run)
        return best

    bars: List[List[int]] = []
    for x in full:
        # a full-height column with nothing below it is a barline unless a notehead (solid) sits on top: a stem
        # spanning the staff with a narrow top end would need its head below the staff, attached at the bottom
        if attached(x, y1, 1) or attached(x, y0, -1) >= 0.8 * sp:
            continue
        widths = [run(x, y) for y in between]
        broad = [y for y, w in zip(between, widths) if w > 0.6 * sp]
        bare = [w for y, w in zip(between, widths) if y not in dots] or widths     # repeat dots may touch it
        thick = min(bare) > 0.3 * sp and max(bare) - min(bare) <= 0.25 * sp and max(bare) <= sp   # heavy barline
        if len(broad) > 0.1 * len(between) and not set(broad) <= dots and not thick:   # notehead or beam attached
            continue
        if bars and x - bars[-1][-1] <= 1.5 * sp:                      # thin+thick / repeat barlines
            bars[-1].append(x)
        else:
            bars.append([x])
    xs = [round(sum(b) / len(b)) for b in bars if b[0] > x_start + 3 * sp]
    if not xs:
        return [], False
    if x_end - xs[-1] > 3 * sp:                                        # no closing barline
        xs.append(x_end)
    spans, prev = [], x_start
    for x in xs:
        spans.append((left + prev, left + x))
        prev = x
    reliable = x_end - xs[-1] <= 3 * sp and all(b - a >= 4 * sp for a, b in spans[1:])
    return spans, reliable


def _stem(px, cx: int, cy: int, sp: float) -> Optional[Tuple[int, int, int]]:
    """Longest vertical ink run (x, top, bottom) of >= 2.5 staff spaces leaving the head's side - its stem."""
    best = None
    for x in range(cx - round(0.9 * sp), cx + round(0.9 * sp) + 1):
        for start in (cy - round(0.3 * sp), cy + round(0.3 * sp)):
            try:
                if not px[x, start]:
                    continue
                a = b = start
                while px[x, a - 1]:
                    a -= 1
                while px[x, b + 1]:
                    b += 1
            except IndexError:
                continue
            if b - a >= 2.5 * sp and (best is None or b - a > best[2] - best[1]):
                best = (x, a, b)
    return best


def _beams(px, stem: Tuple[int, int, int], cy: int, sp: float, lines: frozenset = frozenset()) -> int:
    """Beams or flags at the stem's far end: thick ink runs beside the stem and joined to it, counted from
    the tip inward (a fingering digit touching the tip is not joined sideways, so it doesn't count).
    A thin run centred on a staff line (`lines`: its rows) is the line; on a blurred scan two or three beams merge
    into one run, which then counts by its length (beam 0.5 + gap 0.25 staff spaces)."""
    x, a, b = stem
    up = cy - a > b - cy
    ys = list(range(a, min(a + round(2.6 * sp), cy - round(0.8 * sp)))) if up else \
        list(range(b, max(b - round(2.6 * sp), cy + round(0.8 * sp)), -1))

    def ink(cx: int, cy_: int) -> bool:
        try:
            return px[cx, cy_] > 0
        except IndexError:
            return False

    def beam(col: int, run: List[int]) -> bool:
        """A long run continues as thick a little further out: merged beams (a flag thins and curves away)."""
        mid = run[len(run) // 2]
        if not ink(col, mid):
            return False
        lo = hi = mid
        while ink(col, lo - 1):
            lo -= 1
        while ink(col, hi + 1):
            hi += 1
        return hi - lo + 1 >= 0.8 * len(run)

    best = 0
    for side in (-1, 1):
        col, runs, run = x + side * round(0.5 * sp), 0, []
        for y in ys + [None]:
            if y is not None and ink(col, y):
                run.append(y)
                continue
            line = run and run[len(run) // 2] in lines and len(run) <= len(lines) // 5 + 2
            if len(run) >= 0.25 * sp and not line and any(all(ink(c, r) for c in range(x, col + side, side)) for r in run):
                merged = len(run) >= 1.1 * sp and beam(col + side * round(0.5 * sp), run)
                runs += 1 if not merged else 2 if len(run) < 1.85 * sp else 3
            run = []
        best = max(best, runs)
    return best


def _dotted(light, cx: int, cy: int, sp: float) -> bool:
    """An augmentation dot: a small blob just right of the head, not touching anything (it sits in a space;
    staccato dots sit above/below; the thick bars of a following accidental belong to a bigger shape)."""
    x0, x1 = cx + round(0.75 * sp), cx + round(1.7 * sp)
    y0, y1 = cy - round(0.75 * sp), cy + round(0.35 * sp)
    seen = set()
    for y in range(y0, y1):
        for x in range(x0, x1):
            try:
                if not light[x, y] or (x, y) in seen:
                    continue
            except IndexError:
                return False
            stack, pts = [(x, y)], []
            seen.add((x, y))
            while stack:
                px_, py_ = stack.pop()
                pts.append((px_, py_))
                for nx, ny in ((px_ + 1, py_), (px_ - 1, py_), (px_, py_ + 1), (px_, py_ - 1)):
                    if x0 - 1 <= nx <= x1 and y0 - 1 <= ny <= y1 and (nx, ny) not in seen:
                        try:
                            if light[nx, ny]:
                                seen.add((nx, ny))
                                stack.append((nx, ny))
                        except IndexError:
                            pass
            xs, ys = [q[0] for q in pts], [q[1] for q in pts]
            if min(xs) >= x0 and max(xs) < x1 and min(ys) >= y0 and max(ys) < y1 \
                    and max(xs) - min(xs) <= 0.55 * sp and max(ys) - min(ys) <= 0.55 * sp:
                return True
    return False


def noteheads(img: Image.Image, system: System) -> List[Tuple[int, int, Optional[Fraction]]]:
    """Filled noteheads on the top staff as (page x, staff step, written length); step 0 = bottom line, 1 = the
    space above, ...; length from the beams/flags at the stem tip and an augmentation dot (None if unclear).

    Erosion by ~0.45 staff spaces wipes out staff lines, stems, beams, dots, digits and most text;
    only blobs as tall as a notehead survive. Hollow (half/whole) and grace notes are not found.
    # ponytail: clef/time-signature blobs filtered by size only, a template match if they leak through
    """
    left, top, _, _ = system.box
    bw = _binarize(img.crop(system.box))
    sp = system.spacing
    staves = [s for s in _staves(_line_centers(_row_ink(_long_runs(bw, max(16, bw.width // 20)))))
              if len(s) == 5 and abs((s[-1] - s[0]) / 4 - sp) <= 0.2 * sp]
    if not staves:
        return []
    staff = staves[0]
    rows = _row_ink(bw)
    line_rows = frozenset(y for c in staff for y in range(round(c) - round(0.3 * sp), round(c) + round(0.3 * sp) + 1)
                      if 0 <= y < bw.height and rows[y] >= rows[round(c)] / 2)      # the rows each staff line covers
    k = max(3, round(0.45 * sp) | 1)
    core = bw.filter(ImageFilter.MinFilter(k))
    W, H = core.size
    lo, hi = max(0, int(staff[0] - 5 * sp)), min(H, int(staff[-1] + 5 * sp))
    if len(staves) > 1:
        hi = min(hi, int((staff[-1] + staves[1][0]) / 2))
    data, raw = core.load(), bw.load()
    seen = set()
    lines = bw.crop((0, round(staff[0]) - 1, W, round(staff[0]) + 2)).getbbox()
    x_min = (lines[0] if lines else 0) + 3 * sp                       # skip the clef
    heads = []
    band = core.crop((0, lo, W, hi)).getdata()
    for i in [i for i, v in enumerate(band) if v]:                      # only the surviving ink
        x, y = i % W, lo + i // W
        if (x, y) in seen:
            continue
        stack, pts = [(x, y)], []
        seen.add((x, y))
        while stack:
            cx, cy = stack.pop()
            pts.append((cx, cy))
            for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                if 0 <= nx < W and lo <= ny < hi and data[nx, ny] and (nx, ny) not in seen:
                    seen.add((nx, ny))
                    stack.append((nx, ny))
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        w, h = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
        if not (0.4 * sp <= h <= 0.9 * sp and 0.6 * sp <= w <= 1.2 * sp) or len(pts) < 0.2 * sp * sp \
                or len(pts) > 0.85 * w * h:                    # rests are upright rectangles, heads are tilted
            continue
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        stem = _stem(raw, round(cx), round(cy), sp)
        if cx < x_min or not stem:                                 # no stem: 'tr', beam stubs, digits
            continue
        step = round((staff[-1] - cy) / (sp / 2))                  # bottom line = 0
        n = _beams(raw, stem, round(cy), sp, line_rows)
        dot = Fraction(3, 2) if _dotted(raw, round(cx), round(cy), sp) else 1
        dur = Fraction(1, 4 * 2 ** n) * dot if n <= 4 else None
        heads.append((left + round(cx), step, dur))
    return sorted(heads)


SHARP_STEPS, FLAT_STEPS = (8, 5, 9, 6, 3, 7, 4), (4, 7, 3, 6, 2, 5, 1)   # treble staff steps of F C G D A E B / B E A D G C F


def key_signature(img: Image.Image, system: System) -> Optional[int]:
    """Key-signature accidentals right after the clef on the top staff: +n sharps, -n flats, 0 none, None unclear.

    Staff lines are erased where nothing crosses them, then the blobs after the clef are classified by shape
    (sharp: two long vertical strokes of equal reach; flat: one stroke with a bowl at its foot on the right)
    and their heights must follow F C G D A E B / B E A D G C F, the pattern every clef shares up to a shift.
    # ponytail: staff-space geometry only, a template match for unusual fonts
    """
    bw = _binarize(img.crop(system.box))
    sp, (W, H) = system.spacing, bw.size
    staves = [s for s in _staves(_line_centers(_row_ink(_long_runs(bw, max(16, W // 20)))))
              if len(s) == 5 and abs((s[-1] - s[0]) / 4 - sp) <= 0.2 * sp]
    if not staves:
        return None
    staff = staves[0]
    edge = bw.crop((0, round(staff[0]) - 1, W, round(staff[0]) + 2)).getbbox()
    if not edge:
        return None
    x0, x1 = edge[0], min(W, edge[0] + round(16 * sp))
    y0, y1 = max(0, round(staff[0] - 2 * sp)), min(H, round(staff[-1] + 2 * sp))
    px = bw.load()
    ink = {(x, y) for y in range(y0, y1) for x in range(x0, x1) if px[x, y]}
    rows = _row_ink(bw)
    for c in staff:                                    # erase the staff lines where no symbol crosses them
        band = [y for y in range(round(c - 0.3 * sp), round(c + 0.3 * sp) + 1) if rows[y] >= rows[round(c)] / 2]
        if band:
            band = list(range(band[0] - 1, band[-1] + 2))       # plus the ragged edge rows of a scanned line
            for x in range(x0, x1):
                if (x, band[0] - 1) not in ink or (x, band[-1] + 1) not in ink:
                    ink.difference_update((x, y) for y in band)
    erased = set(ink)
    blobs = []
    while ink:
        stack = [ink.pop()]
        pts = list(stack)
        while stack:
            cx, cy = stack.pop()
            for q in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                if q in ink:
                    ink.discard(q)
                    stack.append(q)
                    pts.append(q)
        if len(pts) >= 0.15 * sp * sp:
            blobs.append(pts)
    boxes = sorted((min(p[0] for p in b), min(p[1] for p in b), max(p[0] for p in b), max(p[1] for p in b), b)
                   for b in blobs)
    clef = next((b for b in boxes if b[3] - b[1] >= 2.5 * sp and b[0] < x0 + 3 * sp), None)
    if clef is None:
        return None

    # vertical strokes after the clef: columns whose longest ink run is 1.6-3.6 staff spaces (blur can close the
    # gap between a sharp's two strokes in places, so strokes come from runs, not from connected blobs)
    def longest(x: int) -> Tuple[int, int, int]:
        best, a = (0, 0, 0), None
        for y in range(y0, y1 + 1):
            if y < y1 and px[x, y]:
                a = y if a is None else a
            elif a is not None:
                best, a = max(best, (y - a, a, y - 1)), None
        return best

    strokes: List[List[Tuple[int, int, int, int]]] = []          # per stroke: (x, length, top, bottom) columns
    for x in range(clef[2] + 1, min(x1, clef[2] + round(10 * sp))):
        n, a, b = longest(x)
        if 1.6 * sp <= n <= 3.6 * sp:
            if strokes and x - strokes[-1][-1][0] <= 1:
                strokes[-1].append((x, n, a, b))
            else:
                strokes.append([(x, n, a, b)])
        elif n > 3.6 * sp:                                     # a note stem: the key signature is over
            break
    def bowl(x: int, y: int) -> bool:
        """Ink (staff lines erased) 0.3-0.7 spaces right of a stroke, 0.2-0.6 spaces above y: a flat's bowl."""
        return any((x + d, y - e) in erased for d in range(round(0.3 * sp), round(0.7 * sp))
                   for e in range(round(0.2 * sp), round(0.6 * sp)))

    found: List[Tuple[str, float]] = []
    prev, i = clef[2], 0
    while i < len(strokes):
        st = strokes[i]
        xa, top, bot = st[0][0], min(c[2] for c in st), max(c[3] for c in st)
        if xa - prev > (2.5 if not found else 1.3) * sp:
            break
        nxt = strokes[i + 1] if i + 1 < len(strokes) else None
        if nxt and nxt[0][0] - st[-1][0] <= 0.7 * sp and abs(min(c[2] for c in nxt) - top) <= 0.6 * sp \
                and abs(max(c[3] for c in nxt) - bot) <= 0.6 * sp:
            k, pos, prev, i = "sharp", (top + bot) / 2, nxt[-1][0], i + 2
        elif bowl(st[-1][0], bot) and not bowl(st[-1][0], top + round(0.6 * sp)) and not any(
                all((x, y) in erased for x in range(st[-1][0], st[-1][0] + round(1.1 * sp)))
                for y in range(bot - round(0.6 * sp), bot + 1)):
            k, pos, prev, i = "flat", bot - 0.5 * sp, st[-1][0] + round(0.6 * sp), i + 1
        else:
            break
        if found and k != found[0][0]:
            break
        found.append((k, (staff[-1] - pos) / (sp / 2)))
    if not found:
        return 0
    steps = [s for _, s in found]
    want = SHARP_STEPS if found[0][0] == "sharp" else FLAT_STEPS
    if len(steps) > 7 or any(abs((b - a) - (want[i + 1] - want[i])) > 1.2 for i, (a, b) in enumerate(zip(steps, steps[1:]))):
        return None
    return len(found) * (1 if found[0][0] == "sharp" else -1)


def crop_png(img: Image.Image, box: Tuple[int, int, int, int], spacing: float,
             spans: Optional[List[Tuple[int, int]]] = None, first: int = 1) -> bytes:
    """Crop, rescale so the staff spacing is TARGET_SPACING px, and encode as PNG.

    With `spans` (from measures()), small red measure numbers first, first+1, ... are drawn at
    the top of the crop over each measure: a visual anchor the model can refer to.
    """
    c = img.crop(box)
    scale = min(TARGET_SPACING / spacing, MAX_CROP_WIDTH / c.width)
    if abs(scale - 1) > 0.05:
        c = c.resize((max(1, round(c.width * scale)), max(1, round(c.height * scale))), Image.LANCZOS)
    c = ImageOps.autocontrast(c, cutoff=0.5)
    if spans:
        c = c.convert("RGB")
        draw = ImageDraw.Draw(c)
        font = ImageFont.load_default(size=20)
        for i, (a, b) in enumerate(spans):
            xa, xb = (a - box[0]) * c.width / (box[2] - box[0]), (b - box[0]) * c.width / (box[2] - box[0])
            draw.text(((xa + xb) / 2, 2), f"m{first + i}", fill=(220, 0, 0), font=font, anchor="mt")
            draw.line((xb, 2, xb, 18), fill=(220, 0, 0), width=2)
    buf = io.BytesIO()
    c.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def whole_page_png(img: Image.Image, max_side: int = 3000) -> bytes:
    c = img.copy()
    c.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    ImageOps.autocontrast(c, cutoff=0.5).save(buf, "PNG", optimize=True)
    return buf.getvalue()


if __name__ == "__main__":
    # synthetic staff (spacing 20 px): clef blob, key signature F# C#, a stemmed note, a barline with a measure
    # number touching its top (a blurred scan), a final barline
    page = Image.new("L", (1400, 300), 255)
    d = ImageDraw.Draw(page)
    for y in range(100, 181, 20):
        d.rectangle((20, y - 1, 1390, y), fill=0)
    d.ellipse((28, 60, 58, 215), fill=0)                                # the clef
    for x, y in ((80, 100), (110, 130)):                                # sharps on F5 and C5
        d.rectangle((x, y - 28, x + 2, y + 26), fill=0)
        d.rectangle((x + 9, y - 30, x + 11, y + 24), fill=0)
        d.polygon(((x - 4, y - 4), (x + 15, y - 10), (x + 15, y - 5), (x - 4, y + 1)), fill=0)
        d.polygon(((x - 4, y + 10), (x + 15, y + 4), (x + 15, y + 9), (x - 4, y + 15)), fill=0)
    head = Image.new("L", (30, 30), 0)
    ImageDraw.Draw(head).ellipse((1, 5, 28, 24), fill=255)
    page.paste(0, (298, 125), head.rotate(20))                          # a tilted quarter-note head on B4 ...
    d.rectangle((324, 70, 326, 138), fill=0)                            # ... with its stem
    d.rectangle((600, 100, 602, 180), fill=0)                           # barline ...
    d.rectangle((600, 78, 602, 99), fill=0)                             # ... with a "1" sitting on it
    d.rectangle((1384, 100, 1390, 180), fill=0)                         # final barline
    sysm = System((0, 0, 1400, 300), 20.0, 1)
    spans, reliable = measures(page, sysm)
    assert reliable and [round(a / 10) for a, _ in spans] == [2, 60], spans
    assert key_signature(page, sysm) == 2, key_signature(page, sysm)
    heads = noteheads(page, sysm)
    assert [(st, dur) for _, st, dur in heads] == [(4, Fraction(1, 4))], heads
    print("omr self-check ok")
