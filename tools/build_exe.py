# -*- coding: utf-8 -*-
"""
一键打包：跑 PyInstaller → 校验 → 压缩 → 算 SHA256。

用法（在 LunaPet 目录下）：
    .venv\\Scripts\\python.exe tools\\build_exe.py
    .venv\\Scripts\\python.exe tools\\build_exe.py --zip-only   # 只重新打包 zip

产物：
    dist/LunaPet/LunaPet.exe                     可直接双击的目录版
    dist/luna-desktop-pet-v<版本>-win64.zip       分发给别人的压缩包
    dist/SHA256SUMS.txt                          校验和（发 Release 时一起上传）
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
APP_DIR = DIST / "LunaPet"
EXE = APP_DIR / "LunaPet.exe"
VERSION = "1.0.0"
ZIP_NAME = f"luna-desktop-pet-v{VERSION}-win64.zip"


def _run_pyinstaller() -> None:
    """调用 PyInstaller 打包（onedir）。"""
    spec = ROOT / "LunaPet.spec"
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(spec)]
    print("[1/3] 正在打包，第一次会因为要复制 Qt/onnxruntime 而比较慢……")
    started = time.time()
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        raise SystemExit(f"PyInstaller 失败（退出码 {result.returncode}），看上面日志")
    print(f"     完成，用时 {time.time() - started:.1f} 秒")


def _check_exe() -> None:
    if not EXE.is_file():
        raise SystemExit(f"没找到 {EXE}，打包可能没成功")
    size_mb = EXE.stat().st_size / 1024 / 1024
    print(f"[2/3] 可执行文件就绪：{EXE}（{size_mb:.1f} MB）")


def _make_zip() -> Path:
    """把整个目录压成 zip（onedir 必须整体分发）。"""
    zip_path = DIST / ZIP_NAME
    if zip_path.exists():
        zip_path.unlink()
    print(f"[3/3] 压缩到 {zip_path.name} ……")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in APP_DIR.rglob("*"):
            if path.is_file():
                zf.write(path, Path("LunaPet") / path.relative_to(APP_DIR))
    print(f"     压缩包 {zip_path.stat().st_size / 1024 / 1024:.1f} MB")
    return zip_path


def _write_sha256(zip_path: Path) -> None:
    digest = hashlib.sha256()
    with zip_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    line = f"{digest.hexdigest()}  {zip_path.name}\n"
    out = DIST / "SHA256SUMS.txt"
    out.write_text(line, encoding="utf-8")
    print(f"     校验和：{line.strip()}")


def main() -> int:
    parser = argparse.ArgumentParser(description="打包 LunaPet 为 Windows 可执行版")
    parser.add_argument("--zip-only", action="store_true", help="跳过 PyInstaller，只重新压缩")
    args = parser.parse_args()

    DIST.mkdir(exist_ok=True)
    if not args.zip_only:
        _run_pyinstaller()
    if not APP_DIR.is_dir():
        raise SystemExit("dist/LunaPet 不存在，先完整跑一次打包")

    # 顺手带上两份说明，用户解压就知道怎么用
    for name in ("README.md", "LICENSE", "THIRD_PARTY.md", "CREDITS.md"):
        src = ROOT / name
        if src.is_file():
            shutil.copy2(src, APP_DIR / name)

    _check_exe()
    zip_path = _make_zip()
    _write_sha256(zip_path)
    print("\n完成：")
    print(f"  目录版：{APP_DIR}")
    print(f"  压缩包：{zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
