@echo off
REM GGUF Runner Hub launcher — hardened for Windows 7 legacy console
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title GGUF Runner Hub
set "GGUF_ROOT=%~dp0"
if "%GGUF_ROOT:~-1%"=="\" set "GGUF_ROOT=%GGUF_ROOT:~0,-1%"

call "%~dp0scripts\detect_windows.bat" 2>nul
if exist "%~dp0profiles\preferred_python.txt" (
  set /p GGUF_PREF_PY=<"%~dp0profiles\preferred_python.txt"
)
call "%~dp0scripts\env_python.bat" 2>nul

if not exist "models" mkdir models 2>nul
if not exist "sessions" mkdir sessions 2>nul
if not exist "workspace" mkdir workspace 2>nul
if not exist "books" mkdir books 2>nul
if not exist "memory" mkdir memory 2>nul

echo ============================================
echo  GGUF Runner — OS class: %GGUF_OS_CLASS%
if defined GGUF_WMIC_VER echo  Windows version: %GGUF_WMIC_VER%
if defined GGUF_REG_VER echo  Registry version: %GGUF_REG_VER%
echo ============================================

set "PY="
if /I "%GGUF_OS_CLASS%"=="legacy" (
  if exist "%~dp0python38\python.exe" (
    set "PY=%~dp0python38\python.exe"
    echo [OK] Legacy Windows — using Python 3.8
  ) else (
    echo [ERROR] Windows 7/8.0 but python38 folder is missing.
    echo Download a full Setup from the latest GitHub Actions build.
    echo Current install path: %~dp0
    if exist "%~dp0python\python.exe" echo   [found] python\python.exe  (3.11 — NOT compatible with Win7)
    if not exist "%~dp0python38\python.exe" echo   [missing] python38\python.exe
    pause
    exit /b 1
  )
) else (
  if exist "%~dp0python\python.exe" (
    set "PY=%~dp0python\python.exe"
    echo [OK] Modern Windows — using Python 3.11
  ) else if exist "%~dp0python38\python.exe" (
    set "PY=%~dp0python38\python.exe"
    echo [OK] Fallback Python 3.8
  )
)

if not defined PY (
  where python >nul 2>&1 && for /f "delims=" %%P in ('where python') do (
    if not defined PY set "PY=%%P"
  )
)

if not defined PY (
  echo [ERROR] No suitable Python found.
  pause
  exit /b 1
)

echo [OK] Using: %PY%

REM Block known-bad combo: legacy OS + Python 3.11 path
echo %PY% | findstr /I "\\python\\python.exe" >nul
if not errorlevel 1 (
  if /I "%GGUF_OS_CLASS%"=="legacy" (
    echo [ERROR] Blocked: Python 3.11 cannot run on legacy Windows.
    echo api-ms-win-core-path-l1-1-0.dll is missing on Win7.
    pause
    exit /b 1
  )
)

"%PY%" -c "import sys; print(sys.version)" 2>nul
if errorlevel 1 (
  echo [ERROR] Python failed to start.
  pause
  exit /b 1
)

REM Ensure customtkinter is available (try offline auto-install if missing)
"%PY%" -c "import customtkinter" 2>nul
if errorlevel 1 (
  echo [WARN] customtkinter missing — trying to install...
  "%PY%" -m pip install --disable-pip-version-check customtkinter 2>nul
  "%PY%" -c "import customtkinter" 2>nul
  if errorlevel 1 (
    echo [ERROR] customtkinter is not installed and auto-install failed.
    echo.
    echo Fix options:
    echo   1. Reinstall full offline Setup EXE from latest GitHub Actions
    echo   2. With internet:  "%PY%" -m pip install customtkinter psutil
    echo   3. Run SETUP.bat once online
    echo   4. Then run: scripts\smoke_import.py
    pause
    exit /b 1
  )
  echo [OK] customtkinter installed
)

if exist "scripts\python\app_hub.py" (
  echo Starting GGUF Runner Hub...
  echo (UI opens even without a model — use RAG / web tools or add a GGUF later)
  "%PY%" "%~dp0scripts\python\app_hub.py"
) else if exist "scripts\python\desktop_chat.py" (
  echo Starting desktop chat...
  "%PY%" "%~dp0scripts\python\desktop_chat.py"
) else (
  echo [ERROR] scripts\python\app_hub.py not found.
  pause
  exit /b 1
)
set ERR=%ERRORLEVEL%
if not "%ERR%"=="0" (
  echo.
  echo Hub exited with code %ERR%
  pause
)
exit /b %ERR%
