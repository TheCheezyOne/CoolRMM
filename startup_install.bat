@echo off
:: ============================================================================
:: Cool RMM Agent — GPO Startup Script
:: Runs as SYSTEM at every boot, before user login
:: Self-healing: installs, updates, and restarts the agent automatically
::
:: DEPLOY INSTRUCTIONS:
::   1. Edit SHARE_PATH below to point to your network share
::   2. Place this script + nssm.exe + cool_rmm_agent.exe on that share
::   3. In Group Policy: Computer Configuration > Windows Settings >
::      Scripts (Startup/Shutdown) > Startup > Add this script
::   4. Link the GPO to the OU containing your 142 machines
:: ============================================================================

:: ── CONFIG — edit these ─────────────────────────────────────────────────────
set SHARE_PATH=\\YOUR_DC\SYSVOL\YourDomain\CoolRMM
set INSTALL_DIR=C:\CoolRMM
set SERVICE_NAME=CoolRMMAgent
set AGENT_EXE=cool_rmm_agent.exe
set NSSM_EXE=nssm.exe
set LOG_FILE=C:\CoolRMM\deploy.log
set VERSION_FILE=C:\CoolRMM\version.txt
set SHARE_VERSION_FILE=%SHARE_PATH%\version.txt
:: ─────────────────────────────────────────────────────────────────────────────

:: Timestamp for log
for /f "tokens=*" %%i in ('powershell -NoProfile -Command "Get-Date -Format \"yyyy-MM-dd HH:mm:ss\""') do set TS=%%i

:: Ensure install directory exists
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

echo [%TS%] === Cool RMM startup check on %COMPUTERNAME% === >> %LOG_FILE%

:: ── STEP 1: Check for version update ────────────────────────────────────────
set LOCAL_VER=0
set SHARE_VER=0

if exist "%VERSION_FILE%" (
    set /p LOCAL_VER=< "%VERSION_FILE%"
)
if exist "%SHARE_VERSION_FILE%" (
    set /p SHARE_VER=< "%SHARE_VERSION_FILE%"
)

if "%LOCAL_VER%" == "%SHARE_VER%" (
    echo [%TS%] Agent is current [v%LOCAL_VER%] >> %LOG_FILE%
    goto CHECK_SERVICE
)

:: Version mismatch — update the agent
echo [%TS%] Updating agent: local=v%LOCAL_VER% share=v%SHARE_VER% >> %LOG_FILE%

:: Stop the service before replacing the exe
sc query %SERVICE_NAME% >nul 2>&1
if %ERRORLEVEL% == 0 (
    echo [%TS%] Stopping service for update... >> %LOG_FILE%
    net stop %SERVICE_NAME% >nul 2>&1
    timeout /t 3 /nobreak >nul
)

:: Copy updated files from share
copy /Y "%SHARE_PATH%\%AGENT_EXE%" "%INSTALL_DIR%\%AGENT_EXE%" >nul
if %ERRORLEVEL% NEQ 0 (
    echo [%TS%] ERROR: Could not copy agent from share. Check network/permissions. >> %LOG_FILE%
    goto CHECK_SERVICE
)

copy /Y "%SHARE_PATH%\%NSSM_EXE%" "%INSTALL_DIR%\%NSSM_EXE%" >nul
copy /Y "%SHARE_VERSION_FILE%" "%VERSION_FILE%" >nul
echo [%TS%] Agent updated to v%SHARE_VER% >> %LOG_FILE%

:: ── STEP 2: Check service exists ────────────────────────────────────────────
:CHECK_SERVICE
sc query %SERVICE_NAME% >nul 2>&1
if %ERRORLEVEL% == 0 (
    echo [%TS%] Service exists >> %LOG_FILE%
    goto CHECK_RUNNING
)

:: Service missing — install it
echo [%TS%] Service not found. Installing... >> %LOG_FILE%

if not exist "%INSTALL_DIR%\%NSSM_EXE%" (
    copy /Y "%SHARE_PATH%\%NSSM_EXE%" "%INSTALL_DIR%\%NSSM_EXE%" >nul
)
if not exist "%INSTALL_DIR%\%AGENT_EXE%" (
    copy /Y "%SHARE_PATH%\%AGENT_EXE%" "%INSTALL_DIR%\%AGENT_EXE%" >nul
)

"%INSTALL_DIR%\%NSSM_EXE%" install %SERVICE_NAME% "%INSTALL_DIR%\%AGENT_EXE%"
"%INSTALL_DIR%\%NSSM_EXE%" set %SERVICE_NAME% Start SERVICE_AUTO_START
"%INSTALL_DIR%\%NSSM_EXE%" set %SERVICE_NAME% AppRestartDelay 10000
"%INSTALL_DIR%\%NSSM_EXE%" set %SERVICE_NAME% AppStdout "%INSTALL_DIR%\agent_stdout.log"
"%INSTALL_DIR%\%NSSM_EXE%" set %SERVICE_NAME% AppStderr "%INSTALL_DIR%\agent_stderr.log"
"%INSTALL_DIR%\%NSSM_EXE%" set %SERVICE_NAME% Description "Cool RMM monitoring agent"

if %ERRORLEVEL% == 0 (
    echo [%TS%] Service installed successfully >> %LOG_FILE%
) else (
    echo [%TS%] ERROR: Service install failed [code %ERRORLEVEL%] >> %LOG_FILE%
)

:: ── STEP 3: Check service is running ────────────────────────────────────────
:CHECK_RUNNING
for /f "tokens=3 delims=: " %%H in ('sc query %SERVICE_NAME% ^| findstr "STATE"') do set STATE=%%H

if "%STATE%" == "RUNNING" (
    echo [%TS%] Service is RUNNING — all good >> %LOG_FILE%
    goto CLEANUP
)

:: Not running — start it
echo [%TS%] Service state: %STATE% — starting... >> %LOG_FILE%
net start %SERVICE_NAME% >nul 2>&1

timeout /t 3 /nobreak >nul

for /f "tokens=3 delims=: " %%H in ('sc query %SERVICE_NAME% ^| findstr "STATE"') do set STATE=%%H
echo [%TS%] Service state after start attempt: %STATE% >> %LOG_FILE%

:: ── CLEANUP: Trim log file if it gets large (keep last 500 lines) ────────────
:CLEANUP
for /f %%A in ('powershell -NoProfile -Command "(Get-Content '%LOG_FILE%').Count"') do set LINECOUNT=%%A
if %LINECOUNT% GTR 500 (
    powershell -NoProfile -Command ^
      "$lines = Get-Content '%LOG_FILE%'; $lines[-500..-1] | Set-Content '%LOG_FILE%'"
    echo [%TS%] Log trimmed to 500 lines >> %LOG_FILE%
)

echo [%TS%] Startup check complete >> %LOG_FILE%
exit /b 0
