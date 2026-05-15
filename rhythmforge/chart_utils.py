from __future__ import annotations

from statistics import median
from typing import Any


COLOR_RANK = {
    "normal": 0,
    "bright": 1,
    "accent": 2,
    "highlight": 3,
}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _best_color(notes: list[dict[str, Any]]) -> str:
    return max(
        (str(n.get("color", "normal")) for n in notes),
        key=lambda c: COLOR_RANK.get(c, 0),
        default="normal",
    )


def normalize_chord_visuals(
    notes: list[dict[str, Any]],
    *,
    chord_window: float = 0.034,
) -> list[dict[str, Any]]:
    """Normalize visual attributes of clean simultaneous chord groups.

    Auto-generated charts can contain a chord whose notes share the intended
    musical timing but have slightly different scroll_speed/raw_time/color
    metadata.  That makes 3+ note chords look uneven even when their ``time``
    fields are already equal.  This function locks every clean multi-lane chord
    group to one timestamp, one visual speed, and one visual color.

    Same-lane duplicates inside the window are not normalized, because they are
    usually jacks/trills rather than chords.
    """
    if not notes:
        return notes

    ordered = sorted(notes, key=lambda n: (_as_float(n.get("time")), _as_int(n.get("lane"))))

    i = 0
    while i < len(ordered):
        start_time = _as_float(ordered[i].get("time"))
        group = [ordered[i]]
        j = i + 1
        while j < len(ordered):
            if _as_float(ordered[j].get("time")) - start_time > chord_window:
                break
            group.append(ordered[j])
            j += 1

        lanes = [_as_int(n.get("lane")) for n in group]
        distinct_lanes = set(lanes)

        if len(group) >= 2 and len(distinct_lanes) == len(group):
            times = [_as_float(n.get("time")) for n in group]
            speeds = [
                _as_float(
                    n.get("visual_scroll_speed", n.get("scroll_speed", n.get("raw_scroll_speed"))),
                    0.0,
                )
                for n in group
            ]
            speeds = [s for s in speeds if s > 0]

            canonical_time = round(float(median(times)), 4)
            canonical_speed = round(float(median(speeds)), 2) if speeds else None
            canonical_color = _best_color(group)
            group_id = "chord:" + f"{canonical_time:.4f}:" + ",".join(map(str, sorted(distinct_lanes)))

            for n in group:
                original_time = _as_float(n.get("time"), canonical_time)
                n.setdefault("raw_time", round(original_time, 4))
                n["time"] = canonical_time
                n["render_time"] = canonical_time
                n["chord_locked"] = True
                n["visual_group_id"] = group_id
                n["color"] = canonical_color
                if canonical_speed is not None:
                    n["scroll_speed"] = canonical_speed
                    n["raw_scroll_speed"] = canonical_speed
                    n["visual_scroll_speed"] = canonical_speed

        i = j

    return ordered


def suppress_notes_inside_holds(
    notes: list[dict[str, Any]],
    *,
    pad_before: float = 0.10,
    pad_after: float = 0.14,
    remove_roll_overlap: bool = True,
) -> list[dict[str, Any]]:
    """Remove notes that visually/gameplay-overlap long notes.

    In Rhythm4G, a hold lane must be completely reserved from the hold head
    through its tail.  A tap/jack/chord on top of a hold is unreadable and
    impossible to play cleanly, especially after density-fill or chord-pattern
    passes run after hold placement.

    Rules:
    - Keep hold notes.
    - Remove all non-hold notes on the same lane whose time is inside the hold
      interval, including a small visual pad before/after the sustain.
    - Remove roll notes if their active interval intersects a hold.  Roll notes
      are any-key mash sections, so overlapping them with a hold creates
      contradictory input.
    """
    if not notes:
        return notes

    holds: list[tuple[int, float, float]] = []
    for n in notes:
        if str(n.get("type", "tap")) != "hold":
            continue
        lane = _as_int(n.get("lane"), -999)
        if lane < 0:
            continue
        start = _as_float(n.get("time"), 0.0)
        end = _as_float(n.get("end_time", n.get("time")), start)
        if end < start:
            start, end = end, start
        holds.append((lane, start - pad_before, end + pad_after))

    if not holds:
        return notes

    def overlaps(a0: float, a1: float, b0: float, b1: float) -> bool:
        return not (a1 < b0 or a0 > b1)

    cleaned: list[dict[str, Any]] = []
    for n in notes:
        typ = str(n.get("type", "tap"))
        if typ == "hold":
            cleaned.append(n)
            continue

        lane = _as_int(n.get("lane"), -999)
        t0 = _as_float(n.get("time"), 0.0)
        t1 = _as_float(n.get("end_time", n.get("time")), t0)
        if t1 < t0:
            t0, t1 = t1, t0

        remove = False
        for hold_lane, hold_start, hold_end in holds:
            if typ == "roll" and remove_roll_overlap and overlaps(t0, t1, hold_start, hold_end):
                remove = True
                break
            if lane == hold_lane and overlaps(t0, t1, hold_start, hold_end):
                remove = True
                break

        if not remove:
            cleaned.append(n)

    return cleaned
