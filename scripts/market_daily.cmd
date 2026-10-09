@echo off
REM Daily US / Bursa Malaysia run: update data, build decision cards, sync moomoo orders and fills
REM (skipped in preview mode), print holdings and exit alerts, then ask the AI personas (skipped when
REM no LLM key is set). Orders are only sent from the US / MY tab.  Usage: market_daily.cmd US
cd /d "%~dp0.."
set MARKET=%1
if "%MARKET%"=="" set MARKET=US
python data\market_update.py --market %MARKET% --universe all --stocks-only --macro || exit /b 1
python strategy\market_pitch.py --market %MARKET%
python strategy\market_orders.py --market %MARKET% --sync
python strategy\market_positions.py --market %MARKET%
python strategy\market_personas.py --market %MARKET% --consult
