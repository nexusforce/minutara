@echo off
rem Double-click to run Minutara like a desktop app. The engine runs windowless
rem (pythonw) and writes minutara.log next to this file; quit from the app's
rem settings panel. For a visible console instead, run: minutara.bat serve --open
setlocal
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\pythonw.exe" (
  start "" "%~dp0.venv\Scripts\pythonw.exe" -m minutara.main serve --open
) else if exist "%~dp0python\pythonw.exe" (
  rem Portable build layout
  set "HF_HOME=%~dp0models"
  set "XDG_CACHE_HOME=%~dp0models"
  set "PYTHONPATH=%~dp0lib;%~dp0"
  start "" "%~dp0python\pythonw.exe" -m minutara.main serve --open
) else (
  rem No windowless interpreter found — fall back to a console
  call minutara.bat serve --open
  if errorlevel 1 pause
)
