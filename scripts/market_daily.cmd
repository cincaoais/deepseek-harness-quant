@echo off
REM Daily US / Bursa Malaysia run: update data, build decision cards, print holdings and exit alerts,
REM then ask the AI personas (skipped when no LLM key is set).  Usage: market_daily.cmd US
cd /d "%~dp0.."
set MARKET=%1
if "%MARKET%"=="" set MARKET=US
python data\market_update.py --market %MARKET% --universe all --stocks-only --macro || exit /b 1
python strategy\market_pitch.py --market %MARKET%
python strategy\market_positions.py --market %MARKET%
python strategy\market_personas.py --market %MARKET% --consult
