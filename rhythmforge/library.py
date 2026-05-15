from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SUPPORTED_AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}



def project_root() -> Path:
    """Return the portable application data root.

    Source mode:
        The current working directory is used, so running commands from the
        project folder creates/reads ./music and ./charts.

    PyInstaller mode:
        The directory containing Rhythm4G.exe is used. This prevents the app
        from writing user music/charts into PyInstaller's temporary extraction
        directory and keeps a copied release folder portable.
    """
    import sys

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd().resolve()


def portable_path(path: str | Path) -> str:
    """Serialize a path without machine-specific absolute prefixes when possible."""
    p = Path(path).expanduser().resolve()
    root = project_root().resolve()
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return str(p)


def resolve_portable_path(value: str | Path, *, chart_path: str | Path | None = None) -> Path:
    """Resolve a chart-stored audio path.

    New charts store audio paths as project-relative strings such as
    ``music/example.mp3``. Older charts may contain absolute paths, so those are
    still accepted. A final compatibility fallback checks next to the chart file.
    """
    raw = Path(str(value)).expanduser()
    if raw.is_absolute():
        return raw.resolve()

    candidates = [project_root() / raw]
    if chart_path is not None:
        cp = Path(chart_path).expanduser().resolve()
        candidates.extend([cp.parent / raw, cp.parent / raw.name, cp.parent.parent / raw])

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def music_dir() -> Path:
    p = project_root() / "music"
    p.mkdir(parents=True, exist_ok=True)
    return p


def charts_dir() -> Path:
    p = project_root() / "charts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_dir() -> Path:
    p = project_root() / ".rhythmforge_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def records_path() -> Path:
    return project_root() / "records.json"


def settings_path() -> Path:
    return project_root() / "settings.json"


def default_settings() -> dict[str, Any]:
    return {
        "gameplay_keys": {
            "4": ["d", "f", "j", "k"],
            "6": ["s", "d", "f", "j", "k", "l"],
        },
        "special_keys": {"speed": "q", "echo": "w", "normal": "e"},
    }


def load_settings() -> dict[str, Any]:
    base = default_settings()
    path = settings_path()
    if not path.exists():
        return base
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            for lane_count, keys in loaded.get("gameplay_keys", {}).items():
                base["gameplay_keys"][str(lane_count)] = normalize_key_names(list(keys))
            if isinstance(loaded.get("special_keys"), dict):
                base["special_keys"].update({str(k): normalize_key_names([v])[0] for k, v in loaded["special_keys"].items()})
    except Exception:
        return base
    return base


def save_settings(*, gameplay_keys: dict[str, list[str]] | None = None, special_keys: dict[str, str] | None = None) -> dict[str, Any]:
    data = load_settings()
    if gameplay_keys is not None:
        for lane_count, keys in gameplay_keys.items():
            normalized = normalize_key_names(list(keys))
            expected = int(lane_count)
            if len(normalized) != expected:
                raise ValueError(f"{expected}키 설정에는 정확히 {expected}개의 키가 필요합니다.")
            data["gameplay_keys"][str(expected)] = normalized
    if special_keys is not None:
        normalized_special = {str(name): normalize_key_names([key])[0] for name, key in special_keys.items()}
        if len(set(normalized_special.values())) != len(normalized_special):
            raise ValueError("특수키끼리 서로 달라야 합니다.")
        all_game = set()
        for keys in data.get("gameplay_keys", {}).values():
            all_game.update(keys)
        overlap = all_game & set(normalized_special.values())
        if overlap:
            raise ValueError(f"특수키가 플레이 키와 겹칩니다: {', '.join(sorted(overlap))}")
        data["special_keys"] = normalized_special
    settings_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def gameplay_keys_for_lanes(lanes: int) -> list[str]:
    settings = load_settings()
    keys = settings.get("gameplay_keys", {}).get(str(int(lanes)))
    if keys and len(keys) == int(lanes):
        return normalize_key_names(list(keys))
    defaults = default_settings()["gameplay_keys"].get(str(int(lanes)))
    if defaults:
        return list(defaults)
    return [chr(ord("a") + i) for i in range(int(lanes))]


def special_keys_from_settings() -> dict[str, str]:
    settings = load_settings()
    return {str(k): str(v) for k, v in settings.get("special_keys", default_settings()["special_keys"]).items()}


def safe_stem(path: Path) -> str:
    stem = "".join(c if c.isalnum() or c in "._- " else "_" for c in path.stem).strip(" ._")
    return stem or "song"


def unique_path(directory: Path, filename: str) -> Path:
    base = Path(filename).stem
    suffix = Path(filename).suffix
    candidate = directory / filename
    i = 2
    while candidate.exists():
        candidate = directory / f"{base}_{i}{suffix}"
        i += 1
    return candidate


def import_audio(src: str | Path) -> Path:
    src = Path(src).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(src)
    if src.suffix.lower() not in SUPPORTED_AUDIO_EXTS:
        raise ValueError(f"Unsupported audio file: {src.suffix}. Use one of {sorted(SUPPORTED_AUDIO_EXTS)}")
    dst = unique_path(music_dir(), src.name)
    if src.resolve() == dst.resolve():
        return dst
    shutil.copy2(src, dst)
    return dst


@dataclass(frozen=True)
class ChartInfo:
    path: Path
    title: str
    difficulty: str
    note_count: int
    tempo_bpm: float
    duration: float
    audio_path: str
    offset_ms: int
    base_scroll_speed: float
    high_score: int = 0
    best_combo: int = 0

    @property
    def label(self) -> str:
        minutes = int(self.duration // 60)
        seconds = int(self.duration % 60)
        record = f"  BEST {self.high_score} / {self.best_combo}x" if self.high_score or self.best_combo else ""
        return f"{self.title}  [{self.difficulty}]  {self.note_count} notes  {self.tempo_bpm:.1f} BPM  {minutes}:{seconds:02d}{record}"


def chart_record_id(data: dict[str, Any], path: str | Path | None = None) -> str:
    explicit = data.get("chart_id")
    if explicit:
        return str(explicit)
    song_id = str(data.get("song_id", "unknown"))
    difficulty = str(data.get("difficulty", "unknown"))
    note_count = str(data.get("note_count", len(data.get("notes", []))))
    return f"{song_id}:{difficulty}:{note_count}"


def load_records() -> dict[str, Any]:
    path = records_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_records(records: dict[str, Any]) -> None:
    records_path().write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def record_for_chart(chart_data: dict[str, Any]) -> dict[str, Any]:
    return load_records().get(chart_record_id(chart_data), {})


def update_record(chart_path: str | Path, *, score: int, max_combo: int, accuracy: float, hit_count: int, total_notes: int) -> dict[str, Any]:
    chart_path = Path(chart_path)
    data = json.loads(chart_path.read_text(encoding="utf-8"))
    rid = chart_record_id(data, chart_path)
    records = load_records()
    old = records.get(rid, {})
    new = dict(old)
    new["title"] = str(data.get("title", chart_path.stem))
    new["difficulty"] = str(data.get("difficulty", "?"))
    new["high_score"] = max(int(old.get("high_score", 0) or 0), int(score))
    new["best_combo"] = max(int(old.get("best_combo", 0) or 0), int(max_combo))
    new["best_accuracy"] = max(float(old.get("best_accuracy", 0.0) or 0.0), float(accuracy))
    new["last_score"] = int(score)
    new["last_combo"] = int(max_combo)
    new["last_accuracy"] = float(accuracy)
    new["last_hit_count"] = int(hit_count)
    new["last_total_notes"] = int(total_notes)
    new["updated_at"] = datetime.now().isoformat(timespec="seconds")
    records[rid] = new
    save_records(records)
    return new


def read_chart_info(path: str | Path) -> ChartInfo | None:
    path = Path(path)
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        rec = load_records().get(chart_record_id(data, path), {})
        return ChartInfo(
            path=path.resolve(),
            title=str(data.get("title", path.stem)),
            difficulty=str(data.get("difficulty", "?")),
            note_count=int(data.get("note_count", len(data.get("notes", [])))),
            tempo_bpm=float(data.get("tempo_bpm", 0.0) or 0.0),
            duration=float(data.get("duration", 0.0) or 0.0),
            audio_path=str(data.get("audio_path", "")),
            offset_ms=int(float(data.get("offset_ms", 0) or 0)),
            base_scroll_speed=float(data.get("base_scroll_speed", data.get("scroll_speed", 720)) or 720),
            high_score=int(rec.get("high_score", 0) or 0),
            best_combo=int(rec.get("best_combo", 0) or 0),
        )
    except Exception:
        return None


def list_charts() -> list[ChartInfo]:
    infos: list[ChartInfo] = []
    for path in sorted(charts_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        info = read_chart_info(path)
        if info is not None:
            infos.append(info)
    return infos


def normalize_key_names(keys: list[str]) -> list[str]:
    out: list[str] = []
    for k in keys:
        cleaned = str(k).strip().lower()
        if not cleaned:
            continue
        if len(cleaned) == 1 and (cleaned.isalnum() or cleaned in "-=[];\',./"):
            out.append(cleaned)
        elif cleaned in {"space", "tab", "left", "right", "up", "down"}:
            out.append(cleaned)
        else:
            raise ValueError(f"Unsupported key name: {k}")
    if len(out) != len(set(out)):
        raise ValueError("Key bindings must not contain duplicates.")
    return out


def patch_chart_settings(chart_path: str | Path, *, offset_ms: int | None = None, speed_multiplier: float | None = None, keys: list[str] | None = None, special_keys: dict[str, str] | None = None) -> None:
    chart_path = Path(chart_path)
    data = json.loads(chart_path.read_text(encoding="utf-8"))
    if offset_ms is not None:
        data["offset_ms"] = int(offset_ms)
    if keys is not None:
        normalized = normalize_key_names(keys)
        lanes = int(data.get("lanes", len(normalized)) or len(normalized))
        if len(normalized) != lanes:
            raise ValueError(f"This chart has {lanes} lanes, so exactly {lanes} gameplay keys are required.")
        data["keys"] = normalized
    if special_keys is not None:
        normalized_special = {str(name): normalize_key_names([key])[0] for name, key in special_keys.items()}
        all_game_keys = set(data.get("keys", []))
        for key in normalized_special.values():
            if key in all_game_keys:
                raise ValueError("Special-effect keys must not overlap gameplay lane keys.")
        data["special_keys"] = normalized_special
    if speed_multiplier is not None:
        speed_multiplier = max(0.55, min(1.85, float(speed_multiplier)))
        original_base = float(data.get("base_scroll_speed", data.get("scroll_speed", 720)) or 720)
        data["ui_speed_multiplier"] = speed_multiplier
        data["scroll_speed"] = round(original_base * speed_multiplier, 2)
        for note in data.get("notes", []):
            raw = float(note.get("scroll_speed", original_base) or original_base)
            # Avoid compounding when the same chart is patched multiple times.
            raw_original = float(note.get("raw_scroll_speed", raw) or raw)
            note["raw_scroll_speed"] = round(raw_original, 2)
            note["scroll_speed"] = round(raw_original * speed_multiplier, 2)
    chart_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
