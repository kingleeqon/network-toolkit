@echo off
setlocal
rem ============================================================
rem  Register / remove a Windows Scheduled Task that runs the
rem  daily pipeline: iface_collect -> crc_compare
rem
rem  Usage:
rem    install_task.bat [config_json] [HH:MM] [python_exe] [dry]
rem    install_task.bat --remove
rem
rem  Examples:
rem    install_task.bat
rem    install_task.bat scripts\daily_config.json 08:30
rem    install_task.bat scripts\daily_config.json 08:30 "C:\Python314\python.exe"
rem    install_task.bat scripts\daily_config.json 08:30 "" dry
rem ============================================================

set "TASKNAME=NetToolkit-DailyCRC"
set "HERE=%~dp0"
pushd "%HERE%.."
set "ROOT=%CD%"
popd
set "WRAPPER=%HERE%_run_daily.bat"

if /I "%~1"=="--remove" goto :remove

set "CONFIG=%~1"
set "ATTIME=%~2"
set "PYEXE=%~3"
set "MODE=%~4"

if "%CONFIG%"=="" set "CONFIG=%HERE%daily_config.json"
if "%ATTIME%"=="" set "ATTIME=08:30"
if "%PYEXE%"=="" set "PYEXE=python"

rem resolve relative config path against the project root
if not exist "%CONFIG%" (
  if exist "%ROOT%\%CONFIG%" set "CONFIG=%ROOT%\%CONFIG%"
)
if not exist "%CONFIG%" (
  echo [ERR] config not found: %CONFIG%
  echo       create it first, e.g. copy scripts\daily_config.json and edit paths
  goto :fail
)
echo [1/4] config: %CONFIG%

rem ---- 2. generate the wrapper bat (avoids nested-quote hell in schtasks) ----
rem  NOTE: no redirection inside the wrapper. daily_crc.py writes its own log to
rem        logs\daily_crc_YYYYMMDD.log, and an extra ">>" here would be parsed by
rem        cmd as a redirect of the echo line itself (which silently truncates it).
> "%WRAPPER%" echo @echo off
>> "%WRAPPER%" echo cd /d "%ROOT%"
>> "%WRAPPER%" echo "%PYEXE%" "%HERE%daily_crc.py" --config "%CONFIG%"

echo [2/4] wrapper written: %WRAPPER%

rem ---- 3. sanity check: interpreter + deps ----
"%PYEXE%" -c "import pandas, openpyxl" >nul 2>&1
if errorlevel 1 (
  echo [ERR] "%PYEXE%" cannot import pandas/openpyxl.
  echo       Pass the full path of a working python.exe as the 3rd argument,
  echo       e.g. install_task.bat scripts\daily_config.json 08:30 "C:\Python314\python.exe"
  goto :fail
)
echo [3/4] python ok: %PYEXE%

rem ---- 4. register ----
set "SCHTASKS_CMD=schtasks /Create /F /SC DAILY /ST %ATTIME% /TN "%TASKNAME%" /TR "%WRAPPER%""
if /I "%MODE%"=="dry" (
  echo [4/4] dry mode, would run:
  echo       %SCHTASKS_CMD%
  goto :done
)
%SCHTASKS_CMD%
if errorlevel 1 goto :fail
echo [4/4] task registered: %TASKNAME%  (daily at %ATTIME%)
echo.
echo   config : %CONFIG%
echo   log    : %ROOT%\logs\daily_crc_YYYYMMDD.log
echo   test   : python scripts\daily_crc.py --config "%CONFIG%" --dry-run
echo   remove : install_task.bat --remove
goto :done

:remove
schtasks /Delete /F /TN "%TASKNAME%"
if errorlevel 1 (
  echo [ERR] delete failed ^(task may not exist^)
  goto :fail
)
echo task removed: %TASKNAME%
goto :done

:fail
endlocal
exit /b 1

:done
endlocal
exit /b 0
