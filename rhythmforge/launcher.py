from __future__ import annotations

import json
import queue
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

from .chartgen import analyze_audio
from .config import DEFAULT_SPECIAL_KEYS, DIFFICULTIES
from .game import play_chart
from .library import ChartInfo, charts_dir, control_keys_from_settings, gameplay_keys_for_lanes, import_audio, list_charts, normalize_key_names, patch_chart_settings, records_path, save_settings, settings_path, special_keys_from_settings


class Rhythm4GLauncher(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Rhythm4G Launcher")
        self.geometry("1240x860")
        self.minsize(900, 680)
        self.configure(bg="#0b1020")

        self.selected_audio: Path | None = None
        self.chart_infos: list[ChartInfo] = []
        self.status_queue: queue.Queue[tuple[str, object]] = queue.Queue()

        self.difficulty_var = tk.StringVar(value="normal")
        self.difficulty_vars: dict[str, tk.BooleanVar] = {diff: tk.BooleanVar(value=(diff == "normal")) for diff in DIFFICULTIES}
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
        control = control_keys_from_settings()
        self.pause_key_var = tk.StringVar(value=control.get("pause", "p"))
        self.retry_key_var = tk.StringVar(value=control.get("retry", "backspace"))
        self.back_key_var = tk.StringVar(value=control.get("back", "escape"))
        self.audio_label_var = tk.StringVar(value="아직 선택된 음악 파일이 없습니다.")
        self.status_var = tk.StringVar(value="MP3를 불러오거나 기존 채보를 선택하세요.")

        self._build_style()
        self._build_ui()
        # Checkbutton-based difficulty selection can generate several charts at once.
        # difficulty_var is kept as the primary lane/key preset currently shown.
        self.difficulty_var.trace_add("write", lambda *_: self.apply_default_keys_for_difficulty())
        self.refresh_chart_list()
        self.after(120, self._poll_status_queue)

    def _ui_font(self) -> str:
        # Prefer fonts that display Korean/Japanese/Chinese titles in Tk widgets.
        available = {name.lower(): name for name in tkfont.families(self)}
        for candidate in (
            "Microsoft YaHei UI", "Microsoft YaHei", "Meiryo UI", "Meiryo",
            "Yu Gothic UI", "Yu Gothic", "Malgun Gothic", "Microsoft JhengHei UI",
            "Microsoft JhengHei", "Noto Sans CJK KR", "Noto Sans CJK JP",
            "Noto Sans CJK SC", "Segoe UI",
        ):
            if candidate.lower() in available:
                return available[candidate.lower()]
        return "TkDefaultFont"

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        ui_font = self._ui_font()
        self.option_add("*Font", (ui_font, 10))
        style.configure("TFrame", background="#0b1020")
        style.configure("Panel.TFrame", background="#151b2e", relief="flat")
        style.configure("Hero.TFrame", background="#10172a", relief="flat")
        style.configure("TLabel", background="#0b1020", foreground="#e8ebf7", font=(ui_font, 10))
        style.configure("Panel.TLabel", background="#151b2e", foreground="#e8ebf7", font=(ui_font, 10))
        style.configure("Hero.TLabel", background="#10172a", foreground="#e8ebf7", font=(ui_font, 10))
        style.configure("Muted.TLabel", background="#151b2e", foreground="#a8b0ca", font=(ui_font, 9))
        style.configure("Title.TLabel", background="#0b1020", foreground="#ffffff", font=(ui_font, 27, "bold"))
        style.configure("Section.TLabel", background="#151b2e", foreground="#ffffff", font=(ui_font, 14, "bold"))
        style.configure("Accent.TButton", font=(ui_font, 10, "bold"), padding=(12, 9))
        style.configure("TButton", padding=(10, 7))
        style.configure("TRadiobutton", background="#151b2e", foreground="#e8ebf7")
        style.configure("TCheckbutton", background="#151b2e", foreground="#e8ebf7")
        style.configure("TScale", background="#151b2e")
        style.configure("Horizontal.TProgressbar", background="#8ab4ff")
        style.configure("Treeview", background="#0d1324", fieldbackground="#0d1324", foreground="#edf2ff", borderwidth=0, rowheight=30, font=(ui_font, 10))
        style.configure("Treeview.Heading", background="#202945", foreground="#ffffff", font=(ui_font, 10, "bold"))
        style.map("Treeview", background=[("selected", "#385890")], foreground=[("selected", "#ffffff")])

    def _build_ui(self) -> None:
        # The launcher can become taller than the window after importing long
        # file names or showing many settings. Put the main content in a
        # vertically scrollable canvas so every button remains reachable on
        # smaller screens and in packaged builds.
        outer = ttk.Frame(self, padding=22)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)

        title_row = ttk.Frame(outer, style="Hero.TFrame", padding=(18, 14))
        title_row.grid(row=0, column=0, sticky="ew")
        title_row.columnconfigure(1, weight=1)
        ttk.Label(title_row, text="Rhythm4G", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(title_row, text="by 집돌이 페렐만  ·  Auto Chart Rhythm Game", style="Hero.TLabel").grid(row=0, column=1, sticky="w", padx=(16, 0))
        ttk.Label(title_row, textvariable=self.status_var, style="Hero.TLabel").grid(row=0, column=2, sticky="e")

        scroll_shell = ttk.Frame(outer)
        scroll_shell.grid(row=1, column=0, sticky="nsew", pady=(20, 0))
        scroll_shell.rowconfigure(0, weight=1)
        scroll_shell.columnconfigure(0, weight=1)

        self.main_canvas = tk.Canvas(
            scroll_shell,
            bg="#0b1020",
            borderwidth=0,
            highlightthickness=0,
            yscrollincrement=24,
        )
        self.main_canvas.grid(row=0, column=0, sticky="nsew")

        self.main_scrollbar = ttk.Scrollbar(
            scroll_shell,
            orient="vertical",
            command=self.main_canvas.yview,
        )
        self.main_scrollbar.grid(row=0, column=1, sticky="ns")
        self.main_canvas.configure(yscrollcommand=self.main_scrollbar.set)

        root = ttk.Frame(self.main_canvas)
        self._main_window_id = self.main_canvas.create_window((0, 0), window=root, anchor="nw")

        def _sync_scroll_region(_event: object | None = None) -> None:
            self.main_canvas.configure(scrollregion=self.main_canvas.bbox("all"))

        def _sync_canvas_width(event: tk.Event) -> None:
            self.main_canvas.itemconfigure(self._main_window_id, width=event.width)
            self._layout_panels(two_columns=event.width >= 980)

        root.bind("<Configure>", _sync_scroll_region)
        self.main_canvas.bind("<Configure>", _sync_canvas_width)
        self.main_canvas.bind("<Enter>", lambda _event: self._bind_mousewheel())
        self.main_canvas.bind("<Leave>", lambda _event: self._unbind_mousewheel())

        body = ttk.Frame(root)
        body.pack(fill="both", expand=True)
        self.body_frame = body

        left = ttk.Frame(body, style="Panel.TFrame", padding=18)
        right = ttk.Frame(body, style="Panel.TFrame", padding=18)
        self.left_panel = left
        self.right_panel = right
        self._layout_panels(two_columns=True)
        right.rowconfigure(2, weight=1)

        ttk.Label(left, text="1. 음악 파일 불러오기", style="Section.TLabel").pack(anchor="w")
        ttk.Label(left, text="오디오 파일을 앱의 music 폴더로 복사한 뒤 분석합니다. 배포 후에도 상대경로로 동작합니다.", style="Muted.TLabel", wraplength=470).pack(anchor="w", pady=(4, 14))
        ttk.Button(left, text="음악 파일 선택", command=self.choose_audio, style="Accent.TButton").pack(fill="x")
        ttk.Label(left, textvariable=self.audio_label_var, style="Muted.TLabel", wraplength=470).pack(anchor="w", pady=(10, 22))

        ttk.Label(left, text="2. 분석/플레이 설정", style="Section.TLabel").pack(anchor="w")
        difficulty_box = ttk.Frame(left, style="Panel.TFrame")
        difficulty_box.pack(fill="x", pady=(10, 14))
        for diff in DIFFICULTIES:
            ttk.Checkbutton(
                difficulty_box,
                text=diff.upper(),
                variable=self.difficulty_vars[diff],
                command=self.on_difficulty_toggle,
            ).pack(side="left", padx=(0, 14))
        ttk.Label(left, text="여러 난이도를 한 번에 선택하면 같은 곡으로 여러 채보를 순차 생성합니다.", style="Muted.TLabel", wraplength=470).pack(anchor="w", pady=(0, 8))

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
            ttk.Entry(fx_row, textvariable=var, width=8).pack(side="left", padx=(0, 12))
        ttk.Label(key_frame, text="전역 제어키", style="Panel.TLabel").grid(row=3, column=0, sticky="w", pady=(12, 5))
        control_row = ttk.Frame(key_frame, style="Panel.TFrame")
        control_row.grid(row=3, column=1, sticky="ew", pady=(12, 5))
        for label, var in [("Pause", self.pause_key_var), ("Retry", self.retry_key_var), ("Back", self.back_key_var)]:
            ttk.Label(control_row, text=label, style="Panel.TLabel").pack(side="left", padx=(0, 4))
            ttk.Entry(control_row, textvariable=var, width=10).pack(side="left", padx=(0, 12))
        ttk.Label(key_frame, text="기본값: Pause=P, Retry=Backspace, Back=Escape. 플레이 키/특수키와 겹치면 저장되지 않습니다.", style="Muted.TLabel").grid(row=4, column=1, sticky="w")
        ttk.Button(key_frame, text="전역 키 설정 저장", command=self.save_global_key_settings).grid(row=5, column=1, sticky="ew", pady=(10, 0))

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
        self.chart_list = ttk.Treeview(
            list_frame,
            columns=("difficulty", "bpm", "notes", "record"),
            show="tree headings",
            selectmode="browse",
            height=14,
        )
        self.chart_list.heading("#0", text="곡 제목")
        self.chart_list.heading("difficulty", text="난이도")
        self.chart_list.heading("bpm", text="BPM")
        self.chart_list.heading("notes", text="노트")
        self.chart_list.heading("record", text="기록")
        self.chart_list.column("#0", width=330, minwidth=180, stretch=True)
        self.chart_list.column("difficulty", width=86, minwidth=76, anchor="center", stretch=False)
        self.chart_list.column("bpm", width=78, minwidth=68, anchor="e", stretch=False)
        self.chart_list.column("notes", width=82, minwidth=68, anchor="e", stretch=False)
        self.chart_list.column("record", width=145, minwidth=110, anchor="e", stretch=False)
        self.chart_list.grid(row=0, column=0, sticky="nsew")
        self.chart_list.bind("<<TreeviewSelect>>", self.on_chart_selected)
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

    def _layout_panels(self, *, two_columns: bool) -> None:
        """Responsive launcher layout.

        Wide windows show analysis/settings and chart list side-by-side.  Narrow
        windows stack the panels vertically, so no controls disappear beyond the
        right edge after long file names or many settings are shown.
        """
        body = getattr(self, "body_frame", None)
        left = getattr(self, "left_panel", None)
        right = getattr(self, "right_panel", None)
        if body is None or left is None or right is None:
            return
        for child in (left, right):
            try:
                child.grid_forget()
            except Exception:
                pass
        for i in range(2):
            body.columnconfigure(i, weight=0)
            body.rowconfigure(i, weight=0)
        if two_columns:
            body.columnconfigure(0, weight=1)
            body.columnconfigure(1, weight=1)
            body.rowconfigure(0, weight=1)
            left.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=(0, 0))
            right.grid(row=0, column=1, sticky="nsew", padx=(10, 0), pady=(0, 0))
        else:
            body.columnconfigure(0, weight=1)
            body.rowconfigure(0, weight=0)
            body.rowconfigure(1, weight=1)
            left.grid(row=0, column=0, sticky="nsew", padx=0, pady=(0, 12))
            right.grid(row=1, column=0, sticky="nsew", padx=0, pady=(0, 0))

    def _bind_mousewheel(self) -> None:
        self.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        self.bind_all("<Button-4>", self._on_mousewheel, add="+")
        self.bind_all("<Button-5>", self._on_mousewheel, add="+")

    def _unbind_mousewheel(self) -> None:
        self.unbind_all("<MouseWheel>")
        self.unbind_all("<Button-4>")
        self.unbind_all("<Button-5>")

    def _on_mousewheel(self, event: tk.Event) -> str:
        # Windows/macOS use MouseWheel; some Linux Tk builds use Button-4/5.
        if getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        else:
            delta = -1 if int(getattr(event, "delta", 0)) > 0 else 1
        self.main_canvas.yview_scroll(delta * 3, "units")
        return "break"

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

    def parse_control_keys(self) -> dict[str, str]:
        control = {
            "pause": normalize_key_names([self.pause_key_var.get()])[0],
            "retry": normalize_key_names([self.retry_key_var.get()])[0],
            "back": normalize_key_names([self.back_key_var.get()])[0],
        }
        if len(set(control.values())) != len(control):
            raise ValueError("제어키끼리도 서로 달라야 합니다.")
        return control

    def selected_difficulties(self) -> list[str]:
        selected = [diff for diff in DIFFICULTIES if self.difficulty_vars[diff].get()]
        if not selected:
            self.difficulty_vars[self.difficulty_var.get()].set(True)
            selected = [self.difficulty_var.get()]
        return selected

    def on_difficulty_toggle(self) -> None:
        selected = self.selected_difficulties()
        if self.difficulty_var.get() not in selected:
            self.difficulty_var.set(selected[0])
        else:
            self.apply_default_keys_for_difficulty()
        self.status_var.set("선택 난이도: " + ", ".join(d.upper() for d in selected))

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
            control_keys = self.parse_control_keys()
            if set(keys) & set(special_keys.values()):
                raise ValueError("플레이 키와 특수키가 겹치면 안 됩니다.")
            if (set(keys) | set(special_keys.values())) & set(control_keys.values()):
                raise ValueError("제어키가 플레이 키 또는 특수키와 겹치면 안 됩니다.")
            save_settings(gameplay_keys={str(lanes): keys}, special_keys=special_keys, control_keys=control_keys)
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
            self.audio_label_var.set(f"선택됨: {self.selected_audio.name}")
            self.status_var.set("음악 파일을 가져왔습니다. 분석을 실행하세요.")
        except Exception as exc:
            messagebox.showerror("파일 가져오기 실패", str(exc))

    def analyze_selected_audio(self) -> None:
        if self.selected_audio is None:
            messagebox.showwarning("음악 파일 필요", "먼저 음악 파일을 선택하세요.")
            return

        difficulties = self.selected_difficulties()
        try:
            keys = self.parse_keys()
            lanes = self.lane_count_for_current_difficulty()
            # Multi-difficulty generation may include both 4-key and 6-key charts.
            # Key settings remain global; save the key row currently shown for its
            # matching lane count, but do not block chart generation for other lane
            # counts because they can use existing/default global settings.
            if len(keys) != lanes:
                raise ValueError(f"현재 표시된 주 난이도는 {lanes}키이므로 전역 플레이 키는 정확히 {lanes}개여야 합니다.")
            special_keys = self.parse_special_keys()
            control_keys = self.parse_control_keys()
            if set(keys) & set(special_keys.values()):
                raise ValueError("플레이 키와 특수키가 겹치면 안 됩니다.")
            if (set(keys) | set(special_keys.values())) & set(control_keys.values()):
                raise ValueError("제어키가 플레이 키 또는 특수키와 겹치면 안 됩니다.")
            save_settings(gameplay_keys={str(lanes): keys}, special_keys=special_keys, control_keys=control_keys)
        except Exception as exc:
            messagebox.showerror("키 설정 오류", str(exc))
            return

        self.analyze_button.configure(state="disabled")
        self.progress.start(12)
        self.status_var.set("분석 중입니다. 선택한 난이도를 순차 생성합니다: " + ", ".join(d.upper() for d in difficulties))
        audio = self.selected_audio
        offset_ms = int(self.offset_var.get())
        speed_multiplier = float(self.speed_var.get())
        manual_bpm = float(self.manual_bpm_var.get()) if self.use_manual_bpm_var.get() and float(self.manual_bpm_var.get()) > 0 else None

        def worker() -> None:
            try:
                outputs: list[Path] = []
                for difficulty in difficulties:
                    out = analyze_audio(audio, difficulty=difficulty, manual_bpm=manual_bpm)
                    patch_chart_settings(out, offset_ms=offset_ms, speed_multiplier=speed_multiplier)
                    outputs.append(Path(out))
                self.status_queue.put(("analyze_ok_many", outputs))
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
                elif kind == "analyze_ok_many":
                    self.progress.stop()
                    self.analyze_button.configure(state="normal")
                    outputs = [Path(p) for p in payload]
                    self.refresh_chart_list()
                    self.status_var.set(f"채보 {len(outputs)}개 생성 완료: " + ", ".join(p.name for p in outputs))
                    if self.auto_play_var.get() and outputs:
                        self.after(100, lambda p=outputs[0]: self.play_chart_path(Path(p)))
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
        for iid in self.chart_list.get_children():
            self.chart_list.delete(iid)
        for idx, info in enumerate(self.chart_infos):
            minutes = int(info.duration // 60)
            seconds = int(info.duration % 60)
            record = f"{info.high_score:,} / {info.best_combo}x" if info.high_score or info.best_combo else "-"
            self.chart_list.insert(
                "",
                tk.END,
                iid=str(idx),
                text=info.title,
                values=(info.difficulty.upper(), f"{info.tempo_bpm:.1f}", f"{info.note_count:,}", record),
            )
        if self.chart_infos:
            self.status_var.set(f"채보 {len(self.chart_infos)}개를 찾았습니다.")
        else:
            self.status_var.set("아직 생성된 채보가 없습니다.")

    def selected_chart_info(self) -> ChartInfo | None:
        selected = self.chart_list.selection()
        if not selected:
            return None
        try:
            idx = int(selected[0])
        except (TypeError, ValueError):
            return None
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
            control = control_keys_from_settings()
            self.pause_key_var.set(str(control.get("pause", "p")))
            self.retry_key_var.set(str(control.get("retry", "backspace")))
            self.back_key_var.set(str(control.get("back", "escape")))
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
            control_keys = self.parse_control_keys()
            if set(keys) & set(special_keys.values()):
                raise ValueError("플레이 키와 특수키가 겹치면 안 됩니다.")
            if (set(keys) | set(special_keys.values())) & set(control_keys.values()):
                raise ValueError("제어키가 플레이 키 또는 특수키와 겹치면 안 됩니다.")
            save_settings(gameplay_keys={str(lanes): keys}, special_keys=special_keys, control_keys=control_keys)
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
