# -*- coding: utf-8 -*-
r"""
把「立绘 + 彩蛋语音」打成可选素材包，作为 Release 附件单独分发。

为什么单独一个包：
- 立绘和那段日语录音都是游戏（Navel《近月少女的礼仪》）的版权素材，不能进 git 仓库；
- 但用户下载 exe 之后又想开箱就能看见露娜、点彩蛋能出声，所以额外放一个可选附件，
  谁需要谁下，本项目随时可以整包撤下。

用法（在 LunaPet 目录下）：
    .venv\Scripts\python.exe tools\pack_sprites.py
    .venv\Scripts\python.exe tools\pack_sprites.py --sprite-dir D:\素材\Sprite --version 1.0.2

产物：
    dist/luna-assets-v<版本>.zip       顶层是 Sprite/、assets/voice/ 与 素材说明.txt
    dist/SHA256SUMS.txt                （与程序包共用，追加一行）

用户怎么用：把它解压到 LunaPet.exe 所在的那个文件夹里，得到
    LunaPet\LunaPet.exe
    LunaPet\Sprite\lun_s_0_0_00.png
    LunaPet\assets\voice\taihen_ni_kibun_ga_ii.wav
程序启动时会自己找到（见 app/config.py::sprite_dir 与 app/voice/signature.py）。
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_exe import DIST, ROOT, _write_sha256  # noqa: E402 复用校验和与目录常量

README_NAME = "素材说明.txt"

# 彩蛋语音的文件名（和 app/voice/signature.py 保持一致）
VOICE_FILES = ("taihen_ni_kibun_ga_ii.wav", "taihen_ni_kibun_ga_ii.ogg")

README_TEXT = """\
关于这个素材包
==============

1. 里面是什么
   Sprite\\ 文件夹里是《近月少女的礼仪》（月に寄りそう乙女の作法，Navel）中
   樱小路露娜的立绘差分图（透明背景 PNG，673 张）。程序靠这些图把露娜画到桌面上。

   assets\\voice\\ 里是一小段日语录音（taihen_ni_kibun_ga_ii.wav）。
   设置 → 其它 →「让她说一句「心情大好～」」按钮放的就是它。

2. 版权归谁
   角色、立绘、设定、语音的版权归 Navel 及相关权利人所有。
   本素材包不是本项目（luna-desktop-pet）的原创内容，也不受项目的 MIT 许可覆盖。

3. 你可以怎么用
   - 仅限个人学习、研究、自用（比如自己玩这个桌宠）。
   - 请勿商用、请勿二次分发、请勿打包进你自己的产品。
   - 如果你是权利人并希望撤下：在项目仓库开一个 issue 说明即可，
     该项目会立即删除本素材包附件（仓库本身不含这些图片与录音）。

4. 怎么装
   把压缩包里的内容解压到 LunaPet.exe 所在的目录，形成：

       LunaPet\\LunaPet.exe
       LunaPet\\Sprite\\lun_s_0_0_00.png
       LunaPet\\Sprite\\...
       LunaPet\\assets\\voice\\taihen_ni_kibun_ga_ii.wav

   然后双击 LunaPet.exe 即可；程序会按 exe 同级目录自动寻找立绘与这段语音。
   立绘也可以放到任意位置，再在 设置 → 立绘 → 选目录 里指定；
   语音也可以换成自己的 wav，在 设置 → 其它 →「彩蛋音频」里选。

5. 没有这个包会怎样
   程序照样能启动，只是没有立绘可画（托盘提示"没找到立绘"），
   点「心情大好～」会提示去下载素材包或指定自己的 wav。
"""


def _collect(sprite_dir: Path) -> list[Path]:
    """
    收集 lun_*.png。

    注意：Windows 上 glob 不分大小写，`lun_*.png` 已经能匹配 `LUN_*.png`；
    如果两种模式都 glob 一遍，同一张图会被写进 zip 两次（体积翻倍 + 重名告警），
    所以这里按“文件名小写”去重。
    """
    found: dict[str, Path] = {}
    for pattern in ("lun_*.png", "LUN_*.png"):
        for path in sprite_dir.glob(pattern):
            if path.is_file():
                found.setdefault(path.name.lower(), path)
    return [found[key] for key in sorted(found)]


def _collect_voice(voice_dir: Path) -> list[Path]:
    """彩蛋语音（wav 播放用，ogg 只是留档）；文件不在就跳过，不阻断打包。"""
    out: list[Path] = []
    for name in VOICE_FILES:
        p = voice_dir / name
        if p.is_file():
            out.append(p)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="把立绘 + 彩蛋语音打成可选素材包")
    parser.add_argument("--sprite-dir", default=str(ROOT.parent / "Sprite"),
                        help="立绘目录（默认 ../Sprite）")
    parser.add_argument("--voice-dir", default=str(ROOT / "assets" / "voice"),
                        help="彩蛋语音目录（默认 assets/voice）")
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

    voice_files = _collect_voice(Path(args.voice_dir).expanduser())
    if not voice_files:
        print(f"提示：{args.voice_dir} 里没找到彩蛋语音，本次只打包立绘")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"luna-assets-v{version}.zip"
    if zip_path.exists():
        zip_path.unlink()

    total_mb = sum(p.stat().st_size for p in files) / 1024 / 1024
    voice_mb = sum(p.stat().st_size for p in voice_files) / 1024 / 1024
    print(f"打包 {len(files)} 张立绘（{total_mb:.1f} MB）+ {len(voice_files)} 个语音文件"
          f"（{voice_mb:.2f} MB）→ {zip_path.name} ……")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in files:
            zf.write(path, Path("Sprite") / path.name)
        for path in voice_files:
            zf.write(path, Path("assets") / "voice" / path.name)
        zf.writestr(README_NAME, README_TEXT)

    size_mb = zip_path.stat().st_size / 1024 / 1024
    print(f"完成：{zip_path}（{size_mb:.1f} MB）")
    _write_sha256(zip_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
