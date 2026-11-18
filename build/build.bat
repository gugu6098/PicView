@echo off
rem Build PicView: PSD thumbnail handler -> onedir app -> single-file setup.
rem Requires: zig (pip install ziglang), pyinstaller, python on PATH.
setlocal
cd /d "%~dp0.."
set "ROOT=%CD%"
set "PY=python"

echo [1/4] compiling PSD thumbnail handler...
pushd "%ROOT%\src\thumb"
zig c++ -target x86_64-windows-gnu -shared -O2 -o PsdThumb.dll psdthumb.cpp psdthumb.def -lole32 -lgdi32
if errorlevel 1 goto :fail
popd

echo [2/4] building onedir app...
pushd "%ROOT%\src"
%PY% -m PyInstaller --onedir --noconsole --name PicView --icon "%ROOT%\src\assets\picview.ico" ^
  --add-data "%ROOT%\src\PicView.html;." ^
  --add-data "%ROOT%\src\assets\picview.ico;." ^
  --add-data "%ROOT%\src\thumb\PsdThumb.dll;." ^
  --distpath "%ROOT%\dist" --workpath "%ROOT%\build\_work" --specpath "%ROOT%\build" ^
  picview_launcher.py
if errorlevel 1 goto :fail
popd

echo [3/4] packing payload...
%PY% "%ROOT%\build\make_payload.py" "%ROOT%\dist\PicView" "%ROOT%\build\payload.zip"
if errorlevel 1 goto :fail

echo [4/4] building single-file setup...
pushd "%ROOT%\src"
%PY% -m PyInstaller --onefile --noconsole --name PicView-Setup --icon "%ROOT%\src\assets\picview.ico" ^
  --add-data "%ROOT%\build\payload.zip;." ^
  --distpath "%ROOT%\dist" --workpath "%ROOT%\build\_work2" --specpath "%ROOT%\build" ^
  picview_setup.py
if errorlevel 1 goto :fail
popd

echo.
echo Done:
echo   %ROOT%\dist\PicView-Setup.exe      single file to share
echo   %ROOT%\dist\PicView\PicView.exe    installed layout, fastest start
exit /b 0

:fail
echo.
echo Build failed.
exit /b 1
