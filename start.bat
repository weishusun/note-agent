@echo off
rem Start note-agent: backend on http://127.0.0.1:8000, frontend on http://localhost:5173
rem Each runs in its own window; close the windows to stop.

where uv >nul 2>nul
if errorlevel 1 (
  echo [note-agent] uv was not found on PATH. Install uv first: https://docs.astral.sh/uv/
  pause
  exit /b 1
)
where npm >nul 2>nul
if errorlevel 1 (
  echo [note-agent] npm was not found on PATH. Install Node.js first.
  pause
  exit /b 1
)

start "note-agent backend" cmd /k "cd /d "%~dp0backend" && uv run uvicorn app.main:app --port 8000"
start "note-agent frontend" cmd /k "cd /d "%~dp0frontend" && npm run dev -- --port 5173 --strictPort"

rem Give both servers a moment to come up before opening the page.
timeout /t 5 /nobreak >nul
start "" http://localhost:5173
