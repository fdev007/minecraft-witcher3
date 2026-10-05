@echo off
rem Minecraft x The Witcher 3. Double-click: start everything. From a console: mcw install ^| start ^| stop ^| status ^| doctor ^| uninstall
setlocal
if "%~1"=="" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0mcw.ps1" start
  pause
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0mcw.ps1" %*