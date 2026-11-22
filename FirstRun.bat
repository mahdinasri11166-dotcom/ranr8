@echo off
REM GGUF Runner First Run — hardened for Windows 7
setlocal EnableExtensions
cd /d "%~dp0"
title GGUF Runner — First Run

REM Prefer OEM/ANSI for critical messages on legacy consoles to avoid "cannot write to device"
call "%~dp0scripts\detect_windows.bat" 2>nul
call "%~dp0scripts\install_detect_windows.bat" 2>nul

if not exist "%~dp0profiles" mkdir "%~dp0profiles" 2>nul
echo GGUF_OS_CLASS=%GGUF_OS_CLASS%> "%~dp0profiles\os_class.txt" 2>nul

echo.
echo ============================================
echo  GGUF Runner First Run
echo  OS class: %GGUF_OS_CLASS%
echo ============================================

if /I "%GGUF_OS_CLASS%"=="legacy" (
  if not exist "%~dp0python38\python.exe" (
    echo [ERROR] Windows 7/8.0 needs python38 folder.
    echo Re-download the full Setup EXE from GitHub Actions.
    pause
    exit /b 1
  )
  echo [OK] python38 present
) else (
  if exist "%~dp0python\python.exe" (
    echo [OK] python 3.11 present
  ) else if exist "%~dp0python38\python.exe" (
    echo [OK] python38 fallback present
  )
)

REM Ensure required folders exist (UI will start even without models)
if not exist "models" mkdir models 2>nul
if not exist "books" mkdir books 2>nul
if not exist "sessions" mkdir sessions 2>nul
if not exist "workspace" mkdir workspace 2>nul
if not exist "memory" mkdir memory 2>nul

REM If no model yet, just inform (do not block UI)
dir /b "models\*.gguf" >nul 2>&1
if errorlevel 1 (
  echo.
  echo [INFO] No .gguf model found in models\
  echo        UI will still open. You can use RAG (books) and web tools.
  echo        Place a GGUF file in models\ later to enable full chat.
  echo.
)

echo.
echo [..] Quick import check...
if /I "%GGUF_OS_CLASS%"=="legacy" (
  if exist "%~dp0python38\python.exe" "%~dp0python38\python.exe" "%~dp0scripts\smoke_import.py" 2>nul
) else (
  if exist "%~dp0python\python.exe" "%~dp0python\python.exe" "%~dp0scripts\smoke_import.py" 2>nul
)
echo.
call "%~dp0CHAT.bat"
