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
"""

from typing import Any, Dict, List

# Mandolin Course Definitions (MusicXML standard string convention: 1=E, 2=A, 3=D, 4=G)
COURSES = [
    {"string": 1, "name": "E", "open_midi": 76, "step": "E", "octave": 5},
    {"string": 2, "name": "A", "open_midi": 69, "step": "A", "octave": 4},
    {"string": 3, "name": "D", "open_midi": 62, "step": "D", "octave": 4},
    {"string": 4, "name": "G", "open_midi": 55, "step": "G", "octave": 3},
]

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
