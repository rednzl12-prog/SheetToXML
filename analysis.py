"""Details about a piece: key, range, rhythm, form, harmony, difficulty and practice tips.

analyze() is deterministic, offline and fast (pure arithmetic on the Score). ai_summary()
turns its output into a short "About this piece" note with one text-only LLM call.
Bar numbers follow the MusicXML measure numbers to_musicxml writes (a pickup is bar 0).
"""

import json
import statistics
from collections import Counter
from difflib import SequenceMatcher
from fractions import Fraction
from functools import lru_cache
from itertools import product
from typing import Any, Callable, Dict, List, Optional, Tuple

import abcxml
import llm
import mandolin_optimizer as mando
from abcxml import Bar, Score

F = Fraction
# Krumhansl-Kessler probe-tone profiles, tonic first; the modal ones swap the defining degree
KK_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
KK_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
PROFILES = {"major": KK_MAJOR, "minor": KK_MINOR,
            "mixolydian": KK_MAJOR[:10] + [KK_MAJOR[11], KK_MAJOR[10]],          # flat 7th
            "dorian": KK_MINOR[:8] + [KK_MINOR[9], KK_MINOR[8]] + KK_MINOR[10:]}   # major 6th
FINAL_BONUS = 0.05          # a tune usually ends on its tonic
MODE_MARGIN = 0.03          # a mode must beat plain major/minor by this to be reported
SIG_SLACK = 0.08            # a key that fits the printed signature wins unless another fits this much better
MODE_INDEX = {"major": 0, "dorian": 1, "phrygian": 2, "lydian": 3, "mixolydian": 4, "minor": 5, "locrian": 6}
MAJOR_STEPS = [0, 2, 4, 5, 7, 9, 11]
ACC_WORD = {-2: "double flat", -1: "flat", 0: "natural", 1: "sharp", 2: "double sharp"}
ACC_SIGN = {-2: "bb", -1: "b", 1: "#", 2: "##"}
GDAE = ("G3", "D4", "A4", "E5")
DEFAULT_TEMPO = 100         # quarter notes per minute when the score states none



def _n(n: int, word: str) -> str:
    """3, "note" -> "3 notes"; 1 -> "1 note"."""
    return f"{n} {word}{'' if n == 1 else 's'}"

def _tonic_pc(fifths: int, mode: str) -> int:
    return ((fifths - abcxml.MODE_SHIFT.get(mode, 0)) * 7) % 12


def key_label(fifths: int, mode: str) -> str:
    """(-2, 'major') -> 'B flat major'."""
    tf = fifths - abcxml.MODE_SHIFT.get(mode, 0)
    return "FCGDAEB"[(tf + 1) % 7] + {1: " sharp", -1: " flat"}.get((tf + 1) // 7, "") + " " + mode


def _fifths_for(pc: int, mode: str) -> int:
    """Signature of the simplest spelling of tonic `pc` in `mode`."""
    tf = (pc * 7) % 12
    tf -= 12 if tf > 6 else 0
    f = tf + abcxml.MODE_SHIFT[mode]
    return f + 12 if f < -6 else f - 12 if f > 6 else f


def _pname(p: abcxml.Pitch) -> str:
    return p.step + ACC_SIGN.get(p.alter, "") + str(p.octave)


_decompose = lru_cache(maxsize=256)(abcxml.decompose)


def _type_name(d: Fraction) -> str:
    parts = _decompose(d)
    return parts[-1][1] if parts else min(abcxml.TYPES, key=lambda t: abs(t[1] - d))[0]


def _written(ev: abcxml.Ev) -> Fraction:
    return ev.dur * F(*ev.tuplet) if ev.tuplet else ev.dur


def _beat(meter) -> Fraction:
    n, d = meter[0], meter[1]
    return F(3, d) if n % 3 == 0 and n > 3 and d >= 8 else F(1, d)


def _feel(meter) -> str:
    n, d = meter[0], meter[1]
    compound = n % 3 == 0 and n > 3 and d >= 8
    beats = n // 3 if compound else n
    word = {2: "duple", 3: "triple", 4: "quadruple"}.get(beats)
    return f"{'compound' if compound else 'simple'} {word}" if word else f"irregular ({n}/{d})"


def _where(nums, limit: int = 4) -> str:
    """'bar 4' / 'bars 3-5, 9'."""
    nums = sorted(set(nums))
    return ("bar " if len(nums) == 1 else "bars ") + _ranges(nums, limit)


def _ranges(nums: List[int], limit: int = 6) -> str:
    """[3, 4, 5, 9] -> '3-5, 9'."""
    runs: List[List[int]] = []
    for n in sorted(set(nums)):
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    txt = [f"{a}-{b}" if b > a else str(a) for a, b in runs[:limit]]
    return ", ".join(txt) + (" ..." if len(runs) > limit else "")


# ---------------------------------------------------------------- key

def _detect(hist: List[float], final_pc: Optional[int]) -> List[Tuple[float, float, int, str]]:
    """(score, correlation, tonic pc, mode) for every tonic and profile, best first."""
    out = []
    for mode, prof in PROFILES.items():
        for t in range(12):
            r = statistics.correlation(hist, [prof[(pc - t) % 12] for pc in range(12)])
            out.append((r + FINAL_BONUS * (t == final_pc), r, t, mode))
    return sorted(out, reverse=True)


def _key(score: Score, hist: List[float], final_pc: Optional[int]) -> Dict[str, Any]:
    fifths, mode = score.key
    k: Dict[str, Any] = {"name": key_label(fifths, mode), "fifths": fifths, "mode": mode, "fromSignature": True}
    if sum(hist) == 0 or len(set(hist)) < 2:
        return k
    ranked = _detect(hist, final_pc)

    def pick(pool):
        best = next((x for x in pool if x[3] in ("major", "minor")), None)
        modal = next((x for x in pool if x[3] not in ("major", "minor")), None)
        return modal if modal and (not best or modal[0] > best[0] + MODE_MARGIN) else best

    best = pick(ranked)
    on_sig = pick([x for x in ranked if _fifths_for(x[2], x[3]) == fifths])
    sig_pcs = {(_tonic_pc(fifths, "major") + s) % 12 for s in MAJOR_STEPS}
    outside = sum(h for pc, h in enumerate(hist) if pc not in sig_pcs) / sum(hist)
    if on_sig and outside < 0.05 and on_sig[0] >= best[0] - SIG_SLACK:   # notes that obey the signature back it
        best = on_sig
    _, r, t, m = best
    df = _fifths_for(t, m)
    name = key_label(df, m)
    agrees = t == _tonic_pc(fifths, mode) and df == fifths
    k.update(detected=name, detectedFifths=df, detectedMode=m, confidence=round(r, 2), agrees=agrees)
    if not agrees:
        if df == fifths:
            rel = {("major", "minor"): "the relative minor", ("minor", "major"): "the relative major"}.get(
                (mode, m), "a mode that shares the key signature")
            k["note"] = f"notes suggest {name}, {rel}"
        elif t == _tonic_pc(fifths, mode):
            k["note"] = f"notes suggest {name}: same tonic, but the accidentals contradict the key signature"
        else:
            k["note"] = f"notes suggest {name} - the key signature may be misread, or the piece changes key"
        if m in ("dorian", "mixolydian"):
            k["note"] += f" ({m} is common in folk tunes)"
    if r < 0.6:
        k["note"] = (k.get("note", "") + "; " if k.get("note") else "") + "low confidence: few notes or many accidentals"
    return k


# ---------------------------------------------------------------- harmony

def _triads(fifths: int, mode: str) -> List[Dict[str, Any]]:
    """Diatonic triads (harmonic-minor V in minor keys) with roman numerals and chord names."""
    m = MODE_INDEX.get(mode, 0)
    tonic = _tonic_pc(fifths, mode)
    scale = [(tonic + MAJOR_STEPS[(k + m) % 7] - MAJOR_STEPS[m]) % 12 for k in range(7)]
    tf = fifths - abcxml.MODE_SHIFT.get(mode, 0)
    letter0 = "CDEFGAB".index("FCGDAEB"[(tf + 1) % 7])
    alters = abcxml.key_alters(fifths)
    out = []
    for d in range(7):
        pcs = [scale[d], scale[(d + 2) % 7], scale[(d + 4) % 7]]
        if mode == "minor" and d == 4:
            pcs[1] = (pcs[1] + 1) % 12
        q = {(4, 7): "", (3, 7): "m", (3, 6): "dim", (4, 8): "aug"}.get(((pcs[1] - pcs[0]) % 12, (pcs[2] - pcs[0]) % 12), "")
        letter = "CDEFGAB"[(letter0 + d) % 7]
        roman = ["I", "II", "III", "IV", "V", "VI", "VII"][d]
        roman = (roman if q in ("", "aug") else roman.lower()) + {"dim": "°", "aug": "+"}.get(q, "")
        out.append({"roman": roman, "chord": letter + ACC_SIGN.get(alters.get(letter, 0), "") + q, "pcs": pcs})
    return out


@lru_cache(maxsize=None)
def chord_shape(pcs: Tuple[int, ...], opens: Tuple[int, ...] = (7, 2, 9, 4)) -> str:
    """Easiest fingering (frets low -> high course, from the capo) sounding exactly these pitch classes,
    root first; `opens` = sounding open-course pitch classes (default GDAE)."""
    cands = [[f for f in range(8) if (o + f) % 12 in pcs] for o in opens]
    best = None
    for frets in product(*cands):
        notes = [(o + f) % 12 for o, f in zip(opens, frets)]
        fretted = [f for f in frets if f]
        span = max(fretted) - min(fretted) if fretted else 0
        if set(notes) != set(pcs) or span > 3:
            continue
        bass = 0 if notes[0] == pcs[0] else 1.5 if notes[0] == pcs[-1] else 3
        cost = 2 * span + max(frets) + 0.5 * len(fretted) + bass
        if best is None or cost < best[0]:
            best = (cost, frets)
    return "-".join(map(str, best[1])) if best else ""


def _bar_chord(bar: Bar, start: Fraction, beat: Fraction, triads) -> Optional[Dict[str, Any]]:
    """Most likely diatonic triad(s): duration-weighted pitch classes, strong beats counted more.
    Four-beat bars get one chord per half ('D A')."""
    # ponytail: melody-only template match, at most two chords per bar; real harmony needs the
    # accompaniment or a chord HMM over the whole piece
    given = [e.chord_symbol for e in bar.events if e.chord_symbol]
    if given:
        return {"chord": " ".join(given), "given": True}
    halves = 2 if bar.expected == 4 * beat else 1
    seg = bar.expected / halves
    ws, pos = [[0.0] * 12 for _ in range(halves)], start
    for ev in bar.events:
        boost = 2 if pos % seg == 0 else 1.5 if pos % beat == 0 else 1
        for p in ev.pitches:
            ws[min(halves - 1, int(pos // seg))][p.midi % 12] += float(ev.dur) * boost
        pos += ev.dur
    seventh = triads[3]["pcs"][0]                 # the 7th of V: V7 also explains the leading-tone triad
    picked: List[Tuple[str, str]] = []
    for w in ws:
        if not any(w):
            continue
        total, best, best_s = sum(w), None, -1e9
        for d in (0, 4, 3, 5, 1, 2):              # I V IV vi ii iii win ties, in that order; no diminished
            pcs = set(triads[d]["pcs"]) | ({seventh} if d == 4 else set())
            inside = sum(w[pc] for pc in pcs)
            s = inside - 0.5 * (total - inside)
            if s > best_s + 1e-9 and not triads[d]["chord"].endswith("dim"):
                best, best_s = d, s
        if best is None:
            continue
        t = triads[best]
        name = (t["chord"] + "7", t["roman"] + "7") if best == 4 and w[seventh] else (t["chord"], t["roman"])
        if not picked or picked[-1][0].rstrip("7") != name[0].rstrip("7"):
            picked.append(name)
    return {"chord": " ".join(c for c, _ in picked), "roman": " ".join(r for _, r in picked)} if picked else None


# ---------------------------------------------------------------- form

def _endings(bars: List[Bar]) -> List[Optional[set]]:
    out, cur = [], None
    for b in bars:
        if b.left == "repeat":                    # an ending never runs past a start-repeat
            cur = None
        if b.ending:
            cur = {int(x) for x in b.ending.split(",") if x.strip().isdigit()}
        out.append(cur)
        if b.ending_stop:
            cur = None
    return out


def play_order(bars: List[Bar]) -> List[int]:
    """Bar indices in performance order, following repeats and 1st/2nd endings."""
    endings = _endings(bars)
    order, i, start, rnd, jumped = [], 0, 0, 1, set()
    while i < len(bars) and len(order) < 4 * len(bars):
        b = bars[i]
        if b.left == "repeat" and i != start:
            start, rnd = i, 1
        if endings[i] and rnd not in endings[i]:
            if b.right == "repeat" and i in jumped:
                start = i + 1
            i += 1
            continue
        order.append(i)
        if b.right == "repeat" and i not in jumped:
            jumped.add(i)
            rnd += 1
            i = start
            continue
        if b.right == "repeat":
            start, rnd = i + 1, 1
        i += 1
    return order


def _bar_sig(bar: Bar) -> tuple:
    return tuple((tuple(sorted(p.midi for p in e.pitches)), e.dur) for e in bar.events)


def _unit_sim(a: List[tuple], b: List[tuple]) -> float:
    n = max(len(a), len(b))
    return sum(SequenceMatcher(None, x, y, autojunk=False).ratio() for x, y in zip(a, b)) / n if n else 0


def _form(bars: List[Bar], num: Callable[[int], int]) -> Dict[str, Any]:
    n = len(bars)
    endings = _endings(bars)
    later = [bool(e) and min(e) > 1 for e in endings]          # 2nd+ endings ride along with the 1st
    cut = [False] * n
    for i, b in enumerate(bars[:-1]):
        nxt = bars[i + 1]
        cut[i] = bool(nxt.left) or b.right in ("double", "final") or \
            ((b.right == "repeat" or b.ending_stop) and not nxt.ending)
    sections, cur = [], []
    for i in range(n):
        cur.append(i)
        if cut[i] or i == n - 1:
            sections.append(cur)
            cur = []
    units: List[List[int]] = []
    sizes = []
    for sec in sections:
        length = round(float(sum((bars[i].total / bars[i].expected for i in sec if not later[i]), F(0))))
        size = 8 if length >= 8 and length % 8 == 0 else 4 if length >= 4 and length % 4 == 0 else \
            max(1, length) if length <= 8 else 4
        acc, chunk = F(0), []
        for i in sec:
            if later[i] or not chunk or acc < size:
                chunk.append(i)
            else:
                units.append(chunk)
                sizes.append(size)
                chunk, acc = [i], F(0)
            if not later[i]:
                acc += bars[i].total / bars[i].expected
        units.append(chunk)
        sizes.append(size)
    # ponytail: bar-by-bar similarity at the same offsets; transposed sequences and shifted repeats go
    # unnoticed - compare interval/rhythm signatures with an alignment if forms need to be finer
    sigs = [_bar_sig(b) for b in bars]
    labels: List[str] = []
    reps: List[Tuple[str, List[tuple]]] = []
    for u in units:
        us = [sigs[i] for i in u if not later[i]]
        best = max(((_unit_sim(us, rs), lab) for lab, rs in reps), default=(0.0, ""))
        if best[0] >= 0.999:
            labels.append(best[1])
        elif best[0] >= 0.6:
            labels.append(best[1] + "'")
        else:
            lab = chr(ord("A") + len(reps)) if len(reps) < 26 else f"S{len(reps) + 1}"
            reps.append((lab, us))
            labels.append(lab)
    form: Dict[str, Any] = {
        "repeats": sum(b.right == "repeat" for b in bars),
        "endings": sum(bool(b.ending) for b in bars),
        "playedBars": len(play_order(bars)),
        "sections": [{"label": lab, "bars": f"{num(u[0])}-{num(u[-1])}" if len(u) > 1 else str(num(u[0]))}
                     for lab, u in zip(labels, units)],
        "phraseLength": Counter(sizes).most_common(1)[0][0] if sizes else 0}
    form["pattern"] = " ".join(f"{s['label']} ({s['bars']})" for s in form["sections"])
    distinct = len(reps)
    form["shape"] = "through-composed" if distinct == len(units) else         f"{_n(distinct, 'distinct section')}, some repeated or varied"
    # longest passage of 2+ bars heard again later (exact, non-overlapping); empty bars never match
    ids: Dict[tuple, int] = {}
    sid = [ids.setdefault(s, len(ids)) if any(e.pitches for e in b.events) else -1 - k   # O(n^2) int compares,
           for k, (s, b) in enumerate(zip(sigs, bars))]                                  # not Fraction tuples
    best_len, best_at = 0, (0, 0)
    run = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        new = [0] * (n + 1)
        for j in range(n - 1, i, -1):
            if sid[i] == sid[j]:
                new[j] = min(run[j + 1] + 1, j - i)
                if new[j] > best_len:
                    best_len, best_at = new[j], (i, j)
        run = new
    if best_len >= 2:
        i, j = best_at
        form["longestRepeat"] = {"length": best_len, "bars": f"{num(i)}-{num(i + best_len - 1)}",
                                 "againAt": f"{num(j)}-{num(j + best_len - 1)}"}
    return form


# ---------------------------------------------------------------- analysis

def _labels(names: List[str]) -> List[str]:
    """Course labels low -> high: letters when unique (G D A E), else with octaves (G3 D4 G4 D5)."""
    letters = [n.rstrip("-0123456789") for n in names]
    return letters if len(set(letters)) == len(letters) else list(names)


def _mandolin(stats: dict) -> Dict[str, Any]:
    total = stats.get("totalNotes") or 0
    su = stats.get("stringUsage") or {}
    labels = _labels(stats.get("tuning") or GDAE)
    n = len(labels)
    pct = lambda x: round(100 * x / total) if total else 0
    m = {"totalNotes": total, "highestFret": stats.get("highestFret", 0),
         "openStringsPercent": pct(stats.get("openStringsCount", 0)),
         "firstPositionPercent": pct(stats.get("firstPositionCount", 0)),
         "stringUsage": {labels[n - s]: su.get(s, su.get(str(s), 0)) for s in range(n, 0, -1)},   # string 1 = top
         "outOfRange": stats.get("outOfRange", 0), "transposedOctaves": stats.get("transposedOctaves", 0),
         "reviewBars": stats.get("reviewBars", [])}
    for k in ("arrangement", "instrument", "tuning", "capo", "style", "lowestFret", "positionsUsed",
              "positionShifts", "shiftBars", "unplayable"):
        if k in stats:
            m[k] = stats[k]
    return m


def analyze(score: Score, stats: Optional[dict] = None) -> Dict[str, Any]:
    """JSON-serialisable facts about the piece; `stats` is to_musicxml's second result."""
    bars = score.bars
    pickup = len(bars) > 1 and bars[0].total < bars[0].expected
    num = (lambda i: i) if pickup else (lambda i: i + 1)
    meter = score.meter
    tempo = score.tempo or next((b.tempo for b in bars if b.tempo), None)
    d: Dict[str, Any] = {"title": score.title, "composer": score.composer, "measures": len(bars) - pickup,
                         "pickup": pickup, "meter": f"{meter[0]}/{meter[1]}", "timeSignatureFeel": _feel(meter),
                         "tempo": round(tempo) if tempo else None}
    play = play_order(bars)
    whole = sum((bars[i].total for i in play), F(0))
    bpm = tempo or DEFAULT_TEMPO
    d["durationSeconds"] = round(float(whole) * 4 * 60 / bpm)
    d["durationIncludesRepeats"] = len(play) != len(bars)
    if not tempo:
        d["tempoAssumed"] = DEFAULT_TEMPO

    # one pass over the events
    hist = [0.0] * 12
    key, cur_meter = score.key, meter
    notes, rests, tied, dotted, tuplets, syncop, chords = 0, 0, 0, 0, 0, 0, 0
    lo = hi = None
    final_pc = None
    acc: Dict[str, List[int]] = {}
    acc_notes = 0
    short_bars: Dict[int, List[Fraction]] = {}
    tuplet_bars, chord_bars, sync_bars = set(), set(), set()
    leap = (0, 0, "", "")
    prev_top, prev_tie = None, False
    bar_starts, beats_total = [], F(0)
    for i, b in enumerate(bars):
        key, cur_meter = b.key or key, b.meter or cur_meter
        beat = _beat(cur_meter)
        alters = abcxml.key_alters(key[0])
        pos = b.expected - b.total if i == 0 and pickup else F(0)
        bar_starts.append(pos)
        beats_total += b.total / beat
        for ev in b.events:
            if ev.dur <= 0:                       # zero-length note (grace-like, from MusicXML/AI): no rhythm
                continue
            if not ev.pitches:
                rests += 1
                prev_tie = False
                pos += ev.dur
                continue
            for p in ev.pitches:
                hist[p.midi % 12] += float(ev.dur)
                lo = p if lo is None or p.midi < lo.midi else lo
                hi = p if hi is None or p.midi > hi.midi else hi
            final_pc = min(p.midi for p in ev.pitches) % 12
            if not prev_tie:                      # a tied continuation is not a new note
                notes += 1
                if len(ev.pitches) > 1:
                    chords += 1
                    chord_bars.add(num(i))
                parts = _decompose(_written(ev))
                dotted += len(parts) == 1 and parts[0][2] > 0
                if ev.tuplet:
                    tuplets += 1
                    tuplet_bars.add(num(i))
                off = pos % beat
                if off and (off + ev.dur > beat or ev.tie):
                    syncop += 1
                    sync_bars.add(num(i))
                short_bars.setdefault(num(i), []).append(ev.dur)
                for p in ev.pitches:
                    if p.alter != alters.get(p.step, 0):
                        acc_notes += 1
                        acc.setdefault(f"{p.step} {ACC_WORD.get(p.alter, str(p.alter))}", []).append(num(i))
                top = max(ev.pitches, key=lambda q: q.midi)
                if prev_top is not None and abs(top.midi - prev_top.midi) > leap[0]:
                    leap = (abs(top.midi - prev_top.midi), num(i), _pname(prev_top), _pname(top))
                prev_top = top
            tied += ev.tie
            prev_tie = ev.tie
            pos += ev.dur

    d["key"] = _key(score, hist, final_pc)
    changes = [{"bar": num(i), "key": key_label(*b.key)} for i, b in enumerate(bars) if b.key]
    if changes:
        d["key"]["changes"] = changes
    meters = [{"bar": num(i), "meter": f"{b.meter[0]}/{b.meter[1]}"} for i, b in enumerate(bars) if b.meter]
    if meters:
        d["meterChanges"] = meters
    d["noteCount"], d["restCount"] = notes, rests
    if not notes:
        d["form"] = _form(bars, num) if bars else {}
        return d

    durs = [x for v in short_bars.values() for x in v]
    shortest = min(durs)
    d["range"] = {"lowest": _pname(lo), "highest": _pname(hi), "semitones": hi.midi - lo.midi}
    d["shortestNote"] = _type_name(shortest)
    d["rhythmicDensity"] = round(notes / float(beats_total), 2) if beats_total else 0
    d["rhythm"] = {"dottedNotes": dotted, "tupletNotes": tuplets, "tiedNotes": tied, "syncopatedNotes": syncop,
                   "syncopation": "none" if not syncop else "some" if syncop < 0.1 * notes else "frequent"}
    if chords:
        d["rhythm"]["doubleStops"] = chords
    share = acc_notes / notes
    d["accidentals"] = {"count": acc_notes, "notes": sorted(acc, key=lambda k: -len(acc[k])),
                        "chromaticism": "low" if share < 0.03 else "medium" if share < 0.1 else "high"}
    d["form"] = _form(bars, num)

    # harmony in the signature key, or in its relative when the notes clearly say so
    k = d["key"]
    hf, hm = score.key
    if not k.get("agrees", True) and k.get("detectedFifths") == hf and k.get("confidence", 0) >= 0.75:
        hm = k["detectedMode"]
    if hm not in MODE_INDEX:
        hm = "major"
    triads = _triads(hf, hm)
    tuning, capo = (stats or {}).get("tuning") or GDAE, (stats or {}).get("capo") or 0
    try:
        opens = tuple((mando.parse_note(t) + int(capo)) % 12 for t in tuning)
    except (ValueError, TypeError):
        tuning, capo, opens = GDAE, 0, (7, 2, 9, 4)
    per_bar, cur_meter = [], meter
    for i, b in enumerate(bars):
        cur_meter = b.meter or cur_meter
        c = _bar_chord(b, bar_starts[i], _beat(cur_meter), triads)
        if c:
            per_bar.append({"bar": num(i), **c})
    d["harmony"] = {
        "key": key_label(hf, hm),
        "diatonicChords": [{"roman": t["roman"], "chord": t["chord"]} for t in triads],
        "chordsPerBar": per_bar,
        "chordsNote": "suggested from the melody's strong-beat notes: one chord per bar, two in four-beat bars",
        "mandolinChords": [{"roman": triads[x]["roman"], "chord": triads[x]["chord"],
                            "frets": chord_shape(tuple(triads[x]["pcs"]), opens)} for x in (0, 3, 4, 5)],
        "chordTuning": " ".join(_labels(tuning)) + (f", capo {capo}" if capo else "")}
    if stats:
        d["mandolin"] = _mandolin(stats)

    # difficulty
    pts, reasons = 0.0, []
    common_short = min((x for x in set(durs) if durs.count(x) >= 4), default=min(durs))
    peak = bpm / 60 * float(F(1, 4) / common_short)         # notes per second in the fastest common value
    avg = notes / (float(whole) * 4 * 60 / bpm) if whole else 0
    sp = 0 if peak < 4 else 1 if peak < 6 else 2 if peak < 8 else 3 if peak < 11 else 4
    if sp:
        pts += sp
        reasons.append(f"fastest runs about {peak:.0f} notes per second ({_type_name(common_short)} notes at "
                       f"{round(bpm)} bpm{' assumed' if not tempo else ''})")
    if avg > 3:
        pts += 1
        reasons.append(f"busy throughout ({avg:.1f} notes per second on average)")
    if shortest <= F(1, 32):
        pts += 1
        reasons.append(f"{_type_name(shortest)} notes to read")
    semis = hi.midi - lo.midi
    if semis > 24:
        pts += 1 + (semis > 31)
        reasons.append(f"wide range ({semis} semitones, {_pname(lo)}-{_pname(hi)})")
    if share >= 0.03:
        pts += 0.5 if share < 0.1 else 1.5
        reasons.append(f"{acc_notes} accidentals")
    if syncop >= 0.1 * notes:
        pts += 0.5
        reasons.append("frequent syncopation")
    if tuplets >= 3:
        pts += 0.5
        reasons.append("tuplets")
    if dotted >= 0.25 * notes:
        pts += 0.5
        reasons.append("many dotted rhythms")
    if chords >= 0.1 * notes:
        pts += 1
        reasons.append(f"{chords} double stops / chords")
    if stats:
        m = d["mandolin"]
        if m["highestFret"] > 7:
            pts += 1 + (m["highestFret"] > 12)
            reasons.append(f"up to fret {m['highestFret']}")
        if (m.get("positionShifts") or 0) > 6:
            pts += 1
            reasons.append(f"{m['positionShifts']} position shifts")
        if m["openStringsPercent"] < 15:
            pts += 0.5
            reasons.append(f"few open strings ({m['openStringsPercent']}%)")
        bad = m.get("unplayable") or m["outOfRange"]
        if bad:
            pts += 1
            reasons.append(f"{_n(bad, 'note')} outside the mandolin's range")
    score10 = max(1, min(10, round(1 + pts)))
    d["difficulty"] = {"level": "beginner" if score10 <= 3 else "intermediate" if score10 <= 6 else "advanced",
                       "score": score10, "reasons": reasons or ["moderate speed, simple rhythms, comfortable range"]}
    d["tips"] = _tips(d, short_bars, acc, tuplet_bars, chord_bars, leap, bpm, tempo)
    return d


def _tips(d, short_bars, acc, tuplet_bars, chord_bars, leap, bpm, tempo) -> List[str]:
    tips = []
    fast = sorted(n for n, ds in short_bars.items() if sum(x <= F(1, 16) for x in ds) >= 4)
    if fast:
        runs: List[List[int]] = []
        for n in fast:
            if runs and n == runs[-1][-1] + 1:
                runs[-1].append(n)
            else:
                runs.append([n])
        for r in sorted(runs, key=len, reverse=True)[:2]:
            kinds = sorted({x for n in r for x in short_bars[n] if x <= F(1, 16)}, reverse=True)
            kind = "/".join(dict.fromkeys(_type_name(x) for x in kinds))
            tips.append(f"{_where(r).capitalize()}: fast {kind}-note runs - practise slowly with a metronome, "
                        "strict down-up picking")
    for name in d["accidentals"]["notes"][:2]:
        tips.append(f"Watch the {name} ({_where(acc[name])}) - it is outside the key signature")
    m = d.get("mandolin")
    if m and m["highestFret"] > 7:
        where = f" in {_where(m['shiftBars'])}" if m.get("shiftBars") else ""
        pos = f" (positions {', '.join(map(str, m['positionsUsed']))})" if m.get("positionsUsed") else ""
        tips.append(f"Goes up to fret {m['highestFret']}{pos} - plan the shift{where} and keep the hand relaxed")
    if leap[0] >= 12:
        size = {12: "an octave", 24: "two octaves"}.get(leap[0], f"{leap[0]} semitones")
        tips.append(f"Bar {leap[1]} jumps {size} ({leap[2]} to {leap[3]}) - "
                    "practise that string crossing on its own")
    f = d["form"]
    if f.get("repeats"):
        tips.append(f"Follow the {_n(f['repeats'], 'repeat sign')}" +
                    (" and take the 1st/2nd endings" if f.get("endings") else "") +
                    f" - {f['playedBars']} bars are played in all")
    if tuplet_bars:
        tips.append(f"Tuplets in {_where(tuplet_bars)} - keep the notes of each group even")
    if chord_bars:
        tips.append(f"Double stops in {_where(chord_bars)} - fret both notes before you pick")
    if d["rhythm"]["syncopation"] == "frequent":
        tips.append("Many notes start off the beat - count aloud and tap the beat with your foot")
    if d["timeSignatureFeel"].startswith("compound"):
        tips.append(f"Feel {d['meter']} as {d['timeSignatureFeel'].split()[1]} dotted-quarter beats; "
                    "pick down-up-down on each group of three")
    if m and m["openStringsPercent"] >= 30:
        tips.append(f"{m['openStringsPercent']}% of the notes are open strings - let them ring, but mute any that clash")
    tips.append(f"Start around {round(bpm * 0.6)} bpm and build up to {round(bpm)}" +
                ("" if tempo else " (no tempo marked - pick one that suits the style)"))
    if f.get("phraseLength"):
        tips.append(f"Learn it in {f['phraseLength']}-bar phrases, then join them")
    return tips[:6]


# ---------------------------------------------------------------- AI note

SUMMARY_SYSTEM = ("You write short, friendly notes for mandolin players. Use the given analysis as your facts. "
                  "Add background about the work, composer or style ONLY if you are certain it is true of this "
                  "exact piece; otherwise leave background out. Never invent dates, history or attributions.")


def ai_summary(details: dict, *, provider: str, models: List[str], key: str, base_url: str = "",
               cancel: Optional[Callable[[], bool]] = None) -> str:
    """Markdown 'About this piece' note (120-220 words) from analyze()'s output; raises llm.LLMError."""
    facts = {k: v for k, v in details.items() if k != "harmony"}
    if "harmony" in details:
        facts["harmony"] = {k: v for k, v in details["harmony"].items() if k != "chordsPerBar"}
    prompt = ("Write an 'About this piece' note in Markdown, 120-200 words, in three short paragraphs. Start "
              "directly with the text: no heading, no title line, no preamble.\n"
              "1. Background: one or two sentences, only if you are sure of them; a generic or placeholder "
              "title means skip this paragraph. Do not compare this score with any original version.\n"
              "2. What to listen for: form, key, rhythm, character - from the analysis.\n"
              "3. Mandolin advice from the analysis: frets, open strings, and the bars named in tips.\n"
              "The score is one melody line; never mention a bass line, chords underneath or other parts. "
              "Every number, bar, fret or position you mention must appear in the analysis - do not name "
              "positions unless 'positionsUsed' is given. Bold at most three short phrases.\n\n"
              "Analysis (JSON):\n" + json.dumps(facts, ensure_ascii=False))
    text, _ = llm.generate_with_fallback(provider, models, key, SUMMARY_SYSTEM, [prompt], base_url=base_url,
                                         max_tokens=2048, thinking=llm.PROVIDERS.get(provider, {}).get("thinking"),
                                         timeout=120, cancel=cancel)
    lines = text.strip().splitlines()
    if lines and "about this piece" in lines[0].lower():     # the page already has this heading
        lines = lines[1:]
    return "\n".join(lines).strip()


if __name__ == "__main__":
    import os
    import sys
    import time
    from pathlib import Path

    here = Path(__file__).resolve().parent
    corpus = Path(os.environ.get("SHEETXML_CORPUS", r"C:\Users\rednz\AppData\Local\Temp\claude"
                                 r"\C--Users-rednz-OneDrive-Desktop-sheettoxml\406f3291-3db7-46b4-8cf7-a74d98bfd264"
                                 r"\scratchpad\corpus"))
    expect = {"jig_G": "G major", "waltz_F": "F major", "polka_Am": "A minor", "reel_Bb": "B flat major",
              "march_D": "D major", "canon": "D major", "maid": "D major"}
    results = {}
    for name, want in expect.items():
        p = corpus / f"{name}.abc"
        if not p.exists():
            continue
        sc = abcxml.parse_abc(p.read_text("utf-8"))
        a = analyze(sc, abcxml.to_musicxml(sc)[1])
        results[name] = a
        assert a["key"]["name"] == want and a["key"]["detected"] == want, (name, a["key"])
        assert a["form"]["sections"] and a["tips"] and 3 <= len(a["tips"]) <= 6, name
        json.dumps(a)
        print(f"{name:9} {a['key']['detected']:13} r={a['key']['confidence']} {a['difficulty']['level']:12} "
              f"{a['difficulty']['score']}/10  {a['form']['pattern']}")
    if results:
        assert results["canon"]["difficulty"]["score"] > results["waltz_F"]["difficulty"]["score"]
        assert results["maid"]["form"]["repeats"] == 2 and results["maid"]["form"]["endings"] == 4
        assert results["maid"]["form"]["playedBars"] > results["maid"]["measures"] + 10
        assert results["jig_G"]["pickup"] and results["jig_G"]["timeSignatureFeel"] == "compound duple"
        assert results["polka_Am"]["accidentals"]["notes"][0] == "G sharp"
        assert results["polka_Am"]["harmony"]["mandolinChords"][2]["chord"] == "E"   # harmonic-minor V
    else:
        print("corpus not found - skipping corpus checks")

    # modes, form and repeats on hand-made tunes
    dorian = abcxml.parse_abc("M:4/4\nL:1/8\nK:G\n|:A2 EA cAGB|A2 EA B2 dB|A2 EA cAGB|dBAG FDEF|\n"
                              "A2 EA cABc|dcBA B2 dB|e2 dB dBAG|EFGB A4:|\n")
    k = analyze(dorian)["key"]
    assert k["detected"] == "A dorian" and not k["agrees"] and "shares the key signature" in k["note"], k
    mixo = abcxml.parse_abc("M:4/4\nL:1/8\nK:G\n|:GABc dBGB|=FGAB c2 BA|GABc dBGB|AF=FA G4|\n"
                            "dBGB =f2 ed|cBAG =F2 FA|GABc dBGd|=FAGF G4:|\n")
    k = analyze(mixo)["key"]
    assert k["detected"] == "G mixolydian" and "same tonic" in k["note"], k
    k = analyze(abcxml.parse_abc("M:4/4\nL:1/8\nK:G\n|:A2 EA BAGB|A2 EA B2 dB:|\n"))["key"]   # no C at all
    assert k["detectedFifths"] == 1, k                          # stays on the signature's notes
    aa = analyze(abcxml.parse_abc("M:4/4\nL:1/4\nK:C\nCDEF|GABc|cBAG|FEDC|CDEF|GABc|cBAG|FEDC|"
                                  "EEEE|GGGG|AAAA|C4|\n"))
    assert aa["form"]["pattern"].startswith("A (1-4) A (5-8) B") or aa["form"]["pattern"].startswith("A (1-8)"), aa["form"]
    assert aa["form"]["longestRepeat"]["length"] == 4, aa["form"]
    rep = abcxml.parse_abc("M:2/4\nL:1/4\nK:D\n|:DE|[1 FG:|[2 Ad|]\n|:dc|BA:|\n")
    assert [i + 1 for i in play_order(rep.bars)] == [1, 2, 1, 3, 4, 5, 4, 5], play_order(rep.bars)
    assert chord_shape((7, 11, 2)) == "0-0-2-3" and chord_shape((2, 6, 9)) == "2-0-0-2", (chord_shape((7, 11, 2)),)
    assert chord_shape((7, 11, 2), (7, 2, 7, 2)) == "0-0-4-0"                       # G on GDGD
    assert list(_mandolin({"tuning": ["G3", "D4", "G4", "D5"], "stringUsage": {1: 5, 4: 1}, "totalNotes": 6})
                ["stringUsage"].items()) == [("G3", 1), ("D4", 0), ("G4", 0), ("D5", 5)]
    assert key_label(-2, "major") == "B flat major" and key_label(3, "minor") == "F sharp minor"
    assert analyze(abcxml.Score())["measures"] == 0
    assert analyze(abcxml.parse_abc("M:4/4\nL:1/4\nK:G\nG0 A2B2|\n"))["shortestNote"] == "half"   # zero-length note

    # the Canon (exact vector-PDF reading) - speed and one full JSON for a reviewer
    import vectorpdf
    canon = vectorpdf.transcribe((here / "resources" / "Pachelbel_Canon.pdf").read_bytes())
    stats = abcxml.to_musicxml(canon)[1]
    t0 = time.perf_counter()
    details = analyze(canon, stats)
    ms = (time.perf_counter() - t0) * 1000
    assert details["key"]["name"] == "D major" and details["key"]["agrees"], details["key"]
    print(f"Canon PDF: {len(canon.bars)} bars analysed in {ms:.1f} ms")
    print(json.dumps(details, indent=1, ensure_ascii=False))
    assert ms < 100, ms

    if "--ai" in sys.argv:         # one live call: python -B analysis.py --ai
        env = dict(line.split("=", 1) for line in (here / ".env").read_text("utf-8").splitlines() if "=" in line)
        qkey = env.get("QWEN_API_KEY", "").strip().strip('"')
        print(ai_summary(details, provider="qwen", models=["qwen3.8-flash"], key=qkey))
    print("analysis self-check OK")
