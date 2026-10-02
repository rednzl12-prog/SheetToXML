"""
Mandolin Tablature & Fingering Optimization Engine
Strictly implements the Mandolin Fingering & Tablature Specification (MANDOLIN_TAB_FINGERING_SPEC.md).

Features:
- Standard 4-course tuning: G3 (MIDI 55), D4 (MIDI 62), A4 (MIDI 69), E5 (MIDI 76)
- Phrase-level Viterbi / Dynamic Programming path optimization
- Ergonomic cost model:
    * Fret height penalties (strong preference for 0-7 open position)
    * Minimal string-crossing cost
    * Compact hand window continuity
    * Chord feasibility (no simultaneous notes on same course, reachable fret spread)
    * Idiomatic open string usage
- MusicXML 4.0 tab injection with 4-line staff details, TAB clefs, and <technical><string><fret> notations
"""

from typing import List, Dict, Any, Tuple, Optional
from lxml import etree
import copy

# Mandolin Course Definitions (MusicXML standard string convention: 1=E, 2=A, 3=D, 4=G)
COURSES = [
    {"string": 1, "name": "E", "open_midi": 76, "step": "E", "octave": 5},
    {"string": 2, "name": "A", "open_midi": 69, "step": "A", "octave": 4},
    {"string": 3, "name": "D", "open_midi": 62, "step": "D", "octave": 4},
    {"string": 4, "name": "G", "open_midi": 55, "step": "G", "octave": 3},
]

SEMITONES_MAP = {
    "C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11
}

def pitch_to_midi(step: str, alter: int, octave: int) -> int:
    """Convert musical pitch step, alter, and octave to MIDI note number."""
    step_val = SEMITONES_MAP.get(step.upper(), 0)
    midi = 12 + (octave * 12) + step_val + alter
    return midi

def get_mandolin_candidates(midi_pitch: int, max_fret: int = 20) -> List[Dict[str, Any]]:
    """Enumerate all physically valid mandolin positions for a given MIDI pitch."""
    candidates = []
    for c in COURSES:
        fret = midi_pitch - c["open_midi"]
        if 0 <= fret <= max_fret:
            candidates.append({
                "string": c["string"],
                "course": c["name"],
                "fret": fret,
                "open": fret == 0,
                "midi": midi_pitch
            })
    return candidates

def calculate_transition_cost(
    prev_pos: Dict[str, Any],
    curr_pos: Dict[str, Any],
    skill_level: str = "beginner"
) -> float:
    """
    Ergonomic cost function implementing Sections 11, 12, 13, 20, and 21 of the spec.
    Evaluates phrase continuity, fret height, hand position shifts, and string crossings.
    """
    cost = 0.0
    s_prev, f_prev = prev_pos["string"], prev_pos["fret"]
    s_curr, f_curr = curr_pos["string"], curr_pos["fret"]
    is_beginner = (skill_level.lower() == "beginner")

    # 1. Fret Height Cost (Spec Sec 11 & 21)
    # Frets 0-7: Extremely comfortable normal open-position territory
    if f_curr <= 7:
        cost += 0.0
    elif f_curr <= 12:
        cost += (f_curr - 7) * (7.0 if is_beginner else 3.5)
    else:
        # Frets 13+ incur heavy penalty unless required
        cost += 35.0 + (f_curr - 12) * (18.0 if is_beginner else 8.0)

    # 2. String Crossing Cost (Spec Sec 11)
    s_diff = abs(s_curr - s_prev)
    if s_diff == 0:
        cost += 0.0  # Same course
    elif s_diff == 1:
        cost += 1.2  # Adjacent course crossing is easy
    elif s_diff == 2:
        cost += 7.5  # Skipping one course has moderate ergonomic cost
    else:
        cost += 20.0  # Crossing 3 courses rapidly is strongly discouraged

    # 3. Position Shift & Hand Window Continuity (Spec Sec 11, 12)
    # If either note is an open string (fret 0), hand shift cost is negligible
    if curr_pos["open"] or prev_pos["open"]:
        cost += 0.5
    else:
        f_diff = abs(f_curr - f_prev)
        if s_curr == s_prev:
            # Movement along the same course
            cost += f_diff * 1.5
            if f_diff > 4:
                cost += (f_diff - 4) * 4.0  # Uncomfortable stretch on same course
        else:
            # Shift across courses
            cost += f_diff * 2.2
            if f_diff > 5:
                cost += (f_diff - 5) * 5.0

    # 4. Open String Preference (Spec Sec 13 & 21)
    if curr_pos["open"]:
        if f_prev <= 7:
            cost -= 2.5  # Reward idiomatic open string in first position
        else:
            cost += 4.0  # Penalty for abruptly jumping down to open string from high position

    return cost

def optimize_melodic_phrase(
    midi_sequence: List[int],
    skill_level: str = "beginner",
    max_fret: int = 20
) -> List[Dict[str, Any]]:
    """
    Global phrase-level optimization using Viterbi Dynamic Programming.
    Optimizes phrase continuity across all notes instead of greedy note-by-note choices.
    """
    if not midi_sequence:
        return []

    # Step 1: Enumerate candidate positions for every note
    steps_candidates = [get_mandolin_candidates(m, max_fret) for m in midi_sequence]

    # Handle any notes out of range (fallback to closest valid position)
    for idx, cands in enumerate(steps_candidates):
        if not cands:
            # Fallback for out of range pitch: find nearest course
            m = midi_sequence[idx]
            best_c = min(COURSES, key=lambda c: abs(m - c["open_midi"]))
            f = max(0, min(max_fret, m - best_c["open_midi"]))
            steps_candidates[idx] = [{
                "string": best_c["string"],
                "course": best_c["name"],
                "fret": f,
                "open": f == 0,
                "midi": m
            }]

    n_steps = len(steps_candidates)
    dp = []  # dp[t][cand_idx] = (min_cost, prev_cand_idx)

    # Initial step
    first_step = []
    for c in steps_candidates[0]:
        init_cost = 0.0
        if c["fret"] <= 7:
            init_cost += c["fret"] * 0.5
        else:
            init_cost += 15.0 + (c["fret"] - 7) * 4.0
        if c["open"]:
            init_cost -= 2.0
        first_step.append((init_cost, -1))
    dp.append(first_step)

    # Forward DP pass
    for t in range(1, n_steps):
        curr_step = []
        for j, c_curr in enumerate(steps_candidates[t]):
            best_cost = float("inf")
            best_prev = 0
            for i, c_prev in enumerate(steps_candidates[t - 1]):
                trans = calculate_transition_cost(c_prev, c_curr, skill_level)
                total = dp[t - 1][i][0] + trans
                if total < best_cost:
                    best_cost = total
                    best_prev = i
            curr_step.append((best_cost, best_prev))
        dp.append(curr_step)

    # Traceback to extract optimal path
    best_last_idx = min(range(len(steps_candidates[-1])), key=lambda i: dp[-1][i][0])
    optimal_path = []
    curr_idx = best_last_idx

    for t in range(n_steps - 1, -1, -1):
        optimal_path.append(steps_candidates[t][curr_idx])
        curr_idx = dp[t][curr_idx][1]

    optimal_path.reverse()
    return optimal_path

def optimize_chord_fingering(
    midi_pitches: List[int],
    skill_level: str = "beginner",
    max_fret: int = 20
) -> List[Dict[str, Any]]:
    """
    Select playable chord fingering ensuring NO two notes share the same course (Spec Sec 15)
    and minimizing chord stretch spread.
    """
    if not midi_pitches:
        return []
    if len(midi_pitches) == 1:
        cands = get_mandolin_candidates(midi_pitches[0], max_fret)
        if cands:
            # Sort by low fret
            cands.sort(key=lambda c: (c["fret"] > 7, c["fret"]))
            return [cands[0]]
        return [{"string": 1, "course": "E", "fret": 0, "open": True, "midi": midi_pitches[0]}]

    # For multi-note chord, assign each pitch to a distinct course (1 to 4)
    # Enumerate all candidate combinations without string collisions
    all_cands = [get_mandolin_candidates(p, max_fret) for p in midi_pitches]

    best_combo = None
    best_score = float("inf")

    # Recursive search for valid string-unique combinations
    def search_combo(note_idx: int, current_strings: set, current_assignment: list):
        nonlocal best_combo, best_score
        if note_idx == len(midi_pitches):
            # Evaluate chord shape score
            fretted = [c["fret"] for c in current_assignment if not c["open"]]
            span = (max(fretted) - min(fretted)) if len(fretted) > 1 else 0
            # Chord spread > 4 frets is penalized
            spread_cost = span * 3.0 if span <= 4 else 25.0 + (span - 4) * 10.0
            high_fret_cost = sum(c["fret"] * 2.0 for c in current_assignment if c["fret"] > 7)
            total = spread_cost + high_fret_cost
            if total < best_score:
                best_score = total
                best_combo = list(current_assignment)
            return

        for cand in all_cands[note_idx]:
            if cand["string"] not in current_strings:
                current_strings.add(cand["string"])
                current_assignment.append(cand)
                search_combo(note_idx + 1, current_strings, current_assignment)
                current_assignment.pop()
                current_strings.remove(cand["string"])

    search_combo(0, set(), [])

    if best_combo:
        return best_combo

    # Fallback if no 100% collision-free assignment found: assign greedily to closest courses
    used_strings = set()
    fallback = []
    for p in midi_pitches:
        cands = get_mandolin_candidates(p, max_fret)
        available = [c for c in cands if c["string"] not in used_strings]
        choice = available[0] if available else (cands[0] if cands else None)
        if choice:
            used_strings.add(choice["string"])
            fallback.append(choice)
        else:
            fallback.append({"string": 1, "course": "E", "fret": 0, "open": True, "midi": p})
    return fallback

def create_staff2_note(source_note: etree._Element, pos: Optional[Dict[str, Any]]) -> etree._Element:
    """
    Create a MusicXML 4.0 compliant <note> element for Staff 2 (Mandolin TAB)
    cloning the source note's timing and pitch, with strictly valid tag ordering.
    """
    new_note = etree.Element("note")

    # 1. chord tag (if note was part of a chord)
    if source_note.find("chord") is not None:
        etree.SubElement(new_note, "chord")

    # 2. pitch or rest
    is_rest = source_note.find("rest") is not None
    if is_rest:
        src_rest = source_note.find("rest")
        r_elem = etree.SubElement(new_note, "rest")
        if src_rest is not None and src_rest.get("measure"):
            r_elem.set("measure", src_rest.get("measure"))
    else:
        src_pitch = source_note.find("pitch")
        if src_pitch is not None:
            p_elem = etree.SubElement(new_note, "pitch")
            for child in src_pitch:
                c = etree.SubElement(p_elem, child.tag)
                c.text = child.text

    # 3. duration
    dur = source_note.find("duration")
    if dur is not None and dur.text:
        etree.SubElement(new_note, "duration").text = dur.text.strip()

    # 4. tie (start/stop)
    for tie in source_note.findall("tie"):
        t = etree.SubElement(new_note, "tie")
        if tie.get("type"):
            t.set("type", tie.get("type"))

    # 5. voice (Voice 2 for Staff 2)
    etree.SubElement(new_note, "voice").text = "2"

    # 6. type
    ntype = source_note.find("type")
    if ntype is not None and ntype.text:
        etree.SubElement(new_note, "type").text = ntype.text.strip()

    # 7. dot
    for _ in source_note.findall("dot"):
        etree.SubElement(new_note, "dot")

    # 8. accidental
    acc = source_note.find("accidental")
    if acc is not None and acc.text:
        etree.SubElement(new_note, "accidental").text = acc.text.strip()

    # 9. time-modification (tuplets)
    tmod = source_note.find("time-modification")
    if tmod is not None:
        new_tmod = etree.SubElement(new_note, "time-modification")
        for child in tmod:
            c = etree.SubElement(new_tmod, child.tag)
            c.text = child.text

    # 10. stem
    stem = source_note.find("stem")
    if stem is not None and stem.text:
        etree.SubElement(new_note, "stem").text = stem.text.strip()

    # 11. staff (Staff 2)
    etree.SubElement(new_note, "staff").text = "2"

    # 12. beam
    for b in source_note.findall("beam"):
        nb = etree.SubElement(new_note, "beam")
        if b.get("number"):
            nb.set("number", b.get("number"))
        nb.text = b.text.strip() if b.text else "begin"

    # 13. notations (Technical string and fret for TAB)
    if pos is not None and not is_rest:
        notat = etree.SubElement(new_note, "notations")
        tech = etree.SubElement(notat, "technical")
        etree.SubElement(tech, "string").text = str(pos["string"])
        etree.SubElement(tech, "fret").text = str(pos["fret"])

    return new_note


def apply_mandolin_tab_to_score(
    xml_content: str,
    skill_level: str = "beginner",
    create_tab_staff: bool = True
) -> Tuple[str, Dict[str, Any]]:
    """
    Parse a MusicXML string, optimize every note's fingering according to
    MANDOLIN_TAB_FINGERING_SPEC.md, and return the enhanced MusicXML string.
    
    When create_tab_staff=True, creates a pristine dual-staff score:
      - Staff 1: Standard Treble Notation (5 lines)
      - Staff 2: Mandolin Tablature (4 lines, G-D-A-E courses, strictly populated for every measure)
    """
    parser = etree.XMLParser(remove_blank_text=True)
    try:
        root = etree.fromstring(xml_content.encode("utf-8"), parser=parser)
    except Exception as e:
        raise ValueError(f"Invalid XML content: {e}")

    # Ensure score-partwise
    if root.tag != "score-partwise":
        raise ValueError("MusicXML must be score-partwise")

    # Find parts
    parts = root.findall("part")
    if not parts:
        raise ValueError("No <part> found in MusicXML score.")

    part_to_optimize = parts[0]
    part_id = part_to_optimize.get("id", "P1")

    # Setup Part List & Part Names
    score_part = root.find(f".//score-part[@id='{part_id}']")
    if score_part is not None:
        pname = score_part.find("part-name")
        if pname is not None:
            if "mandolin" not in pname.text.lower():
                pname.text = "Mandolin (TAB & Notation)" if create_tab_staff else "Mandolin Tablature"
        p_abbr = score_part.find("part-abbreviation")
        if p_abbr is None:
            p_abbr = etree.SubElement(score_part, "part-abbreviation")
        p_abbr.text = "Man."

    # Process Measures
    measures = part_to_optimize.findall("measure")
    if not measures:
        raise ValueError("No measures found in score.")

    # Configure Measure 1 Attributes
    m1 = measures[0]
    attrs = m1.find("attributes")
    if attrs is None:
        attrs = etree.Element("attributes")
        m1.insert(0, attrs)

    if create_tab_staff:
        # Set <staves>2</staves>
        staves_elem = attrs.find("staves")
        if staves_elem is None:
            staves_elem = etree.SubElement(attrs, "staves")
        staves_elem.text = "2"

        # Ensure Clef 1 = Treble G, Clef 2 = TAB
        c1 = attrs.find(".//clef[@number='1']")
        if c1 is None:
            c1 = etree.SubElement(attrs, "clef", number="1")
            etree.SubElement(c1, "sign").text = "G"
            etree.SubElement(c1, "line").text = "2"
        else:
            s_elem = c1.find("sign")
            if s_elem is not None:
                s_elem.text = "G"
            l_elem = c1.find("line")
            if l_elem is not None:
                l_elem.text = "2"

        c2 = attrs.find(".//clef[@number='2']")
        if c2 is None:
            c2 = etree.SubElement(attrs, "clef", number="2")
            etree.SubElement(c2, "sign").text = "TAB"
            etree.SubElement(c2, "line").text = "5"
        else:
            s_elem = c2.find("sign")
            if s_elem is not None:
                s_elem.text = "TAB"
            l_elem = c2.find("line")
            if l_elem is not None:
                l_elem.text = "5"

        # Configure staff-details for Staff 1 (5 lines) and Staff 2 (4 lines mandolin tuning)
        for old_sd in attrs.findall("staff-details"):
            attrs.remove(old_sd)

        sd1 = etree.SubElement(attrs, "staff-details", number="1")
        etree.SubElement(sd1, "staff-lines").text = "5"

        sd2 = etree.SubElement(attrs, "staff-details", number="2")
        etree.SubElement(sd2, "staff-lines").text = "4"
        for i, (step, octv) in enumerate([("G", 3), ("D", 4), ("A", 4), ("E", 5)], start=1):
            tuning = etree.SubElement(sd2, "staff-tuning", line=str(i))
            etree.SubElement(tuning, "tuning-step").text = step
            etree.SubElement(tuning, "tuning-octave").text = str(octv)
    else:
        # Single-staff TAB
        staff_details = attrs.find("staff-details")
        if staff_details is None:
            staff_details = etree.SubElement(attrs, "staff-details")
        else:
            staff_details.clear()
        staff_details.set("number", "1")
        lines_elem = etree.SubElement(staff_details, "staff-lines")
        lines_elem.text = "4"
        for i, (step, octv) in enumerate([("G", 3), ("D", 4), ("A", 4), ("E", 5)], start=1):
            tuning = etree.SubElement(staff_details, "staff-tuning", line=str(i))
            etree.SubElement(tuning, "tuning-step").text = step
            etree.SubElement(tuning, "tuning-octave").text = str(octv)

    # Clean out any old/partial Staff 2 notes & extract Staff 1 notes
    measure_staff1_map = {}
    note_elements_sequence = []
    current_chord = []

    for m in measures:
        if create_tab_staff:
            s2_notes = [n for n in m.findall("note") if n.find("staff") is not None and n.find("staff").text == "2"]
            to_remove = set(s2_notes)
            children = list(m)
            for i, c in enumerate(children):
                if c.tag == "backup" and i + 1 < len(children) and children[i + 1] in s2_notes:
                    to_remove.add(c)
            for elem in to_remove:
                m.remove(elem)

        m_notes = m.findall("note")
        for n in m_notes:
            if create_tab_staff:
                st = n.find("staff")
                if st is None:
                    st = etree.SubElement(n, "staff")
                st.text = "1"

            is_rest = n.find("rest") is not None
            pitch_elem = n.find("pitch")
            if is_rest or pitch_elem is None:
                if current_chord:
                    note_elements_sequence.append(current_chord)
                    current_chord = []
                continue

            step = pitch_elem.find("step")
            alter = pitch_elem.find("alter")
            octave = pitch_elem.find("octave")

            s_val = step.text.strip() if step is not None and step.text else "C"
            a_val = int(alter.text.strip()) if alter is not None and alter.text else 0
            o_val = int(octave.text.strip()) if octave is not None and octave.text else 4

            midi = pitch_to_midi(s_val, a_val, o_val)
            item = {"element": n, "midi": midi, "step": s_val, "alter": a_val, "octave": o_val}

            is_chord = n.find("chord") is not None
            if is_chord and current_chord:
                current_chord.append(item)
            else:
                if current_chord:
                    note_elements_sequence.append(current_chord)
                current_chord = [item]

        measure_staff1_map[m] = m_notes

    if current_chord:
        note_elements_sequence.append(current_chord)

    # Flatten single notes into phrase sequence for Viterbi optimization
    single_melodic_items = []
    for grp in note_elements_sequence:
        if len(grp) == 1:
            single_melodic_items.append(grp[0]["midi"])

    # Run phrase-level Viterbi optimization
    optimal_single_positions = optimize_melodic_phrase(
        single_melodic_items,
        skill_level=skill_level
    )

    # Build note position map & stats
    stats = {
        "totalNotes": 0,
        "openStringsCount": 0,
        "firstPositionCount": 0,  # frets 0-7
        "upperPositionCount": 0,  # frets 8+
        "highestFret": 0,
        "stringUsage": {1: 0, 2: 0, 3: 0, 4: 0}
    }

    note_pos_map = {}
    single_ptr = 0
    for grp in note_elements_sequence:
        if len(grp) == 1:
            pos = optimal_single_positions[single_ptr]
            single_ptr += 1
            positions = [pos]
        else:
            midis = [item["midi"] for item in grp]
            positions = optimize_chord_fingering(midis, skill_level=skill_level)

        for item, pos in zip(grp, positions):
            note_pos_map[id(item["element"])] = pos
            s_num = pos["string"]
            f_num = pos["fret"]

            stats["totalNotes"] += 1
            stats["stringUsage"][s_num] = stats["stringUsage"].get(s_num, 0) + 1
            if f_num == 0:
                stats["openStringsCount"] += 1
            if f_num <= 7:
                stats["firstPositionCount"] += 1
            else:
                stats["upperPositionCount"] += 1
            if f_num > stats["highestFret"]:
                stats["highestFret"] = f_num

    # If single-staff, inject notations directly onto Staff 1 notes
    if not create_tab_staff:
        for grp in note_elements_sequence:
            for item in grp:
                n_elem = item["element"]
                pos = note_pos_map.get(id(n_elem))
                if pos:
                    notations = n_elem.find("notations")
                    if notations is None:
                        notations = etree.SubElement(n_elem, "notations")
                    technical = notations.find("technical")
                    if technical is None:
                        technical = etree.SubElement(notations, "technical")
                    else:
                        for old_tag in technical.findall("string") + technical.findall("fret"):
                            technical.remove(old_tag)
                    etree.SubElement(technical, "string").text = str(pos["string"])
                    etree.SubElement(technical, "fret").text = str(pos["fret"])
    else:
        # Dual-staff: synthesize Staff 2 (Mandolin TAB) for every single measure
        for m, m_notes in measure_staff1_map.items():
            dur_total = 0
            for n in m_notes:
                if n.find("chord") is None and n.find("grace") is None:
                    d = n.find("duration")
                    if d is not None and d.text and d.text.strip().isdigit():
                        dur_total += int(d.text.strip())

            if dur_total > 0 and m_notes:
                bk = etree.SubElement(m, "backup")
                etree.SubElement(bk, "duration").text = str(dur_total)
                for n in m_notes:
                    pos = note_pos_map.get(id(n))
                    s2_n = create_staff2_note(n, pos)
                    m.append(s2_n)

    formatted_xml = etree.tostring(root, pretty_print=True, encoding="utf-8", xml_declaration=True).decode("utf-8")
    return formatted_xml, stats

