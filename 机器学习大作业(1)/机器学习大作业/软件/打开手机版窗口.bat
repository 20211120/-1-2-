@echo off
chcp 936 >nul
cd /d "%~dp0"
set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" (
  echo Î´ÕÒµ½ Edge ä¯ÀÀÆ÷
  pause
  exit /b
)
netstat -ano | findstr ":5000" | findstr "LISTENING" >nul
if errorlevel 1 (
  start "" "%~dp0ÄÏº½ÆÀÂÛÇø.exe"
  ping -n 7 127.0.0.1 >nul
)
start "" "%EDGE%" --app="http://127.0.0.1:5000/m/" --user-data-dir="%LOCALAPPDATA%\NUAA-Campus-App-Mobile" --no-first-run --no-default-browser-check --window-size=430,870
exit