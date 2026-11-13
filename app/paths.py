# -*- coding: utf-8 -*-
"""
运行目录解析：让同一份代码在"源码运行"和"打包成 exe 后运行"都找得到文件。

三种情况：
1. 源码运行（python main.py）：项目根 = LunaPet 目录，一切照旧。
2. 打包成 exe（PyInstaller onedir）：exe 自己在 LunaPet\\ 里，
   · 程序自带、只读的东西（assets/、config 样例）在 exe 同级或 _MEIPASS 里；
   · 会写的东西（config.json、data/、logs/）优先放 exe 同级；
     如果 exe 装在 Program Files 这类没有写权限的地方，就退到
     %LOCALAPPDATA%\\LunaPet ——不然用户一启动就报"无法保存配置"。
3. 用户想强制指定：设置环境变量 LUNPET_HOME 指向一个目录即可。

这样打包后双击 exe 就能用，也不会把用户数据塞进安装目录里。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "LunaPet"


def _frozen() -> bool:
    """是否跑在 PyInstaller 打出来的 exe 里。"""
    return bool(getattr(sys, "frozen", False))


def _exe_dir() -> Path:
    """exe 所在目录（打包后）；源码运行时返回项目根。"""
    if _frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _bundle_dir() -> Path:
    """只读资源目录：打包后是临时解包目录（_MEIPASS），源码运行时是项目根。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


def _writable(path: Path) -> bool:
    """试着往这个目录写个临时文件，能写才算可写。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:  # noqa: BLE001 权限/只读盘/被杀软拦都算不可写
        return False


def _home_dir() -> Path:
    """可写根目录：环境变量 → exe 同级 → %LOCALAPPDATA%\\LunaPet → 用户主目录。"""
    forced = os.environ.get("LUNPET_HOME", "").strip()
    if forced:
        target = Path(forced).expanduser()
        if _writable(target):
            return target

    if not _frozen():
        return Path(__file__).resolve().parent.parent

    exe_dir = _exe_dir()
    if _writable(exe_dir):
        return exe_dir

    local = os.environ.get("LOCALAPPDATA", "")
    fallback = Path(local) / APP_NAME if local else Path.home() / ("." + APP_NAME.lower())
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


# 对外暴露的几个目录（config.py 用它们，别的地方也直接 import 这几个）
HOME = _home_dir()                 # 可写根目录
BUNDLE = _bundle_dir()             # 只读资源根

ROOT = HOME                        # 兼容老代码里 ROOT 的含义：可写根
CONFIG_DIR = HOME / "config"
DATA_DIR = HOME / "data"
LOGS_DIR = HOME / "logs"


def assets_dir() -> Path:
    """assets 目录：打包后在 _MEIPASS 里，源码运行时在项目根；写过的自定义优先 HOME。"""
    for base in (HOME, BUNDLE):
        candidate = base / "assets"
        if candidate.is_dir():
            return candidate
    return BUNDLE / "assets"


ASSETS_DIR = assets_dir()


def describe() -> dict:
    """给 --check / 日志用的环境快照。"""
    return {
        "frozen": _frozen(),
        "home": str(HOME),
        "bundle": str(BUNDLE),
        "assets": str(ASSETS_DIR),
        "exe": sys.executable,
    }
