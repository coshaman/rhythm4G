# RhythmForge Architecture v4

## Modules

```text
rhythmforge/
  main.py        CLI/UI entrypoint
  launcher.py    Tkinter desktop launcher
  chartgen.py    audio analysis and chart generation
  game.py        pygame-ce gameplay loop
  effects.py     cached speed/echo audio variant generator
  library.py     portable paths, chart list, settings patch, records
  config.py      difficulty presets and judgement windows
```

## Portable data model

`library.project_root()` is the only root resolver.

- Source mode: current working directory
- PyInstaller mode: directory containing `RhythmForge.exe`

Runtime data is stored relative to that root:

```text
music/
charts/
records.json
.rhythmforge_cache/
```

Generated charts store `audio_path` as a relative path whenever possible.

## Chart generation

`chartgen.analyze_audio()` uses:

- `librosa.load()` for decoding
- onset envelope for attack detection
- beat tracking for grid reference
- RMS energy for highlight detection
- chroma/spectral centroid for lane assignment

Timing is hybrid:

- Strong rhythmic hits snap to the beat/subbeat grid.
- Loose melodic hits keep non-grid raw timing.
- Each note stores `time`, `raw_time`, and `grid_locked`.

## Gameplay

`game.RhythmGame` loads chart JSON, resolves the audio path, and builds runtime notes.

Important systems:

- 240 FPS event/render loop for lower input quantization
- lane-wise nearest-note judgement for dense chords
- score/combo/accuracy tracking
- grey grid rendering from `grid_times`
- record save through `library.update_record()`
- special FX stream switching through `effects.prepare_effect_files()`

## Special effects

pygame mixer does not provide real-time MP3 DSP. v4 creates cached WAV variants:

- speed: librosa time-stretch, default rate 1.15
- echo: delayed/attenuated audio layers

The game switches the current music stream while preserving the estimated song position.

## Records

`records.json` is keyed by `chart_id`. Each entry stores high score, best combo, best accuracy, and last play stats.
