from __future__ import annotations

import json
import queue
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .chartgen import analyze_audio
from .config import DEFAULT_SPECIAL_KEYS, DIFFICULTIES
from .game import play_chart
from .library import ChartInfo, charts_dir, gameplay_keys_for_lanes, import_audio, list_charts, normalize_key_names, patch_chart_settings, records_path, save_settings, settings_path, special_keys_from_settings


class Rhythm4GLauncher(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Rhythm4G Launcher")
        self.geometry("1080x760")
        self.minsize(940, 650)
        self.configure(bg="#111522")

        self.selected_audio: Path | None = None
        self.chart_infos: list[ChartInfo] = []
        self.status_queue: queue.Queue[tuple[str, object]] = queue.Queue()

        self.difficulty_var = tk.StringVar(value="normal")
        self.offset_var = tk.IntVar(value=-20)
        self.speed_var = tk.DoubleVar(value=1.0)
        self.use_manual_bpm_var = tk.BooleanVar(value=False)
        self.manual_bpm_var = tk.DoubleVar(value=0.0)
        self.auto_play_var = tk.BooleanVar(value=False)
        self.keys_var = tk.StringVar(value=" ".join(gameplay_keys_for_lanes(4)))
        special = special_keys_from_settings()
        self.speed_key_var = tk.StringVar(value=special.get("speed", DEFAULT_SPECIAL_KEYS["speed"]))
        self.echo_key_var = tk.StringVar(value=special.get("echo", DEFAULT_SPECIAL_KEYS["echo"]))
        self.normal_key_var = tk.StringVar(value=special.get("normal", DEFAULT_SPECIAL_KEYS["normal"]))
        self.audio_label_var = tk.StringVar(value="아직 선택된 음악 파일이 없습니다.")
        self.status_var = tk.StringVar(value="MP3를 불러오거나 기존 채보를 선택하세요.")

        self._build_style()
        self._build_ui()
        self.difficulty_var.trace_add("write", lambda *_: self.apply_default_keys_for_difficulty())
        self.refresh_chart_list()
        self.after(120, self._poll_status_queue)

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#111522")
        style.configure("Panel.TFrame", background="#181d2f", relief="flat")
        style.configure("TLabel", background="#111522", foreground="#e8ebf7", font=("Segoe UI", 10))
        style.configure("Panel.TLabel", background="#181d2f", foreground="#e8ebf7", font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background="#181d2f", foreground="#a8b0ca", font=("Segoe UI", 9))
        style.configure("Title.TLabel", background="#111522", foreground="#ffffff", font=("Segoe UI", 24, "bold"))
        style.configure("Section.TLabel", background="#181d2f", foreground="#ffffff", font=("Segoe UI", 14, "bold"))
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))
        style.configure("TRadiobutton", background="#181d2f", foreground="#e8ebf7")
        style.configure("TCheckbutton", background="#181d2f", foreground="#e8ebf7")
        style.configure("TScale", background="#181d2f")
        style.configure("Horizontal.TProgressbar", background="#8ab4ff")

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=22)
        root.pack(fill="both", expand=True)

        title_row = ttk.Frame(root)
        title_row.pack(fill="x")
        ttk.Label(title_row, text="Rhythm4G", style="Title.TLabel").pack(side="left")
        ttk.Label(title_row, text="  by 집돌이 페렐만").pack(side="left", padx=(10, 0))
        ttk.Label(title_row, textvariable=self.status_var).pack(side="right", anchor="e")

        body = ttk.Frame(root)
        body.pack(fill="both", expand=True, pady=(20, 0))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body, style="Panel.TFrame", padding=18)
        right = ttk.Frame(body, style="Panel.TFrame", padding=18)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        right.rowconfigure(2, weight=1)

        ttk.Label(left, text="1. 음악 파일 불러오기", style="Section.TLabel").pack(anchor="w")
        ttk.Label(left, text="오디오 파일을 앱의 music 폴더로 복사한 뒤 분석합니다. 배포 후에도 상대경로로 동작합니다.", style="Muted.TLabel", wraplength=470).pack(anchor="w", pady=(4, 14))
        ttk.Button(left, text="음악 파일 선택", command=self.choose_audio, style="Accent.TButton").pack(fill="x")
        ttk.Label(left, textvariable=self.audio_label_var, style="Muted.TLabel", wraplength=470).pack(anchor="w", pady=(10, 22))

        ttk.Label(left, text="2. 분석/플레이 설정", style="Section.TLabel").pack(anchor="w")
        difficulty_box = ttk.Frame(left, style="Panel.TFrame")
        difficulty_box.pack(fill="x", pady=(10, 14))
        for diff in DIFFICULTIES:
            ttk.Radiobutton(difficulty_box, text=diff.upper(), value=diff, variable=self.difficulty_var).pack(side="left", padx=(0, 14))

        form = ttk.Frame(left, style="Panel.TFrame")
        form.pack(fill="x", pady=(4, 8))
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="싱크 오프셋(ms)", style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Spinbox(form, from_=-300, to=300, increment=5, textvariable=self.offset_var, width=9).grid(row=0, column=1, sticky="e", pady=6)
        ttk.Label(form, text="노트 속도 배율", style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=6)
        speed = ttk.Scale(form, from_=0.70, to=1.70, variable=self.speed_var, orient="horizontal")
        speed.grid(row=1, column=1, sticky="ew", pady=6)
        self.speed_text = ttk.Label(form, text="1.00x", style="Panel.TLabel")
        self.speed_text.grid(row=1, column=2, padx=(8, 0))
        self.speed_var.trace_add("write", lambda *_: self.speed_text.configure(text=f"{self.speed_var.get():.2f}x"))
        ttk.Checkbutton(form, text="BPM 수동 지정", variable=self.use_manual_bpm_var).grid(row=2, column=0, sticky="w", pady=6)
        bpm_box = ttk.Frame(form, style="Panel.TFrame")
        bpm_box.grid(row=2, column=1, sticky="e", pady=6)
        ttk.Spinbox(bpm_box, from_=40.0, to=260.0, increment=0.1, textvariable=self.manual_bpm_var, width=9).pack(side="left")
        ttk.Label(bpm_box, text="BPM", style="Panel.TLabel").pack(side="left", padx=(6, 0))
        ttk.Label(form, text="예: 185곡이 92로 잡히면 185를 직접 입력", style="Muted.TLabel").grid(row=3, column=1, sticky="e", pady=(0, 4))

        key_frame = ttk.Frame(left, style="Panel.TFrame")
        key_frame.pack(fill="x", pady=(10, 8))
        key_frame.columnconfigure(1, weight=1)
        ttk.Label(key_frame, text="전역 플레이 키", style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(key_frame, textvariable=self.keys_var).grid(row=0, column=1, sticky="ew", pady=5)
        ttk.Label(key_frame, text="현재 선택 난이도의 lane 수에 저장됩니다. normal/hard/extreme=4키, master=6키", style="Muted.TLabel").grid(row=1, column=1, sticky="w")
        ttk.Label(key_frame, text="전역 특수키", style="Panel.TLabel").grid(row=2, column=0, sticky="w", pady=(12, 5))
        fx_row = ttk.Frame(key_frame, style="Panel.TFrame")
        fx_row.grid(row=2, column=1, sticky="ew", pady=(12, 5))
        for label, var in [("Speed", self.speed_key_var), ("Echo", self.echo_key_var), ("Normal", self.normal_key_var)]:
            ttk.Label(fx_row, text=label, style="Panel.TLabel").pack(side="left", padx=(0, 4))
            ttk.Entry(fx_row, textvariable=var, width=4).pack(side="left", padx=(0, 12))
        ttk.Button(key_frame, text="전역 키 설정 저장", command=self.save_global_key_settings).grid(row=3, column=1, sticky="ew", pady=(10, 0))

        ttk.Checkbutton(left, text="분석 완료 후 바로 플레이", variable=self.auto_play_var).pack(anchor="w", pady=(8, 16))
        self.analyze_button = ttk.Button(left, text="분석해서 채보 만들기", command=self.analyze_selected_audio, style="Accent.TButton")
        self.analyze_button.pack(fill="x")
        self.progress = ttk.Progressbar(left, mode="indeterminate")
        self.progress.pack(fill="x", pady=(14, 0))

        ttk.Label(right, text="노래/채보 목록", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(right, text="선택 후 플레이하거나, 오프셋/속도를 채보에 저장하세요. 키 설정은 settings.json 전역 설정을 사용합니다.", style="Muted.TLabel", wraplength=470).grid(row=1, column=0, sticky="w", pady=(4, 10))

        list_frame = ttk.Frame(right, style="Panel.TFrame")
        list_frame.grid(row=2, column=0, sticky="nsew")
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)
        self.chart_list = tk.Listbox(
            list_frame,
            bg="#0f1320",
            fg="#eef2ff",
            selectbackground="#344a7a",
            selectforeground="#ffffff",
            activestyle="none",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#2c334a",
            font=("Consolas", 10),
        )
        self.chart_list.grid(row=0, column=0, sticky="nsew")
        self.chart_list.bind("<<ListboxSelect>>", self.on_chart_selected)
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.chart_list.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.chart_list.configure(yscrollcommand=scroll.set)

        button_row = ttk.Frame(right, style="Panel.TFrame")
        button_row.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        button_row.columnconfigure(0, weight=1)
        button_row.columnconfigure(1, weight=1)
        ttk.Button(button_row, text="목록 새로고침", command=self.refresh_chart_list).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(button_row, text="선택한 곡 플레이", command=self.play_selected_chart, style="Accent.TButton").grid(row=0, column=1, sticky="ew", padx=(6, 0))

        settings_row = ttk.Frame(right, style="Panel.TFrame")
        settings_row.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        settings_row.columnconfigure(0, weight=1)
        ttk.Button(settings_row, text="선택한 채보에 오프셋/속도 저장", command=self.patch_selected_chart).grid(row=0, column=0, sticky="ew")

        folder_row = ttk.Frame(right, style="Panel.TFrame")
        folder_row.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        folder_row.columnconfigure(0, weight=1)
        ttk.Label(folder_row, text=f"Charts: {charts_dir()}\nRecords: {records_path()}", style="Muted.TLabel", wraplength=470).grid(row=0, column=0, sticky="w")

    def parse_keys(self) -> list[str]:
        raw = self.keys_var.get().replace(",", " ").split()
        return normalize_key_names(raw)

    def parse_special_keys(self) -> dict[str, str]:
        special = {
            "speed": normalize_key_names([self.speed_key_var.get()])[0],
            "echo": normalize_key_names([self.echo_key_var.get()])[0],
            "normal": normalize_key_names([self.normal_key_var.get()])[0],
        }
        if len(set(special.values())) != len(special):
            raise ValueError("특수키끼리도 서로 달라야 합니다.")
        return special

    def lane_count_for_current_difficulty(self) -> int:
        cfg = DIFFICULTIES.get(self.difficulty_var.get(), DIFFICULTIES["normal"])
        return int(cfg["lanes"])

    def apply_default_keys_for_difficulty(self) -> None:
        self.keys_var.set(" ".join(gameplay_keys_for_lanes(self.lane_count_for_current_difficulty())))

    def save_global_key_settings(self) -> None:
        try:
            lanes = self.lane_count_for_current_difficulty()
            keys = self.parse_keys()
            if len(keys) != lanes:
                raise ValueError(f"현재 난이도는 {lanes}키 채보를 생성하므로 정확히 {lanes}개의 플레이 키가 필요합니다.")
            special_keys = self.parse_special_keys()
            if set(keys) & set(special_keys.values()):
                raise ValueError("플레이 키와 특수키가 겹치면 안 됩니다.")
            save_settings(gameplay_keys={str(lanes): keys}, special_keys=special_keys)
            self.status_var.set(f"전역 키 설정을 저장했습니다. ({lanes}키: {' '.join(keys)})")
        except Exception as exc:
            messagebox.showerror("전역 키 설정 저장 실패", str(exc))

    def choose_audio(self) -> None:
        path = filedialog.askopenfilename(
            title="음악 파일 선택",
            filetypes=[
                ("Audio files", "*.mp3 *.wav *.ogg *.flac *.m4a"),
                ("MP3", "*.mp3"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            self.selected_audio = import_audio(path)
            self.audio_label_var.set(str(self.selected_audio))
            self.status_var.set("음악 파일을 가져왔습니다. 분석을 실행하세요.")
        except Exception as exc:
            messagebox.showerror("파일 가져오기 실패", str(exc))

    def analyze_selected_audio(self) -> None:
        if self.selected_audio is None:
            messagebox.showwarning("음악 파일 필요", "먼저 음악 파일을 선택하세요.")
            return
        try:
            keys = self.parse_keys()
            lanes = self.lane_count_for_current_difficulty()
            if len(keys) != lanes:
                raise ValueError(f"현재 난이도는 {lanes}키 채보를 생성하므로 정확히 {lanes}개의 플레이 키가 필요합니다.")
            special_keys = self.parse_special_keys()
            if set(keys) & set(special_keys.values()):
                raise ValueError("플레이 키와 특수키가 겹치면 안 됩니다.")
            save_settings(gameplay_keys={str(lanes): keys}, special_keys=special_keys)
        except Exception as exc:
            messagebox.showerror("키 설정 오류", str(exc))
            return

        self.analyze_button.configure(state="disabled")
        self.progress.start(12)
        self.status_var.set("분석 중입니다. 길이가 긴 MP3는 시간이 걸릴 수 있습니다.")
        audio = self.selected_audio
        difficulty = self.difficulty_var.get()
        offset_ms = int(self.offset_var.get())
        speed_multiplier = float(self.speed_var.get())
        manual_bpm = float(self.manual_bpm_var.get()) if self.use_manual_bpm_var.get() and float(self.manual_bpm_var.get()) > 0 else None

        def worker() -> None:
            try:
                out = analyze_audio(audio, difficulty=difficulty, manual_bpm=manual_bpm)
                patch_chart_settings(out, offset_ms=offset_ms, speed_multiplier=speed_multiplier)
                self.status_queue.put(("analyze_ok", out))
            except Exception as exc:
                self.status_queue.put(("error", f"{exc}\n\n{traceback.format_exc()}"))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_status_queue(self) -> None:
        try:
            while True:
                kind, payload = self.status_queue.get_nowait()
                if kind == "analyze_ok":
                    self.progress.stop()
                    self.analyze_button.configure(state="normal")
                    self.refresh_chart_list()
                    self.status_var.set(f"채보 생성 완료: {Path(payload).name}")
                    if self.auto_play_var.get():
                        self.after(100, lambda p=payload: self.play_chart_path(Path(p)))
                elif kind == "error":
                    self.progress.stop()
                    self.analyze_button.configure(state="normal")
                    self.status_var.set("작업 실패")
                    messagebox.showerror("Rhythm4G 오류", str(payload))
        except queue.Empty:
            pass
        self.after(120, self._poll_status_queue)

    def refresh_chart_list(self) -> None:
        self.chart_infos = list_charts()
        self.chart_list.delete(0, tk.END)
        for info in self.chart_infos:
            self.chart_list.insert(tk.END, info.label)
        if self.chart_infos:
            self.status_var.set(f"채보 {len(self.chart_infos)}개를 찾았습니다.")
        else:
            self.status_var.set("아직 생성된 채보가 없습니다.")

    def selected_chart_info(self) -> ChartInfo | None:
        selected = self.chart_list.curselection()
        if not selected:
            return None
        idx = int(selected[0])
        if idx < 0 or idx >= len(self.chart_infos):
            return None
        return self.chart_infos[idx]

    def on_chart_selected(self, _event: object | None = None) -> None:
        info = self.selected_chart_info()
        if info is None:
            return
        self.offset_var.set(info.offset_ms)
        try:
            data = json.loads(info.path.read_text(encoding="utf-8"))
            self.speed_var.set(float(data.get("ui_speed_multiplier", 1.0)))
            self.manual_bpm_var.set(float(data.get("tempo_bpm", 0.0) or 0.0))
            self.use_manual_bpm_var.set(False)
            lanes = int(data.get("lanes", self.lane_count_for_current_difficulty()) or self.lane_count_for_current_difficulty())
            self.keys_var.set(" ".join(gameplay_keys_for_lanes(lanes)))
            special = special_keys_from_settings()
            self.speed_key_var.set(str(special.get("speed", DEFAULT_SPECIAL_KEYS["speed"])))
            self.echo_key_var.set(str(special.get("echo", DEFAULT_SPECIAL_KEYS["echo"])))
            self.normal_key_var.set(str(special.get("normal", DEFAULT_SPECIAL_KEYS["normal"])))
        except Exception:
            self.speed_var.set(1.0)
        self.status_var.set(f"선택됨: {info.title} [{info.difficulty}]")

    def patch_selected_chart(self) -> None:
        info = self.selected_chart_info()
        if info is None:
            messagebox.showwarning("채보 선택 필요", "먼저 오른쪽 목록에서 채보를 선택하세요.")
            return
        try:
            keys = self.parse_keys()
            lanes = self.lane_count_for_current_difficulty()
            if len(keys) != lanes:
                raise ValueError(f"현재 난이도는 {lanes}키 채보를 생성하므로 정확히 {lanes}개의 플레이 키가 필요합니다.")
            special_keys = self.parse_special_keys()
            if set(keys) & set(special_keys.values()):
                raise ValueError("플레이 키와 특수키가 겹치면 안 됩니다.")
            save_settings(gameplay_keys={str(lanes): keys}, special_keys=special_keys)
            patch_chart_settings(info.path, offset_ms=int(self.offset_var.get()), speed_multiplier=float(self.speed_var.get()))
            self.refresh_chart_list()
            self.status_var.set("선택한 채보 설정을 저장했습니다.")
        except Exception as exc:
            messagebox.showerror("설정 저장 실패", str(exc))

    def play_selected_chart(self) -> None:
        info = self.selected_chart_info()
        if info is None:
            messagebox.showwarning("채보 선택 필요", "먼저 오른쪽 목록에서 플레이할 채보를 선택하세요.")
            return
        self.play_chart_path(info.path)

    def play_chart_path(self, path: Path) -> None:
        self.status_var.set("게임 실행 중입니다. ESC로 종료하면 런처로 돌아옵니다.")
        self.withdraw()
        try:
            play_chart(path)
        except Exception as exc:
            messagebox.showerror("플레이 실패", str(exc))
        finally:
            self.deiconify()
            self.lift()
            self.refresh_chart_list()
            self.status_var.set("런처로 돌아왔습니다.")


def main() -> None:
    app = Rhythm4GLauncher()
    app.mainloop()


if __name__ == "__main__":
    main()
