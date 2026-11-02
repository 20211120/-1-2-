@echo off
chcp 936 >nul
cd /d "%~dp0"
set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" (
  echo 未找到 Edge 浏览器
  pause
  exit /b
)
rem 先确保服务在运行
netstat -ano | findstr ":5000" | findstr "LISTENING" >nul
if errorlevel 1 (
  start "" "%~dp0南航评论区.exe"
  ping -n 7 127.0.0.1 >nul
)
start "" "%EDGE%" --app="http://127.0.0.1:5000/" --user-data-dir="%LOCALAPPDATA%\NUAA-Campus-App" --no-first-run --no-default-browser-check --window-size=1100,760
exit