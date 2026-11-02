@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws=New-Object -ComObject WScript.Shell; $d=(Get-Location).Path; $exe=Join-Path $d '南航评论区.exe'; $ico=(Join-Path $d '封图.ico')+',0'; $a=$ws.CreateShortcut((Join-Path $d '打开-电脑版.lnk')); $a.TargetPath=$exe; $a.WorkingDirectory=$d; $a.IconLocation=$ico; $a.Description='南航评论区 · 电脑版'; $a.Save(); $b=$ws.CreateShortcut((Join-Path $d '打开-手机版.lnk')); $b.TargetPath=$exe; $b.Arguments='--mobile'; $b.WorkingDirectory=$d; $b.IconLocation=$ico; $b.Description='南航评论区 · 手机版'; $b.Save(); Write-Host '快捷方式已修复'"
pause