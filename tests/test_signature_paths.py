# -*- coding: utf-8 -*-
"""
彩蛋音频查找顺序的测试（app/voice/signature.py::signature_candidates）。

要保住的行为：exe 版把素材包解压到 `LunaPet\\assets\\voice\\` 后，
点「让她说一句「心情大好～」」能真的找到那个 wav —— 不必让用户手填路径。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import paths  # noqa: E402
from app import voice as voice_pkg  # noqa: F401,E402  （确保包已导入）
from app.voice import signature  # noqa: E402


def _touch_wav(folder: Path, name: str = signature.SIGNATURE_NAME) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_bytes(b"RIFF....WAVE")            # 内容不重要，只看路径能否命中
    return p


def test_candidates_cover_exe_side_assets(tmp_path, monkeypatch):
    """HOME/assets/voice（素材包解压处）必须出现在候选里。"""
    home = tmp_path / "LunaPet"
    bundle = tmp_path / "_internal"
    monkeypatch.setattr(paths, "HOME", home)
    monkeypatch.setattr(paths, "BUNDLE", bundle)
    monkeypatch.setattr(paths, "ASSETS_DIR", bundle / "assets")
    monkeypatch.setattr(paths, "DATA_DIR", home / "data")

    got = [str(p).lower() for p in signature.signature_candidates(None)]
    assert str(home / "assets" / "voice" / signature.SIGNATURE_NAME).lower() in got
    assert str(bundle / "assets" / "voice" / signature.SIGNATURE_NAME).lower() in got


def test_candidates_dedupe_and_order(tmp_path, monkeypatch):
    """同一个路径不要出现两次，且配置项排第一。"""
    home = tmp_path / "LunaPet"
    monkeypatch.setattr(paths, "HOME", home)
    monkeypatch.setattr(paths, "BUNDLE", home)          # 故意让 bundle 与 home 相同
    monkeypatch.setattr(paths, "DATA_DIR", home / "data")

    cfg_file = tmp_path / "mine.wav"
    cfg_file.write_bytes(b"RIFF....WAVE")

    from app.config import Config

    cfg = Config(path=tmp_path / "config.json")
    cfg.set("voice.signature_file", str(cfg_file))

    got = signature.signature_candidates(cfg)
    assert got[0] == cfg_file
    low = [str(p).lower() for p in got]
    assert len(low) == len(set(low)), "候选里不该有重复路径"


def test_relative_signature_file_resolved_against_root(tmp_path, monkeypatch):
    """配置里写相对路径时，按程序目录解析（不受当前工作目录影响）。"""
    home = tmp_path / "LunaPet"
    monkeypatch.setattr(paths, "HOME", home)
    monkeypatch.setattr(paths, "ROOT", home)
    monkeypatch.setattr(paths, "BUNDLE", home)
    monkeypatch.setattr(paths, "DATA_DIR", home / "data")

    from app.config import Config

    cfg = Config(path=tmp_path / "config.json")
    cfg.set("voice.signature_file", r"assets\voice\custom.wav")

    got = signature.signature_candidates(cfg)
    assert got[0] == home / "assets" / "voice" / "custom.wav"


def test_signature_path_prefers_existing(tmp_path, monkeypatch):
    """没配置时按顺序找一个存在的：前两个候选都不存在，就用 data/voice 里的那个。"""
    home = tmp_path / "LunaPet"
    bundle = tmp_path / "_internal"
    data_dir = home / "data"
    monkeypatch.setattr(paths, "HOME", home)
    monkeypatch.setattr(paths, "ROOT", home)
    monkeypatch.setattr(paths, "BUNDLE", bundle)
    monkeypatch.setattr(paths, "DATA_DIR", data_dir)
    # 打包资源与用户 assets 都没有 voice 目录
    monkeypatch.setattr(paths, "ASSETS_DIR", bundle / "assets")

    made = _touch_wav(data_dir / "voice")
    assert signature.signature_path(None) == made


def test_signature_path_falls_back_to_first_candidate(tmp_path, monkeypatch):
    """一个都没有时返回第一个候选，供错误提示显示"本来该在哪"。"""
    home = tmp_path / "LunaPet"
    bundle = tmp_path / "_internal"
    monkeypatch.setattr(paths, "HOME", home)
    monkeypatch.setattr(paths, "ROOT", home)
    monkeypatch.setattr(paths, "BUNDLE", bundle)
    monkeypatch.setattr(paths, "DATA_DIR", home / "data")
    monkeypatch.setattr(paths, "ASSETS_DIR", bundle / "assets")

    path = signature.signature_path(None)
    assert path.name == signature.SIGNATURE_NAME
    assert not path.is_file()


def test_explicit_missing_file_is_not_replaced(tmp_path, monkeypatch):
    """用户手填的路径不存在时，就该报那个路径，不能偷偷换成默认那段。"""
    from app.config import Config

    home = tmp_path / "LunaPet"
    data_dir = home / "data"
    monkeypatch.setattr(paths, "HOME", home)
    monkeypatch.setattr(paths, "ROOT", home)
    monkeypatch.setattr(paths, "BUNDLE", tmp_path / "bundle")
    monkeypatch.setattr(paths, "DATA_DIR", data_dir)
    monkeypatch.setattr(paths, "ASSETS_DIR", tmp_path / "bundle" / "assets")

    _touch_wav(data_dir / "voice")        # 默认那段其实是存在的
    cfg = Config(path=tmp_path / "config.json")
    cfg.set("voice.signature_file", str(tmp_path / "nope.wav"))

    assert signature.signature_path(cfg) == tmp_path / "nope.wav"


def test_hint_mentions_both_solutions():
    """提示文案要同时给出"下素材包"和"自己选一个 wav"两条路。"""
    text = signature.signature_hint()
    assert "素材包" in text
    assert "wav" in text.lower()
