@echo off
rem Build PicView.exe (and the PSD thumbnail handler it embeds).
rem Requires: zig (pip install ziglang), pyinstaller, python on PATH.
setlocal enabledelayedexpansion
cd /d "%~dp0.."
set "ROOT=%CD%"

echo [1/2] compiling PSD thumbnail handler...
pushd "%ROOT%\src\thumb"
zig c++ -target x86_64-windows-gnu -shared -O2 -o PsdThumb.dll psdthumb.cpp psdthumb.def -lole32 -lgdi32
if errorlevel 1 goto :fail
popd

echo [2/2] packaging PicView.exe...
pushd "%ROOT%\src"
pyinstaller --onefile --noconsole --icon "%ROOT%\src\assets\picview.ico" ^
  --add-data "%ROOT%\src\PicView.html;." ^
  --add-data "%ROOT%\src\assets\picview.ico;." ^
  --add-data "%ROOT%\src\thumb\PsdThumb.dll;." ^
  --distpath "%ROOT%\dist" --workpath "%ROOT%\build\_work" --specpath "%ROOT%\build" ^
  picview_launcher.py
if errorlevel 1 goto :fail
popd

if exist "%ROOT%\dist\picview_launcher.exe" move /y "%ROOT%\dist\picview_launcher.exe" "%ROOT%\dist\PicView.exe" >nul
echo.
echo Done: %ROOT%\dist\PicView.exe
exit /b 0

:fail
echo.
echo Build failed.
exit /b 1
