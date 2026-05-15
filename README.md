# RhythmForge v4

MP3/WAV/OGG/FLAC/M4A 파일을 불러와 자동으로 리듬게임 채보를 만들고, PC 키보드로 플레이할 수 있는 Python 기반 MVP입니다.

## v4 변경점

- `normal / hard / extreme / master` 난이도 지원
- 동시타(chord) 판정 개선
  - 각 lane별로 판정창 안의 가장 가까운 노트를 독립 처리합니다.
  - 같은 timestamp에 여러 lane 노트가 있어도 각 키 입력이 따로 정상 판정됩니다.
- hybrid timing 채보 생성
  - 강한 박자/드럼 onset은 beat/subbeat grid에 스냅합니다.
  - 보컬/멜로디성 onset은 non-grid timing을 유지합니다.
  - non-grid 노트는 플레이 화면에서 작은 흰 점으로 표시됩니다.
- 플레이 화면 회색 grid 표시
  - grid는 timing reference이며, 모든 노트가 반드시 grid 위에 나오지는 않습니다.
- 점수, 콤보, 최대 콤보, 판정별 카운트, accuracy 표시
- 채보별 최고 점수/최대 콤보/최고 accuracy 기록 저장
  - 기록은 앱 폴더의 `records.json`에 저장됩니다.
- 키 변경 기능
  - 런처에서 lane key와 special-effect key를 변경하고 chart JSON에 저장합니다.
- 특수키 음향 효과
  - 기본 `Q`: Rush, 곡을 빠르게 재생하는 cached speed variant로 전환
  - 기본 `W`: Echo, echo variant로 전환
  - 기본 `E`: Normal, 원본 재생으로 복귀
- 배포 가능 상대경로 구조
  - `RhythmForge.exe`, `music/`, `charts/`, `records.json`, `.rhythmforge_cache/`가 모두 exe 폴더 기준으로 동작합니다.

## 설치 및 실행

Python 3.11 또는 3.12 권장.

```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python run_rhythmforge.py
```

또는:

```powershell
python -m rhythmforge.main app
```

## 사용법

1. 런처에서 `음악 파일 선택`을 누릅니다.
2. 난이도를 고릅니다.
   - normal: 기본 입문 난이도
   - hard: 기본 리듬게임 경험자용
   - extreme: 고밀도 4키
   - master: 기본 6키 고밀도
3. 필요하면 아래 설정을 바꿉니다.
   - 싱크 오프셋(ms)
   - 노트 속도 배율
   - 플레이 키
   - 특수키
4. `분석해서 채보 만들기`를 누릅니다.
5. 오른쪽 목록에서 생성된 채보를 선택하고 플레이합니다.

## 기본 조작

기본 lane key:

- normal/hard/extreme: `D F J K`
- master: `S D F J K L`

기본 special key:

- `Q`: Rush. 곡을 더 빠르게 재생하는 variant로 전환합니다.
- `W`: Echo. 곡에 echo가 들어간 variant로 전환합니다.
- `E`: Normal. 원본 음원으로 복귀합니다.
- `R`: 곡 재시작
- `ESC`: 게임 종료 후 런처로 복귀

## 채보 파일 구조

생성된 채보는 `charts/`에 JSON으로 저장됩니다.

중요 필드:

```json
{
  "version": 4,
  "chart_id": "...",
  "audio_path": "music/example.mp3",
  "difficulty": "hard",
  "lanes": 4,
  "keys": ["d", "f", "j", "k"],
  "special_keys": {"speed": "q", "echo": "w", "normal": "e"},
  "grid_times": [0.5, 0.75, 1.0],
  "notes": [
    {
      "time": 1.234,
      "raw_time": 1.221,
      "grid_locked": true,
      "lane": 2,
      "scroll_speed": 760,
      "color": "accent"
    }
  ]
}
```

`audio_path`는 가능한 경우 `music/example.mp3`처럼 상대경로로 저장됩니다.

## 기록 저장

기록은 앱 실행 폴더의 `records.json`에 저장됩니다.

저장 항목:

- `high_score`
- `best_combo`
- `best_accuracy`
- `last_score`
- `last_combo`
- `last_accuracy`
- `updated_at`

## exe 배포

Windows에서:

```powershell
.\build_windows.ps1
```

PowerShell 실행 정책 문제가 있으면:

```powershell
.\build_windows.bat
```

빌드 결과:

```text
release\RhythmForge\
  RhythmForge.exe
  music\
  charts\
  README.md
```

이 `release\RhythmForge` 폴더 전체를 zip으로 묶어 배포하면 됩니다.

## 배포 시 경로 주의

이 프로젝트는 특정 PC의 절대경로에 의존하지 않습니다.

- 소스 실행: 현재 작업 폴더 기준
- exe 실행: `RhythmForge.exe`가 있는 폴더 기준
- 음악: `music/`
- 채보: `charts/`
- 기록: `records.json`
- 특수키용 음향 효과 캐시: `.rhythmforge_cache/`

따라서 배포 후 사용자가 아무 폴더에 압축을 풀어도 `RhythmForge.exe`를 실행하면 같은 폴더 안의 `music/`, `charts/`를 사용합니다.

## 현재 한계

- 자동 채보는 onset/beat/RMS/chroma 기반 휴리스틱입니다. 상용 리듬게임 수준의 수작업 채보 품질과는 다릅니다.
- 특수키 음향 효과는 실시간 DSP가 아니라 cached WAV variant로 전환하는 방식입니다. 아주 긴 곡은 첫 효과 준비에 시간이 걸릴 수 있습니다.
- MP3 decoding 문제가 있으면 ffmpeg 설치가 필요할 수 있습니다.
