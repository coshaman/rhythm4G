# Rhythm4G v8

**Rhythm4G**는 MP3/WAV/OGG/FLAC/M4A 파일을 불러와 자동으로 리듬게임 채보를 만들고, PC 키보드로 플레이할 수 있는 Python 기반 리듬게임 MVP입니다.

- 게임명: Rhythm4G
- 개발자: 집돌이 페렐만

## v8 주요 변경점

- 3개 이상 동타에서 한 노트만 미묘하게 크기/위치가 달라 보이던 문제를 수정했습니다. 같은 동타 그룹은 `time`, `render_time`, `scroll_speed`, `visual_scroll_speed`, `color`를 하나로 고정합니다.
- 기존 v7 채보도 다시 분석하지 않고 플레이 시 자동으로 동타 시각 보정을 적용합니다.
- 노트 본체 크기는 색상/레인/시간 pulse에 따라 변하지 않게 고정하고, 강조 효과는 바깥 glow만 사용합니다.
- 회색 grid 속도, lane column 외부 노트 표시 방지, UTF-8/한글 폰트 처리는 v7 수정사항을 유지합니다.
- BPM 수동 지정과 half/double BPM 보정을 유지합니다.
- 기존 상대경로/포터블 배포 구조를 유지합니다.

## 폴더 구조

```text
Rhythm4G/
  run_rhythmforge.py
  rhythmforge/
  music/
  charts/
  records.json        # 플레이 기록, 자동 생성
  settings.json       # 전역 키 설정, 자동 생성
  .rhythmforge_cache/ # 특수 음향 효과용 캐시, 자동 생성
```

## 개발 환경 실행

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python run_rhythmforge.py
```

PowerShell 실행 정책 오류가 나면 현재 터미널에서만 다음을 실행하세요.

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## 런처 사용법

1. `python run_rhythmforge.py` 실행
2. `음악 파일 선택`으로 MP3 등을 선택
3. 난이도 선택
4. 자동 BPM이 틀리는 곡이면 `BPM 수동 지정` 체크 후 BPM 입력
5. 필요하면 오프셋, 노트 속도, 전역 키 설정 변경
6. `전역 키 설정 저장` 클릭
7. `분석해서 채보 만들기` 클릭
8. 오른쪽 목록에서 채보 선택 후 플레이

## 기본 키

- normal / hard / extreme: `D F J K`
- master: `S D F J K L`
- Rush: `Q`
- Echo: `W`
- Normal FX: `E`

키 설정은 `settings.json`에 저장됩니다. 채보 파일마다 키를 따로 저장하지 않습니다.

## CLI 실행

```powershell
python -m rhythmforge.main auto "music\song.mp3" --difficulty normal
python -m rhythmforge.main auto "music\song.mp3" --difficulty hard --bpm 185
python -m rhythmforge.main generate "music\song.mp3" --difficulty master --bpm 185
python -m rhythmforge.main play "charts\song.hard.json"
python -m rhythmforge.main app
```

## Windows exe 빌드

```powershell
.\build_windows.ps1
```

실행 정책 때문에 실패하면 다음을 사용하세요.

```powershell
.\build_windows.bat
```

빌드 결과:

```text
release\Rhythm4G\
  Rhythm4G.exe
  music\
  charts\
  README.md
```

배포할 때는 `release\Rhythm4G` 폴더 전체를 압축해서 전달하면 됩니다. 특정 PC의 절대경로에 의존하지 않습니다.

## BPM/싱크 조정

- 185 BPM 곡이 92.x BPM으로 잡히면 런처에서 `BPM 수동 지정`을 켜고 `185`를 입력하세요.
- 노트가 늦게 맞는 느낌이면 오프셋을 올리고, 빠르게 맞는 느낌이면 오프셋을 낮추세요.

예시:

- 노트가 늦게 내려오는 느낌: `-20 -> 0 -> 20`
- 노트가 너무 빨리 오는 느낌: `-20 -> -40 -> -60`

## 한계

자동 채보는 onset, beat, RMS energy, chroma, spectral centroid 기반 휴리스틱입니다. 드럼이 선명한 곡에서는 잘 작동하지만, 루바토가 강하거나 박자가 의도적으로 흔들리는 곡에서는 수동 BPM/오프셋 조정이 필요할 수 있습니다.

## v9 변경사항

- 일본어/중국어 곡 제목 표시를 위해 런처와 플레이 화면의 CJK 폰트 탐색을 강화했습니다. JSON 저장은 UTF-8/`ensure_ascii=False`를 유지합니다.
- 노트 색상과 키빔/타격 이펙트 색상을 분리했습니다. 노트는 파랑/노랑/보라/분홍 계열, 키빔은 중립적인 청백색 계열을 사용하여 겹치는 상황의 가시성을 높였습니다.
- 리트라이 키를 `R` 고정에서 전역 설정으로 변경했습니다. 기본값은 `Backspace`이며 런처에서 바꿀 수 있습니다.
- 일시정지 키를 추가했습니다. 기본값은 `P`이며 런처에서 바꿀 수 있습니다.
- 뒤로가기 키도 전역 제어키로 관리합니다. 기본값은 `Escape`입니다.
- 점수 체계를 1,000,000점 고정 만점으로 변경했습니다. 전부 PERFECT이면 정확히 1,000,000점입니다.

