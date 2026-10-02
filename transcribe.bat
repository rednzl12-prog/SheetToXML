@echo off
title SheetXML - Drag and Drop Transcriber
cd /d "%~dp0"
if "%~1"=="" (
    echo ==============================================================
    echo  SheetXML Drag-and-Drop Transcriber
    echo ==============================================================
    echo Drag and drop any PDF or sheet music image onto this batch file
    echo to transcribe it immediately into a .musicxml file!
    echo.
    echo Or press any key to launch the interactive Web GUI...
    pause >nul
    python app.py
    goto end
)

echo Transcribing: %~nx1
python app.py "%~1"
echo.
pause

:end
