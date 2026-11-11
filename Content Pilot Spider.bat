@echo off
cd /d "%~dp0"
start "" /min "%~dp0.venv\Scripts\pythonw.exe" "%~dp0run.py"
