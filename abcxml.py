"""ABC-subset parser and MusicXML 4.0 writer (notation staff + mandolin TAB staff).

Vision models read notation into compact ABC far more reliably than into raw MusicXML,
and ABC leaves key-signature accidentals and bar arithmetic to code. All durations are
Fractions of a whole note.
"""

import copy
import io
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction
from math import ceil, gcd
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

import mandolin_optimizer as mando

STEP_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
SHARPS, FLATS = "FCGDAEB", "BEADGCF"
TONIC_FIFTHS = {"C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5, "F": -1}
MODE_SHIFT = {"major": 0, "mixolydian": -1, "dorian": -2, "minor": -3, "phrygian": -4, "lydian": 1, "locrian": -5}
MODE_NAMES = {"": "major", "maj": "major", "ion": "major", "m": "minor", "min": "minor", "aeo": "minor",
              "mix": "mixolydian", "dor": "dorian", "phr": "phrygian", "lyd": "lydian", "loc": "locrian"}
ACC_NAME = {-2: "flat-flat", -1: "flat", 0: "natural", 1: "sharp", 2: "double-sharp"}
TYPES = [("whole", Fraction(1)), ("half", Fraction(1, 2)), ("quarter", Fraction(1, 4)), ("eighth", Fraction(1, 8)),
         ("16th", Fraction(1, 16)), ("32nd", Fraction(1, 32)), ("64th", Fraction(1, 64)), ("128th", Fraction(1, 128))]
BEAM_LEVEL = {"eighth": 1, "16th": 2, "32nd": 3, "64th": 4, "128th": 5}
# every notatable length (plain, dotted, double-dotted), longest first
NOTATABLE = sorted(((v * (2 - Fraction(1, 2 ** d)), t, d) for t, v in TYPES for d in range(3)), reverse=True)
LOW_MIDI = mando.COURSES[-1]["open_midi"]          # G3
HIGH_MIDI = mando.COURSES[0]["open_midi"] + 20     # E5 + 20 frets = C7


@dataclass
class Pitch:
    step: str
    alter: int
    octave: int
    show: bool = False                 # print an accidental sign

    @property
    def midi(self) -> int:
        return 12 + 12 * self.octave + STEP_SEMITONE[self.step] + self.alter

    def same(self, other: "Pitch") -> bool:
        return (self.step, self.alter, self.octave) == (other.step, other.alter, other.octave)


@dataclass
class Ev:
    """One rhythmic event: a note, chord or rest."""
    dur: Fraction                      # sounding length, whole notes
    pitches: List[Pitch] = field(default_factory=list)   # empty = rest
    tie: bool = False                  # tied into the next event
    chord_symbol: str = ""
    tuplet: Optional[Tuple[int, int]] = None             # (actual, normal)
    tpos: str = ""                     # "start" / "stop" on the first / last member
    whole_bar: bool = False            # full-measure rest


@dataclass
class Bar:
    events: List[Ev] = field(default_factory=list)
    expected: Fraction = Fraction(1)
    key: Optional[Tuple[int, str]] = None      # set when the key changes here
    meter: Optional[Tuple[int, int, str]] = None
    tempo: Optional[float] = None
    left: str = ""                     # "repeat"
    right: str = ""                    # "repeat" | "double" | "final"
    ending: str = ""                   # volta numbers that start here, e.g. "1, 2"
    ending_stop: Optional[Tuple[str, str]] = None  # (numbers, "stop" | "discontinue")

    @property
    def total(self) -> Fraction:
        return sum((e.dur for e in self.events), Fraction(0))


@dataclass
class Score:
    bars: List[Bar] = field(default_factory=list)
    title: str = ""
    composer: str = ""
    key: Tuple[int, str] = (0, "major")
    meter: Tuple[int, int, str] = (4, 4, "")
    tempo: Optional[float] = None      # quarter notes per minute
    warnings: List[str] = field(default_factory=list)

    def problems(self, first_ok: bool = True, last_ok: bool = True) -> List[Tuple[int, Fraction, Fraction]]:
        """(bar index, length found, length expected) for bars that don't fill their meter."""
        out = []
        for i, b in enumerate(self.bars):
            if b.total == b.expected:
                continue
            short = b.total < b.expected
            if short and ((i == 0 and first_ok) or (i == len(self.bars) - 1 and last_ok)):
                continue
            out.append((i, b.total, b.expected))
        return out


# ---------------------------------------------------------------- field parsing

def parse_key(text: str) -> Optional[Tuple[int, str]]:
    """'D', 'F#m', 'Bb minor', 'D Major', 'G clef=bass', 'none' -> (fifths, mode); clefs are ignored."""
    t = text.strip().replace("♯", "#").replace("♭", "b")
    if t.lower().startswith("none"):
        return 0, "major"
    m = re.match(r"([A-Ga-g])([#b]?)\s*([A-Za-z]*)", t)
    mode = MODE_NAMES.get(m.group(3).lower()[:3]) if m else None
    if not m or (m.group(1).islower() and mode is None):     # 'd minor' is a key, 'bass' / 'clef=...' are not
        return None
    if mode is None:
        mode = "major"
    fifths = TONIC_FIFTHS[m.group(1).upper()] + {"#": 7, "b": -7, "": 0}[m.group(2)] + MODE_SHIFT[mode]
    if abs(fifths) > 7:
        fifths -= 12 if fifths > 0 else -12      # enharmonic twin, e.g. G# major -> Ab major
    return fifths, mode


def parse_meter(text: str) -> Optional[Tuple[int, int, str]]:
    t = text.strip()
    if t.upper() == "C":
        return 4, 4, "common"
    if t.upper() == "C|":
        return 2, 2, "cut"
    m = re.match(r"\(?\s*(\d+(?:\s*\+\s*\d+)*)\s*\)?\s*/\s*(\d+)", t)      # 3/4, 2+3/8, (2+3)/8
    if not m or not int(m.group(2)):
        return None                                                       # M:none -> keep current
    return sum(int(x) for x in m.group(1).split("+")), int(m.group(2)), ""


def parse_tempo(text: str) -> Optional[float]:
    m = re.search(r"(\d+)\s*/\s*(\d+)\s*=\s*(\d+)", text)
    if m and int(m.group(2)):
        return float(int(m.group(3)) * Fraction(int(m.group(1)), int(m.group(2))) * 4)
    m = re.fullmatch(r'\s*(?:"[^"]*"\s*)?(\d+)\s*', text)
    return float(m.group(1)) if m else None


def key_alters(fifths: int) -> Dict[str, int]:
    return {s: 1 for s in SHARPS[:fifths]} if fifths > 0 else {s: -1 for s in FLATS[:-fifths]}


def _mult(s: str) -> Fraction:
    """ABC length suffix ('', '2', '/', '3/2', '//4') as a multiplier."""
    m = re.fullmatch(r"(\d*)((?:/\d*)*)", s or "")
    if not m:
        return Fraction(1)
    f = Fraction(int(m.group(1)) if m.group(1) else 1)
    for d in re.findall(r"/(\d*)", m.group(2)):
        f /= int(d) if d and int(d) else 2
    return f


# ---------------------------------------------------------------- ABC parser

_TOKEN = re.compile(r"""
   (?P<ws>\s+)
 | (?P<inline>\[[A-Za-z]:[^\]\n]*\])
 | (?P<chordsym>"[^"\n]*")
 | (?P<deco>![^!\n]*!|\+[^+\n]*\+)
 | (?P<grace>\{[^}\n]*\})
 | (?P<tuplet>\(\d+(?::\d*){0,2})
 | (?P<bar>\[?[|:]+\]?(?:\[?\d[\d,-]*)?)
 | (?P<volta>\[\d[\d,-]*)
 | (?P<chord>\[(?:[\^_=]*[A-Ga-g][,']*[\d/]*-?\s*)+\][\d/]*-?)
 | (?P<note>[\^_=]*[A-Ga-g][,']*[\d/]*)
 | (?P<rest>[zxZX][,']*[\d/]*)
 | (?P<tie>-)
 | (?P<broken>>+|<+)
 | (?P<overlay>&)
 | (?P<misc>[()~.HLMOPSTuvy\\*`$])
 | (?P<other>.)
""", re.X)
_NOTE = re.compile(r"([\^_=]*)([A-Ga-g])([,']*)([\d/]*)(-?)")
_FIELD = re.compile(r"^\s*([A-Za-z+]):(?![|:\]])\s*(.*)$")
# bar numbers a model may prefix to a line: "m12:", "Bar 3.", "12|" (a music line never starts with a digit)
_BAR_PREFIX = re.compile(r"^\s*(?:(?:m|bar|measure)\s*)?\d+\s*(?:[:.)](?![|:])\s*)?", re.I)
_UNI_ACC = str.maketrans({"♯": "^", "♭": "_", "♮": "=", "’": "'"})


def _unicode_accidentals(line: str) -> str:
    """'F♯' / '♯F' -> '^F' outside quotes; 'F♯m' inside a chord symbol -> 'F#m'."""
    parts = re.split(r'("[^"]*")', line)
    for i, s in enumerate(parts):
        if i % 2:
            parts[i] = s.replace("♯", "#").replace("♭", "b")
        else:
            s = re.sub(r"([A-Ga-g][,']*)([♯♭♮]+)(?![A-Ga-g♯♭♮])", lambda m: m.group(2) + m.group(1), s)
            parts[i] = s.translate(_UNI_ACC)
    return "".join(parts)


class _Parser:
    def __init__(self, meter: Tuple[int, int, str] = (4, 4, "")):
        self.sc = Score(meter=meter)
        self.meter = meter
        self.key = self.sc.key
        self.alters: Dict[str, int] = {}
        self.unit: Optional[Fraction] = None
        self.tempo: Optional[float] = None
        self.cur = Bar(expected=self._bar_len())
        self.acc: Dict[Tuple[str, int], int] = {}      # accidentals in force this bar
        self.tie_alter: Dict[Tuple[str, int], int] = {}  # pitches carried over a tie
        self.tup: Optional[List[int]] = None           # [actual, normal, total, left]
        self.broken: Optional[Tuple[Ev, Fraction, Fraction]] = None  # (previous event, its factor, next factor)
        self.symbol = ""
        self.ending_open = ""
        self.started = False
        self.in_body = False                           # a K: field has been seen
        self.main_voice: Optional[str] = None          # first V: id; other voices are skipped
        self.skip = False                              # inside another voice
        self.overlay = False                           # inside an '&' overlay until the next barline
        self.skipped_voices = set()
        self.unsupported = set()

    def _bar_len(self) -> Fraction:
        return Fraction(self.meter[0], self.meter[1])

    def _compound(self) -> bool:
        return self.meter[0] % 3 == 0 and self.meter[0] > 3 and self.meter[1] >= 8

    # -- fields
    def field(self, letter: str, value: str):
        v = value.strip()
        if letter == "V":
            self.voice(v)
        elif letter == "K":
            self.in_body = True
            k = parse_key(v)
            if k and k != self.key:
                self.key, self.alters = k, key_alters(k[0])
                if not self.started:
                    self.sc.key = k
                else:
                    self.cur.key = k
        elif letter == "M":
            m = parse_meter(v)
            if m and m != self.meter:
                self.meter = m
                if not self.started:
                    self.sc.meter = m
                else:
                    self.cur.meter = m
                if not self.cur.events:
                    self.cur.expected = self._bar_len()
        elif letter == "L":
            m = re.match(r"(\d+)\s*/\s*(\d+)", v)
            if m and int(m.group(2)) and int(m.group(1)):
                self.unit = Fraction(int(m.group(1)), int(m.group(2)))
        elif letter == "Q":
            t = parse_tempo(v)
            if t and t != self.tempo:
                self.tempo = t
                if not self.started:
                    self.sc.tempo = t
                else:
                    self.cur.tempo = t
        elif letter == "T" and not self.sc.title:
            self.sc.title = v
        elif letter == "C" and not self.sc.composer:
            self.sc.composer = v

    def voice(self, value: str):
        """Keep the first voice only. V: lines before K: are declarations and switch nothing."""
        vid = (value.split() or [""])[0]
        if self.main_voice is None:
            self.main_voice = vid
        elif self.in_body:
            self.skip = vid != self.main_voice
            if self.skip:
                self.skipped_voices.add(vid)

    # -- events
    def _unit(self) -> Fraction:
        if self.unit is None:
            self.unit = Fraction(1, 16) if self.meter[0] / self.meter[1] < 0.75 else Fraction(1, 8)
        return self.unit

    def _pitch(self, acc: str, letter: str, marks: str) -> Pitch:
        step = letter.upper()
        octave = (4 if letter.isupper() else 5) + marks.count("'") - marks.count(",")
        key = (step, octave)
        if acc:
            alter = acc.count("^") - acc.count("_")
            self.acc[key] = alter
            return Pitch(step, alter, octave, True)
        if key in self.tie_alter:
            return Pitch(step, self.tie_alter[key], octave)
        if key in self.acc:
            return Pitch(step, self.acc[key], octave)
        return Pitch(step, self.alters.get(step, 0), octave)

    def _add(self, pitches: List[Pitch], dur: Fraction, tie: bool = False, whole_bar: bool = False) -> Ev:
        ev = Ev(dur=dur, pitches=pitches, tie=tie, chord_symbol=self.symbol, whole_bar=whole_bar)
        self.symbol = ""
        if self.broken:
            prev, a, b = self.broken
            prev.dur *= a
            ev.dur *= b
            self.broken = None
        if self.tup:
            actual, normal, total, left = self.tup
            ev.dur *= Fraction(normal, actual)
            ev.tuplet = (actual, normal)
            ev.tpos = "start" if left == total else ""
            self.tup[3] -= 1
            if self.tup[3] == 0:
                ev.tpos = "stop" if total > 1 else ""
                if total == 1:
                    ev.tuplet = None
                self.tup = None
        self.tie_alter = {(p.step, p.octave): p.alter for p in pitches} if tie else {}
        self.cur.events.append(ev)
        self.started = True
        return ev

    def note(self, tok: str):
        m = _NOTE.fullmatch(tok)
        if not m:
            self.unsupported.add(tok)
            return
        acc, letter, marks, length, _ = m.groups()
        self._add([self._pitch(acc, letter, marks)], self._unit() * _mult(length))

    def chord(self, tok: str):
        inner, tail = tok[1:].split("]", 1)
        first_len, tied, pitches = None, False, []
        for m in _NOTE.finditer(inner):
            acc, letter, marks, length, tie = m.groups()
            if first_len is None:
                first_len = _mult(length)
            tied = tied or bool(tie)
            pitches.append(self._pitch(acc, letter, marks))
        t = re.fullmatch(r"([\d/]*)(-?)", tail)
        outer, tied2 = (t.group(1), bool(t.group(2))) if t else ("", False)
        self._add(pitches, self._unit() * (first_len or 1) * _mult(outer), tie=tied or tied2)

    def rest(self, tok: str):
        kind, length = tok[0], tok.lstrip("zxZX,'")      # octave marks on rests mean nothing
        mult = _mult(length)
        if kind in "ZX":
            for i in range(int(mult) if mult.denominator == 1 and mult >= 1 else 1):
                if i:
                    self.finish(False, "")
                self._add([], self.cur.expected, whole_bar=True)
            return
        self._add([], self._unit() * mult)

    def tie_prev(self):
        ev = self.cur.events[-1] if self.cur.events else None
        if ev is None and self.sc.bars:
            ev = self.sc.bars[-1].events[-1]
        if ev is not None and ev.pitches:
            ev.tie = True
            self.tie_alter = {(p.step, p.octave): p.alter for p in ev.pitches}

    def tuplet(self, tok: str):
        parts = tok[1:].split(":")
        actual = int(parts[0])
        default = {2: 3, 3: 2, 4: 3, 6: 2, 8: 3}.get(actual, 3 if actual in (5, 7, 9) and self._compound() else 2)
        normal = int(parts[1]) if len(parts) > 1 and parts[1] else default
        total = int(parts[2]) if len(parts) > 2 and parts[2] else actual
        if actual > 0 and normal > 0 and total > 0:
            self.tup = [actual, normal, total, total]

    def broken_rhythm(self, tok: str):
        """Applied when the next event arrives, so a stray '>' before a barline changes nothing."""
        n = min(len(tok), 3)
        small = Fraction(1, 2 ** n)
        a, b = (2 - small, small) if tok[0] == ">" else (small, 2 - small)
        if not self.cur.events:
            self.unsupported.add(tok)
            return
        self.broken = (self.cur.events[-1], a, b)

    # -- bars
    def finish(self, end_repeat: bool, kind: str):
        bar = self.cur
        if self.tup:                               # a tuplet cut short by the barline ends here
            actual, normal, total, left = self.tup
            if total - left >= 2:
                bar.events[-1].tpos = "stop"
            elif total - left == 1:                # a lone member is no tuplet: restore its length
                ev = bar.events[-1]
                ev.dur *= Fraction(actual, normal)
                ev.tuplet, ev.tpos = None, ""
            self.unsupported.add(f"({actual}…|")
            self.tup = None
        if self.broken:
            self.unsupported.add(">|")
            self.broken = None
        bar.right = "repeat" if end_repeat else kind
        if end_repeat and self.ending_open:
            bar.ending_stop = (self.ending_open, "stop")
            self.ending_open = ""
        self.sc.bars.append(bar)
        self.cur = Bar(expected=self._bar_len())
        self.acc = {}

    def barline(self, tok: str):
        m = re.fullmatch(r"(\[?[|:]+\]?)(?:\[?(\d[\d,-]*))?", tok)
        if not m:
            self.unsupported.add(tok)
            return
        b, volta = m.groups()
        end_rep = b.startswith(":")
        start_rep = b.rstrip("]").endswith(":")
        kind = "final" if b.endswith("]") else "double" if b.count("|") > 1 or b.startswith("[") else ""
        if self.cur.events:
            self.finish(end_rep, kind)
            if kind and self.ending_open and not end_rep:      # || or |] closes an open volta
                self.sc.bars[-1].ending_stop = (self.ending_open, "discontinue")
                self.ending_open = ""
        elif self.sc.bars:
            last = self.sc.bars[-1]
            if end_rep:
                last.right = "repeat"
                if self.ending_open:
                    last.ending_stop = (self.ending_open, "stop")
                    self.ending_open = ""
            elif kind and not last.right:
                last.right = kind
        if start_rep:
            self.cur.left = "repeat"
        if volta:
            self.volta(volta)

    def volta(self, text: str):
        nums = []
        for part in text.split(","):
            lo, _, hi = part.partition("-")
            nums += range(int(lo), int(hi or lo) + 1) if lo.isdigit() and (not hi or hi.isdigit()) else []
        if not nums:
            return
        if self.ending_open and self.sc.bars and not self.sc.bars[-1].ending_stop:
            self.sc.bars[-1].ending_stop = (self.ending_open, "discontinue")
        self.cur.ending = self.ending_open = ", ".join(str(n) for n in nums)

    # -- driver
    def feed(self, text: str):
        for raw in text.splitlines():
            line = raw.split("%", 1)[0]
            if not line.strip():
                continue
            fm = _FIELD.match(line)
            if fm:                                 # every 'X:' line is a field (w:, s:, +:, unknown ones ignored)
                letter = fm.group(1)
                if letter == "V" or (not self.skip and letter in "TCMLQK"):
                    self.field(letter, fm.group(2))
                continue
            if self.skip:
                if "[V:" not in line:
                    continue
            line = _BAR_PREFIX.sub("", line, 1)
            if any(c in line for c in "♯♭♮’"):
                line = _unicode_accidentals(line)
            for m in _TOKEN.finditer(line):
                kind, tok = m.lastgroup, m.group()
                if self.skip or self.overlay:
                    if kind == "inline" and tok[1] == "V":
                        self.voice(tok[3:-1])
                    elif kind == "bar" and self.overlay:
                        self.overlay = False
                        self.barline(tok)
                    continue
                if kind in ("ws", "deco", "grace", "misc"):
                    continue
                elif kind == "inline":
                    self.field(tok[1], tok[3:-1])
                elif kind == "chordsym":
                    s = tok.strip('"')
                    if s and s[0] not in "^_<>@":
                        self.symbol = s
                elif kind == "tuplet":
                    self.tuplet(tok)
                elif kind == "bar":
                    self.barline(tok)
                elif kind == "volta":
                    self.volta(tok[1:])
                elif kind == "chord":
                    self.chord(tok)
                elif kind == "note":
                    self.note(tok)
                elif kind == "rest":
                    self.rest(tok)
                elif kind == "tie":
                    self.tie_prev()
                elif kind == "broken":
                    self.broken_rhythm(tok)
                elif kind == "overlay":
                    self.overlay = True
                    self.skipped_voices.add("&")
                else:
                    self.unsupported.add(tok)

    def done(self) -> Score:
        if self.cur.events:
            self.finish(False, "")
        if self.ending_open and self.sc.bars and not self.sc.bars[-1].ending_stop:
            self.sc.bars[-1].ending_stop = (self.ending_open, "discontinue")
        if self.skipped_voices:
            self.sc.warnings.append("Kept only the first voice; ignored: " + " ".join(sorted(self.skipped_voices)))
        if self.unsupported:
            self.sc.warnings.append("Ignored unsupported ABC: " + " ".join(sorted(self.unsupported))[:80])
        return self.sc


def parse_abc(text: str, meter: Tuple[int, int, str] = (4, 4, "")) -> Score:
    p = _Parser(meter)
    p.feed(text)
    return p.done()


# ---------------------------------------------------------------- ABC writer

def key_name(fifths: int, mode: str = "major") -> str:
    """(2, 'major') -> 'D'; (0, 'dorian') -> 'Ddor'."""
    tf = fifths - MODE_SHIFT.get(mode, 0)
    acc = (tf + 1) // 7
    suffix = {"major": "", "minor": "m"}.get(mode, mode[:3])
    return "FCGDAEB"[(tf + 1) % 7] + {0: "", 1: "#", -1: "b"}.get(acc, "") + suffix


def _len(f: Fraction) -> str:
    if f == 1:
        return ""
    if f.denominator == 1:
        return str(f.numerator)
    return ("" if f.numerator == 1 else str(f.numerator)) + "/" + ("" if f.denominator == 2 else str(f.denominator))


def score_to_abc(score: Score, unit: Fraction = Fraction(1, 8), bars_per_line: int = 4) -> str:
    """Inverse of parse_abc: parse_abc(score_to_abc(s)) rebuilds the same bars, pitches and lengths."""
    def meter(m):
        return {"common": "C", "cut": "C|"}.get(m[2], f"{m[0]}/{m[1]}")

    head = ["X:1"] + [f"{k}:{v}" for k, v in (("T", score.title), ("C", score.composer)) if v]
    head += [f"M:{meter(score.meter)}", f"L:{unit.numerator}/{unit.denominator}"]
    if score.tempo:
        head.append(f"Q:1/4={round(score.tempo)}")
    head.append("K:" + key_name(*score.key))

    alters = key_alters(score.key[0])
    tie_alter: Dict[Tuple[str, int], int] = {}
    line, lines = [], []
    for bi, bar in enumerate(score.bars):
        acc: Dict[Tuple[str, int], int] = {}
        out = []
        if bar.left:
            out.append("|:")
        if bar.ending:
            out.append("[" + bar.ending.replace(" ", ""))
        if bar.key:
            alters = key_alters(bar.key[0])
            out.append(f"[K:{key_name(*bar.key)}]")
        if bar.meter:
            out.append(f"[M:{meter(bar.meter)}]")
        if bar.tempo:
            out.append(f"[Q:1/4={round(bar.tempo)}]")
        i = 0
        while i < len(bar.events):
            ev = bar.events[i]
            tok = ""
            if ev.tuplet and ev.tpos == "start":
                n = next((j - i + 1 for j in range(i, len(bar.events)) if bar.events[j].tpos == "stop"), 1)
                tok += f"({ev.tuplet[0]}:{ev.tuplet[1]}:{n}"
            if ev.chord_symbol:
                tok += f'"{ev.chord_symbol}"'
            written = ev.dur * Fraction(*ev.tuplet) if ev.tuplet else ev.dur
            if ev.whole_bar:
                tok += "Z"
            elif not ev.pitches:
                tok += "z" + _len(written / unit)
            else:
                notes = []
                for p in ev.pitches:
                    k = (p.step, p.octave)
                    implied = tie_alter.get(k, acc.get(k, alters.get(p.step, 0)))
                    sign = ""
                    if p.alter != implied or p.show:
                        sign = {1: "^", 2: "^^", -1: "_", -2: "__", 0: "="}[max(-2, min(2, p.alter))]
                        acc[k] = p.alter
                    letter = p.step if p.octave <= 4 else p.step.lower()
                    marks = "," * (4 - p.octave) if p.octave < 4 else "'" * (p.octave - 5) if p.octave > 5 else ""
                    notes.append(sign + letter + marks)
                body = notes[0] if len(notes) == 1 else "[" + "".join(notes) + "]"
                tok += body + _len(written / unit) + ("-" if ev.tie else "")
            tie_alter = {(p.step, p.octave): p.alter for p in ev.pitches} if ev.tie else {}
            out.append(tok)
            i += 1
        right = {"repeat": ":|", "double": "||", "final": "|]"}.get(bar.right, "|")
        line.append(" ".join(out) + " " + right)
        if len(line) == bars_per_line or bi == len(score.bars) - 1:
            lines.append(" ".join(line))
            line = []
    return "\n".join(head + lines) + "\n"


# ---------------------------------------------------------------- MusicXML writer

@dataclass
class _Piece:
    ev: Ev
    sound: Fraction
    typ: str
    dots: int
    first: bool
    last: bool
    tm: Optional[Tuple[int, int]] = None
    beams: Dict[int, str] = field(default_factory=dict)


def decompose(x: Fraction) -> List[Tuple[Fraction, str, int]]:
    """Split a length into notatable pieces (to be joined by ties)."""
    out = []
    for value, typ, dots in NOTATABLE:
        while x >= value:
            out.append((value, typ, dots))
            x -= value
    return out if x == 0 else []


def _pieces(ev: Ev) -> List[_Piece]:
    if ev.whole_bar:
        return [_Piece(ev, ev.dur, "whole", 0, True, True)]
    actual, normal = ev.tuplet or (1, 1)
    parts = decompose(ev.dur * actual / normal)
    if parts:
        n = len(parts)
        tm = (actual, normal) if ev.tuplet else None
        return [_Piece(ev, v * normal / actual, t, d, i == 0, i == n - 1, tm) for i, (v, t, d) in enumerate(parts)]
    # not a clean notatable length: show the nearest plain note under a time-modification
    typ, value = min(TYPES, key=lambda tv: abs(float(ev.dur - tv[1])) if ev.dur >= tv[1] / 2 else 9)
    ratio = (ev.dur / value).limit_denominator(16)
    return [_Piece(ev, ev.dur, typ, 0, True, True, (ratio.denominator, ratio.numerator))]


def _beam_groups(pieces: List[_Piece], group_len: Fraction):
    """Fill piece.beams: consecutive short notes inside one beat group are joined."""
    runs, cur, pos, cur_slot = [], [], Fraction(0), None
    for pc in pieces:
        slot = int(pos // group_len)
        if pc.typ in BEAM_LEVEL and pc.ev.pitches and not pc.ev.whole_bar:
            if cur and slot != cur_slot:
                runs.append(cur)
                cur = []
            cur.append(pc)
            cur_slot = slot
        elif cur:
            runs.append(cur)
            cur = []
        pos += pc.sound
    if cur:
        runs.append(cur)
    for run in runs:
        if len(run) < 2:
            continue
        for level in range(1, max(BEAM_LEVEL[p.typ] for p in run) + 1):
            i = 0
            while i < len(run):
                if BEAM_LEVEL[run[i].typ] < level:
                    i += 1
                    continue
                j = i
                while j + 1 < len(run) and BEAM_LEVEL[run[j + 1].typ] >= level:
                    j += 1
                if j > i:
                    for k in range(i, j + 1):
                        run[k].beams[level] = "begin" if k == i else "end" if k == j else "continue"
                elif level > 1:
                    run[i].beams[level] = "forward hook" if i == 0 else "backward hook"
                i = j + 1


def _sub(parent, tag: str, value=None, **attrs):
    e = etree.SubElement(parent, tag, **{k: str(v) for k, v in attrs.items() if v is not None})
    if value is not None:
        e.text = str(value)
    return e


_KINDS = {"": "major", "m": "minor", "min": "minor", "-": "minor", "7": "dominant", "maj7": "major-seventh",
          "M7": "major-seventh", "m7": "minor-seventh", "min7": "minor-seventh", "dim": "diminished",
          "o": "diminished", "dim7": "diminished-seventh", "aug": "augmented", "+": "augmented",
          "sus4": "suspended-fourth", "sus": "suspended-fourth", "sus2": "suspended-second", "6": "major-sixth",
          "m6": "minor-sixth", "9": "dominant-ninth", "m9": "minor-ninth", "maj9": "major-ninth",
          "m7b5": "half-diminished", "5": "power"}


def _harmony(parent, symbol: str):
    m = re.fullmatch(r"([A-G])([#b]?)([^/]*)(?:/([A-G])([#b]?))?", symbol.strip())
    if not m:
        d = _sub(parent, "direction", placement="above")
        _sub(_sub(d, "direction-type"), "words", symbol)
        return
    h = _sub(parent, "harmony")
    root = _sub(h, "root")
    _sub(root, "root-step", m.group(1))
    if m.group(2):
        _sub(root, "root-alter", 1 if m.group(2) == "#" else -1)
    suffix = m.group(3)
    kind = _KINDS.get(suffix, "other")
    _sub(h, "kind", kind, text=suffix)
    if m.group(4):
        bass = _sub(h, "bass")
        _sub(bass, "bass-step", m.group(4))
        if m.group(5):
            _sub(bass, "bass-alter", 1 if m.group(5) == "#" else -1)


def _positions(events: List[Ev], skill_level: str) -> Dict[int, List[Dict[str, Any]]]:
    """Mandolin string/fret for every pitched event (tied continuations keep their predecessor's)."""
    pos: Dict[int, List[Dict[str, Any]]] = {}
    singles, chords, cont = [], [], {}
    prev = None
    for ev in events:
        if not ev.pitches:
            prev = None if ev.pitches == [] and not ev.whole_bar else prev
            continue
        if prev is not None and prev.tie and len(prev.pitches) == len(ev.pitches) \
                and all(a.same(b) for a, b in zip(prev.pitches, ev.pitches)):
            cont[id(ev)] = prev
        elif len(ev.pitches) == 1:
            singles.append(ev)
        else:
            chords.append(ev)
        prev = ev
    for ev, p in zip(singles, mando.optimize_melodic_phrase([e.pitches[0].midi for e in singles], skill_level)):
        pos[id(ev)] = [p]
    for ev in chords:
        pos[id(ev)] = mando.optimize_chord_fingering([p.midi for p in ev.pitches], skill_level)
    for ev in events:
        if id(ev) in cont:
            pos[id(ev)] = pos[id(cont[id(ev)])]
    return pos


def _octave_shift(events: List[Ev]) -> int:
    midis = [p.midi for e in events for p in e.pitches]
    if not midis:
        return 0
    lo, hi = min(midis), max(midis)
    for s in (0, 1, -1, 2, -2, 3, -3):
        if lo + 12 * s >= LOW_MIDI and hi + 12 * s <= HIGH_MIDI:
            return s
    return 0


def to_musicxml(score: Score, tab: bool = True, skill_level: str = "beginner",
                transpose: bool = True) -> Tuple[str, Dict[str, Any]]:
    """Build a MusicXML 4.0 string; with tab=True the part has a notation staff and a mandolin TAB staff."""
    sc = copy.deepcopy(score)
    events = [e for b in sc.bars for e in b.events]
    shift = _octave_shift(events) if (tab and transpose) else 0
    if shift:
        for e in events:
            for p in e.pitches:
                p.octave += shift

    layout = [[pc for ev in b.events for pc in _pieces(ev)] for b in sc.bars]
    den = 1
    for pcs in layout:
        for pc in pcs:
            d = (pc.sound * 4).denominator
            den = den * d // gcd(den, d)
    divisions = den if den <= 5040 else 960

    positions = _positions(events, skill_level) if tab else {}
    flat = {id(e): i for i, e in enumerate(events)}
    problems = {i for i, _, _ in sc.problems()}
    pickup = bool(sc.bars) and sc.bars[0].total < sc.bars[0].expected and len(sc.bars) > 1

    root = etree.Element("score-partwise", version="4.0")
    if sc.title:
        _sub(_sub(root, "work"), "work-title", sc.title)
    ident = _sub(root, "identification")
    if sc.composer:
        _sub(ident, "creator", sc.composer, type="composer")
    _sub(_sub(ident, "encoding"), "software", "SheetXML")
    plist = _sub(_sub(root, "part-list"), "score-part", id="P1")
    _sub(plist, "part-name", "Mandolin")
    _sub(plist, "part-abbreviation", "Mand.")
    inst = _sub(plist, "score-instrument", id="P1-I1")
    _sub(inst, "instrument-name", "Mandolin")
    _sub(inst, "instrument-sound", "pluck.mandolin")
    midi = _sub(plist, "midi-instrument", id="P1-I1")
    _sub(midi, "midi-channel", 1)
    _sub(midi, "midi-program", 26)
    part = _sub(root, "part", id="P1")

    stats = {"totalNotes": 0, "openStringsCount": 0, "firstPositionCount": 0, "upperPositionCount": 0,
             "highestFret": 0, "stringUsage": {1: 0, 2: 0, 3: 0, 4: 0}, "outOfRange": 0,
             "transposedOctaves": shift, "reviewBars": []}

    def tie_flags(ev: Ev, p: Pitch, first: bool, last: bool) -> Tuple[bool, bool]:
        i = flat[id(ev)]
        stop = not first or (i > 0 and events[i - 1].tie and any(p.same(q) for q in events[i - 1].pitches))
        start = not last or (ev.tie and i + 1 < len(events) and any(p.same(q) for q in events[i + 1].pitches))
        return start, stop

    def note(m, pc: _Piece, pitch: Optional[Pitch], chord: bool, staff: int, pos: Optional[dict]):
        ev = pc.ev
        n = _sub(m, "note")
        if chord:
            _sub(n, "chord")
        if pitch is None:
            _sub(n, "rest", measure="yes" if ev.whole_bar else None)
        else:
            p = _sub(n, "pitch")
            _sub(p, "step", pitch.step)
            if pitch.alter:
                _sub(p, "alter", pitch.alter)
            _sub(p, "octave", pitch.octave)
        _sub(n, "duration", int(pc.sound * 4 * divisions))
        t_start = t_stop = False
        if pitch is not None:
            t_start, t_stop = tie_flags(ev, pitch, pc.first, pc.last)
            if t_stop:
                _sub(n, "tie", type="stop")
            if t_start:
                _sub(n, "tie", type="start")
        _sub(n, "voice", 5 if staff == 2 else 1)
        if not ev.whole_bar:
            _sub(n, "type", pc.typ)
            for _ in range(pc.dots):
                _sub(n, "dot")
        if pitch is not None and pitch.show and pc.first and staff == 1:
            _sub(n, "accidental", ACC_NAME[max(-2, min(2, pitch.alter))])
        if pc.tm:
            tm = _sub(n, "time-modification")
            _sub(tm, "actual-notes", pc.tm[0])
            _sub(tm, "normal-notes", pc.tm[1])
        if tab:
            _sub(n, "staff", staff)
        for level, state in sorted(pc.beams.items()):
            _sub(n, "beam", state, number=level)
        tup = ev.tuplet and ((pc.first and ev.tpos == "start") or (pc.last and ev.tpos == "stop"))
        if t_start or t_stop or tup or pos:
            nots = _sub(n, "notations")
            if t_stop:
                _sub(nots, "tied", type="stop")
            if t_start:
                _sub(nots, "tied", type="start")
            if tup:
                _sub(nots, "tuplet", type="start" if ev.tpos == "start" else "stop",
                     **({"bracket": "yes"} if ev.tpos == "start" else {}))
            if pos:
                tech = _sub(nots, "technical")
                _sub(tech, "string", pos["string"])
                _sub(tech, "fret", pos["fret"])

    def barline(m, location: str, bar: Bar):
        if location == "left":
            if not (bar.left or bar.ending):
                return
            b = _sub(m, "barline", location="left")
            if bar.left:
                _sub(b, "bar-style", "heavy-light")
            for num in ([bar.ending] if bar.ending else []):
                _sub(b, "ending", number=num, type="start")
            if bar.left:
                _sub(b, "repeat", direction="forward")
            return
        if not (bar.right or bar.ending_stop):
            return
        b = _sub(m, "barline", location="right")
        _sub(b, "bar-style", {"repeat": "light-heavy", "double": "light-light", "final": "light-heavy"}.get(bar.right, "regular"))
        if bar.ending_stop:
            _sub(b, "ending", number=bar.ending_stop[0], type=bar.ending_stop[1])
        if bar.right == "repeat":
            _sub(b, "repeat", direction="backward")

    beat = Fraction(3, sc.meter[1]) if sc.meter[0] % 3 == 0 and sc.meter[0] > 3 and sc.meter[1] >= 8 \
        else Fraction(1, 4) if sc.meter[1] >= 8 else Fraction(1, sc.meter[1])
    number = 0 if pickup else 1
    for bi, (bar, pcs) in enumerate(zip(sc.bars, layout)):
        m = _sub(part, "measure", number=number, **({"implicit": "yes"} if pickup and bi == 0 else {}))
        number += 1
        barline(m, "left", bar)
        if bi == 0 or bar.key or bar.meter:
            attrs = _sub(m, "attributes")
            if bi == 0:
                _sub(attrs, "divisions", divisions)
            if bi == 0 or bar.key:
                fifths, mode = sc.key if bi == 0 else bar.key
                k = _sub(attrs, "key")
                _sub(k, "fifths", fifths)
                _sub(k, "mode", mode)
            if bi == 0 or bar.meter:
                bt = sc.meter if bi == 0 else bar.meter
                t = _sub(attrs, "time", **({"symbol": bt[2]} if bt[2] else {}))
                _sub(t, "beats", bt[0])
                _sub(t, "beat-type", bt[1])
            if bi == 0:
                if tab:
                    _sub(attrs, "staves", 2)
                c1 = _sub(attrs, "clef", **({"number": 1} if tab else {}))
                _sub(c1, "sign", "G")
                _sub(c1, "line", 2)
                if tab:
                    c2 = _sub(attrs, "clef", number=2)
                    _sub(c2, "sign", "TAB")
                    _sub(c2, "line", 5)
                    sd = _sub(attrs, "staff-details", number=2)
                    _sub(sd, "staff-lines", 4)
                    for line, (step, octave) in enumerate([("G", 3), ("D", 4), ("A", 4), ("E", 5)], start=1):
                        st = _sub(sd, "staff-tuning", line=line)
                        _sub(st, "tuning-step", step)
                        _sub(st, "tuning-octave", octave)
        tempo = sc.tempo if bi == 0 else bar.tempo
        if tempo:
            d = _sub(m, "direction", placement="above")
            met = _sub(_sub(d, "direction-type"), "metronome")
            _sub(met, "beat-unit", "quarter")
            _sub(met, "per-minute", round(tempo))
            _sub(d, "sound", tempo=round(tempo))
        if bi in problems:
            d = _sub(m, "direction", placement="above")
            _sub(_sub(d, "direction-type"), "words", "check", color="#CC0000")
            stats["reviewBars"].append(number - 1)
        _beam_groups(pcs, beat)
        for pc in pcs:
            if pc.first and pc.ev.chord_symbol:
                _harmony(m, pc.ev.chord_symbol)
            for k, p in enumerate(pc.ev.pitches or [None]):
                note(m, pc, p, k > 0, 1, None)
        if tab and pcs:
            bk = _sub(m, "backup")
            _sub(bk, "duration", sum(int(pc.sound * 4 * divisions) for pc in pcs))
            for pc in pcs:
                ev_pos = positions.get(id(pc.ev), [])
                for k, p in enumerate(pc.ev.pitches or [None]):
                    note(m, pc, p, k > 0, 2, ev_pos[k] if ev_pos else None)
        barline(m, "right", bar)

    for ev in events:
        for p, pos in zip(ev.pitches, positions.get(id(ev), [])):
            stats["totalNotes"] += 1
            stats["stringUsage"][pos["string"]] += 1
            stats["openStringsCount"] += pos["fret"] == 0
            stats["firstPositionCount"] += pos["fret"] <= 7
            stats["upperPositionCount"] += pos["fret"] > 7
            stats["highestFret"] = max(stats["highestFret"], pos["fret"])
            stats["outOfRange"] += not LOW_MIDI <= p.midi <= HIGH_MIDI

    xml = etree.tostring(root, pretty_print=True, encoding="UTF-8", xml_declaration=True,
                         doctype='<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
                                 '"http://www.musicxml.org/dtds/partwise.dtd">').decode("utf-8")
    return xml, stats


# ---------------------------------------------------------------- MusicXML reader

_XML_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=True,
                              remove_comments=True, remove_pis=True)
_KIND_SUFFIX: Dict[str, str] = {}
for _s, _k in _KINDS.items():
    _KIND_SUFFIX.setdefault(_k, _s)
_UNIT_VALUE = dict(TYPES) | {"breve": Fraction(2), "long": Fraction(4)}


def _mxl_root(data: bytes) -> bytes:
    """The score inside a compressed .mxl: the first rootfile named by META-INF/container.xml."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        path = None
        if "META-INF/container.xml" in names:
            rf = etree.fromstring(z.read("META-INF/container.xml"), _XML_PARSER).find(".//{*}rootfile")
            path = rf.get("full-path") if rf is not None else None
        path = path or next((n for n in names if not n.startswith("META-INF/")
                             and n.lower().endswith((".xml", ".musicxml"))), None)
        if not path or path not in names:
            raise ValueError("no MusicXML score inside the .mxl archive")
        if z.getinfo(path).file_size > 200_000_000:
            raise ValueError("MusicXML inside the .mxl archive is too large")
        return z.read(path)


def _xml_dur(el, div: Fraction) -> Fraction:
    return Fraction((el.findtext("duration") or "0").strip()) / div / 4


def _xml_symbol(h) -> str:
    step = (h.findtext("root/root-step") or "").strip()
    if not step:
        return ""
    alter = {1: "#", -1: "b"}.get(round(float(h.findtext("root/root-alter") or 0)), "")
    kind = h.find("kind")
    suffix = ""
    if kind is not None:
        suffix = kind.get("text") if kind.get("text") is not None else _KIND_SUFFIX.get((kind.text or "").strip(), "")
    bass = (h.findtext("bass/bass-step") or "").strip()
    if bass:
        bass = "/" + bass + {1: "#", -1: "b"}.get(round(float(h.findtext("bass/bass-alter") or 0)), "")
    return step + alter + suffix + bass


def _xml_tempo(el) -> Optional[float]:
    """Quarter notes per minute from sound@tempo, else from a metronome mark."""
    for s in el.iter("sound"):
        if s.get("tempo"):
            return float(s.get("tempo"))
    met = next(el.iter("metronome"), None)
    if met is None:
        return None
    unit = _UNIT_VALUE.get((met.findtext("beat-unit") or "").strip())
    m = re.search(r"\d+(?:\.\d+)?", met.findtext("per-minute") or "")
    if not unit or not m:
        return None
    unit *= 2 - Fraction(1, 2 ** len(met.findall("beat-unit-dot")))
    return float(m.group()) * float(unit * 4)


def _close_tuplets(bar: Bar):
    """Make every run of tuplet events a start..stop group; a lone member keeps its length but loses the bracket."""
    group: List[Ev] = []

    def close():
        if len(group) == 1:
            group[0].tuplet, group[0].tpos = None, ""
        elif group:
            group[0].tpos, group[-1].tpos = "start", "stop"
            for e in group[1:-1]:
                e.tpos = ""
        group.clear()

    for ev in bar.events:
        if group and (ev.tuplet != group[0].tuplet or ev.tpos == "start"):
            close()
        if ev.tuplet:
            group.append(ev)
            if ev.tpos == "stop":
                close()
    close()


def parse_musicxml(xml: str | bytes) -> Score:
    """Partwise MusicXML (or .mxl bytes) -> Score: first part, first notation staff, its busiest voice.

    Grace and cue notes are skipped; gaps in the kept voice become rests; durations come from <duration>.
    """
    if isinstance(xml, str):
        root = etree.fromstring(re.sub(r"^\s*<\?xml[^>]*\?>", "", xml), _XML_PARSER)
    else:
        root = etree.fromstring(_mxl_root(xml) if xml[:2] == b"PK" else xml, _XML_PARSER)
    # ponytail: partwise only, convert timewise with the official XSLT if an engine ever emits it
    if root.tag != "score-partwise":
        raise ValueError(f"expected <score-partwise>, got <{root.tag}>")
    part = root.find("part")
    if part is None:
        raise ValueError("MusicXML has no <part>")
    sc = Score()
    sc.title = (root.findtext("work/work-title") or root.findtext("movement-title") or "").strip()
    sc.composer = (root.findtext("identification/creator[@type='composer']") or "").strip()

    # staff: first one that is not a TAB staff (clef TAB, or string tuning on a staff without a pitch clef)
    staves, clefs, tuned = 1, {}, set()
    for a in part.iter("attributes"):
        staves = max(staves, int(a.findtext("staves") or 1))
        for c in a.findall("clef"):
            clefs[c.get("number", "1")] = (c.findtext("sign") or "").strip().upper()
        for d in a.findall("staff-details"):
            if d.find("staff-tuning") is not None:
                tuned.add(d.get("number", "1"))
    numbers = [str(i) for i in range(1, staves + 1)]
    tab = {s for s in numbers if clefs.get(s) == "TAB" or (s in tuned and clefs.get(s) not in ("G", "F", "C"))}
    staff = next((s for s in numbers if s not in tab), "1")
    if staff in tab:
        sc.warnings.append("No notation staff found; read the pitches of the TAB staff.")

    def ours(n) -> bool:
        return (n.findtext("staff") or "1").strip() == staff

    counts = Counter((n.findtext("voice") or "1").strip() for n in part.iter("note")
                     if ours(n) and n.find("grace") is None and n.find("cue") is None and n.find("rest") is None)
    # ponytail: one voice for the whole part, pick per measure if scores swap voices between bars
    voice = counts.most_common(1)[0][0] if counts else "1"
    if len(counts) > 1:
        sc.warnings.append(f"Kept voice {voice}; dropped {sum(counts.values()) - counts[voice]} notes of other voices.")

    def pick(attrs, tag):
        return next((x for x in attrs.findall(tag) if x.get("number", staff) == staff), None)

    div, key, meter, tempo, symbol = Fraction(1), None, None, None, ""
    overlap = 0
    for mi, meas in enumerate(part.findall("measure")):
        bar = Bar()
        pos = end = vpos = onset = Fraction(0)

        def add(ev: Ev):
            nonlocal symbol
            ev.chord_symbol, symbol = symbol, ""
            bar.events.append(ev)

        for el in meas:
            if el.tag == "attributes":
                if (el.findtext("divisions") or "").strip():
                    div = Fraction(el.findtext("divisions").strip()) or div
                k = pick(el, "key")
                if k is not None and k.findtext("fifths") is not None:
                    mode = (k.findtext("mode") or "major").strip()
                    new = (int(k.findtext("fifths")), mode if mode in MODE_SHIFT else "major")
                    if key is None:
                        sc.key = new
                    elif new != key:
                        bar.key = new
                    key = new
                t = pick(el, "time")
                if t is not None and t.findtext("beats") and t.findtext("beat-type"):
                    sym = t.get("symbol", "")
                    new = (sum(int(x) for x in re.findall(r"\d+", t.findtext("beats"))),
                           int(t.findtext("beat-type")), sym if sym in ("common", "cut") else "")
                    if meter is None:
                        sc.meter = new
                    elif new != meter:
                        bar.meter = new
                    meter = new
            elif el.tag in ("direction", "sound"):
                t = _xml_tempo(el)
                if t and t != tempo:
                    if mi == 0 and sc.tempo is None:
                        sc.tempo = t
                    else:
                        bar.tempo = t
                    tempo = t
            elif el.tag == "harmony":
                symbol = _xml_symbol(el) or symbol
            elif el.tag == "backup":
                pos = max(Fraction(0), pos - _xml_dur(el, div))
            elif el.tag == "forward":
                pos += _xml_dur(el, div)
                end = max(end, pos)
            elif el.tag == "barline":
                rep, style, ending = el.find("repeat"), (el.findtext("bar-style") or "").strip(), el.find("ending")
                num = ", ".join(x for x in re.split(r"[,\s]+", ending.get("number") or "") if x) if ending is not None else ""
                if el.get("location", "right") == "left":
                    if rep is not None and rep.get("direction") == "forward":
                        bar.left = "repeat"
                    if num and ending.get("type") == "start":
                        bar.ending = num
                else:
                    if rep is not None and rep.get("direction") == "backward":
                        bar.right = "repeat"
                    elif style in ("light-light", "heavy-heavy"):
                        bar.right = "double"
                    elif style == "light-heavy":
                        bar.right = "final"
                    if num and ending.get("type") in ("stop", "discontinue"):
                        bar.ending_stop = (num, ending.get("type"))
            elif el.tag == "note":
                if el.find("grace") is not None:
                    continue
                d, chord = _xml_dur(el, div), el.find("chord") is not None
                if not chord:
                    onset, pos = pos, pos + d
                    end = max(end, pos)
                if el.find("cue") is not None or not ours(el) or (el.findtext("voice") or "1").strip() != voice:
                    continue
                p = el.find("pitch")
                pitch = None if p is None else Pitch(p.findtext("step").strip().upper(),
                                                     round(float(p.findtext("alter") or 0)),
                                                     int(p.findtext("octave")), el.find("accidental") is not None)
                tie = any(t.get("type") == "start" for t in el.findall("tie") + el.findall("notations/tied"))
                tpos = next((t.get("type") for t in el.findall("notations/tuplet") if t.get("type") in ("start", "stop")), "")
                if chord and bar.events and bar.events[-1].pitches and pitch is not None:
                    ev = bar.events[-1]
                    ev.pitches.append(pitch)
                    ev.tie = ev.tie or tie
                    ev.tpos = ev.tpos or tpos
                    continue
                if onset < vpos:
                    overlap += 1
                    continue
                if onset > vpos:
                    add(Ev(dur=onset - vpos))
                tm = el.find("time-modification")
                ratio = (int(tm.findtext("actual-notes")), int(tm.findtext("normal-notes"))) if tm is not None else None
                rest = el.find("rest")
                add(Ev(dur=d, pitches=[pitch] if pitch is not None else [], tie=tie and pitch is not None,
                       tuplet=ratio if ratio and ratio[0] != ratio[1] else None, tpos=tpos,
                       whole_bar=rest is not None and rest.get("measure") == "yes"))
                vpos = onset + d
        if vpos < end:
            add(Ev(dur=end - vpos))
        bar.expected = Fraction(*(meter or sc.meter)[:2])
        if not bar.events:
            add(Ev(dur=bar.expected, whole_bar=True))
        _close_tuplets(bar)
        sc.bars.append(bar)
    if overlap:
        sc.warnings.append(f"Skipped {overlap} overlapping notes in voice {voice}.")
    return sc


if __name__ == "__main__":
    import pathlib
    import app

    F = Fraction

    def evs(s: Score):
        return [[(tuple(sorted(p.midi for p in e.pitches)), e.dur, e.tie) for e in b.events] for b in s.bars]

    def durs(text: str):
        return [e.dur for b in parse_abc(text).bars for e in b.events]

    def same(a: Score, b: Score, what: str, stops: bool = True):
        assert evs(a) == evs(b), (what, evs(a), evs(b))
        for x, y in zip(a.bars, b.bars):
            assert (x.left, x.right, x.ending, x.key, x.meter, x.tempo) == \
                   (y.left, y.right, y.ending, y.key, y.meter, y.tempo), (what, x, y)
            assert not stops or x.ending_stop == y.ending_stop, (what, x, y)
            assert [e.chord_symbol for e in x.events] == [e.chord_symbol for e in y.events], what
            assert [e.tuplet for e in x.events] == [e.tuplet for e in y.events], what
        assert (a.key, a.meter, a.tempo, a.title, a.composer) == (b.key, b.meter, b.tempo, b.title, b.composer), what

    # -- ABC written by mid-tier models
    s = parse_abc("K:C\n|: C4 :|: D4 :: E4 || F4 [| G4 |]")
    assert [(b.left, b.right) for b in s.bars] == [("repeat", "repeat"), ("repeat", "repeat"), ("repeat", "double"),
                                                   ("", "double"), ("", "final")], s.bars
    s = parse_abc('K:C\n!fermata!C !p!D +trill+E "^text"F "_x"G .A ~B (cd) |')
    assert durs('K:C\n!fermata!C !p!D +trill+E "^text"F "_x"G .A ~B (cd) |') == [F(1, 8)] * 9
    assert not s.warnings and not any(e.chord_symbol for e in s.bars[0].events), s.warnings
    assert durs("K:C\n(3cde) (cde) (3:2:3CDE z4/|") == [F(1, 12)] * 3 + [F(1, 8)] * 3 + [F(1, 12)] * 3 + [F(1, 4)]
    s = parse_abc("X:1\n%%score 1\nK:C\nC D E F G A B c|\nw: la la la | la\n% note\ns: * * |\n+: more\nW: words")
    assert len(s.bars) == 1 and len(s.bars[0].events) == 8 and not s.warnings, (s.bars, s.warnings)
    one = [(72,), (74,), (76,), (77,)]
    for t in ("K:C\nV:1\ncdef|\nV:2\nC,D,E,F,|\nV:1\ncdef|",
              "X:1\nV:1 name=\"Mand\"\nV:2\nK:C\nV:1\ncdef|\nV:2\nC,D,E,F,|\nV:1\ncdef|",
              "K:C\n[V:1] cdef| [V:2] C,D,E,F,|\n[V:1] cdef|",
              "K:C\ncdef & C,D,E,F, | cd ef & G,A,B,C|"):
        s = parse_abc(t)
        assert [[(tuple(p.midi for p in e.pitches)) for e in b.events] for b in s.bars] == [one, one], (t, evs(s))
        assert s.warnings and "first voice" in s.warnings[0], s.warnings
    for k, want in (("D major", (2, "major")), ("Dmaj", (2, "major")), ("D Major", (2, "major")), ("F#m", (3, "minor")),
                    ("Bb minor", (-5, "minor")), ("none", (0, "major")), ("C clef=bass", (0, "major")),
                    ("F♯m", (3, "minor")), ("G bass", (1, "major")), ("Ebm", (-6, "minor")), ("A Dorian", (1, "dorian"))):
        s = parse_abc(f"K:{k}\nC|")
        assert s.key == want, (k, s.key)
    assert parse_abc("K:C clef=bass\nC|").bars[0].events[0].pitches[0].midi == 60
    assert parse_key("d minor") == (-1, "minor") and parse_key("bass") is None and parse_key("clef=bass") is None
    for m, want in (("none", (4, 4, "")), ("C", (4, 4, "common")), ("C|", (2, 2, "cut")), ("2+3/8", (5, 8, "")),
                    ("(2+2+3)/8", (7, 8, ""))):
        assert parse_abc(f"M:{m}\nK:C\nC|").meter == want, m
    assert len(parse_abc("K:C\nC D \\\nE F G A B c|").bars) == 1
    assert [(len(e.pitches), e.dur) for e in parse_abc("K:C\nC y D x2 E z, z'2 G|").bars[0].events] == \
           [(1, F(1, 8)), (1, F(1, 8)), (0, F(1, 4)), (1, F(1, 8)), (0, F(1, 8)), (0, F(1, 4)), (1, F(1, 8))]
    assert durs("K:C\n[C2E2G2] [CEG]2 [CE]>[DF] z>C|") == [F(1, 4), F(1, 4), F(3, 16), F(1, 16), F(3, 16), F(1, 16)]
    s = parse_abc("K:C\nC D E F>|G A B c|")                 # broken rhythm must not leak over the barline
    assert durs("K:C\nC D E F>|G A B c|") == [F(1, 8)] * 8 and s.warnings
    s = parse_abc("K:C\nC2 D2 E2 (3F|G A B c|")             # neither may a tuplet
    assert [e.dur for e in s.bars[1].events] == [F(1, 8)] * 4 and s.bars[0].events[-1].tuplet is None
    s = parse_abc("K:C\nC2 D2 E (3FG|A B c|")
    assert s.bars[0].events[-1].tpos == "stop" and s.bars[1].events[0].dur == F(1, 8)
    for t in ("m12: C D E F G A B c|", "12| C D E F G A B c|", "Bar 3. C D E F G A B c|"):
        s = parse_abc("K:C\n" + t)
        assert len(s.bars) == 1 and len(s.bars[0].events) == 8 and not s.bars[0].right and not s.bars[0].left, t
    assert durs("K:C\n{g}A {/ga}B {AB}c d|") == [F(1, 8)] * 4
    assert [e.pitches[0].midi for e in parse_abc("K:C\nF♯ G ♭B c♮ e♭2|").bars[0].events] == [66, 67, 70, 72, 75]
    assert parse_abc('K:C\n"F♯m"F|').bars[0].events[0].chord_symbol == "F#m"
    s = parse_abc("X:1\nT:A\nM:4/4\nL:1/8\nK:C\nC D E F G A B c|\nT:B\nX:1\nM:4/4\nL:1/8\nQ:1/4=90\nK:C\nc B A G F E D C|")
    assert s.title == "A" and len(s.bars) == 2 and s.bars[1].key is None and s.bars[1].meter is None
    s = parse_abc("K:C\nX4|C8|")
    assert len(s.bars) == 5 and all(b.events[0].whole_bar for b in s.bars[:4])

    # -- ABC -> MusicXML -> Score round trips, and ABC -> Score -> ABC
    tunes = [
        'X:1\nT:Tune\nC:Someone\nM:3/4\nL:1/8\nQ:1/4=96\nK:G\nD2|:"G"G2 B>A G2-|G2 "D7"(3ABc d2|[1 e4 d2:|[2 [G,B,D]6|]',
        "M:4/4\nL:1/16\nK:Dm\n^c4 =B4 _B8|[K:A][M:6/8] a6 b6|[M:2/4] z8|Z|[M:C] [CE]4-[CE]4 c8|",
        "M:6/8\nK:F\n(3:2:3ABc d2 e f|g3- g2 a||[1,2 b3 a3:|[3 f6|]",
        "M:C|\nL:1/4\nK:Bb\n|: B c d e | f2 (3fed :: c4 | B4 |]",
    ]
    for t in tunes:
        a = parse_abc(t)
        assert not a.warnings, (t, a.warnings)
        same(a, parse_abc(score_to_abc(a)), "abc round trip " + t)
        for tab in (True, False):
            xml, _ = to_musicxml(a, tab=tab, transpose=False)
            assert app.validate_musicxml_schema(xml)[0], app.validate_musicxml_schema(xml)[1][:3]
            b = parse_musicxml(xml)
            same(a, b, f"musicxml round trip tab={tab} " + t)
            assert not b.warnings, b.warnings
        b = parse_musicxml(to_musicxml(a, tab=True)[0])            # octave shift only moves pitches
        assert [[e.dur for e in x.events] for x in b.bars] == [[e.dur for e in x.events] for x in a.bars]

    # -- a Guitar Pro export: TAB-only first part, endings, triplet, tie, divisions changes
    ex = pathlib.Path(__file__).with_name("resources") / "music-xml-example.xml"
    s = parse_musicxml(ex.read_bytes())
    assert (s.title, s.key, s.meter, s.tempo, len(s.bars)) == ("Maid Behind the Bar", (2, "major"), (4, 4, ""), 120.0, 21)
    # bar 13 really holds 3 sixteenths + 7 eighths in the file (an export slip), so it is the only problem
    assert s.problems() == [(12, F(17, 16), F(1))], s.problems()
    assert s.bars[9].ending == "1" and s.bars[9].right == "repeat" and s.bars[10].ending_stop == ("2", "stop")
    assert [e.tpos for e in s.bars[14].events[:3]] == ["start", "", "stop"] and s.bars[19].events[-1].tie
    xml, _ = to_musicxml(s)
    assert app.validate_musicxml_schema(xml)[0]
    same(s, parse_abc(score_to_abc(s)), "example via abc", stops=False)   # ABC can't close a last volta at '|' 

    # -- piano-style score: 2 staves, 2 voices on staff 1, backup/forward, grace, cue, harmony, measure rest
    def n(step, dur, voice="1", staff="1", extra=""):
        p = "<rest/>" if step == "r" else f"<pitch><step>{step}</step><octave>5</octave></pitch>"
        return f"<note>{extra}{p}<duration>{dur}</duration><voice>{voice}</voice><staff>{staff}</staff></note>"
    xml = ("<score-partwise><movement-title>Mv</movement-title><part-list><score-part id='P1'/></part-list><part id='P1'>"
           "<measure number='1'><attributes><divisions>2</divisions><key><fifths>-1</fifths></key><time><beats>3</beats>"
           "<beat-type>4</beat-type></time><staves>2</staves><clef number='1'><sign>G</sign></clef>"
           "<clef number='2'><sign>F</sign></clef></attributes>"
           "<direction><direction-type><metronome><beat-unit>quarter</beat-unit><beat-unit-dot/><per-minute>60"
           "</per-minute></metronome></direction-type></direction>"
           "<harmony><root><root-step>B</root-step><root-alter>-1</root-alter></root><kind>dominant</kind></harmony>"
           + "<note><grace/><pitch><step>G</step><octave>5</octave></pitch><voice>1</voice></note>"
           + n("A", 2) + n("B", 2, extra="<chord/>") + n("C", 1) + n("D", 1, extra="<cue/>") + n("E", 2)
           + "<backup><duration>6</duration></backup>" + n("F", 6, "2") + "<backup><duration>6</duration></backup>"
           + n("C", 6, "3", "2") + "</measure><measure number='2'>" + n("r", 6).replace("<rest/>", "<rest measure='yes'/>")
           + "</measure><measure number='3'><forward><duration>2</duration></forward>" + n("G", 4) + "</measure>"
           "</part></score-partwise>")
    s = parse_musicxml(xml)
    assert (s.title, s.key, s.meter, s.tempo) == ("Mv", (-1, "major"), (3, 4, ""), 90.0), (s.title, s.key, s.meter, s.tempo)
    assert evs(s) == [[((81, 83), F(1, 4), False), ((72,), F(1, 8), False), ((), F(1, 8), False), ((76,), F(1, 4), False)],
                      [((), F(3, 4), False)], [((), F(1, 4), False), ((79,), F(1, 2), False)]], evs(s)
    assert s.bars[0].events[0].chord_symbol == "Bb7" and s.bars[1].events[0].whole_bar and not s.problems()
    assert any("dropped 1 notes" in w for w in s.warnings), s.warnings
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("META-INF/container.xml", "<container><rootfiles><rootfile full-path='score/x.xml'/></rootfiles></container>")
        z.writestr("score/x.xml", ex.read_bytes())
    assert evs(parse_musicxml(buf.getvalue())) == evs(parse_musicxml(ex.read_text(encoding="utf-8")))
    print("abcxml self-check ok")
