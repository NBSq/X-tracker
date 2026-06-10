@echo off
setlocal
cd /d "%~dp0.."
python -m app.main --mode rss --mock-ai
endlocal
