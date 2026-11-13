# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置（onedir 模式）。

为什么用 onedir 而不是 onefile：
  · 这个项目要带 PySide6（Qt 运行时）、onnxruntime、chromadb 这些大件，
    onefile 每次启动都要把几百 MB 解压到临时目录，冷启动要十几秒；
  · onedir 双击就是秒开，反正最后也是打包成 zip 分发。

打出来的目录：dist/LunaPet/，里面有 LunaPet.exe + 运行时 + assets/。
用户的配置、记忆、日志会写在 exe 旁边（不可写时自动退到 %LOCALAPPDATA%\\LunaPet）。

用法：
    .venv\\Scripts\\python.exe tools\\build_exe.py
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH)          # noqa: F821 —— PyInstaller 在执行时注入 SPECPATH

# 只带"程序运行真的需要 + 自己画的"素材：
#   assets/persona  人设与提示词 md
#   assets/icons    自制图标
#   assets/*.json   表情表、造型表、音色表、角色卡、素材目录
# 刻意不带：Sprite/ 立绘、assets/sheets*（对照拼图）、assets/voice/*.wav（游戏语音片段）——
# 那些属于游戏版权物，不随仓库与安装包分发（见 THIRD_PARTY.md）。
datas = [
    (str(ROOT / "assets" / "persona"), "assets/persona"),
    (str(ROOT / "assets" / "icons"), "assets/icons"),
]
for json_file in (ROOT / "assets").glob("*.json"):
    datas.append((str(json_file), "assets"))

binaries = []
hiddenimports = []

# 这些包要么带数据文件（模型名表、协议定义），要么靠动态 import 加载子模块，
# PyInstaller 静态分析扫不全，直接整包收集最稳。装不上就跳过，不至于打不出包。
for package in (
    "chromadb",
    "onnxruntime",
    "ctranslate2",
    "faster_whisper",
    "tokenizers",
    "sounddevice",
    "av",
    "kubernetes",
    "opentelemetry",
    "opentelemetry.exporter.otlp",
    "grpc",
    "pydantic",
):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    except Exception:                                     # noqa: BLE001 包没装就跳过
        continue
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# 例子配置一起带上（打包后第一次启动会自动生成 config/config.json）
example = ROOT / "config" / "config.example.json"
if example.is_file():
    datas.append((str(example), "config"))

icon_path = ROOT / "assets" / "icons" / "luna.ico"
icon = str(icon_path) if icon_path.is_file() else None

a = Analysis(                                        # noqa: F821
    [str(ROOT / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter.test", "pytest", "matplotlib", "IPython", "pandas"],
    noarchive=False,
)
pyz = PYZ(a.pure)                                    # noqa: F821

exe = EXE(                                           # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LunaPet",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                                       # UPX 容易被杀软误报，不用
    console=False,                                   # 双击不弹黑框
    disable_windowed_traceback=False,
    icon=icon,
)

coll = COLLECT(                                      # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="LunaPet",
)
