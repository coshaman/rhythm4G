from dataclasses import dataclass

# Difficulty tuning.  Normal is deliberately playable, while hard/extreme/master
# keep the denser v2 style.  The generator treats these values as musical
# constraints, not exact note counts.
DIFFICULTIES = {
    "normal": {
        "lanes": 4,
        "keys": ["d", "f", "j", "k"],
        "onset_quantile": 0.68,
        "weak_onset_quantile": 0.58,
        "min_interval": 0.145,
        "base_scroll_speed": 610,
        "max_notes_per_second": 5.3,
        "beat_divisions": [1, 2],
        "accent_chord_probability": 0.10,
        "stream_probability": 0.10,
        "target_nps": 3.3,
        "grid_snap_strength": 0.88,
    },
    "hard": {
        "lanes": 4,
        "keys": ["d", "f", "j", "k"],
        "onset_quantile": 0.58,
        "weak_onset_quantile": 0.45,
        "min_interval": 0.095,
        "base_scroll_speed": 700,
        "max_notes_per_second": 10.0,
        "beat_divisions": [1, 2, 4],
        "accent_chord_probability": 0.30,
        "stream_probability": 0.28,
        "target_nps": 5.8,
        "grid_snap_strength": 0.78,
    },
    "extreme": {
        "lanes": 4,
        "keys": ["d", "f", "j", "k"],
        "onset_quantile": 0.48,
        "weak_onset_quantile": 0.36,
        "min_interval": 0.062,
        "base_scroll_speed": 805,
        "max_notes_per_second": 14.0,
        "beat_divisions": [1, 2, 3, 4],
        "accent_chord_probability": 0.50,
        "stream_probability": 0.50,
        "target_nps": 8.2,
        "grid_snap_strength": 0.70,
    },
    "master": {
        "lanes": 6,
        "keys": ["s", "d", "f", "j", "k", "l"],
        "onset_quantile": 0.40,
        "weak_onset_quantile": 0.28,
        "min_interval": 0.040,
        "base_scroll_speed": 925,
        "max_notes_per_second": 18.0,
        "beat_divisions": [1, 2, 3, 4, 6],
        "accent_chord_probability": 0.68,
        "stream_probability": 0.68,
        "target_nps": 10.8,
        "grid_snap_strength": 0.62,
    },
}

DEFAULT_SPECIAL_KEYS = {
    "speed": "q",
    "echo": "w",
    "normal": "e",
}

@dataclass(frozen=True)
class JudgementWindow:
    name: str
    ms: float
    score: int

JUDGEMENTS = [
    JudgementWindow("PERFECT", 42, 1000),
    JudgementWindow("GREAT", 75, 700),
    JudgementWindow("GOOD", 112, 400),
    JudgementWindow("BAD", 155, 100),
]
MISS_MS = 175
