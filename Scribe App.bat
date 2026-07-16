@echo off
rem Double-click to run Scribe like a desktop app: starts the engine and
rem opens the GUI in its own window. Closing this console stops Scribe.
cd /d "%~dp0"
call scribe.bat serve --open
if errorlevel 1 pause
