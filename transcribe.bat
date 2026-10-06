@echo off
title SheetXML - Drag and Drop Transcriber
cd /d "%~dp0"
if "%~1"=="" (
    echo ==============================================================
    echo  SheetXML Drag-and-Drop Transcriber
    echo ==============================================================
    echo Drag and drop a PDF, image, MusicXML or ABC file onto this batch file
    echo to convert it into a .musicxml file with mandolin TAB.
    echo.
    echo Or press any key to launch the interactive Web GUI...
    pause >nul
    python -B app.py
    goto end
)

echo Converting: %~nx1
python -B app.py "%~1"
echo.
pause

:end
