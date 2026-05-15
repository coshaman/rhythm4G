from __future__ import annotations

import json
import math
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import pygame

from .config import DEFAULT_SPECIAL_KEYS, JUDGEMENTS, MISS_MS
from .effects import EffectFiles, prepare_effect_files
from .library import gameplay_keys_for_lanes, record_for_chart, resolve_portable_path, special_keys_from_settings, update_record


NOTE_COLORS = {
    "normal": ((92, 171, 255), (224, 242, 255)),
    "bright": ((126, 203, 255), (235, 249, 255)),
    "accent": ((178, 136, 255), (242, 232, 255)),
    "highlight": ((255, 190, 96), (255, 244, 210)),
}
LANE_FLASH_COLORS = {
    "normal": (56, 78, 112),
    "bright": (60, 92, 122),
    "accent": (80, 65, 124),
    "highlight": (120, 86, 45),
}
SPECIAL_LABELS = {
    "normal": "NORMAL",
    "speed": "RUSH x1.15",
    "echo": "ECHO",
}


@dataclass
class RuntimeNote:
    time: float
    lane: int
    scroll_speed: float
    color: str = "normal"
    source: str = "unknown"
    grid_locked: bool = True
    raw_time: float | None = None
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
        special = dict(DEFAULT_SPECIAL_KEYS)
        special.update(special_keys_from_settings())
        self.special_keys = special
        self.special_key_to_mode = self._build_special_key_map(special)

        base_speed = float(self.chart.get("base_scroll_speed", self.chart.get("scroll_speed", 720)))
        self.notes = [
            RuntimeNote(
                time=float(n["time"]),
                raw_time=float(n.get("raw_time", n["time"])),
                grid_locked=bool(n.get("grid_locked", True)),
                lane=int(n["lane"]),
                scroll_speed=float(n.get("scroll_speed", base_speed)),
                color=str(n.get("color", "normal")),
                source=str(n.get("source", "unknown")),
            )
            for n in self.chart["notes"]
        ]
        self.notes.sort(key=lambda n: (n.time, n.lane))
        self.grid_times = [float(x) for x in self.chart.get("grid_times", [])]
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

        self.audio_enabled = False
        self.effects: EffectFiles | None = None
        self.effects_ready = False
        self.effect_mode = "normal"
        self.effect_rate = 1.0
        self.effect_song_anchor = 0.0
        self.effect_started_at = 0.0
        self.effect_message_until = 0.0
        self.effect_message = ""

    def _key_code(self, name: str) -> int:
        name = str(name).strip().lower()
        aliases = {"space": pygame.K_SPACE, "tab": pygame.K_TAB, "left": pygame.K_LEFT, "right": pygame.K_RIGHT, "up": pygame.K_UP, "down": pygame.K_DOWN}
        if name in aliases:
            return aliases[name]
        return pygame.key.key_code(name)

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

    def song_time(self) -> float:
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
        pygame.display.set_caption("Rhythm4G")
        screen = pygame.display.set_mode((self.width, self.height))
        clock = pygame.time.Clock()
        font_big = pygame.font.SysFont("consolas", 42, bold=True)
        font_mid = pygame.font.SysFont("consolas", 26, bold=True)
        font_small = pygame.font.SysFont("consolas", 18)

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
                    if event.key == pygame.K_ESCAPE:
                        self.running = False
                    elif event.key == pygame.K_r:
                        if self.audio_enabled:
                            pygame.mixer.music.stop()
                        self.__init__(self.chart_path)
                        return self.run()
                    elif event.key in self.special_key_to_mode:
                        self.switch_effect(self.special_key_to_mode[event.key], st)
                    elif event.key in self.key_to_lane:
                        self.handle_hit(self.key_to_lane[event.key], st)

            self.update_misses(st)
            self.maybe_save_record(st)
            self.draw(screen, font_big, font_mid, font_small, st)
            pygame.display.flip()
            clock.tick(240)

        if self.audio_enabled:
            pygame.mixer.music.stop()
        pygame.quit()

    def find_candidate(self, lane: int, st: float) -> RuntimeNote | None:
        # Only consider notes in this lane and within the largest judgement window.
        # This supports dense chords because each lane independently resolves its
        # nearest note, even when several notes share the same timestamp.
        max_window = max(j.ms for j in JUDGEMENTS) / 1000.0
        candidates = [
            n for n in self.notes
            if n.lane == lane and not n.hit and not n.missed and abs(n.time - st) <= max_window
        ]
        if not candidates:
            return None
        # Prefer closest timing, then grid-locked rhythmic hits, then stronger colors.
        priority = {"highlight": 3, "accent": 2, "bright": 1, "normal": 0}
        return min(candidates, key=lambda n: (abs(n.time - st), -int(n.grid_locked), -priority.get(n.color, 0)))

    def handle_hit(self, lane: int, st: float) -> None:
        note = self.find_candidate(lane, st)
        if note is None:
            # Empty key presses are shown but do not instantly delete every chord.
            self.show_judgement("EMPTY")
            self.flash_lane(lane, "normal")
            return
        delta_ms = abs(note.time - st) * 1000.0
        for judge in JUDGEMENTS:
            if delta_ms <= judge.ms:
                note.hit = True
                note.judgement = judge.name
                self.judge_counts[judge.name] += 1
                self.hit_count += 1
                self.score += judge.score + min(self.combo, 500) * 3
                self.combo += 1
                self.max_combo = max(self.max_combo, self.combo)
                self.show_judgement(judge.name)
                self.flash_lane(lane, note.color)
                self.hit_bursts.append(HitBurst(lane=lane, created_at=perf_counter(), color=note.color, judgement=judge.name))
                self.last_hit_at = perf_counter()
                return
        self.mark_miss(note)
        self.flash_lane(lane, "normal")

    def mark_miss(self, note: RuntimeNote) -> None:
        if note.hit or note.missed:
            return
        note.missed = True
        note.judgement = "MISS"
        self.judge_counts["MISS"] += 1
        self.break_combo("MISS")

    def update_misses(self, st: float) -> None:
        for note in self.notes:
            if not note.hit and not note.missed and (st - note.time) * 1000.0 > MISS_MS:
                self.mark_miss(note)

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
        if not self.grid_times:
            return
        left = self.board_x - 18
        right = self.width - self.board_x + 18
        # Draw grey beat/subbeat guide lines near visible notes.  The actual chart
        # may contain non-grid notes, so these are visual timing references only.
        min_t = st - 0.25
        max_t = st + 2.8
        for gt in self.grid_times:
            if gt < min_t:
                continue
            if gt > max_t:
                break
            y = self.hit_y - (gt - st) * float(self.chart.get("scroll_speed", self.chart.get("base_scroll_speed", 720)))
            if 90 <= y <= self.height - 92:
                strong = abs((gt / max(float(self.chart.get("beat_interval", 0.5)), 0.001)) - round(gt / max(float(self.chart.get("beat_interval", 0.5)), 0.001))) < 0.02
                color = (84, 88, 104) if strong else (56, 60, 74)
                width = 2 if strong else 1
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
            x = self.board_x + burst.lane * (self.lane_w + self.lane_gap) + self.lane_w // 2
            t = age / 0.34
            radius = int(18 + 38 * t)
            body, outline = NOTE_COLORS.get(burst.color, NOTE_COLORS["normal"])
            alpha_like = max(0, 1.0 - t)
            # pygame without alpha compositing on the main surface: draw thin rings.
            pygame.draw.circle(screen, outline, (x, self.hit_y), radius, max(1, int(4 * alpha_like)))
            pygame.draw.circle(screen, body, (x, self.hit_y), max(4, int(10 * (1 - t))), 0)
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
        hint = font_mid.render("R Retry    ESC Back to launcher", True, (255, 255, 255))
        screen.blit(hint, hint.get_rect(center=(panel.centerx, panel.bottom - 38)))

    def draw(self, screen, font_big, font_mid, font_small, st: float) -> None:
        screen.fill((8, 10, 18))
        now = perf_counter()

        # Subtle vertical gradient bands.
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
        screen.blit(font_small.render(fx_hint, True, (154, 166, 198)), (38, 76))

        # Large, persistent combo/score HUD. Combo now stays readable even during dense sections.
        combo_color = (255, 255, 255) if self.combo < 50 else (255, 226, 146)
        combo_scale = 1.0 + min(0.10, max(0.0, 0.12 - (now - self.last_hit_at)) * 0.9)
        combo_surface = font_big.render(f"{self.combo} COMBO", True, combo_color)
        if combo_scale > 1.01:
            combo_surface = pygame.transform.smoothscale(combo_surface, (int(combo_surface.get_width() * combo_scale), int(combo_surface.get_height() * combo_scale)))
        screen.blit(combo_surface, combo_surface.get_rect(center=(self.width // 2, 142)))
        score_surface = font_mid.render(f"SCORE {self.score:,}     ACC {self.accuracy():.2f}%", True, (198, 209, 238))
        screen.blit(score_surface, score_surface.get_rect(center=(self.width // 2, 184)))

        # No countdown text is drawn over the playfield.  Earlier versions showed
        # START IN briefly behind the lanes, which looked like a stray "T" flash
        # on some window captures.

        for lane in range(self.lanes):
            x = self.board_x + lane * (self.lane_w + self.lane_gap)
            base = (20, 24, 39)
            if now < self.lane_flash_until[lane]:
                base = LANE_FLASH_COLORS.get(self.lane_flash_kind[lane], base)
            pygame.draw.rect(screen, base, (x, 214, self.lane_w, self.height - 318), border_radius=12)
            pygame.draw.rect(screen, (45, 54, 83), (x, 214, self.lane_w, self.height - 318), width=2, border_radius=12)
            # Key cap area
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

        for note in self.notes:
            if note.hit or note.missed:
                continue
            dt = note.time - st
            y = self.hit_y - dt * note.scroll_speed
            if st < -0.05 or y < 104 or y > self.height + 50:
                continue
            x = self.board_x + note.lane * (self.lane_w + self.lane_gap)
            body, outline = NOTE_COLORS.get(note.color, NOTE_COLORS["normal"])
            visual_pulse = 1.0 + 0.045 * math.sin(now * 18 + note.lane)
            w = int(self.lane_w * 0.84 * visual_pulse)
            h = 24 if note.color != "highlight" else 29
            note_rect = pygame.Rect(0, 0, w, h)
            note_rect.center = (x + self.lane_w // 2, int(y))
            if note.color in {"accent", "highlight"}:
                glow = note_rect.inflate(16, 12)
                pygame.draw.rect(screen, body, glow, width=2, border_radius=14)
            pygame.draw.rect(screen, body, note_rect, border_radius=9)
            pygame.draw.rect(screen, outline, note_rect, width=2, border_radius=9)
            if not note.grid_locked:
                pygame.draw.circle(screen, (245, 245, 245), (note_rect.right - 9, note_rect.top + 7), 3)

        self.draw_hit_bursts(screen, font_small, now)

        if self.judgement_text and perf_counter() < self.judgement_until:
            color = (255, 255, 255)
            if self.judgement_text == "MISS":
                color = (255, 126, 126)
            elif self.judgement_text == "PERFECT":
                color = (255, 238, 170)
            jt = font_big.render(self.judgement_text, True, color)
            screen.blit(jt, jt.get_rect(center=(self.width // 2, self.height // 2 + 8)))
        if perf_counter() < self.effect_message_until:
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
        footer = f"Hit {hit}/{total}   Max {self.max_combo}x   {counts}   ESC Back   R Retry"
        screen.blit(font_small.render(footer, True, (158, 170, 202)), (28, self.height - 42))

        if self.finished:
            self.draw_result_overlay(screen, font_big, font_mid, font_small)


def play_chart(chart_path: str | Path) -> None:
    RhythmGame(chart_path).run()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m rhythmforge.game path/to/chart.json")
        raise SystemExit(2)
    play_chart(sys.argv[1])
