# build_exe.ps1 — build Scribe.exe on this computer (GitHub does this for you
# on every merge; use this only if you want a local build).
#
#   powershell -ExecutionPolicy Bypass -File build\build_exe.ps1
#
# Needs x64 Python 3.12+ (on ARM laptops too: the speech libraries are x64-only).
# Result: dist\Scribe\Scribe.exe — copy the whole dist\Scribe folder anywhere.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller pillow
python build\make_icon.py
python -m PyInstaller build\scribe.spec --noconfirm --clean
Copy-Item README.md, LICENSE -Destination dist\Scribe -Force

Write-Host ""
Write-Host "Built dist\Scribe\Scribe.exe" -ForegroundColor Green
