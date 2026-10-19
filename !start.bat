@echo off
title Play Engine Launcher
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "!start.ps1"
pause
