# -*- coding: utf-8 -*-
"""
「心情大好～」按钮的行为测试：放音频 + 换表情 + 出气泡。

这里不启动真的 LunaApp（太慢、还会弹窗），而是把 LunaApp.play_signature
绑到一个只有必要属性的假对象上——它用到的成员就那几个，注入假货即可。

顺带钉住两条失败路径：音频丢了、播放器报错，都只能弹托盘提示，不能抛异常。

跑法：
    .venv\\Scripts\\python.exe -m pytest tests -q
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from main import LunaApp  # noqa: E402
from app.voice.signature import DEFAULT_WAV  # noqa: E402


class _Cfg:
    def __init__(self, values: dict) -> None:
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


class _FakePlayer:
    """记录"放的是哪个文件、音量多少"，并返回一个假时长。"""

    def __init__(self, duration: float = 2.89, boom: Exception | None = None) -> None:
        self.duration = duration
        self.boom = boom
        self.calls: list[tuple[Path, float]] = []

    def play(self, path, volume: float = 1.0) -> float:
        self.calls.append((Path(path), float(volume)))
        if self.boom is not None:
            raise self.boom
        return self.duration


class _FakeVoice:
    def __init__(self, player: _FakePlayer) -> None:
        self.player = player


class _FakeBubble:
    def __init__(self) -> None:
        self.shown: list[tuple[str, int]] = []

    def show_text(self, text, anchor, msec, prefer="top"):  # noqa: ANN001
        self.shown.append((text, int(msec)))


class _FakeTray:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def showMessage(self, title, text, *args, **kwargs):  # noqa: ANN001, ANN002
        self.messages.append((str(title), str(text)))


class _Stub:
    """冒充 LunaApp：只提供 play_signature 用到的那几个成员。"""

    def __init__(self, cfg: _Cfg, player: _FakePlayer) -> None:
        self.cfg = cfg
        self.voice = _FakeVoice(player)
        self.bubble = _FakeBubble()
        self.tray = _FakeTray()
        self.emotions: list[str] = []
        self.anchors = 0

    def _apply_emotion(self, emotion: str, use_pose: bool = True) -> None:
        self.emotions.append(emotion)

    def _bubble_anchor(self):
        self.anchors += 1
        return None            # 假锚点：气泡桩不关心位置


def _run(cfg: _Cfg, player: _FakePlayer) -> _Stub:
    stub = _Stub(cfg, player)
    LunaApp.play_signature(stub)     # 直接把真方法绑到假对象上跑
    return stub


def test_plays_audio_and_changes_expression():
    player = _FakePlayer(duration=2.89)
    stub = _run(_Cfg({"voice.volume": 0.8}), player)

    assert len(player.calls) == 1
    played, volume = player.calls[0]
    assert played == DEFAULT_WAV
    assert volume == 0.8

    # 表情动作用「得意」（对应抱臂）
    assert stub.emotions == ["得意"]

    # 气泡显示中文，时长至少覆盖整段音频
    assert len(stub.bubble.shown) == 1
    text, msec = stub.bubble.shown[0]
    assert text == "心情大好～"
    assert msec >= 2890

    assert stub.tray.messages == []


def test_text_can_be_overridden():
    stub = _run(_Cfg({"voice.signature_text": "今天心情非常好。", "voice.volume": 1.0}),
                _FakePlayer())
    assert stub.bubble.shown[0][0] == "今天心情非常好。"


def test_missing_audio_only_warns(tmp_path):
    missing = tmp_path / "nope.wav"
    player = _FakePlayer()
    stub = _run(_Cfg({"voice.signature_file": str(missing)}), player)

    assert player.calls == []
    assert stub.emotions == []
    assert stub.bubble.shown == []
    assert stub.tray.messages and stub.tray.messages[0][0] == "找不到音频"


def test_player_error_only_warns():
    player = _FakePlayer(boom=RuntimeError("当前系统没有 winsound，无法播放语音"))
    stub = _run(_Cfg({}), player)

    assert stub.emotions == [], "播放失败就不该动表情"
    assert stub.bubble.shown == []
    assert stub.tray.messages and stub.tray.messages[0][0] == "放不出来"
    assert "winsound" in stub.tray.messages[0][1]
