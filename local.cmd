@echo off
REM Agent Template - local dev entrypoint (Windows cmd).
REM Wraps local.ps1 with -ExecutionPolicy Bypass so it works on Windows
REM PowerShell 5.1 with the default Restricted policy. Bypass is
REM process-scoped only and does not change any user/machine policy.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0local.ps1" %*
exit /b %ERRORLEVEL%
