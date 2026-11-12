@echo off
setlocal EnableExtensions DisableDelayedExpansion
pushd "%~dp0"
if errorlevel 1 goto directory_error

if not exist ".venv\Scripts\python.exe" goto missing_python
".venv\Scripts\python.exe" -m mkw_tracker %*
set "tracker_exit=%errorlevel%"
if not "%tracker_exit%"=="0" (
    echo.
    echo The tracker stopped with error %tracker_exit%.
    pause
)
popd
exit /b %tracker_exit%

:missing_python
echo The tracker needs its Python environment first.
echo Run setupWindows.bat, or follow docs\windows-setup.md, then try again.
pause
popd
exit /b 1

:directory_error
echo Could not open the tracker folder.
pause
exit /b 1
