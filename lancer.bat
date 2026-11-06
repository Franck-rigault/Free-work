@echo off
cd /d "%~dp0"
python fetch_offers.py
start "" http://localhost:8765
python -m http.server 8765 --directory docs
pause
