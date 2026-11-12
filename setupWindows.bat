@echo off
setlocal EnableExtensions DisableDelayedExpansion
pushd "%~dp0"
if errorlevel 1 goto directory_error

rem A new Explorer window may not yet have the PATH set by the uv installer.
where uv.exe >nul 2>nul
if not errorlevel 1 goto run_setup
if exist "%USERPROFILE%\.local\bin\uv.exe" set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv.exe >nul 2>nul
if not errorlevel 1 goto run_setup
if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe" set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
where uv.exe >nul 2>nul
if errorlevel 1 goto missing_uv

:run_setup
echo MKW-Mogi-Tracker guided setup
echo Preparing Python 3.12, then opening the reader chooser. This may download Python.
echo For manual scoring only, use: setupWindows.bat --manual-only
echo.
uv.exe run --no-project --python 3.12 python tools/setup.py %*
set "setup_exit=%errorlevel%"
echo.
if not "%setup_exit%"=="0" (
    echo Setup stopped with error %setup_exit%. Read the message above.
    echo See docs\windows-setup.md. You can rerun this script to retry.
) else (
    echo Setup command finished. If installation completed, use startWindows.bat to play.
)
pause
popd
exit /b %setup_exit%

:missing_uv
echo Setup needs uv, the tool that downloads Python and the tracker dependencies.
echo Install uv using the steps in docs\windows-setup.md, then try again.
echo In a new PowerShell window: winget install --id astral-sh.uv -e
pause
popd
exit /b 1

:directory_error
echo Could not open the tracker folder.
pause
exit /b 1
