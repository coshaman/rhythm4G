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
