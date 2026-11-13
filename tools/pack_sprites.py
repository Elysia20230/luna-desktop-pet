# -*- coding: utf-8 -*-
"""
把立绘目录打成「可选素材包」，作为 Release 附件单独分发。

为什么单独一个包：
- 立绘是游戏（Navel《近月少女的礼仪》）的版权素材，不能进 git 仓库；
- 但用户下载 exe 之后又想开箱就能看见露娜，所以额外放一个可选附件，
  谁需要谁下，本项目随时可以整包撤下。

用法（在 LunaPet 目录下）：
    .venv\\Scripts\\python.exe tools\\pack_sprites.py
    .venv\\Scripts\\python.exe tools\\pack_sprites.py --sprite-dir D:\\素材\\Sprite --version 1.0.1

产物：
    dist/luna-sprites-v<版本>.zip      顶层是 Sprite/ 与 素材说明.txt
    dist/SHA256SUMS.txt                （与程序包共用，追加一行）

用户怎么用：把它解压到 LunaPet.exe 所在的那个文件夹里，得到
    LunaPet\\LunaPet.exe
    LunaPet\\Sprite\\lun_s_0_0_00.png
程序启动时会自己找到（见 app/config.py::sprite_dir）。
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_exe import DIST, ROOT, _write_sha256  # noqa: E402 复用校验和与目录常量

README_NAME = "素材说明.txt"

README_TEXT = """\
关于这个素材包
==============

1. 里面是什么
   Sprite\\ 文件夹里是《近月少女的礼仪》（月に寄りそう乙女の作法，Navel）中
   樱小路露娜的立绘差分图（透明背景 PNG，673 张）。程序靠这些图把露娜画到桌面上。

2. 版权归谁
   角色、立绘、设定的版权归 Navel 及相关权利人所有。
   本素材包不是本项目（luna-desktop-pet）的原创内容，也不受项目的 MIT 许可覆盖。

3. 你可以怎么用
   - 仅限个人学习、研究、自用（比如自己玩这个桌宠）。
   - 请勿商用、请勿二次分发、请勿打包进你自己的产品。
   - 如果你是权利人并希望撤下：在项目仓库开一个 issue 说明即可，
     该项目会立即删除本素材包附件（仓库本身不含这些图片）。

4. 怎么装
   把压缩包里的 Sprite 文件夹解压到 LunaPet.exe 所在的目录，形成：

       LunaPet\\LunaPet.exe
       LunaPet\\Sprite\\lun_s_0_0_00.png
       LunaPet\\Sprite\\...

   然后双击 LunaPet.exe 即可；程序会按 exe 同级目录自动寻找立绘。
   也可以把 Sprite 放到任意位置，再在 设置 → 立绘 → 选目录 里指定。

5. 没有这个包会怎样
   程序照样能启动，只是没有立绘可画，会在托盘提示"没找到立绘"。
"""


def _collect(sprite_dir: Path) -> list[Path]:
    """只收 lun_*.png（以及少数大写 LUN_*.png），跳过其他杂项。"""
    files = [p for p in sprite_dir.glob("lun_*.png") if p.is_file()]
    files += [p for p in sprite_dir.glob("LUN_*.png") if p.is_file()]
    return sorted(files)


def main() -> int:
    parser = argparse.ArgumentParser(description="把立绘打成可选素材包")
    parser.add_argument("--sprite-dir", default=str(ROOT.parent / "Sprite"),
                        help="立绘目录（默认 ../Sprite）")
    parser.add_argument("--version", default="", help="版本号（默认读 build_exe.VERSION）")
    parser.add_argument("--out", default=str(DIST), help="输出目录（默认 dist/）")
    args = parser.parse_args()

    from build_exe import VERSION as BUILD_VERSION

    version = (args.version or BUILD_VERSION).strip()
    sprite_dir = Path(args.sprite_dir).expanduser()
    if not sprite_dir.is_dir():
        raise SystemExit(f"立绘目录不存在：{sprite_dir}（用 --sprite-dir 指定）")

    files = _collect(sprite_dir)
    if not files:
        raise SystemExit(f"{sprite_dir} 里没有 lun_*.png，先跑一次 tools/scan_sprites.py")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"luna-sprites-v{version}.zip"
    if zip_path.exists():
        zip_path.unlink()

    total_mb = sum(p.stat().st_size for p in files) / 1024 / 1024
    print(f"打包 {len(files)} 张立绘（{total_mb:.1f} MB）→ {zip_path.name} ……")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in files:
            zf.write(path, Path("Sprite") / path.name)
        zf.writestr(README_NAME, README_TEXT)

    size_mb = zip_path.stat().st_size / 1024 / 1024
    print(f"完成：{zip_path}（{size_mb:.1f} MB）")
    _write_sha256(zip_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
