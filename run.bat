@echo off
title SheetXML - Gemini Sheet Music to MusicXML Transcriber
cd /d "%~dp0"
echo ==============================================================
echo  SheetXML - Gemini 2.5 AI Sheet Music to MusicXML Transcriber
echo ==============================================================
echo Starting local web server and opening browser...
python app.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Failed to run SheetXML.
    echo Please make sure Python is installed and run: pip install -r requirements.txt
    pause
)
