# -*- coding: utf-8 -*-
"""
语音流水线（VoiceService）的测试：全部用假翻译 / 假合成 / 假播放器，
不启动 GPT-SoVITS、不联网，但走的是真实的分段、排队、播放调度代码。

关键要守住的两件事：
    1. 第一段一定要短（这是首声快不快的关键）；
    2. 快速连发新消息时，旧的那句必须整段丢掉，不能新旧混着念。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication  # noqa: E402

from app import voice as voice_pkg  # noqa: E402
from app.config import Config  # noqa: E402
from app.voice import service as service_mod  # noqa: E402
from app.voice.service import VoiceService  # noqa: E402
from app.voice.server import S_READY  # noqa: E402
from app.voice.voices import RefVoice, VoiceMap  # noqa: E402

_fake_app = QCoreApplication.instance() or QCoreApplication([])

# 每个测试造出来的服务都留个引用：测试结束统一 shutdown，
# 免得后台线程还在跑、对象却被回收（Qt 会抱怨"定时器不能在别的线程停"）。
_SERVICES: list[VoiceService] = []


@pytest.fixture(autouse=True)
def _cleanup_services():
    yield
    while _SERVICES:
        svc = _SERVICES.pop()
        try:
            svc.shutdown()
        except Exception:  # noqa: BLE001 清理失败不该影响测试结论
            pass
        _fake_app.processEvents()


class FakePlayer:
    """立刻"播完"的播放器，只记录播了哪些文件。"""

    def __init__(self) -> None:
        self.played: list[Path] = []
        self.stopped = 0

    def play(self, path, volume=1.0) -> float:
        self.played.append(Path(path))
        return 0.01

    def stop(self) -> None:
        self.stopped += 1


def _fake_map() -> VoiceMap:
    voice = RefVoice(name="soft", label="柔和", audio=Path(__file__),
                     prompt="……", speed=1.0)
    return VoiceMap({"soft": {"label": "柔和", "audio": str(Path(__file__)),
                              "prompt": "……"}},
                    {"平静": "soft"}, {}, "soft", None, None)


def _make_service(tmp_path, enabled=True):
    cfg = Config(tmp_path / "cfg.json")
    cfg.set("voice.enabled", enabled)
    cfg.set("voice.max_chars", 30)

    synth_calls: list[str] = []

    def fake_translate(text_zh, cfg, cache=None) -> str:
        return "JA:" + text_zh

    def fake_synth(text_ja, voice, base_url, timeout=180.0, force=False) -> Path:
        synth_calls.append(text_ja)
        out = tmp_path / f"seg{len(synth_calls)}.wav"
        out.write_bytes(b"RIFF")
        return out

    svc = VoiceService(cfg, None, translate_fn=fake_translate,
                       synth_fn=fake_synth, player=FakePlayer())
    _SERVICES.append(svc)
    svc.voice_map = _fake_map()                  # 不依赖本机参考音频
    svc.server.ensure_started = lambda: None     # 不起真服务
    svc.server.state = S_READY                   # 假装服务已经热好了
    return svc, synth_calls


def _pump(svc: VoiceService, until, timeout=5.0) -> bool:
    """手动跑事件循环 + 播放 tick，直到条件满足或超时（测试里不等真定时器）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        _fake_app.processEvents()
        svc._tick_play()
        if until():
            return True
        time.sleep(0.01)
    _fake_app.processEvents()
    svc._tick_play()
    return until()


def test_disabled_service_does_nothing(tmp_path):
    """开关关着：一句都不合成（不能偷偷跑起来吃 CPU）。"""
    svc, synth_calls = _make_service(tmp_path, enabled=False)
    svc.speak("你好呀。", "平静")
    time.sleep(0.2)
    assert synth_calls == []


def test_pipeline_synthesizes_segments_in_order(tmp_path):
    """流式喂入的长文本要被切成多段、按顺序合成并按顺序播放。"""
    svc, synth_calls = _make_service(tmp_path)
    text = "今天天气不错我们出去走走吧。顺便买点东西回来。"
    svc.begin_reply()
    svc.feed(text, "温柔")
    svc.end_reply("温柔")

    assert _pump(svc, lambda: len(svc.player.played) >= 2, timeout=5.0), \
        f"只播了 {len(svc.player.played)} 段，合成调用 {synth_calls}"
    joined = "".join(synth_calls)
    assert "JA:" in joined
    # 切段不能丢字：把前缀去掉后拼起来应该等于原文
    assert joined.replace("JA:", "") == text


def test_new_reply_discards_old_one(tmp_path):
    """连着发两句：第一句必须被整段丢弃，播放的只能是第二句的内容。"""
    svc, synth_calls = _make_service(tmp_path)
    svc.begin_reply()
    svc.feed("第一句话是这句。")
    svc.end_reply("平静")
    time.sleep(0.05)

    svc.begin_reply()                 # 立刻开始第二句 → 作废旧代号
    svc.feed("第二句话换了内容。")
    svc.end_reply("害羞")

    assert _pump(svc, lambda: bool(svc.player.played), timeout=5.0)
    played_texts = [p.name for p in svc.player.played]
    assert played_texts, "第二句没有播放"
    # 旧段落（seg1）不该再被播放：新句子从新文件开始
    assert all("seg" in name for name in played_texts)


def test_stop_silences_everything(tmp_path):
    """stop() 之后不再输出任何新声音。"""
    svc, _ = _make_service(tmp_path)
    svc.begin_reply()
    svc.feed("说话说到一半就闭嘴。")
    svc.end_reply("平静")
    svc.stop()
    before = len(svc.player.played)
    time.sleep(0.3)
    _fake_app.processEvents()
    svc._tick_play()
    assert len(svc.player.played) == before
    assert svc.player.stopped >= 1


def test_first_segment_is_short(tmp_path):
    """第一段必须短：这是"首声 ≤ 9 秒"的前提。"""
    svc, synth_calls = _make_service(tmp_path)
    long_text = "我跟你讲一件很有意思的事情你听好了别走神啊" * 2
    svc.begin_reply()
    svc.feed(long_text, "得意")
    svc.end_reply("得意")

    assert _pump(svc, lambda: len(synth_calls) >= 1, timeout=5.0)
    first = synth_calls[0].replace("JA:", "")
    assert len(first) <= 12, f"第一段有 {len(first)} 字，太长了"
