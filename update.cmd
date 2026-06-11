@echo off
REM Agent Template - update entrypoint (Windows cmd).
REM Wraps update.ps1 with -ExecutionPolicy Bypass so it works on Windows
REM PowerShell 5.1 with the default Restricted policy. Bypass is
REM process-scoped only and does not change any user/machine policy.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0update.ps1" %*
exit /b %ERRORLEVEL%
