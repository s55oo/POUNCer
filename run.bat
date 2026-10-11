@echo off
setlocal
cd /d "%~dp0"
python -m pouncer --config config.ini %*
endlocal
