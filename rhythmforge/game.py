from __future__ import annotations

import json
import math
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import pygame

from .chart_utils import normalize_chord_visuals
from .config import DEFAULT_SPECIAL_KEYS, JUDGEMENTS, MISS_MS
from .effects import EffectFiles, prepare_effect_files
from .library import control_keys_from_settings, gameplay_keys_for_lanes, record_for_chart, resolve_portable_path, special_keys_from_settings, update_record


NOTE_COLORS = {
    "normal": ((98, 185, 255), (229, 246, 255)),
    "bright": ((255, 214, 116), (255, 246, 205)),
    "accent": ((202, 143, 255), (246, 232, 255)),
    "highlight": ((255, 116, 151), (255, 228, 236)),
}
# Key beams/press feedback intentionally use neutral blue-white colors instead of
# note colors.  This keeps stacked notes readable when hit effects overlap them.
LANE_FLASH_COLORS = {
    "normal": (42, 54, 82),
    "bright": (46, 61, 88),
    "accent": (50, 57, 92),
    "highlight": (56, 61, 92),
}
SPECIAL_LABELS = {
    "normal": "NORMAL",
    "speed": "RUSH x1.15",
    "echo": "ECHO",
}


@dataclass
class GridMarker:
    time: float
    scroll_speed: float
    strong: bool = False


@dataclass
class RuntimeNote:
    time: float
    lane: int
    scroll_speed: float
    color: str = "normal"
    source: str = "unknown"
    grid_locked: bool = True
    raw_time: float | None = None
    note_type: str = "tap"
    end_time: float | None = None
    required_hits: int = 0
    remaining_hits: int = 0
    active: bool = False
    hold_judgement: str | None = None
    hit: bool = False
    missed: bool = False
    judgement: str | None = None


@dataclass
class HitBurst:
    lane: int
    created_at: float
    color: str
    judgement: str


class RhythmGame:
    def __init__(self, chart_path: str | Path):
        self.chart_path = Path(chart_path).expanduser().resolve()
        self.chart = json.loads(self.chart_path.read_text(encoding="utf-8"))
        self.audio_path = resolve_portable_path(self.chart["audio_path"], chart_path=self.chart_path)
        if not self.audio_path.exists():
            raise FileNotFoundError(
                "Audio file not found. Expected a portable path like "
                f"music/<song file>, but got: {self.chart['audio_path']}"
            )

        self.lanes = int(self.chart["lanes"])
        # Key bindings are global app settings now, not chart-local settings.
        # This fixes the previous behavior where changing keys in the launcher
        # appeared to be ignored depending on which chart JSON was loaded.
        self.keys = gameplay_keys_for_lanes(self.lanes)
        self.key_to_lane = self._build_key_map(self.keys)
        self.scancode_to_lane = self._build_key_scancode_map(self.keys)
        special = dict(DEFAULT_SPECIAL_KEYS)
        special.update(special_keys_from_settings())
        self.special_keys = special
        self.special_key_to_mode = self._build_special_key_map(special)
        self.special_scancode_to_mode = self._build_special_scancode_map(special)
        self.control_keys = control_keys_from_settings()
        self.control_key_to_action = self._build_control_key_map(self.control_keys)
        self.control_scancode_to_action = self._build_control_scancode_map(self.control_keys)

        # v10 input robustness: on some Windows/Korean/Japanese IME setups,
        # pygame KEYDOWN.key may not be enough for letter keys even though
        # Escape still works.  Keep both key-code and normalized-name maps,
        # and resolve input from event.key, pygame.key.name(event.key), and
        # event.unicode.
        self.key_name_to_lane = {str(name).strip().lower(): lane for lane, name in enumerate(self.keys)}
        self.special_name_to_mode = {str(key).strip().lower(): mode for mode, key in self.special_keys.items()}
        self.control_name_to_action = {str(key).strip().lower(): action for action, key in self.control_keys.items()}

        base_speed = float(self.chart.get("base_scroll_speed", self.chart.get("scroll_speed", 720)))
        # Runtime compatibility pass: old v7 charts may already contain exact
        # chord timestamps, but their visual speed/color/raw_time can still differ
        # per lane.  Normalize before RuntimeNote creation so existing charts do
        # not need to be regenerated.
        chart_notes = normalize_chord_visuals(
            [dict(n) for n in self.chart.get("notes", [])],
            chord_window=0.034 if self.chart.get("difficulty") in {"normal", "hard"} else 0.042,
        )
        self.notes = [
            RuntimeNote(
                time=float(n.get("render_time", n.get("time", 0.0))),
                raw_time=float(n.get("raw_time", n.get("time", 0.0))),
                grid_locked=bool(n.get("grid_locked", True)),
                lane=int(n.get("lane", -1)),
                scroll_speed=float(n.get("visual_scroll_speed", n.get("scroll_speed", base_speed))),
                color=str(n.get("color", "normal")),
                source=str(n.get("source", "unknown")),
                note_type=str(n.get("type", "tap")),
                end_time=float(n.get("end_time", n.get("time", 0.0))) if n.get("end_time") is not None else None,
                required_hits=int(n.get("required_hits", 0) or 0),
                remaining_hits=int(n.get("required_hits", 0) or 0),
            )
            for n in chart_notes
        ]
        self.notes.sort(key=lambda n: (n.time, n.lane))
        base_grid_speed = float(self.chart.get("scroll_speed", self.chart.get("base_scroll_speed", 720)))
        self.grid_markers: list[GridMarker] = []
        for i, g in enumerate(self.chart.get("grid_markers", [])):
            try:
                self.grid_markers.append(GridMarker(
                    time=float(g.get("time", 0.0)),
                    scroll_speed=float(g.get("scroll_speed", base_grid_speed)),
                    strong=bool(g.get("strong", False)),
                ))
            except Exception:
                continue
        if not self.grid_markers:
            beat_interval = max(float(self.chart.get("beat_interval", 0.5) or 0.5), 0.001)
            for x in self.chart.get("grid_times", []):
                t = float(x)
                strong = abs((t / beat_interval) - round(t / beat_interval)) < 0.02
                nearby = [n.scroll_speed for n in self.notes if abs(n.time - t) <= max(0.08, beat_interval * 0.20)]
                speed = float(sum(nearby) / len(nearby)) if nearby else base_grid_speed
                self.grid_markers.append(GridMarker(time=t, scroll_speed=speed, strong=strong))
        self.grid_markers.sort(key=lambda g: g.time)
        self.offset = float(self.chart.get("offset_ms", 0)) / 1000.0
        self.record = record_for_chart(self.chart)

        self.width = max(860, 140 * self.lanes)
        self.height = 880
        self.hit_y = self.height - 155
        self.lane_w = 96
        self.lane_gap = 10
        total_w = self.lanes * self.lane_w + (self.lanes - 1) * self.lane_gap
        self.board_x = (self.width - total_w) // 2

        self.score = 0
        self.combo = 0
        self.max_combo = 0
        self.hit_count = 0
        self.judge_counts = {j.name: 0 for j in JUDGEMENTS}
        self.judge_counts["MISS"] = 0
        self.judgement_text = ""
        self.judgement_until = 0.0
        self.running = True
        self.started_at = 0.0
        self.music_started = False
        self.start_delay = 1.2
        self.finished = False
        self.record_saved = False
        self.lane_flash_until = [0.0 for _ in range(self.lanes)]
        self.lane_flash_kind = ["normal" for _ in range(self.lanes)]
        self.hit_bursts: list[HitBurst] = []
        self.last_hit_at = 0.0
        self.lane_down = [False for _ in range(self.lanes)]
        self.lane_empty_cooldown_until = [0.0 for _ in range(self.lanes)]
        self.early_mash_window = 0.26

        self.audio_enabled = False
        self.effects: EffectFiles | None = None
        self.effects_ready = False
        self.effect_mode = "normal"
        self.effect_rate = 1.0
        self.effect_song_anchor = 0.0
        self.effect_started_at = 0.0
        self.effect_message_until = 0.0
        self.effect_message = ""
        self.paused = False
        self.paused_at = 0.0
        self.paused_song_time = 0.0

    def _key_code(self, name: str) -> int:
        name = str(name).strip().lower()
        aliases = {
            "space": pygame.K_SPACE,
            "tab": pygame.K_TAB,
            "left": pygame.K_LEFT,
            "right": pygame.K_RIGHT,
            "up": pygame.K_UP,
            "down": pygame.K_DOWN,
            "escape": pygame.K_ESCAPE,
            "esc": pygame.K_ESCAPE,
            "backspace": pygame.K_BACKSPACE,
            "delete": pygame.K_DELETE,
            "enter": pygame.K_RETURN,
            "return": pygame.K_RETURN,
            "pause": pygame.K_PAUSE,
        }
        if name in aliases:
            return aliases[name]
        return pygame.key.key_code(name)

    def _key_scancode(self, name: str) -> int | None:
        """Return a physical-key scancode when pygame can provide one.

        Scancodes are layout/IME independent in SDL.  This makes D/F/J/K-style
        rhythm inputs keep working even while Korean/Japanese IME is active.
        """
        try:
            code = self._key_code(name)
            getter = getattr(pygame.key, "get_scancode_from_key", None)
            if getter is None:
                return None
            sc = int(getter(code))
            return sc if sc >= 0 else None
        except Exception:
            return None

    def _build_key_scancode_map(self, keys: list[str]) -> dict[int, int]:
        out: dict[int, int] = {}
        for lane, name in enumerate(keys):
            sc = self._key_scancode(name)
            if sc is not None:
                out[sc] = lane
        return out

    def _build_key_map(self, keys: list[str]) -> dict[int, int]:
        out: dict[int, int] = {}
        for lane, name in enumerate(keys):
            try:
                out[self._key_code(name)] = lane
            except Exception as exc:
                raise ValueError(f"Unsupported gameplay key: {name}") from exc
        return out

    def _build_special_key_map(self, special: dict[str, str]) -> dict[int, str]:
        out: dict[int, str] = {}
        for mode, key in special.items():
            if mode not in {"normal", "speed", "echo"}:
                continue
            try:
                code = self._key_code(key)
            except Exception:
                continue
            if code not in self.key_to_lane:
                out[code] = mode
        return out

    def _build_special_scancode_map(self, special: dict[str, str]) -> dict[int, str]:
        out: dict[int, str] = {}
        for mode, key in special.items():
            if mode not in {"normal", "speed", "echo"}:
                continue
            sc = self._key_scancode(key)
            if sc is not None and sc not in self.scancode_to_lane:
                out[sc] = mode
        return out

    def _build_control_key_map(self, control: dict[str, str]) -> dict[int, str]:
        out: dict[int, str] = {}
        for action in ("pause", "retry", "back"):
            key = control.get(action)
            if not key:
                continue
            try:
                code = self._key_code(key)
            except Exception:
                continue
            if code not in self.key_to_lane and code not in self.special_key_to_mode:
                out[code] = action
        return out

    def _build_control_scancode_map(self, control: dict[str, str]) -> dict[int, str]:
        out: dict[int, str] = {}
        blocked = set(self.scancode_to_lane) | set(self.special_scancode_to_mode)
        for action in ("pause", "retry", "back"):
            key = control.get(action)
            if not key:
                continue
            sc = self._key_scancode(key)
            if sc is not None and sc not in blocked:
                out[sc] = action
        return out

    def _event_key_names(self, event: pygame.event.Event) -> set[str]:
        names: set[str] = set()
        try:
            key_name = pygame.key.name(event.key).strip().lower()
            if key_name:
                names.add("escape" if key_name == "esc" else key_name)
        except Exception:
            pass
        try:
            text = str(getattr(event, "unicode", "")).strip().lower()
            if text:
                names.add(text)
        except Exception:
            pass
        return names

    def _resolve_key_action(self, event: pygame.event.Event) -> tuple[str, str | int] | None:
        """Resolve a KEYDOWN into control/special/lane.

        Resolution order intentionally uses scancode first, then key-code/name.
        SDL scancodes represent the physical key, so gameplay keys work even
        when the OS input method is Korean/Japanese/Chinese instead of English.
        """
        scancode = int(getattr(event, "scancode", -1) or -1)
        if scancode >= 0:
            action = self.control_scancode_to_action.get(scancode)
            if action:
                return ("control", action)
            mode = self.special_scancode_to_mode.get(scancode)
            if mode:
                return ("special", mode)
            lane = self.scancode_to_lane.get(scancode)
            if lane is not None:
                return ("lane", lane)

        action = self.control_key_to_action.get(event.key)
        if action:
            return ("control", action)

        mode = self.special_key_to_mode.get(event.key)
        if mode:
            return ("special", mode)

        lane = self.key_to_lane.get(event.key)
        if lane is not None:
            return ("lane", lane)

        for name in self._event_key_names(event):
            action = self.control_name_to_action.get(name)
            if action:
                return ("control", action)
            mode = self.special_name_to_mode.get(name)
            if mode:
                return ("special", mode)
            lane = self.key_name_to_lane.get(name)
            if lane is not None:
                return ("lane", lane)

        return None

    def song_time(self) -> float:
        if self.paused:
            return self.paused_song_time
        if self.music_started and pygame.mixer.get_init() is not None:
            pos_ms = pygame.mixer.music.get_pos()
            if pos_ms >= 0:
                return self.effect_song_anchor + (pos_ms / 1000.0) * self.effect_rate - self.offset
            return perf_counter() - self.effect_started_at + self.effect_song_anchor - self.offset
        return perf_counter() - self.started_at - self.start_delay - self.offset

    def init_pygame_audio(self) -> bool:
        pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=128)
        pygame.init()
        if pygame.mixer.get_init() is None:
            try:
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=128)
            except pygame.error as exc:
                print(f"[WARN] pygame mixer could not be initialized: {exc}")
                print("[WARN] Gameplay will run without audio. Check your Windows sound output device.")
                return False
        return True

    def _make_font(self, size: int, *, bold: bool = False) -> pygame.font.Font:
        """Create a CJK-capable font for Korean/Japanese/Chinese titles."""
        # pygame.font.match_font is inconsistent for TTC CJK fonts on Windows,
        # so try common font files first, then system font names.
        candidates = [
            r"C:\Windows\Fonts\msyh.ttc",        # Microsoft YaHei, zh + kana
            r"C:\Windows\Fonts\msyhbd.ttc",
            r"C:\Windows\Fonts\meiryo.ttc",      # Japanese
            r"C:\Windows\Fonts\meiryob.ttc",
            r"C:\Windows\Fonts\YuGothR.ttc",
            r"C:\Windows\Fonts\YuGothB.ttc",
            r"C:\Windows\Fonts\msgothic.ttc",
            r"C:\Windows\Fonts\malgun.ttf",      # Korean
            r"C:\Windows\Fonts\malgunbd.ttf",
            r"C:\Windows\Fonts\simsun.ttc",      # Simplified Chinese
            r"C:\Windows\Fonts\mingliu.ttc",     # Traditional Chinese
        ]
        if bold:
            candidates = [p for p in candidates if "bd" in p.lower() or "bold" in p.lower()] + candidates
        for candidate in candidates:
            path = Path(candidate)
            if path.exists():
                try:
                    return pygame.font.Font(str(path), size)
                except Exception:
                    pass

        preferred = [
            "microsoftyahei", "microsoft yahei", "microsoftjhenghei", "microsoft jhenghei",
            "meiryo", "yugothic", "yu gothic", "msgothic", "ms gothic",
            "malgungothic", "malgun gothic", "notosanscjk", "noto sans cjk",
            "notosanscjkkr", "notosanscjkJP", "notosanscjkSC", "notosanscjkTC",
            "notosanskr", "nanumgothic", "applegothic", "arialunicode", "segoeui", "consolas",
        ]
        for name in preferred:
            path = pygame.font.match_font(name, bold=bold)
            if path:
                try:
                    return pygame.font.Font(path, size)
                except Exception:
                    pass
        return pygame.font.SysFont(None, size, bold=bold)

    def _prepare_effects_async(self) -> None:
        def worker() -> None:
            try:
                self.effects = prepare_effect_files(self.audio_path, speed_rate=1.15)
                self.effects_ready = True
            except Exception as exc:
                print(f"[WARN] Special audio effect preprocessing failed: {exc}")
                self.effects = EffectFiles(normal=self.audio_path, speed=None, echo=None)
                self.effects_ready = False

        threading.Thread(target=worker, daemon=True).start()

    def _start_music(self, mode: str = "normal", song_position: float = 0.0) -> None:
        if not self.audio_enabled:
            return
        path = self.audio_path
        rate = 1.0
        start = max(0.0, song_position)
        if mode == "speed" and self.effects and self.effects.speed:
            path = self.effects.speed
            rate = 1.15
            start = max(0.0, song_position / rate)
        elif mode == "echo" and self.effects and self.effects.echo:
            path = self.effects.echo
        else:
            mode = "normal"

        try:
            pygame.mixer.music.stop()
            pygame.mixer.music.load(str(path))
            try:
                pygame.mixer.music.play(start=start)
            except TypeError:
                pygame.mixer.music.play()
            self.effect_mode = mode
            self.effect_rate = rate
            self.effect_song_anchor = max(0.0, song_position)
            self.effect_started_at = perf_counter()
            self.music_started = True
        except pygame.error as exc:
            print(f"[WARN] Could not switch music effect: {exc}")

    def toggle_pause(self, st: float) -> None:
        now = perf_counter()
        if not self.paused:
            self.paused = True
            self.paused_at = now
            self.paused_song_time = st
            if self.audio_enabled and self.music_started:
                pygame.mixer.music.pause()
            self.effect_message = "PAUSED"
            self.effect_message_until = now + 999999.0
            return

        paused_duration = now - self.paused_at
        self.paused = False
        self.started_at += paused_duration
        self.effect_started_at += paused_duration
        if self.audio_enabled and self.music_started:
            pygame.mixer.music.unpause()
        self.effect_message = "RESUME"
        self.effect_message_until = now + 0.6

    def restart(self) -> None:
        if self.audio_enabled:
            pygame.mixer.music.stop()
        self.__init__(self.chart_path)
        self.run()

    def switch_effect(self, mode: str, st: float) -> None:
        if not self.audio_enabled:
            return
        if mode != "normal" and not self.effects_ready:
            self.effect_message = "FX preparing..."
            self.effect_message_until = perf_counter() + 0.8
            return
        self._start_music(mode, max(0.0, st + self.offset))
        self.effect_message = f"FX: {SPECIAL_LABELS.get(mode, mode.upper())}"
        self.effect_message_until = perf_counter() + 0.9

    def run(self) -> None:
        self.audio_enabled = self.init_pygame_audio()
        pygame.key.set_repeat(0)
        pygame.display.set_caption("Rhythm4G")
        screen = pygame.display.set_mode((self.width, self.height))
        clock = pygame.time.Clock()
        font_big = self._make_font(42, bold=True)
        font_mid = self._make_font(26, bold=True)
        font_small = self._make_font(18)

        if self.audio_enabled:
            self._prepare_effects_async()
        self.started_at = perf_counter()
        self.music_started = False

        while self.running:
            now = perf_counter()
            if self.audio_enabled and not self.music_started and now - self.started_at >= self.start_delay:
                self._start_music("normal", 0.0)

            st = self.song_time()

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                elif event.type == pygame.KEYDOWN:
                    resolved = self._resolve_key_action(event)
                    if resolved is None:
                        continue
                    kind, value = resolved
                    if kind == "control":
                        if value == "back":
                            self.running = False
                        elif value == "retry":
                            return self.restart()
                        elif value == "pause":
                            self.toggle_pause(st)
                    elif not self.paused and kind == "special":
                        self.switch_effect(str(value), st)
                    elif not self.paused and kind == "lane":
                        self.handle_hit(int(value), st)
                elif event.type == pygame.KEYUP:
                    resolved = self._resolve_key_action(event)
                    if resolved and resolved[0] == "lane":
                        self.handle_key_up(int(resolved[1]), st)

            if not self.paused:
                self.update_misses(st)
                self.maybe_save_record(st)
            self.draw(screen, font_big, font_mid, font_small, st)
            pygame.display.flip()
            clock.tick(240)

        if self.audio_enabled:
            pygame.mixer.music.stop()
        pygame.quit()

    def find_candidate(self, lane: int, st: float) -> RuntimeNote | None:
        max_window = max(j.ms for j in JUDGEMENTS) / 1000.0
        candidates = [
            n for n in self.notes
            if n.note_type in {"tap", "hold"}
            and n.lane == lane and not n.hit and not n.missed and not n.active
            and abs(n.time - st) <= max_window
        ]
        if not candidates:
            return None
        priority = {"highlight": 3, "accent": 2, "bright": 1, "normal": 0}
        return min(candidates, key=lambda n: (abs(n.time - st), -int(n.grid_locked), -priority.get(n.color, 0)))

    def find_early_mash_target(self, lane: int, st: float) -> RuntimeNote | None:
        max_window = max(j.ms for j in JUDGEMENTS) / 1000.0
        future = [
            n for n in self.notes
            if n.note_type == "tap"
            and n.lane == lane and not n.hit and not n.missed and not n.active
            and max_window < (n.time - st) <= self.early_mash_window
        ]
        if not future:
            return None
        return min(future, key=lambda n: n.time)

    def active_roll_candidate(self, st: float) -> RuntimeNote | None:
        candidates = [
            n for n in self.notes
            if n.note_type == "roll" and not n.hit and not n.missed
            and n.time - 0.04 <= st <= (n.end_time or n.time) + 0.10
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda n: abs(max(n.time, min(st, n.end_time or n.time)) - st))

    def _apply_judgement(self, note: RuntimeNote, judge_name: str, *, combo: bool = True) -> None:
        note.hit = True
        note.active = False
        note.judgement = judge_name
        self.judge_counts[judge_name] += 1
        self.hit_count += 1
        if combo and judge_name != "BAD":
            self.combo += 1
            self.max_combo = max(self.max_combo, self.combo)
        else:
            self.combo = 0
        self.recalculate_score()
        self.show_judgement(judge_name)
        if 0 <= note.lane < self.lanes:
            self.flash_lane(note.lane, note.color)
            self.hit_bursts.append(HitBurst(lane=note.lane, created_at=perf_counter(), color=note.color, judgement=judge_name))
        self.last_hit_at = perf_counter()

    def _judgement_for_delta(self, delta_ms: float) -> str | None:
        for judge in JUDGEMENTS:
            if delta_ms <= judge.ms:
                return judge.name
        return None

    def start_hold(self, note: RuntimeNote, lane: int, st: float, judge_name: str | None = None) -> None:
        """Start a hold note without consuming it as a tap.

        A hold scores only when it survives until end_time.  This method exists
        separately from _apply_judgement so holding a key is represented as a
        sustained active state, not as one instant key press.
        """
        if note.hit or note.missed or note.active:
            return
        note.active = True
        note.hold_judgement = judge_name or self._judgement_for_delta(abs(note.time - st) * 1000.0) or "GOOD"
        self.show_judgement("HOLD")
        self.flash_lane(lane, note.color)
        self.hit_bursts.append(HitBurst(lane=lane, created_at=perf_counter(), color=note.color, judgement=note.hold_judgement))
        self.last_hit_at = perf_counter()

    def handle_hit(self, lane: int, st: float) -> None:
        now = perf_counter()
        if 0 <= lane < len(self.lane_down):
            self.lane_down[lane] = True

        note = self.find_candidate(lane, st)
        if note is not None:
            delta_ms = abs(note.time - st) * 1000.0
            judge_name = self._judgement_for_delta(delta_ms)
            if judge_name is None:
                self.mark_miss(note)
                self.flash_lane(lane, "normal")
                return
            if note.note_type == "hold":
                self.start_hold(note, lane, st, judge_name)
                return
            self._apply_judgement(note, judge_name, combo=True)
            return

        # Roll notes intentionally accept any gameplay key, but only during their
        # visible active span.  They are generated in sections without other
        # lane notes, so normal taps/holds always have priority above this.
        roll = self.active_roll_candidate(st)
        if roll is not None:
            roll.active = True
            roll.remaining_hits = max(0, int(roll.remaining_hits) - 1)
            self.show_judgement(f"ROLL {roll.remaining_hits}")
            self.flash_lane(lane, "bright")
            self.hit_bursts.append(HitBurst(lane=lane, created_at=now, color="bright", judgement="ROLL"))
            self.last_hit_at = now
            if roll.remaining_hits <= 0:
                self._apply_judgement(roll, "PERFECT", combo=True)
            return

        early = self.find_early_mash_target(lane, st)
        if early is not None:
            # Anti-mash behavior: if the player is repeatedly pressing while a
            # note is approaching but not yet judgeable, consume that note as BAD
            # instead of allowing spam to eventually auto-hit it.
            early.hit = True
            early.judgement = "BAD"
            self.judge_counts["BAD"] += 1
            self.hit_count += 1
            self.combo = 0
            self.recalculate_score()
            self.show_judgement("EARLY")
            self.flash_lane(lane, "normal")
            self.lane_empty_cooldown_until[lane] = now + 0.10
            return

        if now >= self.lane_empty_cooldown_until[lane]:
            self.show_judgement("EMPTY")
            self.flash_lane(lane, "normal")
            self.lane_empty_cooldown_until[lane] = now + 0.06

    def finish_hold(self, note: RuntimeNote, judgement: str | None = None) -> None:
        if note.hit or note.missed:
            return
        self._apply_judgement(note, judgement or note.hold_judgement or "GOOD", combo=True)

    def handle_key_up(self, lane: int, st: float) -> None:
        if 0 <= lane < len(self.lane_down):
            self.lane_down[lane] = False
        for note in self.notes:
            if note.note_type != "hold" or note.lane != lane or not note.active or note.hit or note.missed:
                continue
            end = note.end_time or note.time
            if st >= end - 0.08:
                self.finish_hold(note, note.hold_judgement or "GOOD")
            else:
                self.mark_miss(note)
            return

    def mark_miss(self, note: RuntimeNote) -> None:
        if note.hit or note.missed:
            return
        note.missed = True
        note.active = False
        note.judgement = "MISS"
        self.judge_counts["MISS"] += 1
        self.recalculate_score()
        self.break_combo("MISS")

    def update_misses(self, st: float) -> None:
        """Advance miss logic only. Rendering belongs in draw()."""
        miss_window = MISS_MS / 1000.0
        for note in self.notes:
            if note.hit or note.missed:
                continue

            if note.note_type == "hold":
                end = note.end_time or note.time
                if note.active:
                    # Long key press support: as long as the physical lane key is
                    # still down, automatically finish at the tail.
                    if 0 <= note.lane < len(self.lane_down) and self.lane_down[note.lane] and st >= end - 0.01:
                        self.finish_hold(note, note.hold_judgement or "GOOD")
                    elif st > end + miss_window:
                        self.mark_miss(note)
                else:
                    # If the key was already held as the head enters the judge
                    # window, start the hold. This makes sustained KEYDOWN state
                    # matter, rather than requiring repeated keydown events.
                    if 0 <= note.lane < len(self.lane_down) and self.lane_down[note.lane]:
                        delta_ms = abs(note.time - st) * 1000.0
                        judge_name = self._judgement_for_delta(delta_ms)
                        if judge_name is not None:
                            self.start_hold(note, note.lane, st, judge_name)
                            continue
                    if st - note.time > miss_window:
                        self.mark_miss(note)
                continue

            if note.note_type == "roll":
                end = note.end_time or note.time
                if st > end + miss_window and note.remaining_hits > 0:
                    self.mark_miss(note)
                continue

            if st - note.time > miss_window:
                self.mark_miss(note)

    def recalculate_score(self) -> None:
        total = len(self.notes)
        if total <= 0:
            self.score = 0
            return
        perfect_value = float(JUDGEMENTS[0].score)
        judgement_score = sum(self.judge_counts[j.name] * j.score for j in JUDGEMENTS)
        self.score = int(round(1_000_000 * judgement_score / (total * perfect_value)))
        self.score = max(0, min(1_000_000, self.score))

    def accuracy(self) -> float:
        total = len(self.notes)
        if total <= 0:
            return 0.0
        max_score = total * JUDGEMENTS[0].score
        judgement_score = sum(self.judge_counts[j.name] * j.score for j in JUDGEMENTS)
        return max(0.0, min(100.0, judgement_score / max_score * 100.0))

    def maybe_save_record(self, st: float) -> None:
        duration = float(self.chart.get("duration", 0.0) or 0.0)
        all_done = all(n.hit or n.missed for n in self.notes)
        time_done = duration > 0 and st >= duration + 1.2
        if not self.record_saved and (all_done or time_done):
            self.record = update_record(
                self.chart_path,
                score=self.score,
                max_combo=self.max_combo,
                accuracy=self.accuracy(),
                hit_count=self.hit_count,
                total_notes=len(self.notes),
            )
            self.record_saved = True
            self.finished = True
            self.show_judgement("FINISH")

    def flash_lane(self, lane: int, kind: str) -> None:
        if 0 <= lane < self.lanes:
            self.lane_flash_until[lane] = perf_counter() + 0.13
            self.lane_flash_kind[lane] = kind if kind in LANE_FLASH_COLORS else "normal"

    def show_judgement(self, text: str) -> None:
        self.judgement_text = text
        self.judgement_until = perf_counter() + 0.32

    def break_combo(self, text: str) -> None:
        self.combo = 0
        self.show_judgement(text)

    def draw_grid(self, screen: pygame.Surface, st: float) -> None:
        if not self.grid_markers:
            return
        left = self.board_x - 18
        right = self.width - self.board_x + 18
        field_top = 214
        field_bottom = self.height - 104
        min_t = st - 0.25
        max_t = st + 3.2
        for marker in self.grid_markers:
            gt = marker.time
            if gt < min_t:
                continue
            if gt > max_t:
                break
            y = self.hit_y - (gt - st) * marker.scroll_speed
            if field_top <= y <= field_bottom:
                color = (84, 88, 104) if marker.strong else (56, 60, 74)
                width = 2 if marker.strong else 1
                pygame.draw.line(screen, color, (left, int(y)), (right, int(y)), width)

    def draw_rounded_panel(self, screen: pygame.Surface, rect: pygame.Rect, fill: tuple[int, int, int], outline: tuple[int, int, int] | None = None) -> None:
        pygame.draw.rect(screen, fill, rect, border_radius=18)
        if outline:
            pygame.draw.rect(screen, outline, rect, width=2, border_radius=18)

    def draw_hit_bursts(self, screen: pygame.Surface, font_small, now: float) -> None:
        alive: list[HitBurst] = []
        for burst in self.hit_bursts:
            age = now - burst.created_at
            if age > 0.34:
                continue
            alive.append(burst)
            if not (0 <= burst.lane < self.lanes):
                continue
            x = self.board_x + burst.lane * (self.lane_w + self.lane_gap) + self.lane_w // 2
            t = age / 0.34
            radius = int(18 + 38 * t)
            alpha_like = max(0, 1.0 - t)
            ring = (226, 238, 255)
            core = (145, 190, 255)
            pygame.draw.circle(screen, ring, (x, self.hit_y), radius, max(1, int(3 * alpha_like)))
            pygame.draw.circle(screen, core, (x, self.hit_y), max(3, int(7 * (1 - t))), 0)
        self.hit_bursts = alive

    def draw_result_overlay(self, screen: pygame.Surface, font_big, font_mid, font_small) -> None:
        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 138))
        screen.blit(overlay, (0, 0))
        panel = pygame.Rect(0, 0, min(680, self.width - 80), 430)
        panel.center = (self.width // 2, self.height // 2)
        self.draw_rounded_panel(screen, panel, (24, 29, 48), (88, 105, 150))

        title = font_big.render("RESULT", True, (255, 255, 255))
        screen.blit(title, title.get_rect(center=(panel.centerx, panel.top + 52)))
        score_text = font_big.render(f"{self.score:,}", True, (255, 231, 170))
        screen.blit(score_text, score_text.get_rect(center=(panel.centerx, panel.top + 118)))

        acc = self.accuracy()
        stats = [
            ("MAX COMBO", f"{self.max_combo}x"),
            ("ACCURACY", f"{acc:.2f}%"),
            ("BEST SCORE", f"{int(self.record.get('high_score', 0) or 0):,}"),
            ("BEST COMBO", f"{int(self.record.get('best_combo', 0) or 0)}x"),
        ]
        for i, (label, value) in enumerate(stats):
            col = i % 2
            row = i // 2
            x = panel.left + 58 + col * (panel.width // 2)
            y = panel.top + 172 + row * 70
            screen.blit(font_small.render(label, True, (146, 158, 190)), (x, y))
            screen.blit(font_mid.render(value, True, (235, 241, 255)), (x, y + 22))

        judge_line = "   ".join(f"{k} {v}" for k, v in self.judge_counts.items())
        jt = font_small.render(judge_line, True, (190, 199, 222))
        screen.blit(jt, jt.get_rect(center=(panel.centerx, panel.bottom - 80)))
        hint = font_mid.render(f"{self.control_keys.get('retry','backspace').upper()} Retry    {self.control_keys.get('back','escape').upper()} Back", True, (255, 255, 255))
        screen.blit(hint, hint.get_rect(center=(panel.centerx, panel.bottom - 38)))

    def draw_roll_note(self, screen: pygame.Surface, font_mid, note: RuntimeNote, st: float) -> None:
        field_top = 214
        end = note.end_time or note.time
        y1 = self.hit_y - (note.time - st) * note.scroll_speed
        y2 = self.hit_y - (end - st) * note.scroll_speed
        top = min(y1, y2)
        bottom = max(y1, y2)
        if bottom < field_top or top > self.height + 60:
            return
        left = self.board_x - 18
        width = self.lanes * self.lane_w + (self.lanes - 1) * self.lane_gap + 36
        visible_top = int(max(field_top, top))
        visible_bottom = int(min(self.height + 60, bottom))
        rect = pygame.Rect(left, visible_top, width, max(34, visible_bottom - visible_top))
        pygame.draw.rect(screen, (58, 76, 116), rect, border_radius=16)
        pygame.draw.rect(screen, (180, 214, 255), rect, width=3, border_radius=16)
        remaining = max(0, int(note.remaining_hits))
        label = font_mid.render(f"ROLL  {remaining}", True, (245, 250, 255))
        screen.blit(label, label.get_rect(center=rect.center))

    def draw_lane_note(self, screen: pygame.Surface, font_small, note: RuntimeNote, st: float) -> None:
        if not (0 <= note.lane < self.lanes):
            return
        field_top = 214
        field_bottom = self.height - 104
        y_head = self.hit_y - (note.time - st) * note.scroll_speed
        end = note.end_time if note.note_type == "hold" and note.end_time is not None else None

        x = self.board_x + note.lane * (self.lane_w + self.lane_gap)
        body, outline = NOTE_COLORS.get(note.color, NOTE_COLORS["normal"])
        w = int(self.lane_w * 0.84)
        h = 26
        center_x = x + self.lane_w // 2

        if end is not None:
            y_tail = self.hit_y - (end - st) * note.scroll_speed
            top = min(y_head, y_tail)
            bottom = max(y_head, y_tail)
            if bottom < field_top or top > self.height + 56:
                return

            # Draw the sustain body clipped into the playfield.  The bar falls in
            # from the top naturally instead of popping in as a short rectangle.
            visible_top = int(max(field_top, top))
            visible_bottom = int(min(field_bottom + 40, bottom))
            if visible_bottom > visible_top:
                hold_w = int(self.lane_w * 0.44)
                hold_rect = pygame.Rect(0, visible_top, hold_w, max(8, visible_bottom - visible_top))
                hold_rect.centerx = center_x
                fill = (54, 72, 112) if not note.active else (72, 96, 148)
                pygame.draw.rect(screen, fill, hold_rect, border_radius=12)
                pygame.draw.rect(screen, outline, hold_rect, width=2, border_radius=12)

            # Head and tail caps.  The head is the press point, the tail is the
            # release point.  Both are clipped so they appear only in the lane.
            for yy, label_text, is_tail in ((y_head, "HOLD", False), (y_tail, "END", True)):
                cap = pygame.Rect(0, 0, w if not is_tail else int(w * 0.78), h if not is_tail else 20)
                cap.center = (center_x, int(round(yy)))
                if cap.bottom < field_top or cap.top > field_bottom + 40:
                    continue
                if note.color in {"accent", "highlight"} or note.active:
                    glow = cap.inflate(16, 12)
                    pygame.draw.rect(screen, body, glow, width=2, border_radius=14)
                pygame.draw.rect(screen, body, cap, border_radius=9)
                pygame.draw.rect(screen, outline, cap, width=2, border_radius=9)
                txt = font_small.render(label_text, True, (255, 255, 255))
                screen.blit(txt, txt.get_rect(center=cap.center))
            return

        # Tap note.
        if st < -0.05 or y_head < field_top or y_head > self.height + 50:
            return
        note_rect = pygame.Rect(0, 0, w, h)
        note_rect.center = (center_x, int(round(y_head)))
        if note_rect.bottom < field_top:
            return
        if note.color in {"accent", "highlight"}:
            glow = note_rect.inflate(16, 12)
            pygame.draw.rect(screen, body, glow, width=2, border_radius=14)
        pygame.draw.rect(screen, body, note_rect, border_radius=9)
        pygame.draw.rect(screen, outline, note_rect, width=2, border_radius=9)
        if not note.grid_locked:
            pygame.draw.circle(screen, (245, 245, 245), (note_rect.right - 9, note_rect.top + 7), 3)

    def draw(self, screen, font_big, font_mid, font_small, st: float) -> None:
        screen.fill((8, 10, 18))
        now = perf_counter()

        for i in range(0, self.height, 28):
            shade = 12 + int(8 * (i / self.height))
            pygame.draw.rect(screen, (shade, shade + 2, shade + 11), (0, i, self.width, 28))

        title = f"Rhythm4G  |  {self.chart.get('title', 'Untitled')} [{self.chart.get('difficulty', '-').upper()}]"
        bpm = self.chart.get("tempo_bpm", "?")
        count = self.chart.get("note_count", len(self.notes))
        best = f"BEST {int(self.record.get('high_score', 0) or 0):,} / {int(self.record.get('best_combo', 0) or 0)}x"
        header = pygame.Rect(18, 14, self.width - 36, 88)
        self.draw_rounded_panel(screen, header, (18, 22, 36), (40, 48, 76))
        screen.blit(font_small.render(title, True, (235, 239, 255)), (38, 28))
        screen.blit(font_small.render(f"BPM {bpm}   Notes {count}   Offset {int(self.offset * 1000)}ms   {best}", True, (154, 166, 198)), (38, 52))
        fx_hint = f"FX {SPECIAL_LABELS.get(self.effect_mode, self.effect_mode)}   {self.special_keys.get('speed','q').upper()} Rush / {self.special_keys.get('echo','w').upper()} Echo / {self.special_keys.get('normal','e').upper()} Normal"
        control_hint = f"{self.control_keys.get('pause','p').upper()} Pause / {self.control_keys.get('retry','backspace').upper()} Retry / {self.control_keys.get('back','escape').upper()} Back"
        screen.blit(font_small.render(fx_hint + "    " + control_hint, True, (154, 166, 198)), (38, 76))

        combo_color = (255, 255, 255) if self.combo < 50 else (255, 226, 146)
        combo_scale = 1.0 + min(0.10, max(0.0, 0.12 - (now - self.last_hit_at)) * 0.9)
        combo_surface = font_big.render(f"{self.combo} COMBO", True, combo_color)
        if combo_scale > 1.01:
            combo_surface = pygame.transform.smoothscale(combo_surface, (int(combo_surface.get_width() * combo_scale), int(combo_surface.get_height() * combo_scale)))
        screen.blit(combo_surface, combo_surface.get_rect(center=(self.width // 2, 142)))
        score_surface = font_mid.render(f"SCORE {self.score:07,d} / 1,000,000     ACC {self.accuracy():.2f}%", True, (198, 209, 238))
        screen.blit(score_surface, score_surface.get_rect(center=(self.width // 2, 184)))

        for lane in range(self.lanes):
            x = self.board_x + lane * (self.lane_w + self.lane_gap)
            base = (20, 24, 39)
            if now < self.lane_flash_until[lane]:
                base = LANE_FLASH_COLORS.get(self.lane_flash_kind[lane], base)
            pygame.draw.rect(screen, base, (x, 214, self.lane_w, self.height - 318), border_radius=12)
            pygame.draw.rect(screen, (45, 54, 83), (x, 214, self.lane_w, self.height - 318), width=2, border_radius=12)
            cap = pygame.Rect(x + 8, self.hit_y + 28, self.lane_w - 16, 48)
            pygame.draw.rect(screen, (30, 36, 56), cap, border_radius=12)
            pygame.draw.rect(screen, (80, 94, 132), cap, width=2, border_radius=12)
            key_label = self.keys[lane].upper()
            label = font_mid.render(key_label, True, (225, 232, 255))
            screen.blit(label, label.get_rect(center=cap.center))

        self.draw_grid(screen, st)

        pulse = int(225 + 25 * math.sin(now * 10))
        pygame.draw.line(screen, (pulse, pulse, 255), (self.board_x - 22, self.hit_y), (self.width - self.board_x + 22, self.hit_y), 5)
        pygame.draw.line(screen, (90, 108, 160), (self.board_x - 22, self.hit_y + 7), (self.width - self.board_x + 22, self.hit_y + 7), 2)

        # Roll notes are lane-independent, so draw them behind lane-specific notes.
        for note in self.notes:
            if not note.hit and not note.missed and note.note_type == "roll":
                self.draw_roll_note(screen, font_mid, note, st)
        for note in self.notes:
            if not note.hit and not note.missed and note.note_type != "roll":
                self.draw_lane_note(screen, font_small, note, st)

        self.draw_hit_bursts(screen, font_small, now)

        if self.judgement_text and now < self.judgement_until:
            color = (255, 255, 255)
            if self.judgement_text == "MISS":
                color = (255, 126, 126)
            elif self.judgement_text == "PERFECT":
                color = (255, 238, 170)
            elif self.judgement_text.startswith("ROLL"):
                color = (180, 214, 255)
            elif self.judgement_text == "EARLY":
                color = (255, 170, 120)
            jt = font_big.render(self.judgement_text, True, color)
            screen.blit(jt, jt.get_rect(center=(self.width // 2, self.height // 2 + 8)))
        if now < self.effect_message_until:
            et = font_mid.render(self.effect_message, True, (255, 232, 180))
            screen.blit(et, et.get_rect(center=(self.width // 2, 216)))

        hit = sum(1 for n in self.notes if n.hit)
        total = len(self.notes)
        duration = float(self.chart.get("duration", 0.0) or 0.0)
        if duration > 0:
            progress = max(0.0, min(1.0, st / duration))
            pygame.draw.rect(screen, (35, 41, 61), (28, self.height - 68, self.width - 56, 10), border_radius=5)
            pygame.draw.rect(screen, (125, 181, 255), (28, self.height - 68, int((self.width - 56) * progress), 10), border_radius=5)
        counts = "  ".join(f"{k}:{v}" for k, v in self.judge_counts.items() if v)
        footer = f"Hit {hit}/{total}   Max {self.max_combo}x   {counts}   {self.control_keys.get('back','escape').upper()} Back   {self.control_keys.get('retry','backspace').upper()} Retry   {self.control_keys.get('pause','p').upper()} Pause"
        screen.blit(font_small.render(footer, True, (158, 170, 202)), (28, self.height - 42))

        if self.paused and not self.finished:
            overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 120))
            screen.blit(overlay, (0, 0))
            panel = pygame.Rect(0, 0, min(520, self.width - 120), 190)
            panel.center = (self.width // 2, self.height // 2)
            self.draw_rounded_panel(screen, panel, (24, 29, 48), (100, 120, 170))
            pt = font_big.render("PAUSED", True, (255, 255, 255))
            screen.blit(pt, pt.get_rect(center=(panel.centerx, panel.top + 62)))
            hint = font_mid.render(f"{self.control_keys.get('pause','p').upper()} Resume", True, (210, 224, 255))
            screen.blit(hint, hint.get_rect(center=(panel.centerx, panel.top + 124)))

        if self.finished:
            self.draw_result_overlay(screen, font_big, font_mid, font_small)


def play_chart(chart_path: str | Path) -> None:
    RhythmGame(chart_path).run()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m rhythmforge.game path/to/chart.json")
        raise SystemExit(2)
    play_chart(sys.argv[1])
