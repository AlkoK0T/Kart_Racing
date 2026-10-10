@echo off
setlocal
if not defined DATA_FOLDER set "DATA_FOLDER=C:\_work\AK-Cart\dgspeed\CART_COUNTER\online"
pushd "%~dp0"
"%~dp0.venv\Scripts\python.exe" "%~dp0run.py" %*
set "KART_EXIT_CODE=%ERRORLEVEL%"
popd
endlocal & exit /b %KART_EXIT_CODE%
