# -*- coding: utf-8 -*-
"""
露娜桌宠的启动入口 —— 双击 .bat 时真正跑起来的就是这个文件。

为什么不让 .bat 直接启动 main.py？
    双击 .bat 时如果程序在导入阶段就崩了（比如缺库、配置文件坏掉），
    pythonw 没有控制台，窗口闪一下就消失，用户什么都看不到。
    这个文件负责"兜住"所有异常：写日志 + 弹一个中文提示窗。

用法（一般不用手敲，由两个 .bat 调用）：
    launcher.py                 正常启动
    launcher.py --console       保留控制台输出（调试 .bat 用）
    launcher.py --check         只做环境体检，不启动界面
    launcher.py --no-gui        出错时不弹窗，只写日志（自动化测试用）
    launcher.py --simulate-error 故意造一个错，验证错误提示链路是否正常
"""
from __future__ import annotations

import argparse
import os
import platform
import sys
import traceback
from datetime import datetime
from pathlib import Path

# 项目根目录 = 本文件所在目录（不依赖"从哪个目录启动"）
ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "启动错误.txt"


# ---------------------------------------------------------------- 小工具
def _text_stdout() -> None:
    """把标准输出切成 UTF-8，避免中文在 GBK 控制台里变成问号。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _env_report() -> str:
    """环境快照：出问题时这是最有用的上下文。"""
    lines = [
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Python: {sys.version.split()[0]}  实现: {platform.python_implementation()}",
        f"解释器: {sys.executable}",
        f"工作目录: {Path.cwd()}",
        f"项目目录: {ROOT}",
        f"系统: {platform.platform()}",
    ]
    # 逐个试探关键依赖，缺哪个一眼就能看出来
    for mod in ("PySide6", "httpx", "PIL", "chromadb"):
        try:
            __import__(mod)
            lines.append(f"依赖 {mod}: 可用")
        except Exception as exc:  # 这里故意不抛，只记录
            lines.append(f"依赖 {mod}: 缺失（{type(exc).__name__}: {exc}）")
    return "\n".join(lines)


def _write_log(tb: str) -> Path:
    """把错误写进 logs/启动错误.txt（覆盖写，只保留最近一次）。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text(
        f"{_env_report()}\n\n===== 错误详情 =====\n{tb}\n",
        encoding="utf-8",
        errors="replace",
    )
    return LOG_FILE


def _app_root() -> Path:
    """
    程序主体在哪。

    源码运行时 = launcher.py 旁边；
    打包成 exe 后 = PyInstaller 的解包目录（sys._MEIPASS，只读，退出即删）。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else ROOT


def _preflight() -> str | None:
    """启动前的关键依赖检查：返回人话提示；没问题就返回 None。

    放在 import main 之前，这样"缺界面库"不会变成一串 ModuleNotFoundError。
    """
    try:
        import PySide6  # noqa: F401
    except Exception as exc:
        return (
            "缺少界面库 PySide6，露娜没法站起来。\n\n"
            "请在项目目录（本文件所在目录）打开 PowerShell，执行：\n"
            "    .venv\\Scripts\\python.exe -m pip install -r requirements.txt "
            "--index-url https://mirrors.aliyun.com/pypi/simple/\n\n"
            f"原始错误：{exc}"
        )
    # 打包后程序主体被收进 exe 里，磁盘上没有 main.py，别在这里误报
    if not getattr(sys, "frozen", False) and not (ROOT / "main.py").exists():
        return f"找不到程序主体 main.py，请确认这个文件还在：{ROOT / 'main.py'}"
    return None


def _show_error_dialog(body: str, log_path: Path) -> None:
    """用 tkinter 弹一个中文提示窗（不依赖 PySide6，缺界面库时也能提示）。"""
    try:
        import tkinter as tk
        from tkinter import font as tkfont
    except Exception:
        return  # 连 tk 都没有就只能靠日志了

    try:
        # 声明 DPI 感知：否则在 150%/200% 缩放下，窗口会被系统整体放大而超出屏幕
        try:
            from ctypes import windll

            windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                windll.user32.SetProcessDPIAware()
            except Exception:
                pass

        win = tk.Tk()
        win.title("露娜桌宠 · 启动失败")
        # 居中并夹在屏幕内（高分屏缩放时固定 780 像素宽会跑到屏幕外）
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w, h = min(760, sw - 80), min(520, sh - 120)
        win.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
        win.minsize(520, 300)
        win.configure(bg="#241d38")
        win.attributes("-topmost", True)

        ui_font = tkfont.nametofont("TkDefaultFont")
        ui_font.configure(family="Microsoft YaHei UI", size=10)

        # 先把底部按钮区和日志路径占住位置（side="bottom"），
        # 否则文本区默认 24 行会把它们挤出窗口
        bar = tk.Frame(win, bg="#241d38")
        bar.pack(side="bottom", fill="x", padx=14, pady=10)

        tk.Label(
            win, text=f"完整日志：{log_path}", bg="#241d38", fg="#9a8fbe",
            font=("Microsoft YaHei UI", 9),
        ).pack(side="bottom", anchor="w", padx=14)

        tk.Label(
            win, text="露娜没能站起来……下面是原因：",
            bg="#241d38", fg="#f0eaff", font=("Microsoft YaHei UI", 13, "bold"),
        ).pack(side="top", anchor="w", padx=14, pady=(12, 6))

        box = tk.Text(
            win, wrap="word", bg="#1b1630", fg="#ffd9e0", height=10,
            insertbackground="#f0eaff", relief="flat", padx=10, pady=8,
            font=("Microsoft YaHei UI", 9),
        )
        box.pack(fill="both", expand=True, padx=14, pady=6)
        box.insert("1.0", body.strip()[-3000:])
        box.configure(state="disabled")

        def open_dir() -> None:
            try:
                if log_path.exists():
                    os.startfile(str(log_path))  # 直接打开日志文件
                else:
                    os.startfile(str(LOG_DIR))
            except Exception:
                pass

        tk.Button(bar, text="打开日志", command=open_dir, bg="#8a6fc4", fg="white",
                  relief="flat", padx=16, pady=4).pack(side="right", padx=(8, 0))
        tk.Button(bar, text="关闭", command=win.destroy, bg="#4b3f68", fg="white",
                  relief="flat", padx=16, pady=4).pack(side="right")

        win.mainloop()
    except Exception:
        pass  # 弹窗本身出错也不能再炸一次


# ---------------------------------------------------------------- 体检
def _run_check() -> int:
    """--check：只检查环境，不启动界面。返回 0 表示一切正常。"""
    problems = 0
    print("=== 露娜桌宠 环境体检 ===")
    print(f"Python: {sys.version.split()[0]}  ({sys.executable})")
    print(f"项目目录: {ROOT}")

    if sys.version_info < (3, 10):
        print("[!!] Python 版本过低，需要 3.10 以上")
        problems += 1
    else:
        print("[OK] Python 版本")

    for mod in ("PySide6", "httpx", "PIL"):
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", "?")
            print(f"[OK] {mod} {ver}")
        except Exception as exc:
            print(f"[!!] {mod} 导入失败：{type(exc).__name__}: {exc}")
            problems += 1

    try:
        import chromadb  # 可选依赖，只在记忆检索时用
        print(f"[OK] chromadb {getattr(chromadb, '__version__', '?')}")
    except Exception as exc:
        print(f"[--] chromadb 不可用（会退回 SQLite 向量库）：{exc}")

    catalog = ROOT / "assets" / "sprite_catalog.json"
    print(("[OK] 立绘清单 " if catalog.exists() else "[!!] 缺少立绘清单 ") + str(catalog))
    problems += 0 if catalog.exists() else 1

    try:
        sys.path.insert(0, str(ROOT))
        from app.config import Config  # 延迟导入，避免体检时依赖 GUI

        cfg = Config()
        sprite_dir = cfg.sprite_dir()
        pngs = len(list(sprite_dir.glob("*.png"))) if sprite_dir.exists() else 0
        print(f"{'[OK]' if pngs else '[!!]'} 立绘目录 {sprite_dir}（{pngs} 张 png）")
        problems += 0 if pngs else 1
        print(f"[OK] 配置文件 {cfg.path}")
        print(f"[--] 对话接口 Key：{'已填' if cfg.api_key else '未填（离线台词模式）'}")
        print(f"[--] 记忆向量 Key：{'已填' if cfg.embedding_key else '未填（本地哈希降级）'}")
    except Exception as exc:
        print(f"[!!] 配置读取失败：{type(exc).__name__}: {exc}")
        problems += 1

    print(f"=== 体检结束：{problems} 个问题 ===" if problems else "=== 体检通过，可以启动 ===")
    return 1 if problems else 0


# ---------------------------------------------------------------- 主流程
def main() -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--console", action="store_true", help="保留控制台输出")
    parser.add_argument("--no-gui", action="store_true", help="出错时不弹窗，只写日志")
    parser.add_argument("--check", action="store_true", help="只做环境体检")
    parser.add_argument("--simulate-error", action="store_true", help="演练：故意抛错")
    args = parser.parse_args()

    _text_stdout()
    os.chdir(ROOT)  # 统一工作目录，配置与数据都相对项目根
    sys.path.insert(0, str(ROOT))

    if args.check:
        return _run_check()

    try:
        if args.simulate_error:
            raise ImportError("（演练）故意制造的导入错误：用来验证错误提示与日志是否正常。")

        # 先做关键检查：缺库/缺文件时给出人话提示，而不是一串 traceback
        hint = _preflight()
        if hint:
            raise RuntimeError(hint)

        import main as pet_main  # 真正的程序入口

        return int(pet_main.main() or 0)
    except SystemExit as exc:  # 程序自己调用了 sys.exit()
        return int(exc.code or 0) if isinstance(exc.code, int) else 1
    except BaseException:
        tb = traceback.format_exc()
        log_path = _write_log(tb)
        if not args.no_gui:
            _show_error_dialog(tb, log_path)
        if args.console or sys.stderr is not None:
            print(f"\n启动失败，已写入日志：{log_path}", file=sys.stderr or sys.stdout)
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
