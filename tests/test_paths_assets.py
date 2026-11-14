# -*- coding: utf-8 -*-
"""
assets 目录选择的测试（app/paths.py::assets_dir）。

背景：v1.0.1 的 exe 有个潜伏 bug——用户把素材包解压出 `LunaPet\\assets\\`
之后，`assets_dir()` 会因为"HOME 优先"而切到那块目录，
导致打包进 `_internal\\assets` 的 expressions.json / wardrobe.json 全部读不到。
这里锁住修好后的规则：打包时 BUNDLE 优先，源码时 HOME 优先。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import paths  # noqa: E402


def _prepare(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "LunaPet"
    bundle = tmp_path / "_internal"
    (home / "assets" / "voice").mkdir(parents=True)
    (bundle / "assets").mkdir(parents=True)
    (bundle / "assets" / "expressions.json").write_text("{}", encoding="utf-8")
    return home, bundle


def test_frozen_prefers_bundle(tmp_path, monkeypatch):
    home, bundle = _prepare(tmp_path)
    monkeypatch.setattr(paths, "HOME", home)
    monkeypatch.setattr(paths, "BUNDLE", bundle)
    monkeypatch.setattr(paths, "_frozen", lambda: True)

    assert paths.assets_dir() == bundle / "assets"


def test_source_prefers_home(tmp_path, monkeypatch):
    home, bundle = _prepare(tmp_path)
    monkeypatch.setattr(paths, "HOME", home)
    monkeypatch.setattr(paths, "BUNDLE", bundle)
    monkeypatch.setattr(paths, "_frozen", lambda: False)

    assert paths.assets_dir() == home / "assets"


def test_frozen_without_bundle_assets_falls_back_to_home(tmp_path, monkeypatch):
    home = tmp_path / "LunaPet"
    (home / "assets").mkdir(parents=True)
    bundle = tmp_path / "_internal"          # 没有 assets 子目录
    bundle.mkdir()
    monkeypatch.setattr(paths, "HOME", home)
    monkeypatch.setattr(paths, "BUNDLE", bundle)
    monkeypatch.setattr(paths, "_frozen", lambda: True)

    assert paths.assets_dir() == home / "assets"


def test_assets_overlay_points_at_home(tmp_path, monkeypatch):
    home = tmp_path / "LunaPet"
    monkeypatch.setattr(paths, "HOME", home)
    assert paths.assets_overlay() == home / "assets"
