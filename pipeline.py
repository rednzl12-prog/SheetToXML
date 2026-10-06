"""Guided transcription: code does layout and arithmetic, a vision model reads one staff system at a time.

1. Layout (omr): pages -> systems -> measures from barlines; each crop gets red measure numbers.
   omr.noteheads also reads every filled note (staff position + beams/flags/dot) without any model.
2. One header call: title, composer, clef, key, meter, tempo.
3. One call per system with the key's sharps/flats spelled out, the exact measure count, an ABC
   cheat-sheet and the code's own reading per measure. Measures the code reads to exactly the
   meter override the model; elsewhere the code's pitches replace the model's when the note counts
   agree. The result is checked in code (bar count, bar lengths, notehead count, range).
4. Up to two repair calls per system with precise per-bar feedback.
5. Optional votes: independent readings, majority per bar among bars of the right length.
6. Systems are merged into one abcxml.Score; anything unreadable is listed in Score.warnings.
"""

import hashlib
import json
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import abcxml
import llm
import omr

WORKERS = 4
REPAIRS = 2
THINKING = {"gemini": "low", "qwen": "off", "deepseek": "off"}
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


# ------------------------------------------------------------------ model calls

def _ask(provider: str, models: List[str], key: str, system: str, parts: list, base_url: str, thinking: str,
         cancel, notify) -> Tuple[str, str]:
    """generate_with_fallback with an optional on-disk answer cache (dev runs)."""
    h = None
    if CACHE_DIR:
        sig = hashlib.sha256()
        for p in [provider, models[0], thinking, llm.IMAGE_RESOLUTION, system] + parts:
            sig.update(p if isinstance(p, bytes) else str(p).encode())
        h = CACHE_DIR / (sig.hexdigest()[:24] + ".json")
        if h.exists():
            d = json.loads(h.read_text(encoding="utf-8"))
            return d["text"], d["model"]
    text, model = llm.generate_with_fallback(provider, models, key, system, parts, notify=notify, base_url=base_url,
                                             thinking=thinking or None, cancel=cancel)
    if h:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        h.write_text(json.dumps({"text": text, "model": model}), encoding="utf-8")
    return text, model


def _header(provider, models, key, base_url, thinking, png, cancel, notify) -> Tuple[dict, List[str]]:
    q = ('Read the score header and the first staff. Answer with JSON only: {"title": "", "composer": "", '
         '"clef": "treble|bass|alto", "key": "e.g. D major or B minor (count the sharps/flats in the key signature)", '
         '"meter": "e.g. 4/4, 6/8, C, C|", "tempo": "quarter-note bpm as a number, or empty"}')
    warns = []
    try:
        text, _ = _ask(provider, models, key, "You read sheet music headers.", [png, q], base_url, thinking, cancel, notify)
        m = re.search(r"\{.*\}", text, re.S)
        info = json.loads(m.group()) if m else {}
    except (ValueError, llm.LLMError) as e:
        if isinstance(e, llm.LLMError) and e.kind in ("auth", "bad_request", "cancelled"):
            raise
        info = {}
        warns.append(f"Header could not be read ({e}); assumed treble clef, C major, 4/4.")
    k = abcxml.parse_key(str(info.get("key") or "")) or (0, "major")
    mt = abcxml.parse_meter(str(info.get("meter") or "")) or (4, 4, "")
    clef = str(info.get("clef") or "treble").lower()
    tempo = re.search(r"\d+", str(info.get("tempo") or ""))
    return {"title": str(info.get("title") or "").strip(), "composer": str(info.get("composer") or "").strip(),
            "clef": clef if clef in CLEFS else "treble", "key": k, "meter": mt,
            "tempo": float(tempo.group()) if tempo and 20 <= int(tempo.group()) <= 300 else None}, warns


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
        line = re.sub(r"([\^_=]*[A-Ga-gz][,']*)(\d*)(/*\d*)\.", _dotted, line)   # "B2." -> "B3"
        if not re.fullmatch(r"[A-Za-z]:.*", line) and not re.search(r"[|\]]\s*$", line):
            line += " |"
        lines.append(line)
    return "\n".join(lines), labels


def parse(body: str, key: Tuple[int, str], meter: Tuple[int, int, str]) -> abcxml.Score:
    head = f"X:1\nM:{meter[0]}/{meter[1]}\nL:1/8\nK:{abcxml.key_name(*key)}\n"
    return abcxml.parse_abc(head + body, meter)


def _filled(b: abcxml.Bar) -> List[abcxml.Ev]:
    """Events drawn with a filled notehead (shorter than a half note) - what omr.noteheads can see."""
    return [e for e in b.events if e.pitches and (e.dur * Fraction(*e.tuplet) if e.tuplet else e.dur) < Fraction(1, 2)]


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
    for i, found, want in sc.problems(first_ok=piece_start, last_ok=piece_end):
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


# ------------------------------------------------------------------ one system

def read_system(ctx: dict, job: dict, vote_i: int) -> List[Tuple[abcxml.Score, List[str]]]:
    """One independent reading of a system plus its repair rounds; returns every candidate with its problems."""
    models = ctx["models"][vote_i % len(ctx["models"]):] + ctx["models"][:vote_i % len(ctx["models"])]
    n, first = job["n"], job["first"]
    label = (f"This system has exactly {n} measures, labelled m{first} to m{first + n - 1} in red above the staff; "
             f"output exactly {n} lines.") if n else \
        f"The measures are labelled from m{first} in red above the staff where they could be detected; count the barlines yourself."
    task = (f"Transcribe the top staff of this system. {key_text(ctx['key'])} M:{ctx['meter'][0]}/{ctx['meter'][1]} "
            f"(each full measure = {_frac_text(Fraction(ctx['meter'][0], ctx['meter'][1]))}). L:1/8. {label}")
    if job["piece_start"]:
        task += " The first measure of the piece may be a short pickup."
    if job.get("heads"):
        bar = Fraction(ctx["meter"][0], ctx["meter"][1])
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
            text, model = _ask(ctx["provider"], models, ctx["key_"], ctx["system"], prompt, ctx["base_url"],
                               ctx["thinking"], ctx["cancel"], ctx["notify"])
        except llm.LLMError as e:
            if e.kind != "truncated" or rnd == REPAIRS:     # a runaway answer: just ask again
                if cands:
                    break
                raise
            job.setdefault("log", []).append(f"v{vote_i + 1} r{rnd}: runaway answer")
            continue
        body, _ = normalise(text)
        sc = parse(body, ctx["key"], ctx["meter"])
        apply_code(sc, job.get("heads"), ctx["key"])
        probs = check(sc, n, first, job["piece_start"], job["piece_end"], ctx["clef"], job.get("heads"))
        job.setdefault("log", []).append(f"v{vote_i + 1} r{rnd} {model}: {len(sc.bars)} bars, {len(probs)} problems")
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
               cancel: Optional[Callable[[], bool]] = None, thinking: Optional[str] = None) -> abcxml.Score:
    """Sheet music bytes -> Score. Raises llm.LLMError for auth/bad_request/cancelled."""
    say = progress or (lambda msg, frac: None)
    lock = threading.Lock()

    def notify(msg: str):
        with lock:
            say(msg, None)

    def stop():
        if cancel and cancel():
            raise llm.LLMError("Cancelled", "cancelled")

    say("Finding staff systems", 0.0)
    jobs, first = [], 1
    for pi, page in enumerate(omr.load_pages(data, page_range)):
        stop()
        img, systems = omr.find_systems(page, pi == 0)
        if not systems:
            jobs.append({"page": pi, "png": omr.whole_page_png(img), "n": None, "first": first})
            continue
        for s in systems:
            spans, reliable = omr.measures(img, s)
            heads = omr.noteheads(img, s)
            jobs.append({"page": pi, "png": omr.crop_png(img, s.box, s.spacing, spans, first),
                         "plain": omr.crop_png(img, s.box, s.spacing), "n": len(spans) if reliable else None,
                         "first": first,
                         "steps": [[(st, d) for x, st, d in heads if a <= x < b] for a, b in spans] if reliable else None})
            first += len(spans) or 1
    if not jobs:
        raise llm.LLMError("No pages found in the file.", "bad_request")
    for j in jobs:
        j["piece_start"], j["piece_end"] = j is jobs[0], j is jobs[-1]

    say("Reading the header", 0.03)
    head, warnings = _header(provider, models, key, base_url, thinking if thinking is not None else THINKING.get(provider, ""),
                             jobs[0].get("plain") or jobs[0]["png"], cancel, notify)
    ctx = {"provider": provider, "models": models, "key_": key, "base_url": base_url, "cancel": cancel, "notify": notify,
           "thinking": thinking if thinking is not None else THINKING.get(provider, ""), "key": head["key"],
           "meter": head["meter"], "clef": head["clef"],
           "system": SYSTEM_PROMPT.format(clef=head["clef"], positions=CLEFS[head["clef"]][0])}
    for j in jobs:          # staff steps -> diatonic pitches now that the clef is known
        j["heads"] = [[(CLEFS[head["clef"]][2] + st, d) for st, d in m] for m in j["steps"]] if j.get("steps") else None

    done = [0]
    total = len(jobs) * votes

    def run(job, v):
        try:
            return read_system(ctx, job, v)
        finally:
            with lock:
                done[0] += 1
                say(f"Read system {jobs.index(job) + 1}/{len(jobs)}" + (f" (reading {v + 1})" if votes > 1 else ""),
                    0.05 + 0.9 * done[0] / total)

    results: Dict[int, list] = {i: [] for i in range(len(jobs))}
    errors: Dict[int, llm.LLMError] = {}
    with ThreadPoolExecutor(WORKERS) as pool:
        futs = {pool.submit(run, j, v): i for i, j in enumerate(jobs) for v in range(votes)}
        for f, i in futs.items():
            try:
                results[i] += f.result()
            except llm.LLMError as e:
                if e.kind in ("auth", "bad_request", "cancelled"):
                    for g in futs:
                        g.cancel()
                    raise
                errors[i] = e
    if len(errors) == len(jobs):
        raise next(iter(errors.values()))

    score = abcxml.Score(title=head["title"], composer=head["composer"], key=head["key"], meter=head["meter"],
                         tempo=head["tempo"], warnings=warnings)
    cur_key, cur_meter = head["key"], head["meter"]
    for i, job in enumerate(jobs):
        where = f"page {job['page'] + 1}, system {i + 1}" + (f" (m{job['first']}-m{job['first'] + job['n'] - 1})" if job["n"] else "")
        sc = vote(results[i], job["n"]) if results[i] else None
        if sc is None or (job["n"] and len(sc.bars) != job["n"]):
            n = job["n"] or 1
            why = f"({errors.get(i)})" if sc is None else f"(it kept giving {len(sc.bars)} measures instead of {n})"
            sc = parse("Z |\n" * n, cur_key, cur_meter)       # the code's own reading where it adds up, else rests
            apply_code(sc, job.get("heads"), cur_key)
            score.warnings.append(f"{where} could not be read by the model {why}; {sum(bool(_filled(b)) for b in sc.bars)}"
                                  f" of {n} measure(s) filled from the notehead reader, the rest are placeholder rests.")
            score.bars += sc.bars
            continue
        left = check(sc, job["n"], job["first"], job["piece_start"], job["piece_end"], ctx["clef"], job.get("heads"))
        if left:
            score.warnings.append(f"{where}: " + " | ".join(left)[:400])
        if sc.bars:
            if sc.key != cur_key and not sc.bars[0].key:
                sc.bars[0].key = sc.key
            if sc.meter[:2] != cur_meter[:2] and not sc.bars[0].meter:
                sc.bars[0].meter = sc.meter
            for b in sc.bars:
                cur_key, cur_meter = b.key or cur_key, b.meter or cur_meter
        score.bars += sc.bars
    say("Done", 1.0)
    transcribe.jobs = jobs          # dev CLI reads per-system logs from here
    return score


# ------------------------------------------------------------------ dev CLI

def _selftest():
    """No-API checks of the deterministic parts: python pipeline.py --selftest"""
    body, labels = normalise("Sure:\n```abc\nm7: B2. B D. B\nm8: z8 |\n```")
    assert body == "B3 B D3/2 B |\nz8 |" and labels == [7, 8], body
    key, meter = (2, "major"), (4, 4, "")
    sc = parse("e e e e e e e e |\nA4 B4 |", key, meter)              # wrong pitches, wrong rhythm in bar 1
    code = [[(28 + i % 3, Fraction(1, 16)) for i in range(16)], [(32, None)]]   # bar 1 read fully by code
    assert apply_code(sc, code, key) == 1
    assert sc.bars[0].total == 1 and sc.bars[0].events[0].pitches[0].step == "C" \
        and sc.bars[0].events[0].pitches[0].alter == 1                # C# from the key signature
    assert check(sc, 2, 1, True, True, "treble", code) == []
    assert check(parse("d2 d2 d2 |", key, meter), 2, 5, False, False, "treble")[0].startswith("You wrote 1")
    a, b = parse("d4 d4 |", key, meter), parse("d4 e4 |", key, meter)
    assert _bar_sig(vote([(a, []), (b, []), (parse("d4 d4 |", key, meter), [])], 1).bars[0]) == _bar_sig(a.bars[0])
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
    calls = [0]
    real = llm.generate

    def counted(*args, **kw):
        calls[0] += 1
        return real(*args, **kw)
    llm.generate = counted
    sc = transcribe(Path(a.file).read_bytes(), provider=prov, models=a.model, key=key, page_range=a.pages,
                    votes=a.votes, thinking=a.thinking,
                    progress=lambda m, f: print(f"  [{'' if f is None else f'{f:.0%}'}] {m}", file=sys.stderr))
    for i, j in enumerate(transcribe.jobs):
        print(f"system {i + 1} p{j['page'] + 1} m{j['first']} n={j['n']}: " + "; ".join(j.get("log", [])))
    abc = abcxml.score_to_abc(sc)
    print(abc)
    print("problems:", [(i + 1, str(f), str(e)) for i, f, e in sc.problems()])
    print("warnings:", *sc.warnings, sep="\n  ")
    print(f"calls (uncached): {calls[0]}, wall: {time.time() - t0:.1f}s")
    if a.out:
        Path(a.out).write_text(abc, encoding="utf-8")
    if a.ref:
        sys.path.insert(0, str(Path(a.ref).parent))
        import bench
        ref = abcxml.parse_abc(Path(a.ref).read_text(encoding="utf-8"))
        print(bench.compare(ref, sc))
