@echo off
echo Starting Backend and Frontend...

:: Start Backend in a new window
start cmd /k "echo Starting Backend... && cd backend && venv\Scripts\activate && python main.py"

:: Start Frontend in a new window
start cmd /k "echo Starting Frontend... && cd frontend && npm run dev"

echo Done. Both systems are booting up in separate windows.
pause
