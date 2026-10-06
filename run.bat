@echo off
title SheetXML - Sheet Music to MusicXML + Mandolin TAB
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo ==============================================================
echo  SheetXML - Sheet Music to MusicXML + Mandolin TAB
echo ==============================================================
echo Starting local web server and opening browser...
python -B app.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Failed to run SheetXML.
    echo Please make sure Python is installed and run: pip install -r requirements.txt
    pause
)
