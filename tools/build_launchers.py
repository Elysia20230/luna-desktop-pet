# -*- coding: utf-8 -*-
"""
重新生成两个启动用的 .bat 文件。

为什么需要这个生成器？
    Windows 的 cmd.exe 对批处理文件有两个硬要求：
      1) 行尾必须是 CRLF（只有 LF 时，含中文的行会被拆错，命令直接失效）；
      2) 编码要与控制台代码页一致（中文 Windows 是 cp936/GBK）。
    直接用编辑器按 UTF-8 + LF 保存就会踩坑（表现为窗口闪退、报
    "不是内部或外部命令"）。所以统一由这个脚本生成，别手改字节。

用法：
    .venv\\Scripts\\python.exe tools\\build_launchers.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent

# 静默启动：不弹控制台，出错由 launcher.py 负责弹窗
SILENT_BAT = r"""@echo off
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
"""

# 调试启动：保留黑窗口，报错和退出码都留在窗口里
DEBUG_BAT = r"""@echo off
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
"""


def write_bat(path: Path, text: str) -> None:
    """按 cmd.exe 的要求写文件：cp936 + CRLF，无 BOM。"""
    normalized = text.replace("\r\n", "\n").replace("\n", "\r\n")
    with open(path, "w", encoding="cp936", newline="\r\n") as fh:
        fh.write(normalized)
    # 写完立刻回读自检，避免"以为修好了其实没有"
    data = path.read_bytes()
    assert b"\r\n" in data, f"{path.name}: 没有 CRLF"
    assert data.count(b"\n") == data.count(b"\r\n"), f"{path.name}: 混入了 LF-only 行"
    assert not data.startswith(b"\xef\xbb\xbf"), f"{path.name}: 带了 UTF-8 BOM"
    assert b"chcp" not in data.lower(), f"{path.name}: 不应包含 chcp（会打乱 cmd 解析）"
    data.decode("cp936")  # 能用 GBK 解码才算数
    print(f"  已生成 {path.name}  ({len(data)} 字节, cp936, CRLF)")


def main() -> int:
    print(f"输出目录: {ROOT}")
    write_bat(ROOT / "启动露娜.bat", SILENT_BAT)
    write_bat(ROOT / "启动露娜_调试.bat", DEBUG_BAT)
    print("完成：两个启动脚本已按 GBK + CRLF 重新生成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
