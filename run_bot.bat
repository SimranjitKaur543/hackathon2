@echo off
rem Double-click to start the Telegram bot. If it ever stops, it restarts after 10 seconds.
rem Close this window (or press Ctrl+C twice) to stop it for good.
title Offer Engine bot
cd /d "%~dp0"

echo Checking libraries...
python -m pip install -q -r requirements.txt
if not exist data\shop.db (
    echo Creating customer data...
    python data\generate_data.py
)

:loop
echo.
echo [%date% %time%] Starting bot...
python bot.py
echo [%date% %time%] Bot stopped. Restarting in 10 seconds (close this window to stop)...
timeout /t 10 /nobreak >nul
goto loop
