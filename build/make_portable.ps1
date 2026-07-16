# make_portable.ps1 — build a self-contained Scribe on a USB stick.
# Run ONCE on any Windows box with internet. After that the stick runs offline.
#
#   powershell -ExecutionPolicy Bypass -File build\make_portable.ps1 -Target E:\Scribe
#
# No admin rights needed. Nothing is installed on the host.

param(
  [string]$Target = "$PSScriptRoot\..\dist\Scribe",
  [string]$PyVersion = "3.11.9",
  [string]$Model = "small.en"
)

$ErrorActionPreference = "Stop"
$Target = [System.IO.Path]::GetFullPath($Target)
Write-Host "Building into $Target" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $Target | Out-Null

# 1. Embeddable Python — a portable interpreter, no installer.
$zip = "$env:TEMP\python-embed.zip"
$url = "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-amd64.zip"
if (-not (Test-Path "$Target\python\python.exe")) {
  Write-Host "Downloading Python $PyVersion (embeddable)..."
  Invoke-WebRequest -Uri $url -OutFile $zip
  Expand-Archive -Path $zip -DestinationPath "$Target\python" -Force
  Remove-Item $zip
}

# Let the embeddable build import from site-packages.
$pth = Get-ChildItem "$Target\python\python*._pth" | Select-Object -First 1
$content = Get-Content $pth
if ($content -notcontains "import site") {
  ($content -replace '^#import site$', 'import site') + "`n../lib" | Set-Content $pth
}

# 2. pip
if (-not (Test-Path "$Target\python\Scripts\pip.exe")) {
  Write-Host "Bootstrapping pip..."
  Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile "$env:TEMP\get-pip.py"
  & "$Target\python\python.exe" "$env:TEMP\get-pip.py" --no-warn-script-location
  Remove-Item "$env:TEMP\get-pip.py"
}

# 3. Dependencies into a lib/ folder that travels with the stick.
Write-Host "Installing dependencies (this is the slow part)..."
& "$Target\python\python.exe" -m pip install --upgrade pip --no-warn-script-location
& "$Target\python\python.exe" -m pip install --target="$Target\lib" `
    -r "$PSScriptRoot\..\requirements.txt" --no-warn-script-location

# 4. App source
Write-Host "Copying app..."
Copy-Item "$PSScriptRoot\..\scribe" -Destination $Target -Recurse -Force
Copy-Item "$PSScriptRoot\..\README.md" -Destination $Target -Force -ErrorAction SilentlyContinue

# 5. Pre-download the Whisper model so the stick works with no internet.
Write-Host "Pre-baking the $Model model..."
New-Item -ItemType Directory -Force -Path "$Target\models" | Out-Null
$env:HF_HOME = "$Target\models"
& "$Target\python\python.exe" -c @"
from faster_whisper import WhisperModel
WhisperModel('$Model', device='cpu', compute_type='int8')
print('model cached')
"@

# 6. Launchers
@"
@echo off
setlocal
cd /d "%~dp0"
set HF_HOME=%~dp0models
set XDG_CACHE_HOME=%~dp0models
set PYTHONPATH=%~dp0lib;%~dp0
"%~dp0python\python.exe" -m scribe.main %*
"@ | Set-Content "$Target\scribe.bat" -Encoding ASCII

@"
@echo off
cd /d "%~dp0"
call scribe.bat serve
pause
"@ | Set-Content "$Target\Start Phone Remote.bat" -Encoding ASCII

@"
@echo off
cd /d "%~dp0"
call scribe.bat record --title "%~1"
pause
"@ | Set-Content "$Target\Record.bat" -Encoding ASCII

$size = "{0:N1}" -f ((Get-ChildItem $Target -Recurse | Measure-Object Length -Sum).Sum / 1GB)
Write-Host ""
Write-Host "Done. $Target is $size GB." -ForegroundColor Green
Write-Host "Copy that folder to the stick. Run scribe.bat config to check it." -ForegroundColor Green
