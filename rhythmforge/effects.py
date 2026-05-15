from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

from .library import cache_dir


@dataclass(frozen=True)
class EffectFiles:
    normal: Path
    speed: Path | None
    echo: Path | None


def _hash_file(path: Path) -> str:
    h = hashlib.sha1()
    h.update(str(path.resolve()).encode("utf-8", errors="ignore"))
    try:
        stat = path.stat()
        h.update(str(stat.st_mtime_ns).encode())
        h.update(str(stat.st_size).encode())
        with path.open("rb") as f:
            h.update(f.read(512 * 1024))
    except OSError:
        pass
    return h.hexdigest()[:16]


def _peak_normalize(y: np.ndarray) -> np.ndarray:
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    if peak <= 1e-8:
        return y.astype(np.float32)
    return (y / max(1.0, peak)).astype(np.float32)


def prepare_effect_files(audio_path: str | Path, *, speed_rate: float = 1.15) -> EffectFiles:
    """Create portable cached WAV variants for live special keys.

    pygame.mixer.music cannot apply real-time DSP to arbitrary MP3 streams.
    Rhythm4G therefore prepares short-lived cached WAV variants and switches
    the music stream while preserving the current song time as closely as SDL
    allows. If preprocessing fails, the caller can still play the original file.
    """
    audio_path = Path(audio_path).expanduser().resolve()
    root = cache_dir()
    sid = _hash_file(audio_path)
    meta = root / f"{sid}.json"
    speed_wav = root / f"{sid}.speed_{speed_rate:.2f}.wav"
    echo_wav = root / f"{sid}.echo.wav"

    if speed_wav.exists() and echo_wav.exists():
        return EffectFiles(normal=audio_path, speed=speed_wav, echo=echo_wav)

    y, sr = librosa.load(str(audio_path), sr=44100, mono=False)
    if y.ndim == 1:
        y_mono = y
    else:
        y_mono = np.mean(y, axis=0)

    try:
        stretched = librosa.effects.time_stretch(y_mono.astype(np.float32), rate=speed_rate)
        sf.write(speed_wav, _peak_normalize(stretched), sr)
    except Exception:
        speed_wav = None  # type: ignore[assignment]

    try:
        delay = int(sr * 0.245)
        echo = np.zeros(len(y_mono) + delay * 2, dtype=np.float32)
        base = y_mono.astype(np.float32)
        echo[: len(base)] += base
        echo[delay : delay + len(base)] += base * 0.32
        echo[delay * 2 : delay * 2 + len(base)] += base * 0.15
        sf.write(echo_wav, _peak_normalize(echo), sr)
    except Exception:
        echo_wav = None  # type: ignore[assignment]

    try:
        meta.write_text(json.dumps({"source": str(audio_path), "speed_rate": speed_rate}, indent=2), encoding="utf-8")
    except Exception:
        pass

    return EffectFiles(normal=audio_path, speed=speed_wav if isinstance(speed_wav, Path) and speed_wav.exists() else None, echo=echo_wav if isinstance(echo_wav, Path) and echo_wav.exists() else None)
