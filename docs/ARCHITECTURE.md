# Rhythm4G Architecture v8

## Entry points

- `run_rhythmforge.py`: launcher entry point for source mode and PyInstaller.
- `rhythmforge.main`: CLI entry point. Supports `app`, `auto`, and `play`.

## Core modules

- `chartgen.py`
  - Loads audio with librosa.
  - Extracts onset strength, beat frames, RMS, chroma, and spectral centroid.
  - Applies half/double BPM correction and accepts a manual BPM override from CLI/launcher.
  - Clusters near-simultaneous multi-lane notes so natural chords descend exactly together.
  - Generates hybrid timing charts: strong rhythmic hits snap to grid, weaker melodic hits may remain non-grid.
  - Adds rhythm-game motifs such as stairs, trills, jacks, and accent chords.

- `game.py`
  - Pygame-ce game loop.
  - Uses global key settings from `settings.json`, not chart-local key settings.
  - Handles independent lane judgement for simultaneous notes.
  - Draws beat/subbeat grid lines, large combo HUD, hit bursts, lane flashes, progress bar, and result overlay.
  - Saves records through `library.update_record`.

- `launcher.py`
  - Tkinter launcher UI.
  - Imports audio to `music/`.
  - Runs chart analysis in a background thread.
  - Lists charts from `charts/`.
  - Saves global key settings to `settings.json`.
  - Saves chart-local offset and note speed multiplier to chart JSON.

- `library.py`
  - Portable path helpers.
  - Audio import and chart discovery.
  - `records.json` persistence.
  - `settings.json` persistence.

- `effects.py`
  - Creates cached Rush/Echo audio variants in `.rhythmforge_cache/`.

## Portable data model

The app root is:

- source mode: current working directory
- PyInstaller mode: directory containing `Rhythm4G.exe`

Generated files are stored relative to this root:

```text
music/
charts/
records.json
settings.json
.rhythmforge_cache/
```

Chart audio paths are stored as portable relative paths where possible, for example:

```json
"audio_path": "music/song.mp3"
```

## Settings model

Key settings are no longer stored per chart. They are stored in `settings.json`:

```json
{
  "gameplay_keys": {
    "4": ["d", "f", "j", "k"],
    "6": ["s", "d", "f", "j", "k", "l"]
  },
  "special_keys": {
    "speed": "q",
    "echo": "w",
    "normal": "e"
  }
}
```

## Chart-local settings

The following remain chart-local because they can vary by song/chart:

- `offset_ms`
- `ui_speed_multiplier`
- per-note `scroll_speed`



## v8 timing/rendering note

Charts now include `grid_markers` with per-marker `scroll_speed` values. The game renderer uses these markers so grey grid lines move with the same local speed model as nearby notes. Older charts without `grid_markers` still fall back to `grid_times` and the chart-level scroll speed.

Korean and other non-ASCII titles are stored with UTF-8 JSON (`ensure_ascii=False`) and rendered using Korean-capable system fonts when available.


## v8 Chord Visual Normalization

`rhythmforge/chart_utils.py` normalizes clean multi-lane chord groups so simultaneous notes share one render time, one visual scroll speed, and one visual color. The game applies the same pass at load time, so older v7 chart JSON files are corrected without regeneration. Note body geometry is fixed per lane; highlight/accent notes use external glow only.

## v10 변경사항

- 일본어/중국어 곡 제목 표시를 위해 런처와 플레이 화면의 CJK 폰트 탐색을 강화했습니다. JSON 저장은 UTF-8/`ensure_ascii=False`를 유지합니다.
- 노트 색상과 키빔/타격 이펙트 색상을 분리했습니다. 노트는 파랑/노랑/보라/분홍 계열, 키빔은 중립적인 청백색 계열을 사용하여 겹치는 상황의 가시성을 높였습니다.
- 리트라이 키를 `R` 고정에서 전역 설정으로 변경했습니다. 기본값은 `Backspace`이며 런처에서 바꿀 수 있습니다.
- 일시정지 키를 추가했습니다. 기본값은 `P`이며 런처에서 바꿀 수 있습니다.
- 뒤로가기 키도 전역 제어키로 관리합니다. 기본값은 `Escape`입니다.
- 점수 체계를 1,000,000점 고정 만점으로 변경했습니다. 전부 PERFECT이면 정확히 1,000,000점입니다.

