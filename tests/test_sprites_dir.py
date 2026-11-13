# -*- coding: utf-8 -*-
"""
立绘目录查找顺序的测试（app/config.py::sprite_dir）。

要保住的行为：用户把素材包解压到 LunaPet.exe 旁边之后，程序**不用配置**就能找到立绘。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config as config_mod  # noqa: E402
from app import paths  # noqa: E402
from app.config import Config  # noqa: E402


def _make_sprite(folder: Path, name: str = "lun_s_0_0_00.png") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    png = folder / name
    png.write_bytes(b"\x89PNG\r\n\x1a\n")      # 内容不重要，只看文件名是否命中
    return png


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    """把 ROOT / BUNDLE 指到临时目录，避免碰到真实工作区。"""
    home = tmp_path / "app"
    home.mkdir()
    monkeypatch.setattr(config_mod, "ROOT", home)
    monkeypatch.setattr(paths, "BUNDLE", tmp_path / "bundle")
    return home


def test_configured_dir_wins(fake_home: Path, tmp_path: Path) -> None:
    """配置里指定的目录优先级最高。"""
    custom = tmp_path / "my_sprites"
    _make_sprite(custom)
    _make_sprite(fake_home / "Sprite")          # exe 同级也有一份，但应让位
    cfg = Config()
    cfg.set("sprite.dir", str(custom))
    assert cfg.sprite_dir() == custom


def test_exe_sibling_sprite(fake_home: Path) -> None:
    """打包后最常见的情况：Sprite 与 exe 同级。"""
    _make_sprite(fake_home / "Sprite")
    cfg = Config()
    cfg.set("sprite.dir", "")
    assert cfg.sprite_dir() == fake_home / "Sprite"


def test_parent_sprite(fake_home: Path) -> None:
    """源码模式：项目根旁边就是 Sprite。"""
    _make_sprite(fake_home.parent / "Sprite")
    cfg = Config()
    cfg.set("sprite.dir", "")
    assert cfg.sprite_dir() == fake_home.parent / "Sprite"


def test_nested_sprite_folder(fake_home: Path) -> None:
    """用户解压多套了一层：Sprite/Sprite/*.png。"""
    _make_sprite(fake_home / "Sprite" / "Sprite")
    cfg = Config()
    cfg.set("sprite.dir", "")
    assert cfg.sprite_dir() == fake_home / "Sprite" / "Sprite"


def test_empty_sprite_dir_counts_as_found(fake_home: Path) -> None:
    """目录存在但没有图：也返回它，让上层给出"没找到立绘"的提示。"""
    (fake_home / "Sprite").mkdir()
    cfg = Config()
    cfg.set("sprite.dir", "")
    assert cfg.sprite_dir() == fake_home / "Sprite"


def test_nothing_found(fake_home: Path) -> None:
    """一处都没有：返回 None，界面据此提示去下载素材包。"""
    cfg = Config()
    cfg.set("sprite.dir", "")
    assert cfg.sprite_dir() is None
