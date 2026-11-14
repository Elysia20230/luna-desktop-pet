# -*- coding: utf-8 -*-
r"""
GPT-SoVITS 目录的"体检"与"自动查找"（2026-11-14 新增，v1.0.2）。

为什么单独放一个模块：设置面板里要实时显示「这个目录到底对不对」，
但这段逻辑本身跟 Qt 没关系，单独放能直接写单测、也能被工具脚本复用。

用户最常见的困惑是「我该选哪一层」：
    GPT-SoVITS-v2pro-20250604/      ← 选这一层
        api_v2.py                   ← 启动语音服务用的脚本
        runtime/python.exe          ← 自带的 3.9 解释器
        GPT_weights_v2Pro/…
        SoVITS_weights_v2Pro/…
很多人会选到里面的 `runtime\` 或 `GPT_weights_v2Pro\`，于是启动就报"找不到 api_v2.py"。
"""
from __future__ import annotations

from pathlib import Path

# 判定"这一层是 GPT-SoVITS 根目录"的标志文件
API_SCRIPT = "api_v2.py"
# 自带的解释器（相对根目录）；没有的话可以退回系统 python
RUNTIME_PYTHON = Path("runtime") / "python.exe"

# 自动查找时看的几个起点（按经验排序：整合包通常解压在 下载 或 桌面）
SEARCH_ROOTS = ("Downloads", "Desktop", "")

# 找的时候最多往下扎两层（再深就太慢了）
MAX_DEPTH = 2

# 明显不用翻的目录，省时间
_SKIP_NAMES = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "windows", "program files", "program files (x86)", "appdata",
    "$recycle.bin", "system volume information",
}


def _looks_like_voice_root(path: Path) -> bool:
    """光看文件名就判断：是不是"含 api_v2.py 的那一层"。"""
    try:
        return path.is_dir() and (path / API_SCRIPT).is_file()
    except OSError:
        return False


def voice_root_state(path) -> tuple[str, str]:
    """
    体检一个"GPT-SoVITS 根目录"设置。

    返回 (状态码, 给用户看的中文说明)，状态码：
        empty        没填
        ok           看起来正确
        missing_api  目录在，但没有 api_v2.py（大概率选深了一层）
        absent       路径不存在
    """
    raw = str(path or "").strip()
    if not raw:
        return "empty", "未设置 · 只影响「用日语念出来」，聊天和彩蛋都不受影响"

    root = Path(raw).expanduser()
    if not root.exists():
        return "absent", f"× 路径不存在：{root}"

    if not _looks_like_voice_root(root):
        # 给出最容易犯的错：选到了子目录
        hint = "（这一层里没有 api_v2.py，通常是选深了一层，请选它的上一级）"
        return "missing_api", f"× {root}{hint}"

    python = root / RUNTIME_PYTHON
    if python.is_file():
        return "ok", "√ 找到 api_v2.py（用 runtime\\python.exe 启动）"
    return "ok", "√ 找到 api_v2.py（没看到 runtime\\python.exe，需要系统里装了 Python）"


def find_voice_roots(extra_dirs=None, max_depth: int = MAX_DEPTH) -> list[Path]:
    """
    在常见位置浅扫一遍，找出像 GPT-SoVITS 根目录的文件夹。

    只按「有没有 api_v2.py」判断，不看名字——整合包被改名也能认出来。
    扫得很浅（默认两层），几十毫秒就结束。
    """
    home = Path.home()
    roots: list[Path] = []

    if extra_dirs:
        roots.extend(Path(p).expanduser() for p in extra_dirs)
    for name in SEARCH_ROOTS:
        roots.append(home / name if name else home)

    found: list[Path] = []
    seen: set[str] = set()

    def walk(base: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = list(base.iterdir())
        except (OSError, PermissionError):
            return
        for entry in entries:
            key = str(entry).lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                if not entry.is_dir():
                    continue
            except OSError:
                continue
            if entry.name.lower() in _SKIP_NAMES:
                continue
            if _looks_like_voice_root(entry):
                found.append(entry)
                continue          # 已经命中，不再往下钻
            walk(entry, depth + 1)

    for base in roots:
        if base.is_dir():
            walk(base, 0)

    # 去重（不同起点可能扫到同一个目录）
    unique: list[Path] = []
    seen_paths: set[str] = set()
    for p in found:
        key = str(p).lower()
        if key in seen_paths:
            continue
        seen_paths.add(key)
        unique.append(p)
    return unique
