# -*- coding: utf-8 -*-
"""
开源前自检：扫一遍仓库里"绝不该出现"的东西。

检查四类：
  1. 隐私文件：config/config.json（含 Key）、data/、logs/、.venv、dist、build
  2. 游戏素材：Sprite/、assets/sheets*、assets/voice/*.wav|ogg、docs/preview_*.png
  3. 文本里泄露的东西：sk- 开头的 Key、本机绝对路径（C:\\Users\\<名字>、D:\\DSH\\...）
  4. 误入的大文件：>5 MB 的二进制

用法：
    .venv\\Scripts\\python.exe tools\\release_check.py
返回码 0 = 干净，1 = 有需要处理的问题。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 根目录下这些子目录整个跳过（不是"要检查的内容"，而是本地运行时产物）
SKIP_DIRS = {
    ".git", ".venv", "venv", "dist", "build", "__pycache__", ".pytest_cache",
    "data", "logs", "Sprite", "node_modules",
}

# 即使在这些目录里，也不该出现在提交里（列出来是为了报错更明确）
FORBIDDEN_DIRS = [
    ("data", "本地数据：聊天记录 / 记忆 / 模型缓存"),
    ("logs", "运行日志"),
    ("Sprite", "游戏立绘"),
    ("assets/sheets", "立绘对照拼图"),
    ("assets/sheets_f", "立绘对照拼图"),
    (".venv", "Python 虚拟环境"),
    ("dist", "打包产物"),
    ("build", "打包中间产物"),
]

FORBIDDEN_FILES = [
    ("config/config.json", "里面有 API Key！"),
    ("config/_example_tmp.json", "临时文件"),
]

FORBIDDEN_GLOBS = [
    ("assets/voice/*.wav", "游戏语音片段"),
    ("assets/voice/*.ogg", "游戏语音片段"),
    ("docs/preview_*.png", "立绘裁切演示图"),
    ("*.bak", "配置备份"),
]

TEXT_SUFFIXES = {".py", ".md", ".json", ".txt", ".bat", ".spec", ".toml", ".cfg", ".ini", ".yml", ".yaml"}
SECRET_PATTERNS = [
    (re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"), "疑似 API Key（sk-…）"),
    (re.compile(r"[A-Za-z]:\\\\?Users\\\\?[A-Za-z0-9_.-]+"), "本机用户目录绝对路径"),
    (re.compile(r"[A-Za-z]:\\\\?DSH\\\\?"), "本机工作区绝对路径"),
]
BIG_FILE_MB = 5


def _iter_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts[:-1]):
            continue
        if path.name in {"release_check.py"}:
            continue
        yield path


def _git_tracked() -> set[Path] | None:
    """
    已经 git init 的话，只检查"将要提交的文件"（git 认识的那些）。
    这样本地留着的 data/、config.json、立绘就不会被误报——它们本来就在 .gitignore 里。
    """
    import subprocess

    if not (ROOT / ".git").exists():
        return None
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=str(ROOT), capture_output=True, check=True,
        ).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.CalledProcessError):
        return None
    return {Path(item) for item in out.split("\0") if item}


def main() -> int:
    problems: list[str] = []
    warnings: list[str] = []
    tracked = _git_tracked()
    if tracked is not None:
        print(f"（只检查 git 已跟踪的 {len(tracked)} 个文件）\n")

    def _in_repo(rel: str) -> bool:
        """该路径会不会被提交。没有 git 时退化成"存在就算"。"""
        if tracked is None:
            return (ROOT / rel).exists()
        prefix = rel.rstrip("/").replace("\\", "/")
        return any(str(p).replace("\\", "/") == prefix
                   or str(p).replace("\\", "/").startswith(prefix + "/")
                   for p in tracked)

    # ---- 1. 不该存在的目录 ----
    for rel, why in FORBIDDEN_DIRS:
        if _in_repo(rel):
            problems.append(f"[目录] {rel}/ 不该提交（{why}）")

    # ---- 2. 不该存在的文件 ----
    for rel, why in FORBIDDEN_FILES:
        if _in_repo(rel):
            problems.append(f"[文件] {rel} 不该提交（{why}）")
    for pattern, why in FORBIDDEN_GLOBS:
        for hit in ROOT.glob(pattern):
            rel = str(hit.relative_to(ROOT))
            if _in_repo(rel):
                problems.append(f"[文件] {rel} 不该提交（{why}）")

    # ---- 3. 文本里的秘密与绝对路径 ----
    scan_list = (_iter_files() if tracked is None
                 else (ROOT / p for p in sorted(tracked)))
    for path in scan_list:
        if not path.is_file():
            continue
        if path.name == "release_check.py":
            continue  # 本文件里写着"要拦的路径模式"，自己扫自己必然误报
        rel = path.relative_to(ROOT)
        try:
            if path.suffix.lower() in TEXT_SUFFIXES:
                text = path.read_text(encoding="utf-8", errors="ignore")
                for pattern, label in SECRET_PATTERNS:
                    for match in pattern.finditer(text):
                        snippet = match.group(0)
                        problems.append(f"[内容] {rel} 出现{label}：{snippet[:24]}…")
            size_mb = path.stat().st_size / 1024 / 1024
            if size_mb > BIG_FILE_MB:
                warnings.append(f"[体积] {rel} 有 {size_mb:.1f} MB，确认是否该进仓库")
        except OSError as exc:
            warnings.append(f"[读取] {rel} 打不开：{exc}")

    # ---- 输出 ----
    if problems:
        print("发现需要处理的问题：\n")
        for item in problems:
            print("  ✗", item)
    if warnings:
        print("\n提醒（不一定是错）：\n")
        for item in warnings:
            print("  ·", item)
    if not problems:
        print("\n✓ 自检通过：没有发现密钥、隐私文件或游戏素材。")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
