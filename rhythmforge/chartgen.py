from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import librosa
import numpy as np

from .config import DIFFICULTIES
from .library import charts_dir, portable_path


def _safe_float(x: Any) -> float:
    arr = np.asarray(x).reshape(-1)
    return float(arr[0]) if arr.size else 0.0


def _song_id(audio_path: Path) -> str:
    h = hashlib.sha1()
    h.update(audio_path.name.encode("utf-8", errors="ignore"))
    try:
        with audio_path.open("rb") as f:
            h.update(f.read(1024 * 1024))
    except OSError:
        pass
    return h.hexdigest()[:12]


def _normalize(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    if v.size == 0:
        return v
    lo, hi = float(np.min(v)), float(np.max(v))
    if hi - lo < 1e-9:
        return np.zeros_like(v)
    return (v - lo) / (hi - lo)


def _feature_at(feature: np.ndarray, frame: int, default: float = 0.0) -> float:
    if feature.size == 0:
        return default
    return float(feature[int(np.clip(frame, 0, len(feature) - 1))])


def _tempo_confidence_at_times(times: np.ndarray, onset_norm: np.ndarray, sr: int, hop_length: int) -> float:
    if times.size == 0 or onset_norm.size == 0:
        return 0.0
    frames = librosa.time_to_frames(times, sr=sr, hop_length=hop_length)
    vals = []
    for f in frames:
        lo = max(0, int(f) - 1)
        hi = min(len(onset_norm), int(f) + 2)
        if hi > lo:
            vals.append(float(np.max(onset_norm[lo:hi])))
    return float(np.mean(vals)) if vals else 0.0


def _correct_tempo_octave(tempo: float, beat_times: np.ndarray, onset_norm: np.ndarray, sr: int, hop_length: int) -> tuple[float, np.ndarray, str]:
    """Correct common half/double BPM errors from beat trackers.

    librosa often reports 92.5 for tracks that listeners count as 185 BPM.
    For rhythm games, the faster tactus usually feels better because notes align
    with the driving eighth-note pulse.  We probe the midpoint between detected
    beats; if those midpoints also have strong onset energy, we promote the chart
    tempo to double-time and insert midpoint beats.
    """
    tempo = float(tempo or 120.0)
    if len(beat_times) < 4:
        return tempo, beat_times, "raw"

    intervals = np.diff(beat_times)
    med = float(np.median(intervals[intervals > 1e-6])) if np.any(intervals > 1e-6) else 60.0 / tempo
    if med <= 0:
        return tempo, beat_times, "raw"

    beat_score = _tempo_confidence_at_times(beat_times, onset_norm, sr, hop_length)
    midpoints = beat_times[:-1] + np.diff(beat_times) / 2.0
    mid_score = _tempo_confidence_at_times(midpoints, onset_norm, sr, hop_length)

    # Prefer rhythm-game double time for fast tracks.  This fixes cases like
    # 185 BPM being reported as 92.3 BPM, while avoiding accidental doubling of
    # slow ballads with weak midpoint pulse.
    if 70.0 <= tempo < 118.0 and tempo * 2.0 <= 230.0:
        if mid_score >= max(0.24, beat_score * 0.42):
            doubled = np.sort(np.concatenate([beat_times, midpoints]))
            return tempo * 2.0, doubled, "double-time midpoint pulse"

    # Conversely, collapse unlikely ultra-fast estimates if every other beat is
    # much stronger than the intervening beats.
    if tempo > 205.0 and len(beat_times) >= 8:
        even_score = _tempo_confidence_at_times(beat_times[::2], onset_norm, sr, hop_length)
        odd_score = _tempo_confidence_at_times(beat_times[1::2], onset_norm, sr, hop_length)
        if min(even_score, odd_score) < max(even_score, odd_score) * 0.33:
            return tempo / 2.0, beat_times[::2] if even_score >= odd_score else beat_times[1::2], "half-time cleanup"

    return tempo, beat_times, "raw"


def _build_subbeat_grid(beat_times: np.ndarray, divisions: list[int]) -> np.ndarray:
    """Return a dense musical grid: beats plus 1/2, 1/3 and 1/4 subdivisions.

    This is the main timing improvement over the initial MVP.  Instead of using
    raw onset positions directly, notes are pulled toward a musical grid, so they
    feel locked to the track even when onset detection is a few frames late.
    """
    if len(beat_times) < 2:
        return np.asarray([], dtype=float)

    grid: list[float] = []
    divs = sorted(set(max(1, int(d)) for d in divisions))
    for a, b in zip(beat_times[:-1], beat_times[1:]):
        interval = float(b - a)
        if interval <= 0:
            continue
        for div in divs:
            for k in range(div):
                grid.append(float(a + interval * k / div))
    grid.append(float(beat_times[-1]))
    return np.asarray(sorted(set(round(x, 5) for x in grid)), dtype=float)


def _snap_to_grid(t: float, grid: np.ndarray, beat_interval: float, strength: float, salience: float) -> tuple[float, bool]:
    """Hybrid timing: snap rhythmic hits, preserve non-grid melodic hits.

    Earlier versions over-snapped everything.  That improves simple dance tracks
    but makes loose vocals/guitar riffs feel wrong.  This function returns both
    the final note time and whether the note is grid-locked, so the renderer can
    still draw the beat grid while non-grid notes remain exactly where the audio
    analysis found them.
    """
    if grid.size == 0:
        return float(t), False
    idx = int(np.searchsorted(grid, t))
    lo = max(0, idx - 3)
    hi = min(len(grid), idx + 4)
    nearby = grid[lo:hi]
    q = float(nearby[np.argmin(np.abs(nearby - t))])
    tolerance = min(0.078, max(0.030, beat_interval * (0.14 + 0.10 * strength)))
    dist = abs(q - t)
    should_snap = dist <= tolerance and (salience >= 0.52 or dist <= tolerance * strength)
    return (q, True) if should_snap else (float(t), False)


def _local_bpm_at(t: float, beat_times: np.ndarray, global_tempo: float) -> float:
    if len(beat_times) < 3:
        return float(global_tempo or 120.0)
    idx = int(np.searchsorted(beat_times, t))
    lo = max(0, idx - 2)
    hi = min(len(beat_times) - 1, idx + 2)
    intervals = np.diff(beat_times[lo : hi + 1])
    intervals = intervals[intervals > 1e-6]
    if intervals.size == 0:
        return float(global_tempo or 120.0)
    return float(60.0 / np.median(intervals))


def _lane_from_features(
    lanes: int,
    frame_i: int,
    chroma: np.ndarray,
    spectral_centroid: np.ndarray,
    prev_lane: int | None,
    seed: int,
) -> int:
    if chroma.size:
        chroma_vec = chroma[:, min(frame_i, chroma.shape[1] - 1)]
        pitch_class = int(np.argmax(chroma_vec)) if float(np.max(chroma_vec)) > 0 else 0
    else:
        pitch_class = 0
    centroid = _feature_at(spectral_centroid, frame_i)
    lane = int((pitch_class + int(centroid // 700) + seed) % lanes)
    if prev_lane is not None and lane == prev_lane and lanes > 1:
        # Avoid long runs on a single key unless the music really demands it.
        lane = (lane + 1 + (seed % (lanes - 1))) % lanes
    return lane


def _note_color(energy: float, onset_score: float, is_downbeat: bool, highlight: bool) -> str:
    if highlight:
        return "highlight"
    if is_downbeat or onset_score >= 0.78:
        return "accent"
    if energy >= 0.62:
        return "bright"
    return "normal"


def _dedupe_and_limit(notes: list[dict[str, Any]], lanes: int, max_nps: float) -> list[dict[str, Any]]:
    notes.sort(key=lambda n: (float(n["time"]), int(n["lane"])))
    deduped: list[dict[str, Any]] = []
    occupied: set[tuple[int, int]] = set()
    for n in notes:
        key = (int(round(float(n["time"]) * 1000)), int(n["lane"]))
        if key in occupied:
            continue
        occupied.add(key)
        deduped.append(n)

    # Sliding 1-second cap.  If the generator becomes too dense, keep stronger
    # and accent notes first, then drop low-salience filler.
    result: list[dict[str, Any]] = []
    for n in deduped:
        t = float(n["time"])
        window = [x for x in result if t - float(x["time"]) <= 1.0]
        if len(window) < max_nps:
            result.append(n)
            continue
        weakest = min(window, key=lambda x: (x.get("salience", 0.0), x.get("color") == "normal"))
        if float(n.get("salience", 0.0)) > float(weakest.get("salience", 0.0)) + 0.08:
            result.remove(weakest)
            result.append(n)
    result.sort(key=lambda n: (float(n["time"]), int(n["lane"])))
    return result



def _pattern_lanes(pattern: str, lanes: int, step_i: int, seed: int) -> list[int]:
    if lanes <= 0:
        return []
    base = (seed + step_i) % lanes
    if pattern == "stair":
        return [step_i % lanes]
    if pattern == "reverse_stair":
        return [(lanes - 1 - step_i) % lanes]
    if pattern == "trill":
        return [base if step_i % 2 == 0 else (base + 1) % lanes]
    if pattern == "wide_trill":
        return [base if step_i % 2 == 0 else (base + max(2, lanes // 2)) % lanes]
    if pattern == "chord_beat":
        return [base, (base + 2) % lanes] if lanes >= 4 and step_i % 2 == 0 else [base]
    if pattern == "jack":
        return [base]
    return [base]


def _add_rhythm_game_patterns(
    notes: list[dict[str, Any]],
    *,
    beat_times: np.ndarray,
    onset_norm: np.ndarray,
    rms_norm: np.ndarray,
    highlight_curve: np.ndarray,
    highlight_threshold: float,
    sr: int,
    hop_length: int,
    duration: float,
    tempo: float,
    cfg: dict[str, Any],
    difficulty: str,
    seed: int,
) -> None:
    """Add familiar rhythm-game patterns on top of audio onsets.

    The goal is not random density.  We add short 1-measure motifs: stairs,
    trills, jacks, and accent chords.  Energy/onset curves decide where these
    motifs are allowed, so the player feels repeating rhythmic shapes instead of
    isolated analyzer blips.
    """
    if len(beat_times) < 5:
        return
    lanes = int(cfg["lanes"])
    base_speed = float(cfg["base_scroll_speed"])
    beat_interval = float(np.median(np.diff(beat_times))) if len(beat_times) >= 2 else 60.0 / max(tempo, 1.0)
    patterns_by_diff = {
        "normal": ["stair", "trill", "chord_beat"],
        "hard": ["stair", "reverse_stair", "trill", "wide_trill", "chord_beat"],
        "extreme": ["stair", "reverse_stair", "trill", "wide_trill", "jack", "chord_beat"],
        "master": ["stair", "reverse_stair", "trill", "wide_trill", "jack", "chord_beat"],
    }
    step_div_by_diff = {"normal": 2, "hard": 2, "extreme": 4, "master": 4}
    density_gate = {"normal": 0.42, "hard": 0.34, "extreme": 0.28, "master": 0.22}[difficulty]
    step_div = step_div_by_diff[difficulty]
    measure = 4
    existing = {(int(round(float(n["time"]) * 1000)), int(n["lane"])) for n in notes}

    for m_start in range(0, len(beat_times) - measure, measure):
        measure_beats = beat_times[m_start : m_start + measure + 1]
        if len(measure_beats) < measure + 1:
            continue
        center_t = float(measure_beats[0])
        if center_t < 0.55 or center_t > duration - 0.3:
            continue
        frame_i = int(np.clip(librosa.time_to_frames(center_t, sr=sr, hop_length=hop_length), 0, len(onset_norm) - 1))
        energy = _feature_at(rms_norm, min(frame_i, len(rms_norm) - 1))
        onset_val = _feature_at(onset_norm, frame_i)
        highlight = bool(highlight_curve.size and highlight_curve[frame_i] >= highlight_threshold)
        musical_weight = 0.55 * energy + 0.45 * onset_val + (0.20 if highlight else 0.0)
        pseudo = ((seed + m_start * 73) % 100) / 100.0
        if musical_weight < density_gate and pseudo > 0.32:
            continue

        plist = patterns_by_diff[difficulty]
        pattern = plist[(seed + m_start // measure) % len(plist)]
        # Use gallop-like rests occasionally: x-x-xx-x for higher difficulties.
        total_steps = measure * step_div
        active_steps: list[int] = []
        for sidx in range(total_steps):
            if difficulty == "normal" and step_div == 2 and sidx % 2 == 1 and musical_weight < 0.70:
                continue
            if difficulty in {"extreme", "master"} and pattern == "jack" and sidx % 2 == 1:
                active_steps.append(sidx)
            elif pattern == "chord_beat":
                if sidx % step_div == 0 or (difficulty in {"extreme", "master"} and sidx % step_div == step_div // 2):
                    active_steps.append(sidx)
            else:
                # Familiar readable stream: every eighth on hard, sixteenth bursts on extreme/master.
                if step_div == 2 or sidx % 1 == 0:
                    if difficulty == "hard" and sidx % 4 == 3 and musical_weight < 0.62:
                        continue
                    active_steps.append(sidx)

        for sidx in active_steps:
            beat_idx = sidx // step_div
            sub = sidx % step_div
            if beat_idx >= measure:
                continue
            a = float(measure_beats[beat_idx])
            b = float(measure_beats[beat_idx + 1])
            t = a + (b - a) * sub / step_div
            if t < 0.55 or t > duration - 0.15:
                continue
            frame = int(np.clip(librosa.time_to_frames(t, sr=sr, hop_length=hop_length), 0, len(onset_norm) - 1))
            e = _feature_at(rms_norm, min(frame, len(rms_norm) - 1))
            o = _feature_at(onset_norm, frame)
            h = bool(highlight_curve.size and highlight_curve[frame] >= highlight_threshold and e >= 0.48)
            if (0.45 * e + 0.55 * o) < 0.18 and difficulty == "normal":
                continue
            local_bpm = _local_bpm_at(t, beat_times, tempo)
            speed = base_speed * np.clip(0.93 + 0.17 * (local_bpm / max(tempo, 1.0)) + 0.13 * e + (0.10 if h else 0.0), 0.86, 1.35)
            color = _note_color(e, o, sub == 0, h)
            lanes_to_add = _pattern_lanes(pattern, lanes, sidx, seed + m_start)
            # Extra chord on measure heads for high-energy hard+ charts.
            if difficulty in {"hard", "extreme", "master"} and sub == 0 and (h or e > 0.64) and lanes >= 4:
                if ((seed + sidx + m_start) % 3) == 0:
                    lanes_to_add = sorted(set(lanes_to_add + [(lanes_to_add[0] + 2) % lanes]))
            for lane in lanes_to_add:
                key = (int(round(t * 1000)), int(lane))
                if key in existing:
                    continue
                existing.add(key)
                notes.append({
                    "time": round(float(t), 4),
                    "raw_time": round(float(t), 4),
                    "grid_locked": True,
                    "lane": int(lane),
                    "type": "tap",
                    "source": f"motif-{pattern}",
                    "strength": round(float(o), 4),
                    "energy": round(float(e), 4),
                    "local_bpm": round(float(local_bpm), 3),
                    "scroll_speed": round(float(speed), 2),
                    "raw_scroll_speed": round(float(speed), 2),
                    "color": color,
                    "salience": round(float(0.42 + e * 0.32 + o * 0.36 + (0.14 if h else 0.0)), 4),
                })



def _snap_chord_clusters(notes: list[dict[str, Any]], window: float = 0.034) -> None:
    """Force near-simultaneous multi-lane hits to share one exact timestamp.

    Auto analysis often finds the kick/snare/transient in adjacent frequency
    bands a few frames apart.  In a rhythm game that feels like a broken chord,
    even though the musical intent is a chord.  This pass clusters notes that
    are already very close in time and on different lanes, then writes one
    common timestamp back to all notes in the cluster.
    """
    if len(notes) < 2:
        return
    notes.sort(key=lambda n: (float(n["time"]), int(n["lane"])))
    i = 0
    while i < len(notes):
        cluster = [notes[i]]
        j = i + 1
        lanes = {int(notes[i]["lane"])}
        while j < len(notes):
            if float(notes[j]["time"]) - float(cluster[0]["time"]) > window:
                break
            # Multi-lane near-chords are what we want to quantize.  Same-lane
            # rapid jacks should remain separate unless they are exact duplicates.
            if int(notes[j]["lane"]) not in lanes:
                cluster.append(notes[j])
                lanes.add(int(notes[j]["lane"]))
            j += 1
        if len(cluster) >= 2:
            grid_locked = [n for n in cluster if bool(n.get("grid_locked", False))]
            if grid_locked:
                # Prefer a grid-locked timestamp if one exists; this keeps chords
                # visually locked to the grey grid when the beat tracker is sure.
                anchor = float(min(grid_locked, key=lambda n: abs(float(n["time"]) - float(cluster[0]["time"])))["time"])
            else:
                # Robust common visual time for loose/non-grid chord clusters.
                anchor = float(np.median([float(n["time"]) for n in cluster]))
            anchor = round(anchor, 4)
            for n in cluster:
                n.setdefault("raw_time", round(float(n.get("time", anchor)), 4))
                n["time"] = anchor
                n["chord_locked"] = True
        i = max(i + 1, j if len(cluster) <= 1 else i + len(cluster))


def _manual_or_corrected_beats(
    *,
    manual_bpm: float | None,
    duration: float,
    raw_tempo: float,
    raw_beat_times: np.ndarray,
    onset_norm: np.ndarray,
    sr: int,
    hop_length: int,
) -> tuple[float, np.ndarray, str]:
    """Return a rhythm-game beat grid, with optional user BPM override."""
    if manual_bpm is not None and manual_bpm > 0:
        tempo = float(manual_bpm)
        interval = 60.0 / tempo
        # Anchor manual BPM to the first reliable detected beat/onset if possible.
        anchor = 0.0
        if len(raw_beat_times):
            anchor = float(raw_beat_times[0] % interval)
            if anchor > interval * 0.5:
                anchor -= interval
        start = anchor
        while start > 0:
            start -= interval
        while start < -interval:
            start += interval
        beat_times = np.arange(start, duration + interval * 2.0, interval, dtype=float)
        beat_times = beat_times[(beat_times >= 0.0) & (beat_times <= duration + 0.05)]
        return tempo, beat_times, f"manual BPM override ({tempo:.3f})"

    tempo, beat_times, correction = _correct_tempo_octave(raw_tempo, raw_beat_times, onset_norm, sr, hop_length)

    # Additional safety: if the tracker returned a slow tactus but the onset
    # curve shows strong 8th-note pulse, prefer double-time for playability.
    if 60.0 <= tempo < 100.0 and tempo * 2.0 <= 220.0 and len(beat_times) >= 4:
        midpoints = beat_times[:-1] + np.diff(beat_times) / 2.0
        beat_score = _tempo_confidence_at_times(beat_times, onset_norm, sr, hop_length)
        mid_score = _tempo_confidence_at_times(midpoints, onset_norm, sr, hop_length)
        if mid_score >= max(0.18, beat_score * 0.32):
            return tempo * 2.0, np.sort(np.concatenate([beat_times, midpoints])), "double-time safety correction"

    return tempo, beat_times, correction

def analyze_audio(audio_path: str | Path, difficulty: str = "hard", output: str | Path | None = None, manual_bpm: float | None = None) -> Path:
    audio_path = Path(audio_path).expanduser().resolve()
    if not audio_path.exists():
        raise FileNotFoundError(audio_path)
    if difficulty not in DIFFICULTIES:
        raise ValueError(f"Unknown difficulty: {difficulty}. Use one of {list(DIFFICULTIES)}")

    cfg = DIFFICULTIES[difficulty]
    y, sr = librosa.load(str(audio_path), sr=44100, mono=True)
    duration = float(librosa.get_duration(y=y, sr=sr))
    hop_length = 512

    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length, aggregate=np.median)
    onset_norm = _normalize(onset_env)
    tempo, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset_env,
        sr=sr,
        hop_length=hop_length,
        tightness=120,
        trim=False,
    )
    tempo = _safe_float(tempo) or 120.0
    raw_beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop_length)
    tempo, beat_times, tempo_correction = _manual_or_corrected_beats(
        manual_bpm=manual_bpm,
        duration=duration,
        raw_tempo=tempo,
        raw_beat_times=raw_beat_times,
        onset_norm=onset_norm,
        sr=sr,
        hop_length=hop_length,
    )
    beat_interval = float(np.median(np.diff(beat_times))) if len(beat_times) >= 2 else 60.0 / tempo
    grid = _build_subbeat_grid(beat_times, list(cfg["beat_divisions"]))

    onset_frames = librosa.onset.onset_detect(
        onset_envelope=onset_env,
        sr=sr,
        hop_length=hop_length,
        units="frames",
        backtrack=True,
        normalize=True,
        wait=1,
        delta=0.04,
        pre_max=3,
        post_max=3,
        pre_avg=8,
        post_avg=8,
    )
    onset_times = librosa.frames_to_time(onset_frames, sr=sr, hop_length=hop_length)

    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
    rms_norm = _normalize(rms)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length)
    spectral_centroid = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=hop_length)[0]
    centroid_norm = _normalize(spectral_centroid)

    # Highlight detection: high energy plus high onset density.  This yields a
    # restrained color accent in chorus/drop-like sections.
    frame_times = librosa.frames_to_time(np.arange(len(onset_env)), sr=sr, hop_length=hop_length)
    energy_interp = np.interp(frame_times, librosa.frames_to_time(np.arange(len(rms_norm)), sr=sr, hop_length=hop_length), rms_norm)
    highlight_curve = _normalize(0.58 * energy_interp[: len(onset_norm)] + 0.42 * onset_norm)
    highlight_threshold = float(np.quantile(highlight_curve, 0.82)) if highlight_curve.size else 1.0

    candidates: list[tuple[float, int, float, float]] = []
    raw_scores: list[float] = []
    for frame, raw_t in zip(onset_frames, onset_times):
        t = float(raw_t)
        if t < 0.35 or t > duration - 0.12:
            continue
        frame_i = int(np.clip(frame, 0, len(onset_norm) - 1))
        energy = _feature_at(rms_norm, min(frame_i, len(rms_norm) - 1))
        brightness = _feature_at(centroid_norm, min(frame_i, len(centroid_norm) - 1))
        score = 0.68 * _feature_at(onset_norm, frame_i) + 0.24 * energy + 0.08 * brightness
        raw_scores.append(score)
        candidates.append((t, frame_i, score, energy))

    if not candidates:
        raise RuntimeError("No usable onsets detected. Try a louder/percussive MP3 or check ffmpeg/mp3 decoding.")

    strong_threshold = float(np.quantile(raw_scores, cfg["onset_quantile"]))
    weak_threshold = float(np.quantile(raw_scores, cfg["weak_onset_quantile"]))
    lanes = int(cfg["lanes"])
    base_speed = float(cfg["base_scroll_speed"])

    notes: list[dict[str, Any]] = []
    prev_time_by_lane = [-999.0 for _ in range(lanes)]
    prev_lane: int | None = None
    seed = int(hashlib.sha1(audio_path.stem.encode("utf-8", errors="ignore")).hexdigest()[:6], 16)

    for idx, (raw_t, frame_i, score, energy) in enumerate(candidates):
        if score < weak_threshold:
            continue
        t, grid_locked = _snap_to_grid(raw_t, grid, beat_interval, float(cfg.get("grid_snap_strength", 0.75)), score)
        is_strong = score >= strong_threshold
        if not is_strong and difficulty == "hard" and energy < 0.52:
            continue

        nearest_beat_dist = float(np.min(np.abs(beat_times - t))) if len(beat_times) else 999.0
        is_downbeat = nearest_beat_dist <= min(0.050, beat_interval * 0.14)
        local_bpm = _local_bpm_at(t, beat_times, tempo)
        frame_h = int(np.clip(frame_i, 0, len(highlight_curve) - 1))
        is_highlight = bool(highlight_curve.size and highlight_curve[frame_h] >= highlight_threshold and energy >= 0.54)
        speed = base_speed * np.clip(0.90 + 0.22 * (local_bpm / max(tempo, 1.0)) + 0.16 * energy + (0.10 if is_highlight else 0.0), 0.86, 1.35)
        color = _note_color(energy, score, is_downbeat, is_highlight)
        lane = _lane_from_features(lanes, frame_i, chroma, spectral_centroid, prev_lane, seed + idx)

        if t - prev_time_by_lane[lane] < float(cfg["min_interval"]):
            continue

        salience = float(score + 0.25 * energy + (0.25 if is_downbeat else 0.0) + (0.20 if is_highlight else 0.0))
        notes.append({
            "time": round(float(t), 4),
            "raw_time": round(float(raw_t), 4),
            "grid_locked": bool(grid_locked),
            "lane": int(lane),
            "type": "tap",
            "source": "onset-grid",
            "strength": round(float(score), 4),
            "energy": round(float(energy), 4),
            "local_bpm": round(float(local_bpm), 3),
            "scroll_speed": round(float(speed), 2),
            "raw_scroll_speed": round(float(speed), 2),
            "color": color,
            "salience": round(salience, 4),
        })
        prev_time_by_lane[lane] = t
        prev_lane = lane

        # Add controlled chords on accents.  This raises difficulty without
        # making the chart random, because chords appear mostly at downbeats or
        # chorus/drop accents.
        chord_roll = ((seed + idx * 1103515245) & 0xFFFF) / 0xFFFF
        if (is_downbeat or is_highlight or score > strong_threshold + 0.12) and chord_roll < float(cfg["accent_chord_probability"]):
            lane2 = (lane + 2 + (idx % max(1, lanes - 2))) % lanes
            if t - prev_time_by_lane[lane2] >= float(cfg["min_interval"]):
                notes.append({
                    "time": round(float(t), 4),
                    "lane": int(lane2),
                    "type": "tap",
                    "source": "accent-chord",
                    "strength": round(float(score), 4),
                    "energy": round(float(energy), 4),
                    "local_bpm": round(float(local_bpm), 3),
                    "scroll_speed": round(float(speed * 1.02), 2),
                    "raw_scroll_speed": round(float(speed * 1.02), 2),
                    "color": "accent" if not is_highlight else "highlight",
                    "salience": round(salience + 0.12, 4),
                })
                prev_time_by_lane[lane2] = t

    # Musical beat/subbeat fillers.  The initial version was too sparse/easy;
    # this pass adds streams aligned to the same beat grid, especially in high
    # energy sections, while preserving a density cap.
    if grid.size:
        existing_times = np.asarray([float(n["time"]) for n in notes], dtype=float) if notes else np.asarray([])
        min_dist = float(cfg["min_interval"]) * 0.72
        stream_roll_base = seed % 97
        for gi, gt in enumerate(grid):
            t = float(gt)
            if t < 0.55 or t > duration - 0.15:
                continue
            frame_i = int(np.clip(librosa.time_to_frames(t, sr=sr, hop_length=hop_length), 0, len(onset_norm) - 1))
            energy = _feature_at(rms_norm, min(frame_i, len(rms_norm) - 1))
            onset_value = _feature_at(onset_norm, frame_i)
            if existing_times.size and float(np.min(np.abs(existing_times - t))) < min_dist:
                continue
            # Keep hard musical; make extreme/master denser and streamier.
            local_score = 0.55 * onset_value + 0.45 * energy
            chance = float(cfg["stream_probability"]) * (0.45 + 0.85 * local_score)
            pseudo = ((stream_roll_base + gi * 37) % 100) / 100.0
            if local_score < 0.38 and pseudo > chance:
                continue
            if difficulty == "hard" and gi % 2 == 1 and local_score < 0.70:
                continue

            local_bpm = _local_bpm_at(t, beat_times, tempo)
            is_highlight = bool(highlight_curve.size and highlight_curve[frame_i] >= highlight_threshold and energy >= 0.54)
            lane = (gi + int(energy * 10) + seed) % lanes
            speed = base_speed * np.clip(0.90 + 0.20 * (local_bpm / max(tempo, 1.0)) + 0.13 * energy + (0.10 if is_highlight else 0.0), 0.86, 1.35)
            color = _note_color(energy, local_score, gi % max(1, len(cfg["beat_divisions"])) == 0, is_highlight)
            notes.append({
                "time": round(t, 4),
                "lane": int(lane),
                "type": "tap",
                "source": "subbeat-fill",
                "strength": round(float(onset_value), 4),
                "energy": round(float(energy), 4),
                "local_bpm": round(float(local_bpm), 3),
                "scroll_speed": round(float(speed), 2),
                "color": color,
                "salience": round(float(local_score + (0.15 if is_highlight else 0.0)), 4),
            })
            existing_times = np.append(existing_times, t)

    _add_rhythm_game_patterns(
        notes,
        beat_times=beat_times,
        onset_norm=onset_norm,
        rms_norm=rms_norm,
        highlight_curve=highlight_curve,
        highlight_threshold=highlight_threshold,
        sr=sr,
        hop_length=hop_length,
        duration=duration,
        tempo=tempo,
        cfg=cfg,
        difficulty=difficulty,
        seed=seed,
    )

    _snap_chord_clusters(notes, window=0.034 if difficulty in {"normal", "hard"} else 0.042)
    notes = _dedupe_and_limit(notes, lanes, float(cfg["max_notes_per_second"]))

    # If a quiet song is still too empty, force a minimum chart density using the
    # beat grid.  This prevents hard/extreme/master from all feeling easy.
    target_notes = int(max(12, duration * float(cfg["target_nps"])))
    if len(notes) < target_notes and grid.size:
        existing = np.asarray([float(n["time"]) for n in notes], dtype=float) if notes else np.asarray([])
        filler_grid = grid[(grid > 0.55) & (grid < duration - 0.15)]
        for i, t in enumerate(filler_grid):
            if existing.size and float(np.min(np.abs(existing - t))) < float(cfg["min_interval"]) * 0.65:
                continue
            frame_i = int(np.clip(librosa.time_to_frames(float(t), sr=sr, hop_length=hop_length), 0, len(onset_norm) - 1))
            energy = _feature_at(rms_norm, min(frame_i, len(rms_norm) - 1))
            local_bpm = _local_bpm_at(float(t), beat_times, tempo)
            speed = base_speed * np.clip(0.92 + 0.20 * (local_bpm / max(tempo, 1.0)) + 0.12 * energy, 0.86, 1.30)
            notes.append({
                "time": round(float(t), 4),
                "lane": int((i * 2 + seed) % lanes),
                "type": "tap",
                "source": "density-fill",
                "strength": round(_feature_at(onset_norm, frame_i), 4),
                "energy": round(float(energy), 4),
                "local_bpm": round(float(local_bpm), 3),
                "scroll_speed": round(float(speed), 2),
                "color": "normal" if energy < 0.60 else "bright",
                "salience": round(float(0.28 + energy * 0.25), 4),
            })
            existing = np.append(existing, float(t))
            if len(notes) >= target_notes:
                break
        _snap_chord_clusters(notes, window=0.034 if difficulty in {"normal", "hard"} else 0.042)
        notes = _dedupe_and_limit(notes, lanes, float(cfg["max_notes_per_second"]))

    # Strip internal salience from the persisted chart after sorting.
    for n in notes:
        n.pop("salience", None)
    notes.sort(key=lambda n: (float(n["time"]), int(n["lane"])))

    chart_id = f"{_song_id(audio_path)}:{difficulty}:{len(notes)}"

    chart = {
        "version": 6,
        "chart_id": chart_id,
        "song_id": _song_id(audio_path),
        "title": audio_path.stem,
        # Store project-relative paths such as "music/song.mp3" whenever the
        # audio is inside the app folder. This makes generated charts portable
        # across PCs and release folders.
        "audio_path": portable_path(audio_path),
        "difficulty": difficulty,
        "duration": round(duration, 4),
        "tempo_bpm": round(tempo, 3),
        "beat_interval": round(float(beat_interval), 5),
        "grid_times": [round(float(x), 4) for x in grid[(grid >= 0) & (grid <= duration)]][:8000],
        "lanes": lanes,
        "keys": cfg["keys"],
        "special_keys": {"speed": "q", "echo": "w", "normal": "e"},
        # Negative default compensates for the fact that many MP3 decoders and
        # pygame mixer paths add a small perceptual output latency on Windows.
        # Users can still tune this in the JSON if their device differs.
        "offset_ms": -20,
        "base_scroll_speed": base_speed,
        "scroll_speed": base_speed,
        "note_count": len(notes),
        "generator": {
            "name": "Rhythm4G autocharter",
            "version": 6,
            "developer": "집돌이 페렐만",
            "tempo_correction": tempo_correction,
            "timing": "hybrid onset timing with half/double BPM correction and motif-based rhythm-game patterns",
            "density": "aggressive difficulty presets with accent chords, streams, stairs, trills, and jacks",
        },
        "notes": notes,
    }

    if output is None:
        out_dir = charts_dir()
        output = out_dir / f"{audio_path.stem}.{difficulty}.json"
    else:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)

    output.write_text(json.dumps(chart, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a Rhythm4G chart from an MP3 file.")
    parser.add_argument("audio", help="Path to mp3/wav/ogg file")
    parser.add_argument("--difficulty", choices=list(DIFFICULTIES), default="hard")
    parser.add_argument("--output", default=None)
    parser.add_argument("--bpm", type=float, default=None, help="Manual BPM override")
    args = parser.parse_args()
    path = analyze_audio(args.audio, args.difficulty, args.output, manual_bpm=args.bpm)
    print(path)


if __name__ == "__main__":
    main()
