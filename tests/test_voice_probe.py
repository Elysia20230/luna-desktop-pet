# -*- coding: utf-8 -*-
r"""
「GPT-SoVITS 根目录」体检与自动查找的测试（app/voice/probe.py）。

用户最常犯的错是选深一层（选到 runtime\ 或 GPT_weights_v2Pro\），
所以 voice_root_state() 必须能明确说出「这一层没有 api_v2.py」。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.voice import probe  # noqa: E402


def _fake_voice_root(base: Path, with_python: bool = True) -> Path:
    """造一个"像 GPT-SoVITS 根目录"的假目录。"""
    base.mkdir(parents=True, exist_ok=True)
    (base / probe.API_SCRIPT).write_text("# fake api\n", encoding="utf-8")
    if with_python:
        exe = base / probe.RUNTIME_PYTHON
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"MZ")
    return base


def test_empty_state():
    code, text = probe.voice_root_state("")
    assert code == "empty"
    assert "未设置" in text


def test_absent_state(tmp_path):
    code, text = probe.voice_root_state(tmp_path / "nope")
    assert code == "absent"
    assert "不存在" in text


def test_missing_api_state(tmp_path):
    """选深一层（只有 runtime 没有 api_v2.py）要报 missing_api。"""
    deeper = tmp_path / "runtime"
    deeper.mkdir()
    code, text = probe.voice_root_state(deeper)
    assert code == "missing_api"
    assert "api_v2.py" in text


def test_ok_state_with_python(tmp_path):
    root = _fake_voice_root(tmp_path / "GPT-SoVITS-v2pro")
    code, text = probe.voice_root_state(root)
    assert code == "ok"
    assert "python.exe" in text


def test_ok_state_without_python(tmp_path):
    root = _fake_voice_root(tmp_path / "GPT-SoVITS-bare", with_python=False)
    code, text = probe.voice_root_state(root)
    assert code == "ok"
    assert "Python" in text


def test_find_voice_roots(tmp_path, monkeypatch):
    """浅扫能找到假根目录，且不会把"选深一层"的目录当结果。"""
    monkeypatch.setattr(probe, "SEARCH_ROOTS", ())
    deep = tmp_path / "downloads" / "GPT-SoVITS-v2pro"
    _fake_voice_root(deep)
    # 干扰项：没有 api_v2.py 的文件夹
    (tmp_path / "downloads" / "not-voice").mkdir(parents=True)

    found = probe.find_voice_roots(extra_dirs=[tmp_path / "downloads"], max_depth=3)
    assert any(p == deep for p in found)
    assert all((p / probe.API_SCRIPT).is_file() for p in found)


def test_find_voice_roots_empty_when_none(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    # 别让测试去扫本机真实的 下载/桌面（那会把开发机上的整合包扫进来）
    monkeypatch.setattr(probe, "SEARCH_ROOTS", ())
    assert probe.find_voice_roots(extra_dirs=[empty], max_depth=2) == []
