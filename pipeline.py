"""Guided transcription: code does layout and arithmetic, a vision model reads one staff system at a time,
optionally together with an offline OMR reading (Audiveris) of the same score.

1. Layout (omr): pages -> systems -> measures from barlines; each crop gets red measure numbers.
   omr.noteheads also reads every filled note (staff position + beams/flags/dot) without any model.
2. Header: one model call (clef, the key signature's accidentals as a list, meter, title...) cross-checked
   against the key signature counted in pixels, the OMR's header and the bar length the code reads; a
   disagreement gets one focused re-ask, then the majority wins. Decisions land in score.info["header"].
3. With OMR hints, its measures are aligned to the code's (by system breaks, else by notehead pitches).
   A measure is CONFIRMED when the OMR bar has the right length and matches the code's noteheads; a system
   whose measures are all confirmed needs no model call at all.
4. Otherwise one call per system with the key's sharps/flats spelled out, the exact measure count, an ABC
   cheat-sheet and both machine readings per measure. Measures the code reads to exactly the meter override
   the model; elsewhere the code's pitches replace the model's when the note counts agree. The result is
   checked in code (bar count, bar lengths, notehead count, range); up to two repair calls with feedback.
5. Each measure is then decided by vote among the model reading(s), the OMR bar and the code's noteheads
   (only readings of the right length count; the model wins ties).
6. Systems are merged into one abcxml.Score; provenance in score.info, unreadable parts in Score.warnings.
"""

import hashlib
import json
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import abcxml
import llm
import omr

WORKERS = 4
REPAIRS = 2
THINKING = {p: v["thinking"] or "" for p, v in llm.PROVIDERS.items()}   # gemini low, qwen/deepseek off, ...
CACHE_DIR: Optional[Path] = None        # dev only: the CLI caches model answers here

CLEFS = {   # ABC letters by staff position, a sane MIDI range, diatonic index (octave * 7 + step) of the bottom line
    "treble": ("lines E G B d f; spaces F A c e; below the staff: D (just under), C (1 ledger line), "
               "B, (under it), A, (2 ledger lines); above: g (just over), a (1 ledger line), b, c' (2 ledger lines), d', e' (3)",
               (53, 96), 30),
    "bass": ("lines G,, B,, D, F, A,; spaces A,, C, E, G,; below: F,, E,, (1 ledger line); "
             "above: B, (just over), C (1 ledger line), D, E (2 ledger lines)", (29, 69), 18),
    "alto": ("lines F, A, C E G; spaces G, B, D F; below: E, D, (1 ledger line); above: A, B (1 ledger line)", (41, 81), 24),
}


def letter(diatonic: int) -> str:
    """Diatonic index (octave * 7 + step) -> ABC letter: 30 -> E, 35 -> c, 27 -> B,"""
    o, d = divmod(diatonic, 7)
    return "CDEFGAB"[d] + "," * (4 - o) if o <= 4 else "cdefgab"[d] + "'" * (o - 5)


SYSTEM_PROMPT = """You transcribe printed sheet music into ABC notation, one staff system at a time, exactly as printed.

ABC rules (L:1/8, so a plain letter is an EIGHTH note):
- Pitch letters for the {clef} clef: {positions}. Lowercase = the octave above uppercase; ' raises and , lowers an octave.
- Lengths: whole = 8, dotted half = 6, half = 4, dotted quarter = 3, quarter = 2, dotted eighth = 3/2,
  eighth = nothing, dotted 16th = 3/4, 16th = / , 32nd = // .  Examples: d2 = quarter d, d/ = 16th, d// = 32nd, d3/2 = dotted eighth.
- Count beams on each stem: 1 beam (or flag) = eighth, 2 = 16th, 3 = 32nd. No beam: filled head = quarter, hollow head = half, hollow without stem = whole.
- A dot after the note head multiplies by 1.5. Ties: d2-d (the - comes right after the first note).
- Triplets: (3abc - only where a 3 sits on a bracket or at the middle of a beam. A small bold digit right above or below
  a single note is a FINGERING, never a triplet.
- Rests: z with a length (z = eighth rest, z2 = quarter rest, z/ = 16th rest, z4 = half rest). A rest filling a whole measure: Z.
- Accidentals: write ^ (sharp), _ (flat), = (natural) ONLY where one is printed in front of a note inside that measure.
  The key signature is applied automatically - never write it on the notes.
- Chords (notes stacked on one stem): [ceg]2.
- Ignore fingering digits, string/bowing marks, dynamics, hairpins, slurs, articulations, ornaments, text, rehearsal boxes and printed measure numbers.
- Put a space between beats so each beat's group can be checked.
- If the key or time signature changes, start that measure with [K:...] or [M:...].

Output: one fenced code block and nothing else. One line per measure, starting with its red label, ending with |, e.g.
```
m7: D F A G F D F E |
m8: D B, D A G B A G |
```"""

HEADER_Q = ('Read the score header and the first staff. Answer with JSON only: {"title": "", "composer": "", '
            '"clef": "treble|bass|alto", "keySignature": ["the sharps or flats printed right after the clef, left to '
            'right, e.g. F#, C# - [] if there are none"], "key": "e.g. D major or B minor", '
            '"meter": "e.g. 4/4, 6/8, C, C|", "tempo": "quarter-note bpm as a number, or empty"}')
REASK_Q = ('Look only at the start of this staff: the clef, the key signature (the sharps or flats right after the '
           'clef, before the time signature) and the time signature. Count each accidental separately. Answer with '
           'JSON only: {"clef": "treble|bass|alto", "keySignature": ["F#", "C#"] or [], "meter": "e.g. 3/4"}')
HYBRID_NOTE = ("\nTwo machine readings are given per measure. 'offline OMR' is a separate music-recognition program "
               "(written like your ABC, key signature applied); 'code' reads only the filled noteheads (staff position "
               "before the key signature, length from beams/flags/dots; it cannot see rests, half/whole notes, "
               "accidentals, ties or tuplets). CONFIRMED = both agree and the measure adds up: copy it unless the image "
               "clearly shows otherwise. CHECK = they disagree or something is missing: read that measure carefully "
               "from the image - either reading may be wrong.\n")


# ------------------------------------------------------------------ model calls

def _ask(ctx: dict, system: str, parts: list) -> Tuple[str, str]:
    """generate_with_fallback with an optional on-disk answer cache (dev runs); counts calls per model."""
    h = None
    if CACHE_DIR:
        sig = hashlib.sha256()
        for p in [ctx["provider"], ctx["models"][0], ctx["thinking"], llm.IMAGE_RESOLUTION, system] + parts:
            sig.update(p if isinstance(p, bytes) else str(p).encode())
        h = CACHE_DIR / (sig.hexdigest()[:24] + ".json")
    if h and h.exists():
        d = json.loads(h.read_text(encoding="utf-8"))
        text, model = d["text"], d["model"]
    else:
        text, model = llm.generate_with_fallback(ctx["provider"], ctx["models"], ctx["key_"], system, parts,
                                                 notify=ctx["notify"], base_url=ctx["base_url"],
                                                 thinking=ctx["thinking"] or None, cancel=ctx["cancel"])
        if h:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            h.write_text(json.dumps({"text": text, "model": model}), encoding="utf-8")
    with ctx["lock"]:
        ctx["used"][model] += 1
    return text, model


def _json(ctx: dict, png: bytes, question: str) -> Tuple[dict, str]:
    """A JSON answer about an image; ({}, reason) when unusable. Auth/bad request/cancel propagate."""
    try:
        text, _ = _ask(ctx, "You read sheet music headers.", [png, question])
        m = re.search(r"\{.*\}", text, re.S)
        return (json.loads(m.group()), "") if m else ({}, "no JSON in the answer")
    except (ValueError, llm.LLMError) as e:
        if isinstance(e, llm.LLMError) and e.kind in ("auth", "bad_request", "cancelled"):
            raise
        return {}, str(e)


# ------------------------------------------------------------------ header evidence

def fifths_of(accs: Any) -> Optional[int]:
    """['F#', 'C#'] -> 2, ['Bb'] -> -1, [] -> 0; None unless they are the first n of F C G D A E B / B E A D G C F."""
    if not isinstance(accs, list):
        return None
    toks = [re.sub(r"\s+", "", str(a)).replace("♯", "#").replace("♭", "b").replace("-sharp", "#").replace("-flat", "b")
            for a in accs]
    toks = [t[0].upper() + t[1:] for t in toks if t]
    if not toks:
        return 0
    for order, mark, sign in ((abcxml.SHARPS, "#", 1), (abcxml.FLATS, "b", -1)):
        if all(len(t) == 2 and t[0] in "ABCDEFG" and t[1] == mark for t in toks):
            letters = "".join(t[0] for t in toks)
            return sign * len(letters) if letters == order[:len(letters)] else None
    return None


def code_bar_length(jobs: List[dict]) -> Optional[Fraction]:
    """The bar length the code's own note lengths point to: the longest total that at least two measures (and a
    fifth of them) add up to exactly. Measures with rests or hollow notes add up short, so take the longest; it
    supports a meter of exactly that length and contradicts only a shorter one."""
    totals = Counter(sum(d for _, d in m) for j in jobs for m in (j.get("steps") or [])
                     if m and all(d for _, d in m))
    n = sum(totals.values())
    ok = [t for t, c in totals.items() if c >= max(2, 0.2 * n)]
    return max(ok) if ok else None


def _vote(votes: Dict[str, Any], prefer: List[str]) -> Tuple[Any, bool]:
    """Majority over the sources that gave a value; ties go to the first source in `prefer`. -> (value, disputed)."""
    vals = {k: v for k, v in votes.items() if v is not None}
    if not vals:
        return None, False
    count = Counter(vals.values())
    top = max(count.values())
    pick = next(vals[k] for k in prefer if k in vals and count[vals[k]] == top)
    return pick, top < 2 and len(vals) > 1


def decide_header(model: dict, reask: Optional[dict], pixels: Optional[int], omr_hint: Optional[abcxml.Score],
                  code_len: Optional[Fraction]) -> Tuple[dict, dict, List[str]]:
    """Fuse the header evidence. Returns (header, info, disputed fields): key by majority of model / pixels /
    OMR / re-ask (ties: pixels, OMR, re-ask, model); meter by sources naming it plus one vote from the code's
    bar length; clef by majority (ties: model)."""
    def meter(x):
        return abcxml.parse_meter(str(x or "")) if x else None

    reask = reask or {}
    key_votes = {"model": fifths_of(model.get("keySignature")), "pixels": pixels,
                 "omr": omr_hint.key[0] if omr_hint else None, "reask": fifths_of(reask.get("keySignature"))}
    if key_votes["model"] is None and model.get("key"):                   # no usable list: fall back on the name
        k = abcxml.parse_key(str(model["key"]))
        key_votes["model"] = k[0] if k else None
    fifths, key_disputed = _vote(key_votes, ["pixels", "omr", "reask", "model"])
    named = abcxml.parse_key(str(model.get("key") or ""))
    mode = named[1] if named else "major"

    meters = {"model": meter(model.get("meter")), "omr": omr_hint.meter if omr_hint else None,
              "reask": meter(reask.get("meter"))}
    score = Counter()
    for src, m in meters.items():
        if m:
            score[m[:2]] += 1
    for m in list(score):
        score[m] += code_len is not None and Fraction(*m) == code_len
    order = [m[:2] for m in (meters["model"], meters["reask"], meters["omr"]) if m]
    best = max(order, key=lambda m: score[m]) if order else (4, 4)          # max keeps the first of equals
    sym = next((m for m in meters.values() if m and m[:2] == best), (*best, ""))
    meter_disputed = (len(score) > 1 and list(score.values()).count(score[best]) > 1) or \
        (code_len is not None and Fraction(*best) < code_len and not meters["omr"])    # notes overflow the bar

    clefs = {"model": str(model.get("clef") or "").lower() or None, "omr": (omr_hint.info.get("clef") or None) if omr_hint else None,
             "reask": str(reask.get("clef") or "").lower() or None}
    clefs = {k: v for k, v in clefs.items() if v in CLEFS}
    clef, clef_disputed = _vote(clefs, ["model", "reask", "omr"])

    tempo = re.search(r"\d+", str(model.get("tempo") or ""))
    tempo = float(tempo.group()) if tempo and 20 <= int(tempo.group()) <= 300 else (omr_hint.tempo if omr_hint else None)
    head = {"title": str(model.get("title") or "").strip() or (omr_hint.title if omr_hint else ""),
            "composer": str(model.get("composer") or "").strip() or (omr_hint.composer if omr_hint else ""),
            "clef": clef or "treble", "key": (fifths or 0, mode), "meter": sym, "tempo": tempo}
    info = {"key": {**{k: v for k, v in key_votes.items() if v is not None}, "chosen": fifths or 0},
            "meter": {**{k: f"{v[0]}/{v[1]}" for k, v in meters.items() if v},
                      **({"codeBarLength": str(code_len)} if code_len else {}), "chosen": f"{sym[0]}/{sym[1]}"},
            "clef": {**clefs, "chosen": head["clef"]}}
    disputed = [f for f, d in (("key", key_disputed), ("meter", meter_disputed), ("clef", clef_disputed)) if d]
    return head, info, disputed


def read_header(ctx: dict, jobs: List[dict], pages: List[Any], hints: Optional[abcxml.Score]) -> Tuple[dict, List[str]]:
    first = jobs[0]
    model, why = _json(ctx, first.get("plain") or first["png"], HEADER_Q)
    warns = [f"Header could not be read by the model ({why}); used the other evidence."] if why else []
    systems = [(pages[j["page"]], j["system"]) for j in jobs if j.get("system")][:4]
    found = Counter(omr.key_signature(img, s) for img, s in systems)
    found.pop(None, None)
    pixels = found.most_common(1)[0][0] if found and found.most_common(1)[0][1] * 2 > sum(found.values()) else None
    code_len = code_bar_length(jobs)
    head, info, disputed = decide_header(model, None, pixels, hints, code_len)
    if disputed and first.get("system"):
        img, s = pages[first["page"]], first["system"]
        l, t, r, b = s.box
        crop = omr.crop_png(img, (l, t, min(r, l + round(22 * s.spacing)), b), s.spacing)
        ctx["stop"]()
        reask, _ = _json(ctx, crop, REASK_Q)
        head, info, _ = decide_header(model, reask, pixels, hints, code_len)
        info["reasked"] = disputed
    if not model and not hints and pixels is None:
        warns.append("Assumed treble clef, C major, 4/4.")
    return head, info, warns


# ------------------------------------------------------------------ checking

def key_text(key: Tuple[int, str]) -> str:
    fifths, mode = key
    name = abcxml.key_name(fifths, mode)
    if not fifths:
        return f"K:{name} ({mode}): no sharps or flats in the key signature."
    notes = abcxml.SHARPS[:fifths] if fifths > 0 else abcxml.FLATS[:-fifths]
    kind = "sharp" if fifths > 0 else "flat"
    return (f"K:{name} ({mode}, {abs(fifths)} {kind}s): every {', '.join(notes)} is {kind} in every octave unless "
            f"a natural is printed - write them as plain letters.")


def _frac_text(x: Fraction) -> str:
    for unit, name in ((Fraction(1, 8), "eighths"), (Fraction(1, 16), "16ths"), (Fraction(1, 32), "32nds")):
        if (x / unit).denominator == 1:
            return f"{x / unit} {name}"
    return f"{x} of a whole note"


def _dotted(m: re.Match) -> str:
    f = abcxml._mult(m.group(2) + m.group(3)) * Fraction(3, 2)
    return m.group(1) + ("" if f == 1 else str(f.numerator) if f.denominator == 1 else
                         ("" if f.numerator == 1 else str(f.numerator)) + "/" + str(f.denominator))


def normalise(text: str) -> Tuple[str, List[int]]:
    """Model answer -> plain ABC body (one bar per line) and the measure labels it used."""
    m = re.findall(r"```[a-zA-Z]*\n?(.*?)```", text, re.S)
    body = max(m, key=len) if m else text
    lines, labels = [], []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("%") or re.fullmatch(r"[XTCQ]:.*", line):
            continue
        lab = re.match(r"(?:m|bar|measure)?\s*(\d+)\s*[:.)]\s*", line, re.I)
        if lab:
            labels.append(int(lab.group(1)))
            line = line[lab.end():]
        line = re.sub(r"\b(?:CONFIRMED|CHECK)\b:?", "", line).strip()      # echoed marks from the prompt
        line = re.sub(r"([\^_=]*[A-Ga-gz][,']*)(\d*)(/*\d*)\.", _dotted, line)   # "B2." -> "B3"
        if not re.fullmatch(r"[A-Za-z]:.*", line) and not re.search(r"[|\]]\s*$", line):
            line += " |"
        lines.append(line)
    return "\n".join(lines), labels


def parse(body: str, key: Tuple[int, str], meter: Tuple[int, int, str]) -> abcxml.Score:
    head = f"X:1\nM:{meter[0]}/{meter[1]}\nL:1/8\nK:{abcxml.key_name(*key)}\n"
    return abcxml.parse_abc(head + body, meter)


def _written(e: abcxml.Ev) -> Fraction:
    return e.dur * Fraction(*e.tuplet) if e.tuplet else e.dur


def _filled(b: abcxml.Bar) -> List[abcxml.Ev]:
    """Events drawn with a filled notehead (shorter than a half note) - what omr.noteheads can see."""
    return [e for e in b.events if e.pitches and _written(e) < Fraction(1, 2)]


def diatonic(p: abcxml.Pitch) -> int:
    return p.octave * 7 + "CDEFGAB".index(p.step)


Code = List[List[Tuple[int, Optional[Fraction]]]]     # per measure: (diatonic pitch, written length or None)


def complete(heads: List[Tuple[int, Optional[Fraction]]], expected: Fraction) -> bool:
    """The code's own reading of a measure adds up exactly (then it is almost always right)."""
    return bool(heads) and all(d for _, d in heads) and sum(d for _, d in heads) == expected


def code_abc(heads: List[Tuple[int, Optional[Fraction]]]) -> str:
    return " ".join(letter(p) + ("?" if d is None else abcxml._len(d * 8)) for p, d in heads)


def apply_code(sc: abcxml.Score, code: Optional[Code], key: Tuple[int, str]) -> int:
    """Merge the code's notehead reading into a model reading; returns the number of bars touched.

    Bars the code reads completely replace the model's notes (keeping its printed accidentals and a
    final tie); elsewhere, when the model has as many single filled notes as the code found heads, the
    code's staff positions replace the model's pitches - models misread pitch far more than they miscount.
    """
    if not code or len(code) != len(sc.bars):
        return 0
    alters, touched = abcxml.key_alters(key[0]), 0
    for b, hs in zip(sc.bars, code):
        evs = _filled(b)
        same = len(evs) == len(hs) and all(len(e.pitches) == 1 for e in evs)
        full = complete(hs, b.expected)
        if not (same or full):
            continue
        shown = {(p.step, p.octave): p.alter for e in b.events for p in e.pitches if p.show}
        acc: Dict[Tuple[str, int], int] = {}

        def pitch(d: int) -> abcxml.Pitch:
            step, octave = "CDEFGAB"[d % 7], d // 7
            show = (step, octave) in shown and (step, octave) not in acc
            if show:
                acc[(step, octave)] = shown[(step, octave)]
            return abcxml.Pitch(step, acc.get((step, octave), alters.get(step, 0)), octave, show)

        if full and not (same and len(evs) == len(b.events)):
            tie = bool(b.events) and b.events[-1].tie
            b.events = [abcxml.Ev(dur=dur, pitches=[pitch(d)]) for d, dur in hs]
            b.events[-1].tie = tie
        else:
            for e, (d, dur) in zip(evs, hs):
                e.pitches[0] = pitch(d)
                if full:
                    e.dur, e.tuplet, e.tpos = dur, None, ""
        touched += 1
    return touched


def check(sc: abcxml.Score, n: Optional[int], first: int, piece_start: bool, piece_end: bool,
          clef: str, heads: Optional[Code] = None) -> List[str]:
    """Human-readable problems for one system's reading (empty = passes every check)."""
    out = []
    if n and len(sc.bars) != n:
        out.append(f"You wrote {len(sc.bars)} measures but this system has exactly {n} "
                   f"(m{first}-m{first + n - 1}, labelled in red above the staff). Check you did not split, merge, "
                   f"skip or invent a measure.")
    if not sc.bars:
        return out or ["No measures found in the answer."]
    split = {i for i, (a, b) in enumerate(zip(sc.bars, sc.bars[1:]))      # a bar split at a repeat / section end
             if a.total < a.expected and b.total < b.expected and a.total + b.total == a.expected}
    for i, found, want in sc.problems(first_ok=piece_start, last_ok=piece_end):
        if i in split or i - 1 in split:
            continue
        diff = found - want
        out.append(f"m{first + i}: your notes add up to {_frac_text(found)} but {sc.meter[0]}/{sc.meter[1]} needs "
                   f"{_frac_text(want)} ({_frac_text(abs(diff))} too {'long' if diff > 0 else 'short'}) - "
                   f"recount the beams, flags, dots and rests in that measure.")
    lo, hi = CLEFS[clef][1]
    for i, b in enumerate(sc.bars):
        bad = [p for e in b.events for p in e.pitches if not lo <= p.midi <= hi]
        if bad:
            out.append(f"m{first + i}: a note is far outside the staff - check the octave marks "
                       f"(lowercase / ' / ,) against the cheat-sheet.")
    if heads and len(heads) == len(sc.bars):
        for i, (b, hs) in enumerate(zip(sc.bars, heads)):
            got = len(_filled(b))
            if abs(got - len(hs)) > max(1, 0.15 * len(hs)):
                out.append(f"m{first + i}: the code counts {len(hs)} filled noteheads in this measure "
                           f"({code_abc(hs)}) but you wrote {got} notes shorter than a half "
                           f"note - check for missed or invented notes, then recount the beams.")
    if any(w.startswith("Ignored") for w in sc.warnings):
        out.append("Some symbols were not valid ABC (" + sc.warnings[-1].split(": ", 1)[-1] + "); use only the listed syntax.")
    return out


def _bar_sig(b: abcxml.Bar) -> tuple:
    return tuple((tuple(p.midi for p in e.pitches), e.dur) for e in b.events)


def vote(cands: List[Tuple[abcxml.Score, List[str]]], n: Optional[int]) -> abcxml.Score:
    """Per-bar majority over readings with the right bar count; ties go to the later (repaired) reading."""
    best = min(enumerate(cands), key=lambda ic: (len(ic[1][1]), -ic[0]))[1][0]
    n = n or Counter(len(s.bars) for s, _ in cands).most_common(1)[0][0]
    aligned = [s for s, _ in cands if len(s.bars) == n]
    if len(aligned) < 2:
        return best
    out = abcxml.Score(key=aligned[0].key, meter=aligned[0].meter, warnings=list(best.warnings))
    for i in range(n):
        ok = [s.bars[i] for s in aligned if s.bars[i].total == s.bars[i].expected] or [s.bars[i] for s in aligned]
        count = Counter(_bar_sig(b) for b in ok)
        top = max(count.values())
        out.bars.append(next(b for b in reversed(ok) if count[_bar_sig(b)] == top))
    return out


# ------------------------------------------------------------------ hybrid: OMR hints

def agrees(bar: abcxml.Bar, heads: List[Tuple[int, Optional[Fraction]]]) -> bool:
    """The code's noteheads match this bar wherever the code can see: same filled notes, same staff positions,
    same written lengths where the code read one."""
    evs = _filled(bar)
    return len(evs) == len(heads) and all(len(e.pitches) == 1 and diatonic(e.pitches[0]) == d and (w is None or _written(e) == w)
                                          for e, (d, w) in zip(evs, heads))


def _fits(bar: Optional[abcxml.Bar], expected: Fraction, short_ok: bool) -> bool:
    return bar is not None and (bar.total == expected or (short_ok and 0 < bar.total < expected))


def confirmed(bar: Optional[abcxml.Bar], heads: Optional[List[Tuple[int, Optional[Fraction]]]], expected: Fraction,
              short_ok: bool) -> bool:
    """OMR bar of the right length that the code's noteheads agree with (a bar of rests needs no heads; a bar of
    only half/whole notes can't be checked, so it isn't confirmed)."""
    return heads is not None and _fits(bar, expected, short_ok) and agrees(bar, heads) and \
        (bool(heads) or all(not e.pitches for e in bar.events))


def bar_abc(bar: abcxml.Bar, key: Tuple[int, str], meter: Tuple[int, int, str]) -> str:
    """One bar as the model should write it (key signature applied), without barlines or repeat marks."""
    b = abcxml.Bar(events=bar.events, expected=bar.expected)
    line = abcxml.score_to_abc(abcxml.Score(bars=[b], key=key, meter=meter)).strip().splitlines()[-1]
    return line.rstrip("|:] ").strip() or "(empty)"


def _sim(heads: List[Tuple[int, Optional[Fraction]]], bar: abcxml.Bar) -> float:
    a, b = [d for d, _ in heads], [diatonic(e.pitches[-1]) for e in _filled(bar)]
    return 0.6 if not a and not b else SequenceMatcher(None, a, b, autojunk=False).ratio()


def align(code: List[List[Tuple[int, Optional[Fraction]]]], bars: List[abcxml.Bar]) -> List[Optional[int]]:
    """Needleman-Wunsch over measures: for each code measure, the index of its OMR bar or None. A match scores
    (notehead-pitch similarity - 0.5), gaps 0, so only look-alike measures pair up."""
    n, m = len(code), len(bars)
    band = 12 + abs(n - m)
    S = [[0.0] * (m + 1) for _ in range(n + 1)]
    sims: Dict[Tuple[int, int], float] = {}
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best = max(S[i - 1][j], S[i][j - 1])
            if abs(i * m / max(1, n) - j) <= band:
                sims[i, j] = _sim(code[i - 1], bars[j - 1])
                best = max(best, S[i - 1][j - 1] + sims[i, j] - 0.5 + 1e-6)
            S[i][j] = best
    out: List[Optional[int]] = [None] * n
    i, j = n, m
    while i and j:
        if (i, j) in sims and S[i][j] == S[i - 1][j - 1] + sims[i, j] - 0.5 + 1e-6:
            out[i - 1] = j - 1 if sims[i, j] >= 0.5 else None
            i, j = i - 1, j - 1
        elif S[i][j] == S[i - 1][j]:
            i -= 1
        else:
            j -= 1
    return out


def attach_hints(jobs: List[dict], hints: abcxml.Score) -> str:
    """Give each system job the OMR bars of its measures: job["hint"] = [Bar | None] * n for systems with a
    reliable measure count, job["hint_sys"] = the OMR's bars for that system otherwise. Returns how it aligned."""
    starts = sorted(set(hints.info.get("systemStarts") or [0]) | {0})
    hsys = [hints.bars[a:b] for a, b in zip(starts, starts[1:] + [len(hints.bars)]) if a < b]
    if len(hsys) == len(jobs):                                          # same system breaks: pair them in order
        for j, hb in zip(jobs, hsys):
            if not j["n"]:
                j["hint_sys"] = hb
            elif len(hb) == j["n"]:
                j["hint"] = list(hb)
            else:
                j["hint"] = [hb[k] if k is not None else None for k in align(j["heads"] or [[]] * j["n"], hb)]
        return "systems"
    flat = [(j, i) for j in jobs if j["n"] for i in range(j["n"])]        # else align every measure by its notes
    for j in jobs:
        if j["n"]:
            j["hint"] = [None] * j["n"]
    for (j, i), k in zip(flat, align([j["heads"][i] if j["heads"] else [] for j, i in flat], hints.bars)):
        j["hint"][i] = hints.bars[k] if k is not None else None
    return "measures"


def _padded(b: abcxml.Bar, other: abcxml.Bar) -> bool:
    """b is `other` plus rests at the end: a short (split) measure filled up to the meter."""
    sb, so = _bar_sig(b), _bar_sig(other)
    return len(sb) > len(so) and sb[:len(so)] == so and all(not p for p, _ in sb[len(so):])


def decide(models: List[abcxml.Bar], hint: Optional[abcxml.Bar], heads: Optional[List[Tuple[int, Optional[Fraction]]]],
           expected: Fraction, short_ok: bool) -> Tuple[abcxml.Bar, str]:
    """Per-measure vote among the model reading(s) and the OMR bar: only readings of the right length count; each
    scores the readings identical to it plus one when the code's noteheads agree, minus a half when it only pads
    another reading with rests. Ties: the reading that saw more printed accidentals (readers miss them far more
    often than they invent them), then the model where the code sees filled notes (pitch misreads are the OMR's
    weakness), else the OMR (rests and hollow notes are big, clear symbols; models misjudge them more)."""
    cands = [(b, "model") for b in models if _fits(b, expected, short_ok)] + \
        ([(hint, "omr")] if _fits(hint, expected, short_ok) else [])
    if not cands:
        return (models[-1], "model") if models else (hint, "omr")
    sigs = Counter(_bar_sig(b) for b, _ in cands)
    model_first = heads is None or bool(heads)

    def score(k: int):
        b, src = cands[k]
        return (sigs[_bar_sig(b)] + bool(heads and agrees(b, heads)) - 0.5 * any(_padded(b, o) for o, _ in cands),
                sum(p.show for e in b.events for p in e.pitches), (src == "model") == model_first, k)
    return cands[max(range(len(cands)), key=score)]


# ------------------------------------------------------------------ one system

def read_system(ctx: dict, job: dict, vote_i: int) -> List[Tuple[abcxml.Score, List[str]]]:
    """One independent reading of a system plus its repair rounds; returns every candidate with its problems."""
    models = ctx["models"][vote_i % len(ctx["models"]):] + ctx["models"][:vote_i % len(ctx["models"])]
    n, first = job["n"], job["first"]
    bar = Fraction(ctx["meter"][0], ctx["meter"][1])
    label = (f"This system has exactly {n} measures, labelled m{first} to m{first + n - 1} in red above the staff; "
             f"output exactly {n} lines.") if n else \
        f"The measures are labelled from m{first} in red above the staff where they could be detected; count the barlines yourself."
    task = (f"Transcribe the top staff of this system. {key_text(ctx['key'])} M:{ctx['meter'][0]}/{ctx['meter'][1]} "
            f"(each full measure = {_frac_text(bar)}). L:1/8. {label}")
    if job["piece_start"]:
        task += " The first measure of the piece may be a short pickup."
    if job.get("hint"):
        lines = []
        for i, (hb, ok) in enumerate(zip(job["hint"], job["confirmed"])):
            hs = job["heads"][i] if job.get("heads") else None
            omr_txt = bar_abc(hb, ctx["key"], ctx["meter"]) if hb is not None else "(no reading)"
            if hb is not None and hb.total != bar:
                omr_txt += f" (adds up to {_frac_text(hb.total)})"
            code_txt = (code_abc(hs) or "(no filled notes)") + (" (adds up)" if complete(hs, bar) else "") if hs is not None else "(not read)"
            lines.append(f"m{first + i} {'CONFIRMED' if ok else 'CHECK'} | offline OMR: {omr_txt} | code: {code_txt}")
        task += HYBRID_NOTE + "\n".join(lines)
    elif job.get("hint_sys"):
        task += ("\nAn offline OMR program read this system as follows (its measure split may differ; use it only as "
                 "a cross-check):\n" + " | ".join(bar_abc(b, ctx["key"], ctx["meter"]) for b in job["hint_sys"]))
    elif job.get("heads"):
        task += ("\nThe code read the filled noteheads itself: pitch from the staff position (before key-signature "
                 "sharps/flats), length from beams/flags/dots. It cannot see rests, half/whole notes, accidentals, ties "
                 "or tuplets, and a fingering digit can fool it. Measures marked OK add up exactly - copy them and only "
                 "add printed accidentals and ties. Fix the others from the image:\n" + "\n".join(
                     f"m{first + i} {'OK' if complete(hs, bar) else 'CHECK'}: {code_abc(hs) or '(no filled notes)'}"
                     for i, hs in enumerate(job["heads"])))
    if vote_i:
        task += f" (independent reading #{vote_i + 1})"
    cands, prompt = [], [job["png"], task]
    for rnd in range(REPAIRS + 1):
        try:
            text, model = _ask({**ctx, "models": models}, ctx["system"], prompt)
        except llm.LLMError as e:
            if e.kind != "truncated" or rnd == REPAIRS:     # a runaway answer: just ask again
                if cands:
                    break
                raise
            job["log"].append(f"v{vote_i + 1} r{rnd}: runaway answer")
            continue
        job["model"] = model
        body, _ = normalise(text)
        sc = parse(body, ctx["key"], ctx["meter"])
        apply_code(sc, job.get("heads"), ctx["key"])
        probs = check(sc, n, first, job["piece_start"], job["piece_end"], ctx["clef"], job.get("heads"))
        job["log"].append(f"v{vote_i + 1} r{rnd} {model}: {len(sc.bars)} bars, {len(probs)} problems")
        if not probs or any(abcxml.score_to_abc(sc) == abcxml.score_to_abc(c) for c, _ in cands):
            cands.append((sc, probs))
            break                                       # clean, or the model just repeated itself
        cands.append((sc, probs))
        prompt = [job["png"], task + "\n\nYour previous answer:\n```\n" + body + "\n```\nThe code checked it and found:\n- "
                  + "\n- ".join(probs) + "\nLook at those measures in the image again and output the corrected full system "
                  "(every measure, same format). Keep measures that were not flagged unless you see a mistake."]
    return cands


# ------------------------------------------------------------------ public entry point

def transcribe(data: bytes, *, provider: str, models: List[str], key: str, base_url: str = "", page_range: str = "",
               votes: int = 1, progress: Optional[Callable[[str, Optional[float]], None]] = None,
               cancel: Optional[Callable[[], bool]] = None, thinking: Optional[str] = None,
               hints: Optional[abcxml.Score] = None) -> abcxml.Score:
    """Sheet music bytes -> Score. `hints`: another engine's reading of the same pages (typically Audiveris, with
    info["systemStarts"]) to check against. Raises llm.LLMError for auth/bad_request/cancelled."""
    say = progress or (lambda msg, frac: None)
    lock = threading.Lock()

    def notify(msg: str):
        with lock:
            say(msg, None)

    def stop():
        if cancel and cancel():
            raise llm.LLMError("Cancelled", "cancelled")

    hints = hints if hints is not None and hints.bars else None
    ctx: Dict[str, Any] = {"provider": provider, "models": models, "key_": key, "base_url": base_url, "cancel": cancel,
                           "notify": notify, "lock": lock, "used": Counter(), "stop": stop,
                           "thinking": thinking if thinking is not None else THINKING.get(provider, "")}
    say("Finding staff systems", 0.0)
    jobs, first, pages = [], 1, []
    for pi, page in enumerate(omr.load_pages(data, page_range)):
        stop()
        img, systems = omr.find_systems(page, pi == 0)
        pages.append(img)
        if not systems:
            jobs.append({"page": pi, "png": omr.whole_page_png(img), "n": None, "first": first})
            continue
        for s in systems:
            stop()
            spans, reliable = omr.measures(img, s)
            heads = omr.noteheads(img, s)
            jobs.append({"page": pi, "system": s, "png": omr.crop_png(img, s.box, s.spacing, spans, first),
                         "plain": omr.crop_png(img, s.box, s.spacing), "n": len(spans) if reliable else None,
                         "first": first,
                         "steps": [[(st, d) for x, st, d in heads if a <= x < b] for a, b in spans] if reliable else None})
            first += len(spans) or 1
    if not jobs:
        raise llm.LLMError("No pages found in the file.", "bad_request")
    for j in jobs:
        j["piece_start"], j["piece_end"], j["log"] = j is jobs[0], j is jobs[-1], []

    stop()
    say("Reading the header", 0.03)
    head, header_info, warnings = read_header(ctx, jobs, pages, hints)
    ctx.update(key=head["key"], meter=head["meter"], clef=head["clef"],
               system=SYSTEM_PROMPT.format(clef=head["clef"], positions=CLEFS[head["clef"]][0]))
    bar = Fraction(head["meter"][0], head["meter"][1])
    for j in jobs:          # staff steps -> diatonic pitches now that the clef is known
        j["heads"] = [[(CLEFS[head["clef"]][2] + st, d) for st, d in m] for m in j["steps"]] if j.get("steps") else None
    aligned = attach_hints(jobs, hints) if hints else ""
    flat = [(j, i, hb) for j in jobs if j.get("hint") for i, hb in enumerate(j["hint"])]
    for (j, i, a), (k, m, b) in zip(flat, flat[1:]):        # the OMR read a bar split at a repeat / section end
        if a is not None and b is not None and a.total < bar and b.total < bar and a.total + b.total == bar:
            j.setdefault("split", set()).add(i)
            k.setdefault("split", set()).add(m)
    for j in jobs:
        short = [j["piece_start"] and i == 0 or j["piece_end"] and i == (j["n"] or 0) - 1 or i in j.get("split", ())
                 for i in range(j["n"] or 0)]
        j["short"] = short
        j["confirmed"] = [confirmed(hb, j["heads"][i] if j["heads"] else None, bar, short[i])
                          for i, hb in enumerate(j["hint"])] if j.get("hint") else []
        j["skip"] = bool(j["n"]) and len(j["confirmed"]) == j["n"] and all(j["confirmed"])

    todo = [i for i, j in enumerate(jobs) if not j["skip"]]
    for i, j in enumerate(jobs):
        if j["skip"]:
            say(f"system {i + 1}/{len(jobs)}: {j['n']} measures, all confirmed - no AI call needed", None)
    done, total = [0], max(1, len(todo) * votes)

    def run(i, v):
        try:
            return read_system(ctx, jobs[i], v)
        finally:
            with lock:
                done[0] += 1
                j = jobs[i]
                say(f"system {i + 1}/{len(jobs)}: read by {j.get('model', 'the model')}"
                    + (f" ({sum(j['confirmed'])}/{j['n']} measures confirmed)" if j.get("confirmed") else "")
                    + (f" (reading {v + 1})" if votes > 1 else ""), 0.05 + 0.9 * done[0] / total)

    results: Dict[int, list] = {i: [] for i in range(len(jobs))}
    errors: Dict[int, llm.LLMError] = {}
    with ThreadPoolExecutor(WORKERS) as pool:
        futs = {pool.submit(run, i, v): i for i in todo for v in range(votes)}
        for f, i in futs.items():
            try:
                results[i] += f.result()
            except llm.LLMError as e:
                if e.kind in ("auth", "bad_request", "cancelled"):
                    for g in futs:
                        g.cancel()
                    raise
                errors[i] = e
    if todo and not any(results[i] for i in todo) and not hints:     # every reading of every system failed
        raise next(iter(errors.values()))

    score = abcxml.Score(title=head["title"], composer=head["composer"], key=head["key"], meter=head["meter"],
                         tempo=head["tempo"], warnings=warnings)
    systems_info = []
    cur_key, cur_meter = head["key"], head["meter"]
    for i, job in enumerate(jobs):
        n = job["n"]
        where = f"page {job['page'] + 1}, system {i + 1}" + (f" (m{job['first']}-m{job['first'] + n - 1})" if n else "")
        sc = vote(results[i], n) if results[i] else None
        hint = job.get("hint") or (job["hint_sys"] if job.get("hint_sys") and (sc is None or len(sc.bars) == len(job["hint_sys"]))
                                   else None)          # no model reading at all: the OMR's bars stand in
        status = "confirmed" if job["skip"] else "repaired" if len(results[i]) > votes else "model"
        if job["skip"] or sc is None or (n and len(sc.bars) != n):
            k = n or (len(hint) if hint else 1)
            if not job["skip"]:
                status = "fallback"
                why = f"({errors.get(i)})" if sc is None else f"(it kept giving {len(sc.bars)} measures instead of {k})"
            sc = parse("Z |\n" * k, cur_key, cur_meter)       # the code's own reading where it adds up, else rests
            apply_code(sc, job.get("heads"), cur_key)
            models_bars: List[List[abcxml.Bar]] = [[] for _ in range(k)]
            if not job["skip"]:                     # the code's complete bars stand in for the model
                models_bars = [[b] if _filled(b) else [] for b in sc.bars]
        else:
            models_bars = [[b] for b in sc.bars]
        if hint and len(hint) == len(models_bars):
            out, took = [], 0
            for m, (mb, hb) in enumerate(zip(models_bars, hint)):
                heads = job["heads"][m] if job.get("heads") and n else None
                short = job["short"][m] if m < len(job["short"]) else (i == 0 and m == 0) or (i == len(jobs) - 1 and m == len(hint) - 1)
                b, src = decide(mb, hb, heads, bar, short) if (mb or hb is not None) else (sc.bars[m], "rest")
                if status == "fallback" and src == "model" and hb is not None and _bar_sig(hb) == _bar_sig(b):
                    b, src = hb, "omr"                    # same notes as the code's bar, plus the OMR's ties
                if src == "omr":
                    b = abcxml.Bar(events=b.events, expected=bar, left=b.left, right=b.right, ending=b.ending,
                                   ending_stop=b.ending_stop, tempo=b.tempo)
                    took += 1
                elif hb is not None:                              # keep the OMR's repeats and voltas
                    b.left, b.right, b.ending, b.ending_stop = hb.left, hb.right, hb.ending, hb.ending_stop
                out.append(b)
            sc.bars = out
        else:
            took = 0
        if status == "fallback":
            coded = sum(1 for b, mb in zip(sc.bars, models_bars) if mb and b is mb[0])
            rests = len(sc.bars) - took - coded
            score.warnings.append(f"{where} could not be read by the model {why}; of {len(sc.bars)} measures, "
                                  f"{took} came from the offline OMR, {coded} from the notehead reader"
                                  + (f" and {rests} are placeholder rests." if rests else "."))
        left = check(sc, n, job["first"], job["piece_start"], job["piece_end"], head["clef"], job.get("heads"))
        if left and status != "fallback":
            score.warnings.append(f"{where}: " + " | ".join(left)[:400])
        systems_info.append({"page": job["page"] + 1, "system": i + 1,
                             "measures": [job["first"], job["first"] + len(sc.bars) - 1], "status": status,
                             "model": job.get("model", ""), "problems": left, "fromOmr": took,
                             "confirmed": sum(job.get("confirmed") or []), "log": job["log"]})
        if sc.bars:
            if sc.key != cur_key and not sc.bars[0].key:
                sc.bars[0].key = sc.key
            if sc.meter[:2] != cur_meter[:2] and not sc.bars[0].meter:
                sc.bars[0].meter = sc.meter
            for b in sc.bars:
                cur_key, cur_meter = b.key or cur_key, b.meter or cur_meter
        score.bars += sc.bars
    score.info = {"engine": "hybrid" if hints else "ai", "modelsUsed": dict(ctx["used"]),
                  "skippedSystems": sum(j["skip"] for j in jobs), "header": header_info, "systems": systems_info,
                  **({"alignment": aligned} if aligned else {})}
    say("Done", 1.0)
    return score


# ------------------------------------------------------------------ dev CLI

def _selftest():
    """No-API checks of the deterministic parts: python pipeline.py --selftest"""
    F = Fraction
    body, labels = normalise("Sure:\n```abc\nm7: B2. B D. B\nm8: z8 |\n```")
    assert body == "B3 B D3/2 B |\nz8 |" and labels == [7, 8], body
    key, meter = (2, "major"), (4, 4, "")
    sc = parse("e e e e e e e e |\nA4 B4 |", key, meter)              # wrong pitches, wrong rhythm in bar 1
    code = [[(28 + i % 3, F(1, 16)) for i in range(16)], [(32, None)]]   # bar 1 read fully by code
    assert apply_code(sc, code, key) == 1
    assert sc.bars[0].total == 1 and sc.bars[0].events[0].pitches[0].step == "C" \
        and sc.bars[0].events[0].pitches[0].alter == 1                # C# from the key signature
    assert check(sc, 2, 1, True, True, "treble", code) == []
    assert check(parse("d2 d2 d2 |", key, meter), 2, 5, False, False, "treble")[0].startswith("You wrote 1")
    a, b = parse("d4 d4 |", key, meter), parse("d4 e4 |", key, meter)
    assert _bar_sig(vote([(a, []), (b, []), (parse("d4 d4 |", key, meter), [])], 1).bars[0]) == _bar_sig(a.bars[0])

    # header: the accidental list is validated against the order of sharps/flats; majority of the evidence wins
    assert fifths_of(["F#", "C#"]) == 2 and fifths_of(["Bb", "Eb", "Ab"]) == -3 and fifths_of([]) == 0
    assert fifths_of(["C#"]) is None and fifths_of(["F#", "Bb"]) is None and fifths_of("D major") is None
    omr_hint = abcxml.Score(key=(2, "major"), meter=(3, 4, ""), info={"clef": "treble"})
    head, info, disputed = decide_header({"keySignature": ["F#"], "key": "G major", "meter": "4/4", "clef": "treble"},
                                         None, 2, omr_hint, F(3, 4))
    assert head["key"] == (2, "major") and head["meter"][:2] == (3, 4) and not disputed, (head, info, disputed)
    head, info, disputed = decide_header({"keySignature": ["F#"], "meter": "4/4"}, None, 2, None, F(3, 4))
    assert disputed == ["key"], disputed                                   # 1 vs 1: ask again; 3/4 of notes fit in 4/4
    assert decide_header({"meter": "2/4"}, None, None, None, F(3, 4))[2] == ["meter"]     # but not in 2/4
    head, _, _ = decide_header({"keySignature": ["F#"], "meter": "4/4"}, {"keySignature": ["F#", "C#"], "meter": "3/4"},
                               2, None, F(3, 4))
    assert head["key"][0] == 2 and head["meter"][:2] == (3, 4)
    assert code_bar_length([{"steps": [[(0, F(1, 4))] * 3] * 3 + [[(0, F(1, 4))]] * 5}]) == F(3, 4)

    # hybrid: an OMR bar is confirmed by matching noteheads; the vote prefers agreement, the model wins ties
    hb = parse("d2 f2 e2 c2 |", key, meter).bars[0]
    heads = [(diatonic(e.pitches[0]), F(1, 4)) for e in hb.events]
    assert confirmed(hb, heads, F(1), False) and not confirmed(hb, heads[:3], F(1), False)
    assert not confirmed(parse("d4 f4 |", key, meter).bars[0], [], F(1), False)          # hollow notes: unseen
    assert confirmed(parse("Z |", key, meter).bars[0], [], F(1), False)                   # a rest bar
    model = parse("d2 f2 e2 d2 |", key, meter).bars[0]
    assert decide([model], hb, heads, F(1), False)[1] == "omr"                          # OMR + code beat the model
    assert decide([model], hb, None, F(1), False)[1] == "model"                         # 1 vs 1: the model
    assert decide([model], parse("d2 f2 e2 |", key, meter).bars[0], heads, F(1), False)[1] == "model"   # OMR short
    assert decide([parse("d2 c2 e2 d2 |", key, meter).bars[0]], parse("d2 =c2 e2 d2 |", key, meter).bars[0],
                  [(36, F(1, 4)), (35, F(1, 4)), (37, F(1, 4)), (36, F(1, 4))], F(1), False)[1] == "omr"  # saw the natural
    assert decide([parse("Z |", key, meter).bars[0]], parse("A8 |", key, meter).bars[0], [], F(1), False)[1] == "omr"
    split = parse("d2 f2 e2 |", key, meter).bars[0]                      # a bar split at a repeat sign
    assert decide([parse("d2 f2 e2 z2 |", key, meter).bars[0]], split, heads[:3], F(1), True)[1] == "omr"
    assert check(parse("d6 |\nf2 |", key, meter), 2, 1, False, False, "treble") == []
    assert bar_abc(parse("^c2 d2 e2- e2 :|", key, meter).bars[0], key, meter) == "^c2 d2 e2- e2"
    bars = parse("d2 f2 e2 c2 |\nA8 |\nB2 c2 d2 e2 |\ng2 f2 e2 d2 |", key, meter).bars
    code3 = [[(diatonic(e.pitches[0]), F(1, 4)) for e in b.events if len(b.events) > 1] for b in bars[:1] + bars[2:]]
    assert align(code3, bars) == [0, 2, 3], align(code3, bars)                         # OMR found a bar the code missed
    jobs = [{"n": 2, "heads": code3[:2]}, {"n": 1, "heads": code3[2:]}]
    assert attach_hints(jobs, abcxml.Score(bars=bars, info={"systemStarts": [0, 3]})) == "systems"
    assert jobs[0]["hint"] == [bars[0], bars[2]] and jobs[1]["hint"] == [bars[3]], jobs
    print("selftest ok")


if __name__ == "__main__":
    import argparse
    import sys

    if sys.argv[1:] == ["--selftest"]:
        _selftest()
        sys.exit()

    ap = argparse.ArgumentParser(description="Dev CLI: transcribe a score with the guided pipeline.")
    ap.add_argument("file")
    ap.add_argument("--model", action="append", required=True)
    ap.add_argument("--provider", default="")
    ap.add_argument("--votes", type=int, default=1)
    ap.add_argument("--pages", default="")
    ap.add_argument("--thinking", default=None)
    ap.add_argument("--resolution", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--cache", default="", help="directory for cached model answers")
    ap.add_argument("--ref", default="", help="reference ABC to score against (bench.compare)")
    ap.add_argument("--hints", default="", help="another engine's MusicXML / .mxl of the same pages, or a folder of "
                                                "Audiveris exports (hybrid mode)")
    ap.add_argument("--audiveris", action="store_true", help="run Audiveris first and use its reading as hints")
    a = ap.parse_args()

    env = {}
    for line in (Path(__file__).parent / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = re.sub(r"\s+", "", v).strip("\"'")
    prov = a.provider or ("gemini" if a.model[0].startswith("gemini") else "qwen")
    key = env.get({"gemini": "GEMINI_API_KEY", "qwen": "QWEN_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}[prov], "")
    CACHE_DIR = Path(a.cache) if a.cache else None
    if a.resolution:
        llm.IMAGE_RESOLUTION = a.resolution
    t0 = time.time()
    data = Path(a.file).read_bytes()
    log = lambda m, f: print(f"  [{'' if f is None else f'{f:.0%}'}] {m}", file=sys.stderr)  # noqa: E731
    hint_score = None
    if a.hints:
        import audiveris
        if Path(a.hints).is_dir():
            hint_score = audiveris.collect(Path(a.hints))
        else:
            raw = Path(a.hints).read_bytes()
            hint_score = abcxml.parse_musicxml(raw)
            hint_score.info.update(audiveris.layout(raw))
    elif a.audiveris:
        import audiveris
        hint_score = audiveris.transcribe(data, a.file, a.pages, progress=log)
    sc = transcribe(data, provider=prov, models=a.model, key=key, page_range=a.pages, votes=a.votes,
                    thinking=a.thinking, progress=log, hints=hint_score)
    for s in sc.info["systems"]:
        print(f"system {s['system']} p{s['page']} m{s['measures'][0]}-{s['measures'][1]} {s['status']} "
              f"confirmed {s['confirmed']} fromOmr {s['fromOmr']}: " + "; ".join(s["log"]))
    abc = abcxml.score_to_abc(sc)
    print(abc)
    print("header:", json.dumps(sc.info["header"]))
    print("models used:", sc.info["modelsUsed"], "skipped systems:", sc.info["skippedSystems"])
    print("problems:", [(i + 1, str(f), str(e)) for i, f, e in sc.problems()])
    print("warnings:", *sc.warnings, sep="\n  ")
    print(f"wall: {time.time() - t0:.1f}s")
    if a.out:
        Path(a.out).write_text(abc, encoding="utf-8")
    if a.ref:
        sys.path[:0] = [str(Path(a.ref).parent), str(Path(a.ref).parent.parent)]      # bench.py (dev ruler)
        import bench
        ref = abcxml.parse_abc(Path(a.ref).read_text(encoding="utf-8"))
        print(bench.compare(ref, sc))
