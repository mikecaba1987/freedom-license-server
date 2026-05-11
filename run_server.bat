@echo off
title Freedom Downloader License Server
cd /d "%~dp0"

echo Installing dependencies...
python -m pip install -r requirements.txt

echo.
echo Starting license server...
echo Server URL:
echo http://127.0.0.1:5000
echo.
python app.py

pause
