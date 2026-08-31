@echo off
setlocal

set "PROJECT_DIR=%~dp0"
set "PROJECT_PYTHON=%PROJECT_DIR%.venv\Scripts\python.exe"

if not exist "%PROJECT_PYTHON%" (
    echo Python virtual environment was not found.
    echo Follow the Setup section in README.md first.
    pause
    exit /b 1
)

cd /d "%PROJECT_DIR%"
"%PROJECT_PYTHON%" -m streamlit run streamlit_app.py --server.headless true --browser.gatherUsageStats false

if errorlevel 1 (
    echo.
    echo The application stopped with an error.
    pause
)
