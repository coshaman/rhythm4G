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
Remove-Item -Recurse -Force release\Rhythm4G -ErrorAction SilentlyContinue

Write-Host "[4/5] Building Rhythm4G.exe..."
pyinstaller --clean Rhythm4G.spec

Write-Host "[5/5] Preparing release folder..."
New-Item -ItemType Directory -Force -Path release\Rhythm4G | Out-Null
Copy-Item dist\Rhythm4G.exe release\Rhythm4G\Rhythm4G.exe -Force
New-Item -ItemType Directory -Force -Path release\Rhythm4G\music | Out-Null
New-Item -ItemType Directory -Force -Path release\Rhythm4G\charts | Out-Null
Copy-Item README.md release\Rhythm4G\README.md -Force

Write-Host ""
Write-Host "Build complete: release\Rhythm4G\Rhythm4G.exe"
Write-Host "Put MP3 files into the app through the launcher, or copy them to release\Rhythm4G\music."
