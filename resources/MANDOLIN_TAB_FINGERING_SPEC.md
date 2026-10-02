# Mandolin Fingering & Tablature Specification

**Purpose:**  
This document defines the physical layout, pitch mapping, tablature conventions, and ergonomic fingering rules for a standard mandolin. It is intended for use by an LLM or software agent converting standard musical notation into realistic, playable mandolin tablature.

The objective is **not merely to find a mathematically valid fret for each pitch**. The objective is to select a fingering that a real mandolin player would reasonably use, with emphasis on phrase continuity, comfort, technique, and beginner-friendly positioning unless otherwise specified.

---

## 1. Standard Instrument Definition

Assume a standard soprano mandolin with:

- **8 physical strings**
- Arranged as **4 doubled unison courses**
- Standard tuning, low to high:

| Course | Physical Strings | Open Pitch | MIDI Note |
|---|---:|---:|---:|
| G | 2 | G3 | 55 |
| D | 2 | D4 | 62 |
| A | 2 | A4 | 69 |
| E | 2 | E5 | 76 |

The two strings in each course are normally tuned to the same pitch and treated as one playable course.

Standard tuning:

```text
G3 D4 A4 E5
```

Adjacent courses are separated by a perfect fifth:

```text
+7 semitones
```

The mandolin is normally a **non-transposing instrument**.

---

## 2. Fundamental Fretboard Mapping

Each fret raises pitch by one semitone.

For any course:

```text
played_pitch = open_pitch + fret_number
```

Using MIDI note numbers:

```text
fret = desired_pitch_midi - open_course_midi
```

A fingering is valid when:

```text
0 <= fret <= instrument_max_fret
```

For every written pitch, calculate this value independently for G, D, A, and E.

Every valid nonnegative result within the available fret count is a possible position.

### Example: E5

E5 = MIDI 76.

```text
E course: 76 - 76 = 0
A course: 76 - 69 = 7
D course: 76 - 62 = 14
G course: 76 - 55 = 21
```

Therefore:

```text
E5 = E0 = A7 = D14 = G21
```

provided those frets exist on the instrument.

---

## 3. Core Duplicate-Position Rule

Adjacent mandolin courses are seven semitones apart.

Therefore:

```text
same pitch on next-lower course = current fret + 7
```

Examples:

```text
D0 = G7
A0 = D7 = G14
E0 = A7 = D14 = G21
B5 = E7 = A14 = D21 = G28
```

This is one of the most important rules for tablature generation.

---

## 4. Fretboard Reference: Frets 0–12

| Fret | G Course | D Course | A Course | E Course |
|---:|---|---|---|---|
| 0 | G3 | D4 | A4 | E5 |
| 1 | G#3/Ab3 | D#4/Eb4 | A#4/Bb4 | F5 |
| 2 | A3 | E4 | B4 | F#5 |
| 3 | A#3/Bb3 | F4 | C5 | G5 |
| 4 | B3 | F#4 | C#5 | G#5 |
| 5 | C4 | G4 | D5 | A5 |
| 6 | C#4/Db4 | G#4/Ab4 | D#5/Eb5 | A#5/Bb5 |
| 7 | D4 | A4 | E5 | B5 |
| 8 | D#4/Eb4 | A#4/Bb4 | F5 | C6 |
| 9 | E4 | B4 | F#5 | C#6 |
| 10 | F4 | C5 | G5 | D6 |
| 11 | F#4 | C#5 | G#5 | D#6 |
| 12 | G4 | D5 | A5 | E6 |

Fret 12 is exactly one octave above the open course.

For all courses:

```text
pitch(fret + 12) = pitch(fret) + 1 octave
```

---

## 5. Range

The lowest pitch of a standard-tuned mandolin is always:

```text
G3
```

The highest pitch depends on the number of frets.

General formula:

```text
highest_pitch = E5 + max_fret semitones
```

Examples:

| Maximum Fret | Highest Pitch |
|---:|---|
| 20 | C7 |
| 23 | D#7/Eb7 |
| 24 | E7 |
| 29 | A7 |

Do not assume every mandolin has the same fret count.

For generic tablature intended to work on most instruments, avoid relying on unusually high frets unless required.

---

## 6. Notes With Only One Possible Position

Pitches below open D4 cannot be transferred to another course.

Therefore the following pitches are intrinsically single-position:

| Pitch | Only Position |
|---|---|
| G3 | G0 |
| G#3/Ab3 | G1 |
| A3 | G2 |
| A#3/Bb3 | G3 |
| B3 | G4 |
| C4 | G5 |
| C#4/Db4 | G6 |

Thus:

```text
G3 through C#4 can only be played on the G course.
```

Starting with D4, duplicate positions appear.

Examples:

```text
D4 = D0 = G7
A4 = A0 = D7 = G14
E5 = E0 = A7 = D14 = G21
```

At sufficiently high pitches, notes eventually become E-course-only because lower courses run out of frets.

---

## 7. Meaning of Moving Up and Down the Fretboard

Terminology must be interpreted by pitch/fret number, not by page orientation.

### Moving "up" the fretboard

Means:

- toward the bridge/body
- higher fret numbers
- higher pitch

Example:

```text
G0 -> G1 -> G2 -> G3
G3    G#3   A3    A#3
```

Each step raises pitch by one semitone.

### Moving "down" the fretboard

Means:

- toward the nut/headstock
- lower fret numbers
- lower pitch

### Changing courses at the same fret

Moving from G to D to A to E at the same fret raises pitch by a perfect fifth each time:

```text
G -> D -> A -> E
   +7  +7  +7 semitones
```

---

## 8. Mandolin Tablature Layout

Although the mandolin has eight physical strings, standard tablature normally uses **four lines**, one per doubled course.

Conventional orientation:

```text
E|----------------
A|----------------
D|----------------
G|----------------
```

The highest-pitched course appears on top.

A number indicates the fret to press.

Example:

```text
E|--0--2--3--5--
A|--------------
D|--------------
G|--------------
```

This represents:

```text
E5 F#5 G5 A5
```

`0` means open course.

`12` means fret twelve, not fret one followed by fret two.

The doubled strings in a course are normally played together.

---

## 9. Simultaneous Notes

Notes aligned vertically are simultaneous.

Example:

```text
E|--3--
A|--2--
D|--0--
G|--0--
```

This is a four-course chord.

Notes arranged horizontally are sequential.

Example:

```text
E|----------------
A|--0--2--3--5----
D|----------------
G|----------------
```

Hyphens are spacing characters and should not be treated as precise rhythmic notation unless the tablature format explicitly encodes duration.

For accurate rhythm, preserve standard notation, rhythmic stems, MusicXML durations, or another explicit timing representation.

---

## 10. Valid Fingering vs. Good Fingering

A pitch can have several mathematically valid positions but those positions are not equally desirable.

Example:

```text
E5 = E0 = A7 = D14 = G21
```

All four are pitch-correct.

However, the optimal choice depends on:

- surrounding notes
- current hand position
- speed
- phrase direction
- articulation
- open-string preference
- string crossings
- desired timbre
- player skill level
- instrument fret count

Never select tablature by independently choosing the lowest available fret for every note.

**Optimize phrases, not isolated notes.**

---

## 11. Ergonomic Defaults

The following are heuristics, not absolute mechanical limits.

### Fret-region comfort

| Fret Region | Default Interpretation |
|---|---|
| 0–5 | extremely comfortable |
| 0–7 | normal low/open-position territory |
| 7–12 | normal but involves a positional decision |
| 12–15 | playable; use when musically useful |
| 15+ | increasingly specialized and instrument-dependent |

### Melodic motion

Prefer:

- small fret movement
- compact hand positions
- adjacent course changes
- logical directional movement
- shifts during rests or long notes
- consistent fingering through repeated phrases

Avoid when unnecessary:

- rapid large jumps in fret number
- repeated movement between very low and very high positions
- jumping across multiple courses for adjacent notes
- high positions when equivalent low-position fingerings exist
- mathematically correct but physically incoherent note-by-note choices

### Approximate movement heuristic

| Movement | Typical Difficulty |
|---|---|
| 1–2 frets | very easy |
| 3–4 frets | normal |
| 5+ fret immediate shift | increasing ergonomic cost |
| shift during rest/long note | much lower cost |
| adjacent course crossing | easy |
| skipping one course | moderate |
| crossing three courses rapidly | usually undesirable |

---

## 12. Hand Position

For automatic generation, model a **current hand window** rather than relying only on formal classical position terminology.

A phrase occupying a compact range such as:

```text
frets 0–5
```

or:

```text
frets 2–7
```

is usually easier than one requiring:

```text
2 -> 10 -> 3 -> 12 -> 5
```

A fingering algorithm should strongly reward sequences whose fretted notes remain near the current hand region.

Position shifts should be intentional and preferably occur:

- at phrase boundaries
- during rests
- on sustained notes
- before a new melodic register
- when they substantially simplify the following passage

---

## 13. Open Strings

Open strings are often desirable but must not receive absolute priority.

Example:

```text
A4 = A0 = D7 = G14
```

A0 is usually the easiest isolated fingering.

However, D7 may be superior if the surrounding phrase is already being played around frets 5–9 on the D course.

Open strings:

### Advantages

- require no left-hand finger
- highly resonant
- useful in beginner playing
- useful in fiddle, Celtic, old-time, and bluegrass styles
- can simplify fast passages

### Potential disadvantages

- different timbre from fretted notes
- can interrupt positional continuity
- cannot receive normal left-hand vibrato
- may interfere with same-course slides or legato gestures
- may create unnecessary string crossings

Rule:

```text
Prefer open strings when they reduce difficulty without damaging phrase continuity.
```

---

## 14. Technique-Specific Fingering Constraints

Notation and articulation must influence fingering.

### Ties

A tied note should normally remain on the same course and fret.

Do not change positions in the middle of a sustained tie unless required.

### Slides / Glissandi

A slide strongly favors, and often requires, both notes to be on the same course.

Example:

```text
A|--5/7--
```

is physically meaningful.

Changing courses defeats the intended slide.

### Hammer-ons

Hammer-ons normally require:

- same course
- ascending fret number
- practical fret distance

Example:

```text
A|--2h4--
```

### Pull-offs

Pull-offs normally require:

- same course
- descending fret number
- practical fret distance

Example:

```text
A|--4p2--
```

### Tremolo

Mandolin tremolo normally consists of rapid alternating pick strokes on the same course/note.

Do not alternate between equivalent pitch locations merely because they exist.

### Grace Notes and Ornaments

When practical, keep grace notes and principal notes on the same course if doing so makes the ornament executable and idiomatic.

### Sustained Lyrical Passages

Consistent use of one course or one position may be preferable to open-string switching because it preserves timbre and left-hand control.

---

## 15. Chords and Double Stops

Chord and double-stop fingering requires simultaneous physical feasibility.

### Same-course conflict

One course cannot normally produce two different fretted pitches simultaneously.

Invalid example:

```text
C5 = A3
D5 = A5
```

These cannot both be assigned to the A course in the same chord because the course cannot be fretted at fret 3 and fret 5 simultaneously.

One pitch must be assigned to another course if possible.

### Chord ergonomics

Prefer:

- compact fret spans
- open strings when helpful
- common mandolin chord shapes
- fewer difficult stretches
- fewer extreme position shifts
- physically reachable simultaneous finger placements

For beginner output, compact shapes should receive a strong preference.

A simultaneous fret spread of roughly 0–4 frets is generally more comfortable than a very wide spread, although exact difficulty depends on finger assignment and fret region.

---

## 16. Physical Fret Geometry

Fret spacing decreases toward the body.

For scale length `L`, the distance of fret `n` from the nut is:

```text
x_n = L * (1 - 2^(-n/12))
```

Therefore, a five-fret span high on the neck is physically smaller than a five-fret span near the nut.

However, high fret numbers still incur ergonomic costs from:

- body interference
- hand position
- access differences between instruments
- visual orientation
- tone
- reduced fret spacing
- instrument-specific neck/body geometry

Do not model difficulty solely from numerical fret distance.

---

## 17. Sheet Music Parsing Requirements

Before choosing tablature, first resolve the score into absolute sounding pitches and timing.

The parser should correctly handle:

- treble clef
- key signatures
- accidentals
- courtesy accidentals
- octave number
- ties
- slurs
- grace notes
- chords
- multiple voices
- tuplets
- dotted rhythms
- 8va / 8vb
- transposition, if present
- articulations
- ornaments

The mandolin is normally non-transposing, so written pitch corresponds directly to sounding pitch.

---

## 18. Preserve Musical Structure

Do not flatten a score into an uninterrupted pitch sequence.

Preserve:

- measures
- barlines
- repeat signs
- first and second endings
- pickup/anacrusis measures
- D.C.
- D.S.
- Segno
- Coda
- Fine
- key changes
- time-signature changes
- tempo markings where relevant
- rehearsal marks where relevant

Repeat notation changes the structure of the music and must not be silently discarded during transcription.

---

## 19. Phrase-Level Fingering Algorithm

Recommended procedure:

### Step 1 — Parse score

Resolve all notation into:

- absolute pitch
- onset
- duration
- voice
- articulation
- chord membership
- measure
- phrase context

### Step 2 — Enumerate legal positions

For every pitch:

```text
candidate_fret = pitch_midi - open_course_midi
```

Accept all candidates satisfying:

```text
0 <= candidate_fret <= max_fret
```

### Step 3 — Apply hard constraints

Reject fingerings that violate:

- instrument range
- same-course chord feasibility
- required slide continuity
- required hammer-on/pull-off continuity
- tied-note sustain
- impossible simultaneous fretting

### Step 4 — Score remaining candidates

Consider:

- hand-position continuity
- fret height
- fret movement
- string crossing
- open-string usefulness
- technique compatibility
- chord stretch
- timbral consistency
- player skill level

### Step 5 — Optimize the phrase globally

Do not greedily select each note.

Choose the sequence of positions with the best total ergonomic score across the phrase.

Dynamic programming, graph search, or beam search are appropriate implementation strategies.

---

## 20. Suggested Fingering Cost Model

A useful conceptual scoring function is:

```text
total_cost =
    w_position * position_shift_cost
  + w_fret     * fret_height_cost
  + w_cross    * string_crossing_cost
  + w_open     * open_string_cost
  + w_tech     * technique_cost
  + w_chord    * chord_difficulty_cost
  + w_timbre   * timbre_discontinuity_cost
```

Lower cost is better.

### Recommended priority ordering

For ordinary playable tablature:

```text
physical playability
>
technique compatibility
>
hand-position continuity
>
reasonable string crossing
>
low fret number
```

For beginner tablature, increase penalties for:

- frets above 7
- abrupt shifts
- wide chord shapes
- unnecessary course skipping
- technically unusual fingerings

---

## 21. Beginner-Friendly Defaults

Unless the user requests advanced fingering:

Strongly prefer:

- frets 0–7
- open strings when idiomatic
- adjacent course movement
- compact hand positions
- simple chord shapes
- minimal large shifts
- consistent repeated-phrase fingering
- obvious low-position solutions

Avoid:

- gratuitous frets 12+
- rapid position changes
- unnecessary duplicate-note substitutions
- large course jumps
- sophisticated closed-position solutions when open-position playing is clearly easier

For fiddle tunes, reels, jigs, old-time, folk, and beginner bluegrass, open-position play should receive particularly strong preference.

---

## 22. Advanced Fingering Exceptions

Higher positions may be preferable when they:

- reduce string crossings
- enable a slide
- enable hammer-ons or pull-offs
- maintain a consistent timbre
- preserve legato
- keep a fast passage under one hand position
- avoid an awkward open string
- support a repeated pattern
- produce a more coherent phrase
- facilitate a chord or double-stop

Do not reject high positions merely because a lower equivalent exists.

Use them only with a clear musical or ergonomic reason.

---

## 23. Example of Phrase-Level Reasoning

Suppose the melody contains:

```text
D5 E5 F#5 G5
```

All notes can be played on or near the A course:

```text
A|--5--7--9--10--
```

But an alternative uses the E course:

```text
E|-----0--2--3---
A|--5------------
```

Neither is universally correct.

The preferred version depends on:

- preceding phrase
- following phrase
- desired hand position
- tempo
- articulation
- player skill
- tonal consistency

The first version may be preferable when maintaining one course is important.

The second may be preferable for a beginner or when open E produces an easier transition.

The algorithm must evaluate phrase context rather than judging individual notes in isolation.

---

## 24. Data Representation Recommendation

Represent every candidate note position as something like:

```json
{
  "pitch": "E5",
  "midi": 76,
  "course": "A",
  "fret": 7,
  "open": false
}
```

A note may contain multiple candidate positions:

```json
{
  "pitch": "E5",
  "midi": 76,
  "positions": [
    {"course": "E", "fret": 0},
    {"course": "A", "fret": 7},
    {"course": "D", "fret": 14},
    {"course": "G", "fret": 21}
  ]
}
```

The fingering engine should choose among these positions only after examining neighboring notes and technical constraints.

---

## 25. Course Index Convention

For internal representation, use one consistent ordering.

Recommended low-to-high:

```text
0 = G
1 = D
2 = A
3 = E
```

Open MIDI notes:

```json
{
  "G": 55,
  "D": 62,
  "A": 69,
  "E": 76
}
```

Tab rendering reverses the visual order:

```text
E
A
D
G
```

Do not confuse internal pitch ordering with printed tab orientation.

---

## 26. Absolute Rules

The following rules should be treated as foundational:

1. A standard mandolin has 8 strings arranged as 4 doubled courses.
2. Standard tuning is G3–D4–A4–E5.
3. Adjacent courses are seven semitones apart.
4. Every fret raises pitch by one semitone.
5. Fret 12 is one octave above the open course.
6. G3 through C#4 are playable only on the G course.
7. Duplicate pitches occur seven frets higher on each successive lower course.
8. A single course cannot simultaneously play two different fretted pitches.
9. Tablature has four lines, normally displayed E–A–D–G from top to bottom.
10. A tablature number represents a fret number.
11. Open strings are fret 0.
12. Pitch-correct does not imply ergonomically correct.
13. Fingering should be optimized across phrases, not note by note.
14. Technique markings can constrain course choice.
15. Repeats, endings, rhythm, and score structure must be preserved.
16. Beginner-friendly output should strongly favor low positions and coherent hand movement.

---

## 27. Compact Agent Mental Model

If only a minimal model can be retained, use:

```text
Instrument:
8 strings -> 4 doubled courses

Tuning:
G3 D4 A4 E5

Course interval:
+7 semitones

Fret interval:
+1 semitone

Pitch mapping:
fret = desired_midi - open_course_midi

Duplicate rule:
same pitch on next-lower course = current fret + 7

Tab orientation:
E
A
D
G

Primary ergonomic rule:
Choose fingerings for the phrase, not isolated notes.

Beginner default:
Favor frets 0–7, open strings when helpful, adjacent-course movement,
compact hand positions, and no unnecessary high-fret excursions.
```

---

## 28. Primary Design Goal

When converting sheet music to tablature, optimize for:

```text
musically faithful
+ physically possible
+ ergonomically coherent
+ appropriate for requested skill level
```

Do **not** optimize merely for:

```text
pitch correctness
```

A successful transcription should look like something a competent human mandolin player or teacher might reasonably choose to play.
