@echo off
title Maritime Assessment
cd /d "%~dp0"
py start.py
if errorlevel 1 python start.py
pause
