@echo off
chcp 936 >nul
echo 正在停止南航评论区服务...
taskkill /f /im "南航评论区.exe" >nul 2>nul
if errorlevel 1 (
  echo 服务未在运行
) else (
  echo 已停止
)
timeout /t 2 >nul
exit