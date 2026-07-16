@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0python\python.exe" (
  rem Portable build layout
  set HF_HOME=%~dp0models
  set XDG_CACHE_HOME=%~dp0models
  set PYTHONPATH=%~dp0lib;%~dp0
  "%~dp0python\python.exe" -m scribe.main %*
) else (
  rem Git clone layout — use the venv if present, else system python
  if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -m scribe.main %*
  ) else (
    python -m scribe.main %*
  )
)
