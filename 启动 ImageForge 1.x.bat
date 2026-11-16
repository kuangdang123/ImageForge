@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist "%~dp0ImageForge.exe" (
  start "" "%~dp0ImageForge.exe"
  goto :done
)

where pythonw.exe >nul 2>nul
if not errorlevel 1 (
  start "" pythonw.exe "%~dp0app.pyw" >nul 2>&1
  goto :done
)

where pyw.exe >nul 2>nul
if not errorlevel 1 (
  start "" pyw.exe -3 "%~dp0app.pyw" >nul 2>&1
  goto :done
)

where python.exe >nul 2>nul
if not errorlevel 1 (
  start "" python.exe "%~dp0app.pyw"
  goto :done
)

echo 没有找到 Python，请先安装 Python 3.10+，并把 python.exe 加入 PATH。
pause

:done
endlocal