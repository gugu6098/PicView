@echo off
title PicView - refresh thumbnail cache
echo Clearing Windows thumbnail cache and restarting Explorer...
taskkill /f /im explorer.exe >nul 2>&1
timeout /t 2 /nobreak >nul
del /f /q "%LOCALAPPDATA%\Microsoft\Windows\Explorer\thumbcache_*.db" >nul 2>&1
start "" explorer.exe
echo Done. PSD thumbnails will be regenerated on next view.
timeout /t 3 /nobreak >nul
