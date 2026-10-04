@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0" || exit /b 1

rem Always use the same python command for checking, installing and launching.
python -c "import sys; print(sys.executable)" >nul 2>nul
if errorlevel 1 (
    echo Python not found. Install Python and add it to PATH.
    exit /b 1
)

if /i "%~1"=="--install" goto install
if /i "%~1"=="--check" goto check
if not "%~1"=="" (
    echo Usage: run.cmd [--check ^| --install]
    exit /b 2
)
goto check

:install
echo Installing dependencies with the same python command...
python -m pip install -r "requirements.txt"
if errorlevel 1 (
    echo Install failed. Retry: python -m pip install -r requirements.txt
    exit /b 1
)
goto check

:check
python -c "import sys, yt_dlp, requests, tkinter; print('Python:', sys.executable)" 2>nul
if errorlevel 1 (
    echo Missing dependencies or tkinter in this Python.
    echo Install: python -m pip install -r requirements.txt
    echo Or run: run.cmd --install
    echo If tkinter is missing, install Python with Tcl/Tk.
    exit /b 1
)
if /i "%~1"=="--check" (
    echo Dependencies OK. No network request or downloader launch.
    exit /b 0
)

python "vk_video_download.py"
exit /b %ERRORLEVEL%
