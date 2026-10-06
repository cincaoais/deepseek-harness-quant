@echo off
REM Daily US / Bursa Malaysia run: update data, then build decision cards.  Usage: market_daily.cmd US
cd /d "%~dp0.."
set MARKET=%1
if "%MARKET%"=="" set MARKET=US
python data\market_update.py --market %MARKET% --universe all --stocks-only --macro || exit /b 1
python strategy\market_pitch.py --market %MARKET%
