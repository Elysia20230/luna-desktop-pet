@echo off
setlocal
rem ============================================================
rem  露娜桌宠 调试启动（保留黑色窗口，能看到完整报错）
rem  本文件由 tools\build_launchers.py 生成：GBK 编码 + CRLF 行尾。
rem ============================================================
cd /d "%~dp0"

set "PY="
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY for %%i in (python.exe) do if not defined PY set "PY=%%~$PATH:i"

if not defined PY (
    echo.
    echo 没找到 Python 运行环境（.venv）。
    echo 请先按 README.md 的「环境准备」一节装好依赖。
    echo.
    pause
    exit /b 1
)

"%PY%" "%~dp0launcher.py" --console
set "CODE=%ERRORLEVEL%"
echo.
echo ---- 程序已退出，退出码 %CODE% ，按任意键关闭 ----
pause >nul
exit /b %CODE%
