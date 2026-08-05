@echo off
title Gym Macro - by starlingz
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo.
    echo  !! Python not found !!
    echo  Download Python 3.10+ from https://python.org
    echo  Make sure to check "Add Python to PATH" during install
    echo.
    pause
    exit /b 1
)

echo Checking packages...
python -c "import pyautogui, cv2, numpy, requests, mss, pydirectinput" >nul 2>nul
if %errorlevel% neq 0 (
    echo Installing required packages...
    python -m pip install pyautogui pydirectinput opencv-python numpy requests mss pytesseract --quiet
    echo Done!
)

:: Check if Tesseract is available (for weight OCR)
if exist "%~dp0Tesseract-OCR\tesseract.exe" (
    echo Tesseract found (local)
) else if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
    echo Tesseract found (system)
) else (
    echo.
    echo  NOTE: Tesseract OCR not found - weight reading won't work
    echo  Download from: https://github.com/UB-Mannheim/tesseract/wiki
    echo  Install it or copy the Tesseract-OCR folder into this macro folder
    echo.
)

echo Starting Gym Macro...
python gym_macro_gui.py
if %errorlevel% neq 0 (
    echo.
    echo Something went wrong. Check the error above.
    pause
)
