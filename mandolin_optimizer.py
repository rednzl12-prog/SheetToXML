"""Fingering engine for the mandolin family (resources/MANDOLIN_TAB_FINGERING_SPEC.md).

An Arrangement (instrument, tuning, capo, style, hand position, max fret, skill) fixes the courses.
optimize() picks a course and fret for every note of a piece with a Viterbi search over
(fingering, hand position) states, so open strings, hand shifts, string crossings and chord
shapes are judged across the phrase, not note by note. Course index 0 is the lowest course;
MusicXML string 1 is the highest. Tab frets are counted from the capo.
"""

import re
from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations, product
from typing import Any, Dict, List, Optional, Sequence, Tuple

INSTRUMENTS: Dict[str, Dict[str, Any]] = {
    "mandolin": dict(name="Mandolin", abbr="Mand.", frets=20, program=26, octave=0, sound="pluck.mandolin",
                     description="Soprano mandolin: 4 doubled courses tuned in fifths, G3 D4 A4 E5."),
    "mandola": dict(name="Mandola", abbr="Mdla.", frets=19, program=26, octave=0, sound="pluck.mandola",
                    description="Tenor mandola, a fifth below the mandolin (C3 G3 D4 A4); written at pitch."),
    "octave-mandolin": dict(name="Octave Mandolin", abbr="Oct. Mand.", frets=20, program=26, octave=-1,
                            sound="pluck.mandolin.octave",
                            description="An octave below the mandolin (G2 D3 A3 E4): same fingerings, "
                                        "treble clef 8vb."),
    "mandocello": dict(name="Mandocello", abbr="Mcl.", frets=18, program=25, octave=-1, sound="pluck.mandocello",
                       description="An octave below the mandola (C2 G2 D3 A3); treble clef 8vb."),
}

# (id, instrument, name, tuning low->high, description); an instrument's first entry is its standard tuning
TUNINGS: List[Tuple[str, str, str, str, str]] = [
    ("gdae", "mandolin", "GDAE standard", "G3 D4 A4 E5", "Standard tuning in fifths."),
    ("aeae", "mandolin", "AEAE cross", "A3 E4 A4 E5", "Cross tuning for A-major fiddle tunes: ringing open A and E."),
    ("adae", "mandolin", "ADAE", "A3 D4 A4 E5", "G course up to A, for tunes in D and A."),
    ("gdgd", "mandolin", "GDGD cross", "G3 D4 G4 D5", "Cross tuning for tunes in G: open G and D drones."),
    ("gdad", "mandolin", "GDAD", "G3 D4 A4 D5", "E course down to D, for D-modal tunes."),
    ("open-g", "mandolin", "Open G", "G3 D4 G4 B4", "The open courses ring a G major chord."),
    ("open-a", "mandolin", "Open A", "A3 E4 A4 C#5", "The open courses ring an A major chord."),
    ("cgda", "mandola", "CGDA standard", "C3 G3 D4 A4", "Standard mandola tuning."),
    ("octave-gdae", "octave-mandolin", "GDAE standard", "G2 D3 A3 E4", "Standard octave mandolin tuning."),
    ("octave-gdad", "octave-mandolin", "GDAD", "G2 D3 A3 D4", "Irish bouzouki-style tuning."),
    ("mandocello-cgda", "mandocello", "CGDA standard", "C2 G2 D3 A3", "Standard mandocello tuning."),
]
STYLES = [
    ("open", "Open position", "First position with open strings: the beginner-friendly fiddle-tune default."),
    ("closed", "Closed position", "Movable shapes without open strings in one compact hand window; good for "
                                  "tremolo and transposable patterns."),
    ("position", "Fixed position", "Stays around the chosen hand position (index finger at fret N), "
                                   "e.g. 5th or 7th position."),
]
SKILLS = ("beginner", "advanced")
_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_STEP = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_ACC = {"": 0, "#": 1, "♯": 1, "b": -1, "♭": -1}

# Standard mandolin courses, kept for older callers (MusicXML string convention: 1 = E ... 4 = G)
COURSES = [
    {"string": 1, "name": "E", "open_midi": 76, "step": "E", "octave": 5},
    {"string": 2, "name": "A", "open_midi": 69, "step": "A", "octave": 4},
    {"string": 3, "name": "D", "open_midi": 62, "step": "D", "octave": 4},
    {"string": 4, "name": "G", "open_midi": 55, "step": "G", "octave": 3},
]


def note_name(midi: int) -> str:
    return _NAMES[midi % 12] + str(midi // 12 - 1)


def spell(midi: int) -> Tuple[str, int, int]:
    """MIDI -> (step, alter, octave), sharps preferred."""
    n = _NAMES[midi % 12]
    return n[0], len(n) - 1, midi // 12 - 1


def parse_note(name: str) -> int:
    """'C#5' / 'Bb3' / 'g3' -> MIDI; ValueError for anything else."""
    m = re.fullmatch(r"\s*([A-Ga-g])([#b♯♭]?)(-?\d)\s*", str(name))
    if not m:
        raise ValueError(f"'{name}' is not a note name with an octave, like G3 or C#5")
    return 12 * (int(m.group(3)) + 1) + _STEP[m.group(1).upper()] + _ACC[m.group(2)]


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


@dataclass(frozen=True)
class Arrangement:
    instrument: str = "mandolin"
    tuning: Tuple[int, ...] = (55, 62, 69, 76)      # open courses, MIDI, low -> high
    tuning_label: str = "GDAE standard"
    capo: int = 0
    style: str = "open"
    position: int = 5
    max_fret: int = 20                              # highest physical fret
    skill: str = "beginner"

    @property
    def inst(self) -> Dict[str, Any]:
        return INSTRUMENTS[self.instrument]

    @property
    def opens(self) -> Tuple[int, ...]:
        """Sounding open courses with the capo on."""
        return tuple(m + self.capo for m in self.tuning)

    @property
    def frets(self) -> int:
        """Highest tab fret (counted from the capo)."""
        return self.max_fret - self.capo

    @property
    def low(self) -> int:
        return min(self.opens)

    @property
    def high(self) -> int:
        return max(self.opens) + self.frets

    @property
    def names(self) -> List[str]:
        return [note_name(m) for m in self.tuning]

    @property
    def labels(self) -> List[str]:
        """Course labels for text tab: letters when unique (G D A E), else with octaves (G3 D4 G4 D5)."""
        letters = [n.rstrip("-0123456789") for n in self.names]
        return letters if len(set(letters)) == len(letters) else self.names

    def places(self, midi: int) -> List[Tuple[int, int]]:
        """Every (course, tab fret) that sounds this pitch."""
        return [(c, midi - o) for c, o in enumerate(self.opens) if 0 <= midi - o <= self.frets]

    def describe(self) -> str:
        style = {"open": "open position", "closed": "closed position"}.get(self.style) \
            or f"{_ordinal(self.position)} position"
        return f"{self.inst['name']}, {self.tuning_label}, {style}, {self.skill}" + (f", capo {self.capo}" if self.capo else "")


def presets() -> Dict[str, List[Dict[str, Any]]]:
    """Everything a UI needs to offer arrangements; ids are what arrangement() accepts."""
    std = {}
    for tid, inst, _, notes, _ in TUNINGS:
        std.setdefault(inst, notes)
    return {
        "instruments": [{"id": k, "name": v["name"], "tuning": std[k].split(), "frets": v["frets"],
                         "description": v["description"]} for k, v in INSTRUMENTS.items()],
        "tunings": [{"id": tid, "name": name, "instrument": inst, "tuning": notes.split(), "description": desc}
                    for tid, inst, name, notes, desc in TUNINGS],
        "styles": [{"id": sid, "name": name, "description": desc} for sid, name, desc in STYLES],
    }


def _int(value: Any, what: str, lo: int, hi: int) -> int:
    try:
        if isinstance(value, bool):
            raise ValueError
        n = int(str(value).strip())
    except ValueError:
        raise ValueError(f"{what} must be a whole number, got '{value}'") from None
    if not lo <= n <= hi:
        raise ValueError(f"{what} must be between {lo} and {hi}, got {n}")
    return n


def arrangement(opts: Optional[Dict[str, Any]] = None) -> Arrangement:
    """JSON options -> Arrangement. Keys: instrument, tuning (preset id or 4 note names), capo, style,
    position, maxFret, skill; all optional. ValueError with a readable message for bad input."""
    o = {k: v for k, v in (opts or {}).items() if v is not None and v != ""}
    inst = str(o.get("instrument", "mandolin")).strip().lower().replace(" ", "-").replace("_", "-")
    if inst not in INSTRUMENTS:
        raise ValueError(f"Unknown instrument '{o.get('instrument')}'; choose one of: {', '.join(INSTRUMENTS)}")
    std = next(t for t in TUNINGS if t[1] == inst)
    t = o.get("tuning", std[0])
    tid = t.strip().lower().replace(" ", "-").replace("_", "-") if isinstance(t, str) else None
    preset = next((x for x in TUNINGS if x[0] == tid), None)
    if preset:
        if preset[1] != inst:
            raise ValueError(f"Tuning '{preset[0]}' is for the {INSTRUMENTS[preset[1]]['name'].lower()}, "
                             f"not the {INSTRUMENTS[inst]['name'].lower()}")
        label, notes = preset[2], [parse_note(n) for n in preset[3].split()]
    else:
        names = t.replace(",", " ").split() if isinstance(t, str) else list(t) if isinstance(t, (list, tuple)) else []
        if len(names) != 4:
            raise ValueError(f"Tuning must be a preset id or 4 note names low to high (e.g. G3 D4 A4 E5), got {t!r}")
        notes = [parse_note(n) for n in names]
        if not all(36 <= n <= 84 for n in notes):
            raise ValueError("Every course must be tuned between C2 and C6")
        if any(b <= a for a, b in zip(notes, notes[1:])):
            raise ValueError("List the tuning from the lowest course to the highest, each course higher than the last")
        label = "custom tuning " + " ".join(note_name(n) for n in notes)
        hit = next((x for x in TUNINGS if x[1] == inst and [parse_note(n) for n in x[3].split()] == notes), None)
        if hit:
            label = hit[2]
    capo = _int(o.get("capo", 0), "Capo", 0, 7)
    max_fret = _int(o.get("maxFret", INSTRUMENTS[inst]["frets"]), "Max fret", capo + 5, 29)
    style = str(o.get("style", "open")).strip().lower()
    if style not in [s[0] for s in STYLES]:
        raise ValueError(f"Unknown fingering style '{o.get('style')}'; choose open, closed or position")
    top = max(1, max_fret - capo - 4)
    position = _int(o.get("position", 5), "Hand position", 1, top if style == "position" else 29)
    position = min(position, top)
    skill = str(o.get("skill", "beginner")).strip().lower()
    if skill not in SKILLS:
        raise ValueError(f"Skill must be beginner or advanced, got '{o.get('skill')}'")
    return Arrangement(inst, tuple(notes), label, capo, style, position, max_fret, skill)


# ---------------------------------------------------------------- cost model

def _weights(arr: Arrangement) -> Dict[str, Any]:
    """Style x skill weights. Lower total cost = better fingering."""
    beg = arr.skill == "beginner"
    st = 2.0 if beg else 1.0
    w = {"shift": 3.0 if beg else 2.0, "per": 0.8 if beg else 0.5, "open": -1.2 if beg else -0.6,
         "reach": [st, 0, 0, 0, 0, 0, 0.4 if beg else 0.2, st],          # fret - hand = -1 .. 6
         "cross": (0.0, 0.6, 2.5 if beg else 1.8, 6.0 if beg else 4.0), "dev": 0.0,
         "spread": 4 if beg else 5, "beg": beg}
    if arr.style == "closed":
        w.update(shift=4.5 if beg else 3.5, open=7.0)
    elif arr.style == "position":
        w.update(shift=4.0 if beg else 3.0, open=2.5, dev=2.5 if beg else 1.5)
    return w


def _height(f: int, arr: Arrangement, w: Dict[str, Any]) -> float:
    """Fret-height cost (spec 11, 21): 'open' style lives in frets 0-7; the other styles let the hand window decide."""
    if arr.style == "open":
        if f <= 7:
            return 0.1 * f
        return 0.7 + min(f - 7, 5) * (4 if w["beg"] else 2) + max(0, f - 12) * (8 if w["beg"] else 4)
    return 0.03 * f + max(0, f - 12) * (3 if w["beg"] else 1.5)


def _state(shape: Sequence[Tuple[int, int, int]], h: Optional[int], arr: Arrangement, w: Dict[str, Any]) -> float:
    """Cost of one fingering ((pitch index, course, fret), ...) held with the index finger at fret h
    (None = hand not placed yet)."""
    c, fretted = 0.0, [f for _, _, f in shape if f]
    for _, _, f in shape:
        if f == 0:
            c += w["open"]
            if arr.style == "open" and h and h > 5:
                c += 0.6 * (h - 5)              # an open string far from the hand breaks the position's sound
        else:
            c += _height(f, arr, w) + (w["reach"][f - h + 1] if h is not None else 0)
    if len(shape) > 1:
        if fretted:
            c += 0.6 * (max(fretted) - min(fretted))
        cs = sorted(k for _, k, _ in shape)
        c += 1.5 * (cs[-1] - cs[0] + 1 - len(cs))   # a skipped course rings when strummed
    if fretted and h is not None:
        c += 0.02 * h + w["dev"] * abs(h - arr.position)
    return c


def _shapes(midis: Sequence[int], arr: Arrangement, w: Dict[str, Any]) -> List[tuple]:
    """Playable fingerings for simultaneous pitches, each ((pitch index, course, fret), ...) with the highest
    (melody) pitch first. No two notes on one course, fretted spread within reach; when not every pitch fits,
    the largest playable subset that keeps the melody note wins."""
    cand = [i for i in sorted(range(len(midis)), key=lambda i: -midis[i]) if arr.places(midis[i])]
    for k in range(min(len(cand), len(arr.tuning)), 0, -1):
        found = []
        for rest in combinations(cand[1:], k - 1):
            keep = (cand[0],) + rest
            for combo in product(*(arr.places(midis[i]) for i in keep)):
                fretted = [f for _, f in combo if f]
                if len({c for c, _ in combo}) == k and (not fretted or max(fretted) - min(fretted) <= w["spread"]):
                    found.append(tuple((i, c, f) for i, (c, f) in zip(keep, combo)))
        if found:
            if len(found) > 12:                 # ponytail: prune big chords by hand-free cost, beam search if it misses
                found.sort(key=lambda s: _state(s, None, arr, w))
            return found[:12]
    return []

def _shift(d: int, dt: Fraction, gap: bool, free: bool, w: Dict[str, Any]) -> float:
    c = w["shift"] + w["per"] * d + 2.0 * max(0, d - 7)
    if gap or dt >= Fraction(1, 4):
        return c * 0.35                        # shift during a rest or a long note (spec 12)
    if free:
        return c * 0.6                         # previous note was open: the hand was free to move
    return c * 1.4 if dt <= Fraction(1, 16) else c


def _cross(k: int, dt: Fraction, gap: bool, w: Dict[str, Any]) -> float:
    c = w["cross"][min(k, 3)]
    if gap or dt >= Fraction(1, 4):
        return c * 0.5
    return c * 1.4 if dt <= Fraction(1, 16) else c


def optimize(steps: Sequence[Tuple[Sequence[int], Fraction, bool]],
             arr: Optional[Arrangement] = None) -> List[List[Optional[Dict[str, Any]]]]:
    """Fingering for a whole piece. steps: (MIDI pitches sounding together, time to the next step in whole
    notes, rest before it). Returns per step, aligned with its pitches, a dict {string, course, fret, open,
    midi, hand} or None for a pitch that cannot be played (out of range, or a chord too big/wide)."""
    arr = arr or arrangement()
    w = _weights(arr)
    n = len(arr.tuning)
    out: List[List[Optional[Dict[str, Any]]]] = [[None] * len(m) for m, _, _ in steps]
    layers = []                                # (step index, shapes, {(shape, hand): (cost, back key)})
    since, gap = Fraction(0), False
    for t, (midis, dur, rest) in enumerate(steps):
        gap = gap or rest
        shapes = _shapes(midis, arr, w)
        if not shapes:
            since += dur
            continue
        opts = []
        for si, shape in enumerate(shapes):
            fretted = [f for _, _, f in shape if f]
            hands = range(max(1, max(fretted) - 6), min(min(fretted) + 1, arr.frets) + 1) if fretted else []
            opts.append((si, shape[0][1], [(h, _state(shape, h, arr, w)) for h in hands]))
        cur: Dict[Tuple[int, Optional[int]], Tuple[float, Any]] = {}
        if not layers:
            for si, _, hs in opts:
                for h, sc in hs or [(None, _state(shapes[si], None, arr, w))]:
                    cur[(si, h)] = (sc, None)
        else:
            _, pshapes, prev = layers[-1]
            for pk, (pc, _) in prev.items():
                psh, ph = pshapes[pk[0]], pk[1]
                ptop, free = psh[0][1], all(f == 0 for _, _, f in psh)
                for si, top, hs in opts:
                    base = pc + _cross(abs(top - ptop), since, gap, w)
                    for h, sc in hs or [(ph, _state(shapes[si], ph, arr, w))]:
                        c = base + sc
                        if hs and ph is not None and h != ph:
                            c += _shift(abs(h - ph), since, gap, free, w)
                        if c < cur.get((si, h), (float("inf"),))[0]:
                            cur[(si, h)] = (c, pk)
        layers.append((t, shapes, cur))
        since, gap = dur, False
    key = min(layers[-1][2], key=lambda k: layers[-1][2][k][0]) if layers else None
    for t, shapes, states in reversed(layers):
        si, h = key
        for i, c, f in shapes[si]:
            out[t][i] = {"string": n - c, "course": arr.labels[c], "fret": f, "open": f == 0,
                         "midi": steps[t][0][i], "hand": h}
        key = states[key][1]
    return out


def optimize_melodic_phrase(midi_sequence: List[int], skill_level: str = "beginner",
                            max_fret: int = 20) -> List[Optional[Dict[str, Any]]]:
    """Older single-line API on the standard mandolin (eighth notes assumed)."""
    arr = arrangement({"skill": "beginner" if skill_level.lower() == "beginner" else "advanced", "maxFret": max_fret})
    return [r[0] for r in optimize([([m], Fraction(1, 8), False) for m in midi_sequence], arr)]


def optimize_chord_fingering(midi_pitches: List[int], skill_level: str = "beginner",
                             max_fret: int = 20) -> List[Optional[Dict[str, Any]]]:
    """Older single-chord API on the standard mandolin."""
    skill = "beginner" if skill_level.lower() == "beginner" else "advanced"
    return optimize([(midi_pitches, Fraction(1, 4), False)], arrangement({"skill": skill, "maxFret": max_fret}))[0]


if __name__ == "__main__":
    import abcxml

    def check(arr: Arrangement, steps, res):
        """Every placed note sounds its pitch, chords use distinct courses within reach."""
        for (midis, _, _), r in zip(steps, res):
            placed = [p for p in r if p]
            for m, p in zip(midis, r):
                if p:
                    assert arr.opens[len(arr.tuning) - p["string"]] + p["fret"] == m and 0 <= p["fret"] <= arr.frets, p
            assert len({p["string"] for p in placed}) == len(placed), r
            fr = [p["fret"] for p in placed if p["fret"]]
            assert not fr or max(fr) - min(fr) <= _weights(arr)["spread"], r

    a = arrangement()
    assert a.describe() == "Mandolin, GDAE standard, open position, beginner" and a.labels == list("GDAE")
    b = arrangement({"instrument": "Octave Mandolin", "tuning": ["G2", "D3", "A3", "D4"], "capo": "2",
                     "style": "position", "position": 7, "skill": "advanced"})
    assert b.describe() == "Octave Mandolin, GDAD, 7th position, advanced, capo 2", b.describe()
    assert arrangement({"tuning": "A3 E4 A4 E5"}).tuning_label == "AEAE cross"
    assert arrangement({"tuning": "Open A"}).names == ["A3", "E4", "A4", "C#5"]
    assert arrangement({"maxFret": 7}).position == 3 and arrangement({"style": "position", "position": 15}).position == 15
    assert arrangement({"tuning": "G3 D4 A4 D5"}).labels == ["G3", "D4", "A4", "D5"]
    for bad in ({"instrument": "banjo"}, {"tuning": "cgda"}, {"tuning": ["G3", "D4", "A4"]}, {"tuning": "E5 A4 D4 G3"},
                {"tuning": "G1 D4 A4 E5"}, {"tuning": "G3 D4 A4 X5"}, {"capo": 9}, {"capo": "two"},
                {"style": "jazz"}, {"skill": "pro"}, {"position": 0}, {"maxFret": 3},
                {"style": "position", "position": 9, "maxFret": 12}):
        try:
            arrangement(bad)
            raise AssertionError(bad)
        except ValueError as e:
            assert str(e) and "Traceback" not in str(e)
    p = presets()
    assert [i["id"] for i in p["instruments"]] == list(INSTRUMENTS) and len(p["styles"]) == 3
    assert all(arrangement({"instrument": t["instrument"], "tuning": t["id"]}).names == t["tuning"] for t in p["tunings"])

    # capo: frets counted from the capo, pitches below it unplayable on that course
    cap = arrangement({"capo": 2})
    assert cap.places(57) == [(0, 0)] and cap.places(55) == [] and optimize([([55], Fraction(1, 4), False)], cap)[0] == [None]
    # chords: never two notes on one course; a 5-note G chord keeps the open G shape and drops the doubled G4
    r = optimize_chord_fingering([55, 62, 71, 79, 67])
    assert [x and x["fret"] for x in r] == [0, 0, 2, 3, None], r
    assert optimize_melodic_phrase([76])[0]["fret"] == 0 and optimize_melodic_phrase([40]) == [None]

    # the three styles on a fiddle tune: all valid, genuinely different
    tune = abcxml.parse_abc("X:1\nM:4/4\nL:1/8\nK:D\n|:FAdf afdf|g2fe dcBA|Bcde fgaf|edcB A2FA|"
                            "dfaf bgec|d2 [DAf]2 [Ace]4|defg afdf|edcd [d2f2] z2:|")
    steps = [([p.midi for p in e.pitches], e.dur, False) for b in tune.bars for e in b.events if e.pitches]
    fing = {}
    for style in ("open", "closed", "position"):
        for skill in SKILLS:
            arr = arrangement({"style": style, "skill": skill})
            res = optimize(steps, arr)
            check(arr, steps, res)
            assert all(x for r in res for x in r), (style, skill)
            fing[style, skill] = [(x["string"], x["fret"]) for r in res for x in r]
    opens = {k: sum(f == 0 for _, f in v) for k, v in fing.items()}
    assert opens["open", "beginner"] >= 10 and opens["closed", "beginner"] <= 2, opens
    assert len({tuple(fing[s, "beginner"]) for s in ("open", "closed", "position")}) == 3
    pos = optimize(steps, arrangement({"style": "position", "position": 7}))
    hands = [x["hand"] for r in pos for x in r if x["fret"]]
    assert sum(abs(h - 7) <= 1 for h in hands) >= 0.8 * len(hands), hands
    assert max(f for _, f in fing["open", "beginner"]) <= 7
    # a cross tuning re-fingers the same notes: AEAE plays single A and E naturals as open strings
    ae = optimize(steps, arrangement({"tuning": "aeae"}))
    assert [x["fret"] for r in ae for x in r] != [f for _, f in fing["open", "beginner"]]
    assert all(x["fret"] == 0 for r, (m, _, _) in zip(ae, steps) if len(m) == 1 for x in r if x["midi"] in (57, 64, 69, 76))
    print("mandolin_optimizer self-check ok")
