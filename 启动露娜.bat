@echo off
setlocal
rem ============================================================
rem  露娜桌宠 启动脚本
rem  本文件由 tools\build_launchers.py 生成：
rem  必须是 GBK 编码 + CRLF 行尾，手改前请先读 docs\修改指南.md。
rem  如果闪一下就没了，改用「启动露娜_调试.bat」查看原因。
rem ============================================================
cd /d "%~dp0"

rem 优先用项目自带的虚拟环境，其次找系统 PATH 里的 pythonw
set "PYW="
if exist "%~dp0.venv\Scripts\pythonw.exe" set "PYW=%~dp0.venv\Scripts\pythonw.exe"
if not defined PYW for %%i in (pythonw.exe) do if not defined PYW set "PYW=%%~$PATH:i"

if defined PYW (
    start "LunaPet" "%PYW%" "%~dp0launcher.py"
    exit /b 0
)

echo.
echo 没找到 Python 运行环境（.venv）。
echo 请先按 README.md 的「环境准备」一节装好依赖，再双击本文件。
echo.
pause
exit /b 1
