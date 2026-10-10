@echo off
setlocal
pushd "%~dp0"
"%~dp0.venv\Scripts\python.exe" -m behave %*
set "KART_EXIT_CODE=%ERRORLEVEL%"
popd
endlocal & exit /b %KART_EXIT_CODE%
