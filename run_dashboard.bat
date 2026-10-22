@echo off
rem Double-click to open the marketing dashboard at http://localhost:8501
title Offer Engine dashboard
cd /d "%~dp0"
start "" http://localhost:8501
python dashboard.py
pause
