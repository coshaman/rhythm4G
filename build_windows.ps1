$ErrorActionPreference = "Stop"

Write-Host "[1/5] Creating folders..."
New-Item -ItemType Directory -Force -Path music | Out-Null
New-Item -ItemType Directory -Force -Path charts | Out-Null
New-Item -ItemType Directory -Force -Path release | Out-Null

Write-Host "[2/5] Installing dependencies..."
python -m pip install --upgrade pip
pip install -r requirements.txt

Write-Host "[3/5] Cleaning old build files..."
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force release\RhythmForge -ErrorAction SilentlyContinue

Write-Host "[4/5] Building RhythmForge.exe..."
pyinstaller --clean RhythmForge.spec

Write-Host "[5/5] Preparing release folder..."
New-Item -ItemType Directory -Force -Path release\RhythmForge | Out-Null
Copy-Item dist\RhythmForge.exe release\RhythmForge\RhythmForge.exe -Force
New-Item -ItemType Directory -Force -Path release\RhythmForge\music | Out-Null
New-Item -ItemType Directory -Force -Path release\RhythmForge\charts | Out-Null
Copy-Item README.md release\RhythmForge\README.md -Force

Write-Host ""
Write-Host "Build complete: release\RhythmForge\RhythmForge.exe"
Write-Host "Put MP3 files into the app through the launcher, or copy them to release\RhythmForge\music."
