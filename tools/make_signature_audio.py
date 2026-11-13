# -*- coding: utf-8 -*-
"""
把「心情大好～」那段日语录音从 ogg 转成 wav（项目播放层只认 16-bit PCM WAV）。

为什么需要这个脚本
------------------
桌宠的播放层是 `app/voice/player.py` 里的 `WavPlayer`，它用 Windows 自带的 winsound，
而 winsound 只吃 wav（16-bit PCM），不吃 ogg。所以 ogg 必须在**打包时就转好**，
运行期就不需要 ffmpeg 了。

怎么用（换台词 / 重新生成时）
---------------------------
    .venv\\Scripts\\python.exe tools\\make_signature_audio.py

默认读 `assets/voice/taihen_ni_kibun_ga_ii.ogg`，写成同目录同名 `.wav`。
想用自己的录音，就把文件改名成上面那个名字（或加 `--src` 指定），再跑一次这个脚本。

ffmpeg 从哪来
-------------
按顺序找：配置里 voice.python 同目录 → GPT-SoVITS 的 runtime\\ffmpeg.exe → 系统 PATH。
三处都没有就打印中文提示并退出（不会静默失败）。
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import wave
from pathlib import Path

# 让 Windows 控制台也能正常打印中文（不然 GBK 下会乱码）
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parent.parent
VOICE_DIR = ROOT / "assets" / "voice"
DEFAULT_SRC = VOICE_DIR / "taihen_ni_kibun_ga_ii.ogg"
DEFAULT_DST = VOICE_DIR / "taihen_ni_kibun_ga_ii.wav"


def find_ffmpeg() -> Path | None:
    """找 ffmpeg：先看 GPT-SoVITS 的运行时，再看系统 PATH。"""
    try:
        sys.path.insert(0, str(ROOT))
        from app.config import Config  # 延迟导入，脚本单跑也能用

        cfg = Config()
        runtime = str(cfg.get("voice.root") or "")
        if runtime:
            cand = Path(runtime) / "runtime" / "ffmpeg.exe"
            if cand.is_file():
                return cand
    except Exception:  # noqa: BLE001 配置读不到就继续往下找
        pass

    found = shutil.which("ffmpeg")
    return Path(found) if found else None


def duration_of(path: Path) -> float:
    """读 wav 头算时长（秒），失败返回 0。"""
    try:
        with wave.open(str(path), "rb") as fp:
            rate = fp.getframerate() or 1
            return fp.getnframes() / float(rate)
    except Exception:  # noqa: BLE001
        return 0.0


def convert(ffmpeg: Path, src: Path, dst: Path) -> None:
    """调用 ffmpeg 转码：单声道 / 44100 Hz / 16-bit。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-ac", "1", "-ar", "44100", "-sample_fmt", "s16",
        str(dst),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"ffmpeg 退出码 {result.returncode}")


def main() -> int:
    parser = argparse.ArgumentParser(description="把「心情大好～」录音转成 wav")
    parser.add_argument("--src", default=str(DEFAULT_SRC), help="源音频（默认 assets/voice/*.ogg）")
    parser.add_argument("--dst", default=str(DEFAULT_DST), help="输出 wav 路径")
    args = parser.parse_args()

    src = Path(args.src)
    dst = Path(args.dst)
    if not src.is_file():
        print(f"[错误] 找不到源音频：{src}")
        return 1

    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        print("[错误] 找不到 ffmpeg。三种办法：")
        print("  1) 在设置 → 语音里把「GPT-SoVITS 目录」填对（它自带 runtime\\ffmpeg.exe）")
        print("  2) 把 ffmpeg.exe 放进系统 PATH")
        print("  3) 自己用别的工具转成 16-bit PCM 单声道 wav，直接覆盖目标文件")
        return 1

    print(f"使用 ffmpeg：{ffmpeg}")
    print(f"源文件：{src.name}（{src.stat().st_size:,} 字节）")
    convert(ffmpeg, src, dst)
    print(f"已生成：{dst}（{dst.stat().st_size:,} 字节，{duration_of(dst):.2f} 秒）")
    print("提示：想换台词就把自己的录音换成上面的 ogg 再跑一次本脚本。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
