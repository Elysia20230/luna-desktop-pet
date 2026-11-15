# -*- coding: utf-8 -*-
"""
把"翻译 → 合成 → 播放"串起来的服务对象（流水线版）。

界面只调用 `speak(中文, 情绪)` 或流式三连 `begin_reply() / feed() / end_reply()`，
剩下的都在后台做：

    中文片段 ──翻译线程──> 日语 ──合成线程──> wav ──主线程──> 播放

为什么要分段（这是"语音等半天"的关键）：
    纯 CPU 合成 4 秒日语要 8~10 秒，而翻译只要 1.3 秒。整句一起合成，
    用户要等 10 秒以上才听到第一个字；现在把回复切成 12 / 20 字的小段，
    第一段合成完立刻播，剩下的在播放期间继续合成、接着播。
    实测首声从 12 秒降到 8 秒左右，长句中间也不再有大空档。

为什么翻译和合成要分开两个线程：
    翻译 1.3 秒 + 合成 6.5 秒。串行的话第二段要等到第一段播完才开始翻译，
    中间必然出现 1~2 秒空档；两个线程错开就能"边说边备货"。

打断：每次开始新的一句都让代号 +1，旧任务的每一步都检查代号，
      对不上就整段丢弃，所以快速连发只会听到最后一句。
失败一律降级：聊天照常，最多弹一条提示（同类错误 60 秒内只提示一次）。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from .player import WavPlayer
from .server import TTSServer
from .synthesize import (
    SOFT_MARKS, STRONG_MARKS, clip_for_speech, synthesize,
)
from .translate import (
    TranslationCache, has_speech_content, speech_text, translate_to_japanese,
)
from .voices import VoiceMap, load_voice_map

# speak_scope 的三个取值（设置界面里就是这三项）
SCOPE_FIRST = "first"       # 只念第一段：最快，适合"我就听一句"
SCOPE_PIPELINE = "pipeline"  # 分段念完：默认
SCOPE_FULL = "full"         # 整句一次合成：最连贯，最慢


class VoiceService(QObject):
    """露娜的"嗓子"。开关关着时它什么都不做。"""

    failed = Signal(str)        # 给用户看的提示（同一类错误限流）
    spoke = Signal(str, float)  # 开始播放：日语文案, 音频时长（秒）
    status = Signal(str)        # 语音状态文字（启动中/翻译中/合成中/已就绪）
    speech_done = Signal()      # 这一整段回复的日语全部念完了（语音通话靠它判断该重新听）

    def __init__(self, cfg, parent: QObject | None = None, *,
                 translate_fn=None, synth_fn=None, player=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.server = TTSServer(cfg, self)
        self.player = player or WavPlayer()
        self.cache = TranslationCache()
        self.voice_map: VoiceMap = load_voice_map(cfg)

        # 这两个函数可以在测试里替换成假实现，不联网也能验证流水线
        self._translate = translate_fn or translate_to_japanese
        self._synth = synth_fn or synthesize

        # ---- 流水线状态（都受 _cond 保护）----
        self._cond = threading.Condition(threading.Lock())
        self._gen = 0                 # 当前这一句的代号
        self._buf = ""                # 还没交给翻译线程的中文
        self._text = ""               # 最近一次喂入的累计文本（流式）
        self._consumed = 0            # 这份累计文本里已经切出去多少字
        self._ended = False           # 这句文字已经说完（后面没有新字了）
        self._first_done = False      # 是否已经切出过第一段（决定用哪个字数上限）
        self._emotion = ""            # 这句的情绪（决定音色）
        self._items: list[dict] = []  # 顺序播放队列：{"ja","wav","error"}
        self._stop_now = False        # 让工作线程立刻退出

        # ---- 播放状态（只在主线程读写）----
        self._play_idx = 0
        self._play_until = 0.0
        self._all_ended = False
        self._done_emitted = False    # speech_done 每句只发一次

        self._last_error_at = 0.0
        self._last_status = ""

        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._tick_play)

        self.server.state_changed.connect(self._on_server_state)

    # ---- 对外：开关与状态 --------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("voice.enabled", False))

    def should_speak(self, kind: str = "reply") -> bool:
        """这一句要不要出声（单独抽出来，测试里直接验逻辑）。"""
        if not self.enabled:
            return False
        if kind == "proactive" and not bool(self.cfg.get("voice.speak_proactive", True)):
            return False
        return self.voice_map.ok

    def reload(self) -> None:
        """设置改过之后重新读一遍音色表。"""
        self.voice_map = load_voice_map(self.cfg)

    def warm_up(self) -> None:
        """启动时预热（只拉服务、加载权重，不说话）。"""
        if self.enabled and bool(self.cfg.get("voice.autostart", True)):
            self._set_status("语音服务启动中…（首次约 15 秒）")
            self.server.ensure_started()

    # ---- 对外：说一句 ------------------------------------------------------
    def speak(self, text_zh: str, emotion: str = "", kind: str = "reply") -> None:
        """一次说完（主动搭话就走这里）。内部等价于 begin + feed + end。"""
        if not self.should_speak(kind):
            return
        self.begin_reply()
        self.feed(text_zh, emotion)
        self.end_reply(emotion)

    def begin_reply(self) -> int:
        """
        开始新的一句：作废上一句、停掉正在播的声音、把服务叫起来。

        返回这一句的代号（测试里用得上）。
        """
        with self._cond:
            self._gen += 1
            gen = self._gen
            self._buf = ""
            self._text = ""
            self._consumed = 0
            self._ended = False
            self._first_done = False
            self._emotion = ""
            self._items = []
            self._stop_now = False
            self._cond.notify_all()
        # 主线程里操作 QProcess
        self.server.ensure_started()
        self.player.stop()
        self._play_idx = 0
        self._play_until = 0.0
        self._all_ended = False
        self._done_emitted = False
        self._timer.start()
        threading.Thread(target=self._translate_worker, args=(gen,), daemon=True).start()
        threading.Thread(target=self._synth_worker, args=(gen,), daemon=True).start()
        return gen

    def feed(self, text: str, emotion: str = "") -> None:
        """
        流式喂入（引擎每来一段增量就调一次；传的是累计文本，不会重复处理）。

        为什么按"累计文本 + 已消费游标"算，而不是记住上次的长度：
        引擎在流式过程中可能把尾巴**收回去**（情绪标记还没闭合时 `_clean_partial`
        会先藏起来，等闭合再放出来），文本长度会短暂变短。旧实现只认"变长"，
        于是既会把标记碎片永久留在缓冲里（引出"末尾多一句"的 bug），
        又会在文本变短后按旧长度取增量、把后面几个字整段吞掉（漏念）。
        """
        text = text or ""
        with self._cond:
            if emotion:
                self._emotion = emotion
            self._text = text
            # 已消费的位置可能超出现文本长度（尾巴被收回去了）：那就没有新的字，
            # 等文本重新长过游标时再接着切，既不会重复念也不会丢字。
            self._buf = text[self._consumed:] if len(text) > self._consumed else ""
            self._cond.notify_all()

    def end_reply(self, emotion: str = "") -> None:
        """这句文字结束了：让工作线程把尾巴也处理掉。"""
        with self._cond:
            if emotion:
                self._emotion = emotion
            self._ended = True
            self._cond.notify_all()

    def stop(self) -> None:
        """立刻闭嘴（新回复到来、关掉语音、程序退出都用它）。"""
        with self._cond:
            self._gen += 1
            self._stop_now = True
            self._buf = ""
            self._text = ""
            self._consumed = 0
            self._items = []
            self._cond.notify_all()
        self._timer.stop()
        self.player.stop()

    def shutdown(self) -> None:
        """退出程序：停止播放、关掉我们自己拉起来的语音服务。"""
        self.stop()
        self.server.stop()

    # ---- 主线程：播放队列 --------------------------------------------------
    def _tick_play(self) -> None:
        """每 200ms 看一眼队首有没有合成好；一段播完再播下一段。"""
        now = time.time()
        if self._play_until and now < self._play_until:
            return
        with self._cond:
            gen = self._gen
            items = self._items
            idx = self._play_idx
            ended = self._ended and not self._buf
        if idx >= len(items):
            if ended and not self._all_ended:
                # 队列空了且后面不会再有新段：播完就收摊
                still_busy = any(item.get("wav") is None and item.get("error") is None
                                 for item in items)
                if not still_busy:
                    self._all_ended = True
                    self._timer.stop()
                    self._emit_speech_done()
            return

        item = items[idx]
        if item.get("wav") is None and item.get("error") is None:
            return                    # 还没合成好，等下一次 tick
        self._play_idx = idx + 1
        if item.get("error"):
            return                    # 这段失败就跳过，继续下一段
        self._do_play(item["wav"], item.get("ja") or "", gen)

    def _do_play(self, wav: Path, japanese: str, gen: int) -> None:
        try:
            duration = float(self.player.play(
                wav, float(self.cfg.get("voice.volume", 1.0) or 1.0)) or 0.0)
        except Exception as exc:  # noqa: BLE001 播放失败不影响聊天
            self._report(f"播放失败：{exc}")
            return
        # 留一点余量，避免上一段还没落幕就开始下一段
        self._play_until = time.time() + max(0.2, duration) + 0.08
        self.spoke.emit(japanese, duration)

    def _emit_speech_done(self) -> None:
        """
        这一整段回复彻底念完（也可能一句都没念成）。

        语音通话靠它判断"她说完话了，可以重新开麦"，
        所以即使合成失败也要发一次，否则通话会一直卡在"说话中"。
        """
        if self._done_emitted:
            return
        self._done_emitted = True
        self.speech_done.emit()

    # ---- 后台线程 1：切段 + 翻译 ------------------------------------------
    def _translate_worker(self, gen: int) -> None:
        try:
            while True:
                chunk = self._take_chunk(gen)
                if chunk is None:
                    return
                # 情绪标记的残片（"<m"、"【情绪："）和纯标点段落绝不能送去翻译：
                # 模型对没有语义的输入会自己编一整句日语，念出来就是"凭空多一句话"。
                chunk = speech_text(chunk)
                if not chunk or not has_speech_content(chunk):
                    continue
                item = self._append_item(gen)
                if item is None:
                    return
                try:
                    ja = self._translate(chunk, self.cfg, self.cache)
                except Exception as exc:  # noqa: BLE001
                    self._mark_error(gen, item, f"这句没能翻成日语：{exc}")
                    continue
                if not self._alive(gen):
                    return
                if not ja:
                    # 译文为空 = 这段没有可念的内容（纯标点等），静默作废。
                    # 不能报错刷屏：主人只是"这句话她不该开口"，不是出了故障。
                    self._mark_skipped(gen, item)
                    continue
                with self._cond:
                    item["ja"] = ja
                    self._cond.notify_all()
        finally:
            with self._cond:
                self._cond.notify_all()

    def _take_chunk(self, gen: int) -> str | None:
        """
        从缓冲区里切出下一段中文。返回 None = 这一句已经处理完。

        切法：优先在「。！？…」处断；否则攒到上限再硬切。
        上限：第一段 first_chars（默认 12），之后 segment_chars（默认 20）；
        speak_scope="full" 时整句一次切走。
        """
        scope = str(self.cfg.get("voice.speak_scope", SCOPE_PIPELINE) or SCOPE_PIPELINE)
        first_cap = max(4, int(self.cfg.get("voice.first_chars", 12) or 12))
        next_cap = max(first_cap, int(self.cfg.get("voice.segment_chars", 20) or 20))
        with self._cond:
            while True:
                if gen != self._gen:
                    return None
                if self._buf:
                    cap = 10 ** 6 if scope == SCOPE_FULL else (
                        first_cap if not self._first_done else next_cap)
                    if len(self._buf) < cap and not self._ended:
                        self._cond.wait(0.15)      # 字还没到齐，等等看
                        continue
                    chunk, self._buf = self._cut(self._buf, cap)
                    self._consumed += len(chunk)
                    self._first_done = True
                    if scope == SCOPE_FIRST:
                        # 只念第一段：剩下的直接扔掉
                        self._buf = ""
                        self._consumed = len(self._text)
                        self._ended = True
                    return chunk
                if self._ended:
                    return None
                self._cond.wait(0.15)

    @staticmethod
    def _cut(buf: str, cap: int) -> tuple[str, str]:
        """在 buf 前 cap 个字里找一个合适的断点，返回 (这一段的文字, 剩下的)。"""
        limit = min(len(buf), cap)
        for i in range(2, limit + 1):
            if buf[i - 1] in STRONG_MARKS:
                return buf[:i], buf[i:]
        for i in range(len(buf) and min(len(buf), cap), 1, -1):
            if i <= len(buf) and buf[i - 1] in SOFT_MARKS:
                return buf[:i], buf[i:]
        return buf[:limit], buf[limit:]

    # ---- 后台线程 2：合成 --------------------------------------------------
    def _synth_worker(self, gen: int) -> None:
        try:
            while True:
                item = self._next_to_synth(gen)
                if item is None:
                    return
                voice = self.voice_map.resolve(str(item.get("emotion") or self._emotion))
                if voice is None:
                    self._mark_error(gen, item, "音色表里没有可用的参考音频")
                    continue
                # 服务没起来就先等它（冷启动约 15 秒）；不然第一次合成必定连接被拒
                if not self._wait_server(gen):
                    self._mark_error(gen, item, "语音服务没能及时启动，这句就先不念了。")
                    return
                try:
                    wav = self._synth(
                        str(item.get("ja") or ""), voice, self.server.base_url,
                        timeout=float(self.cfg.get("voice.timeout_seconds", 30) or 30),
                    )
                except Exception as exc:  # noqa: BLE001
                    self._mark_error(gen, item, f"朗读没成功：{exc}")
                    continue
                if not self._alive(gen):
                    return
                with self._cond:
                    item["wav"] = wav
                    self._cond.notify_all()
        finally:
            with self._cond:
                self._cond.notify_all()

    def _wait_server(self, gen: int) -> bool:
        """
        等语音服务就绪（最多 voice.start_timeout 秒，默认 45）。

        为什么必须有这一步：服务是冷启动要 15 秒上下，而第一段翻译只要 1.3 秒，
        不做等待就直接 POST /tts，必然"连接被拒绝"，那一句就白念了。
        """
        from .server import S_ATTACHED, S_READY

        timeout = float(self.cfg.get("voice.start_timeout", 45) or 45)
        deadline = time.time() + timeout
        told = False
        while time.time() < deadline:
            if not self._alive(gen):
                return False
            if self.server.state in (S_READY, S_ATTACHED):
                return True
            if not told:
                self._set_status("语音服务启动中…（第一次约 15 秒）")
                told = True
            time.sleep(0.2)
        return False

    def _next_to_synth(self, gen: int) -> dict | None:
        """取"已经翻译好、还没合成"的那一条；都做完了就等/退出。"""
        with self._cond:
            while True:
                if gen != self._gen:
                    return None
                for item in self._items:
                    if item.get("ja") and item.get("wav") is None and not item.get("error") \
                            and not item.get("synth_started"):
                        item["synth_started"] = True
                        return item
                if self._ended and not self._buf and \
                        all(item.get("wav") is not None or item.get("error")
                            for item in self._items):
                    return None
                self._cond.wait(0.15)

    # ---- 小工具 ------------------------------------------------------------
    def _append_item(self, gen: int) -> dict | None:
        """在播放队列尾部挂一个占位项（保证播放顺序 = 翻译顺序）。"""
        with self._cond:
            if gen != self._gen:
                return None
            item = {"ja": None, "wav": None, "error": None,
                    "emotion": self._emotion}
            self._items.append(item)
            self._cond.notify_all()
            return item

    def _mark_skipped(self, gen: int, item: dict) -> None:
        """
        这段静默作废（没有可念的内容），不提示主人。

        为什么要单独记一个状态而不是直接丢掉占位项：播放队列按顺序取"已就绪或已作废"
        的项，占位项既没 wav 也没 error 时会一直卡住收尾判断（speech_done 永远不发，
        语音通话会一直等）。所以作废也要落一个终态。
        """
        with self._cond:
            if gen != self._gen:
                return
            item["error"] = "skip"
            self._cond.notify_all()

    def _mark_error(self, gen: int, item: dict, message: str) -> None:
        with self._cond:
            if gen != self._gen:
                return
            item["error"] = message
            self._cond.notify_all()
        self._report(message)

    def _alive(self, gen: int) -> bool:
        with self._cond:
            return gen == self._gen

    def _on_server_state(self, state: str) -> None:
        self._set_status("语音服务：" + state)

    def _set_status(self, text: str) -> None:
        if text != self._last_status:
            self._last_status = text
            self.status.emit(text)

    def _report(self, text: str) -> None:
        """同一类提示 60 秒内只报一次，免得刷屏。"""
        now = time.time()
        if now - self._last_error_at < 60.0:
            return
        self._last_error_at = now
        self.failed.emit(text)
