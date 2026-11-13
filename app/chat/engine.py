# -*- coding: utf-8 -*-
"""
对话总调度：把"人设 + 记忆 + 屏幕 + 用户这句话"组装成一次请求，
再把流式回来的文字一小段一小段交给界面，最后解析出情绪换立绘。

为什么单独一层：界面只负责显示，记忆只负责存取，这里负责"按顺序串起来"。
以后想接别的模型，改这里（或改 client.py）就够了，界面一行都不用动。
"""
from __future__ import annotations

import json
import time
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from .. import mood
from ..config import DATA_DIR
from ..memory.extractor import MemoryExtractor
from ..memory.retriever import MemoryRetriever
from ..memory.store import MemoryStore
from ..persona import Persona
from ..timecontext import now_local
from .client import ChatWorker
from .offline import reply as offline_reply
from .offline import reply_proactive as offline_proactive
from .offline import reply_screen as offline_screen

HISTORY_FILE = DATA_DIR / "history.json"
HISTORY_KEEP = 200         # 本地滚动保存最近多少条消息（记录窗口要能往前翻）

# 主动说话时塞给模型的"假用户消息"：不写进历史，只用来触发一次生成
PROACTIVE_TRIGGER = "（主人在忙自己的事，已经安静好一会儿了。你自己找句话说，别追问太多。）"

# 「看看我在干什么」用的触发句：同样是假用户消息，不写进历史。
# 为什么要单独一句：这次是主人主动让她看的，语气是"就着画面点评一句"，
# 不是"自言自语"，所以场合提示也不一样（见 persona.screen_comment）。
SCREEN_TRIGGER = "（主人刚让你看了一眼他的屏幕。就着看到的东西随口说一句，别追问。）"


class ChatEngine(QObject):
    """一次只处理一句话；界面通过信号拿结果。"""

    started = Signal()                      # 开始生成
    text_chunk = Signal(str)                # 当前累计的完整文字（便于整段刷新气泡）
    finished = Signal(str, str)             # (干净的文字, 情绪名)
    failed = Signal(str)                    # 人话错误提示
    note = Signal(str)                      # 后台小提示（记忆索引、提炼结果等）
    proactive_said = Signal(str, str)       # 她自己开口说的话 (台词, 情绪)
    screen_said = Signal(str, str)          # 「看屏幕」后的随口点评 (台词, 情绪)

    def __init__(self, cfg, store: MemoryStore, retriever: MemoryRetriever,
                 persona: Persona, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.store = store
        self.retriever = retriever
        self.persona = persona
        self.history: list[dict] = self._load_history()
        # 上一次对话是什么时候（用来告诉她"距离上回说话过了多久"）
        self.last_talk_at = self._find_last_talk()
        self.screen_text = ""               # 最近一次看屏幕的结论，由界面设置
        self._worker: ChatWorker | None = None
        self._buffer = ""
        self._turn_count = 0
        self._extracting = False

    # ---- 对外接口 ---------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    @property
    def online(self) -> bool:
        """有没有配 API Key —— 决定了是真聊天还是念离线台词。"""
        return bool(self.cfg.api_key)

    def set_screen_text(self, text: str) -> None:
        self.screen_text = text or ""

    def stop(self) -> None:
        if self._worker is not None:
            self._worker.stop()

    def clear_history(self) -> None:
        self.history = []
        self.last_talk_at = None    # 清了历史，"上次说话"自然也就没了
        self._save_history()

    # ---- 主流程 -----------------------------------------------------------
    def send(self, user_text: str) -> None:
        user_text = (user_text or "").strip()
        if not user_text or self.busy:
            return

        self._buffer = ""
        self.started.emit()

        if not self.online:
            # 没填 Key：用本地台词，延迟一点点，让动画自然
            self._offline_answer(user_text)
            return

        # 只做"廉价快照"：这两样归属于界面线程，交给工作线程直接读会踩线程安全。
        # history 里都是 str，浅拷贝一份就够。
        conversation = [dict(turn) for turn in self.history[-12:]]
        conversation.append({"role": "user", "content": user_text})
        screen_text = self.screen_text
        max_chars = int(self.cfg.get("api.max_chars", 60))
        now, last_talk = self._time_pair()   # 时间要在主线程取，别让工作线程去读时钟

        def make_system() -> str:
            """
            注意：这个函数跑在 ChatWorker 的工作线程里。
            检索记忆可能要联网算查询向量（几百毫秒到几秒），放在界面线程就会卡窗口。
            """
            memory_text = ""
            try:
                facts = self.retriever.retrieve(user_text)
                memory_text = self.retriever.format_for_prompt(facts)
                hit_ids = [fact["id"] for fact in facts]
                if hit_ids:                       # 记一次"这条被用到过"
                    self.store.touch(hit_ids)
            except Exception:  # noqa: BLE001 检索失败也照常聊天，不影响回答
                memory_text = ""
            return self.persona.system_prompt(
                memory_text=memory_text,
                screen_text=screen_text,
                max_chars=max_chars,
                now=now,
                last_talk=last_talk,
            )

        # 构造线程本身也可能出错（比如参数写错、httpx 导入失败）。
        # 这类错误如果没人接住，Qt 会静默吞掉异常，界面就永远停在"正在斟酌用词…"。
        self._spawn(conversation, make_system,
                    lambda full: self._on_finished(full, user_text))

    def send_proactive(self) -> None:
        """
        没人跟她说话时，她自己开口。

        和 send() 的区别只有三点：
        1. 不写进 history（免得"自言自语"污染下一轮上下文）；
        2. 不触发记忆提炼（那不是对话）；
        3. 结果走 proactive_said 信号，界面对着它弹气泡 / 塞进对话窗。
        """
        if self.busy:
            return

        self._buffer = ""
        if not self.online:
            # 离线：用内置的"自言自语"台词，延迟一点点让动效自然
            QTimer.singleShot(320, self._offline_proactive)
            return

        # 带上最近两条，语气才接得上；再补一条"假用户消息"触发生成
        conversation = [dict(turn) for turn in self.history[-2:]]
        conversation.append({"role": "user", "content": PROACTIVE_TRIGGER})
        screen_text = self.screen_text
        max_chars = int(self.cfg.get("api.max_chars", 60))
        now, last_talk = self._time_pair()

        def make_system() -> str:
            return self.persona.system_prompt(
                memory_text="",
                screen_text=screen_text,
                max_chars=max_chars,
                proactive=True,
                now=now,
                last_talk=last_talk,
            )

        self._spawn(conversation, make_system, self._on_proactive_finished,
                    on_failed=self._on_proactive_failed, stream=False)

    def send_screen_comment(self) -> None:
        """
        主人点了「看看我在干什么」：就着刚才截到的画面，随口点评一句。

        和 send() 的区别（这也是"不算一次对话"的全部含义）：
        1. 不写进 history —— 聊天记录窗口、下一轮上下文里都不会出现这次偷看；
        2. 不触发记忆提炼 —— "偷看一眼"不是聊天内容，不该被记成事实；
        3. 结果走 screen_said 信号，由主窗口直接弹气泡 / 朗读。

        但和 send() 一样是**流式**的（stream=True）：气泡要边生成边逐字长出来，
        语音流水线也能在第一个短句成型时立刻开始合成。
        """
        if self.busy:
            return

        self._buffer = ""
        self.started.emit()

        if not self.online:
            # 没填 Key：用内置的"看到屏幕会说什么"台词，延迟一点点让动效自然
            QTimer.singleShot(320, self._offline_screen)
            return

        # 带最近两条，语气接得上；末尾补一条假用户消息触发生成（不落盘）
        conversation = [dict(turn) for turn in self.history[-2:]]
        conversation.append({"role": "user", "content": SCREEN_TRIGGER})
        screen_text = self.screen_text
        max_chars = int(self.cfg.get("api.max_chars", 60))
        now, last_talk = self._time_pair()

        def make_system() -> str:
            # 跑在工作线程里，只用上面抓好的快照，不要碰界面对象
            return self.persona.system_prompt(
                memory_text="",              # 看屏幕是"随口一句"，不必检索记忆，省几百毫秒
                screen_text=screen_text,
                max_chars=max_chars,
                screen_comment=True,
                now=now,
                last_talk=last_talk,
            )

        self._spawn(conversation, make_system, self._on_screen_finished,
                    on_failed=self._on_screen_failed, stream=True)

    # ---- 看屏幕点评的收尾 -------------------------------------------------
    def _on_screen_finished(self, full: str) -> None:
        """
        「看屏幕」说完了。

        注意：这里**故意不写 history、不存盘、不加轮次、不提炼记忆**。
        只要少了这句注释，以后很容易有人顺手 copy 一份 _on_finished 过来。
        """
        clean, emo = mood.parse(full or self._buffer)
        clean = (clean or "").strip() or "……"
        emo = emo or mood.guess(clean)
        self._worker = None
        self.screen_said.emit(clean, emo)

    def _on_screen_failed(self, message: str) -> None:
        """看屏幕点评失败：记一条后台提示，退回内置台词，别让主人以为她哑了。"""
        self._worker = None
        self.note.emit(f"看屏幕没说话成：{message}")
        self._offline_screen()

    def _offline_screen(self) -> None:
        """离线模式下的"看到屏幕会说什么"。"""
        line, emo = offline_screen(self._role_name())
        self.screen_said.emit(line, emo)

    def _spawn(self, conversation: list[dict], make_system, on_finished,
               on_failed=None, stream: bool = True) -> None:
        """
        真正把 ChatWorker 建起来并接上信号。

        conversation / make_system 都必须是提前准备好的快照，
        因为 worker 跑在别的线程里，直接读界面对象会踩线程安全。

        stream=False 时只把字收到缓冲区，不发 text_chunk——
        主动说话没有"对话窗的气泡"要填，发了反而会在窗口里留下空泡。
        """
        try:
            self._worker = ChatWorker(
                base_url=self.cfg.get("api.base_url", "https://api.deepseek.com"),
                api_key=self.cfg.api_key,
                model=self.cfg.get("api.model", "deepseek-chat"),
                messages=conversation,
                temperature=float(self.cfg.get("api.temperature", 1.1)),
                timeout_connect=int(self.cfg.get("api.timeout_connect", 20)),
                timeout_total=float(self.cfg.get("api.timeout_total", 120)),
                system_factory=make_system,
            )
            self._worker.chunk.connect(self._on_chunk if stream else self._accumulate_only)
            self._worker.finished_ok.connect(on_finished)
            self._worker.failed.connect(on_failed or self._on_failed)
            self._worker.start()
        except Exception as exc:  # noqa: BLE001
            self._worker = None
            self._report_internal_error(exc)
            self.failed.emit("刚才没接上……（内部错误，详情见 logs/运行错误.txt）")

    def _report_internal_error(self, exc: BaseException) -> None:
        """把内部异常写进日志，免得 pythonw 下完全看不到。"""
        try:
            from ..config import ROOT

            log_dir = ROOT / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            with (log_dir / "运行错误.txt").open("a", encoding="utf-8") as fh:
                fh.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 构造对话线程失败 =====\n")
                traceback.print_exception(type(exc), exc, exc.__traceback__, file=fh)
        except Exception:  # noqa: BLE001 连日志都写不了就算了，别把事情搞得更糟
            pass


    def _role_name(self) -> str:
        """角色卡里的称呼（朝日 / 游星…），离线台词里用来替换 {name}。"""
        role = getattr(self.persona, "role", None)
        return getattr(role, "label", "") or ""

    def _offline_answer(self, user_text: str) -> None:
        """离线模式：从台词库里挑一句风格对味的。"""
        line, emo = offline_reply(user_text, self._role_name())
        self._buffer = line

        def _deliver() -> None:
            self.history.append(self._stamp("user", user_text))
            self.history.append(self._stamp("assistant", line))
            self.last_talk_at = now_local()
            self._save_history()
            self.text_chunk.emit(line)
            self.finished.emit(line, emo)
            self._worker = None

        QTimer.singleShot(420, _deliver)

    def _offline_proactive(self) -> None:
        """离线模式下的"自己嘀咕一句"。"""
        line, emo = offline_proactive(self._role_name())
        self.proactive_said.emit(line, emo)

    def _on_proactive_finished(self, full: str) -> None:
        """
        主动说话的收尾。
        注意：这里不写 history、不触发记忆提炼，也不需要 started/text_chunk
        （对话窗没参与这次生成，气泡由主窗口负责显示）。
        """
        clean, emo = mood.parse(full or self._buffer)
        clean = (clean or "").strip() or "……"
        emo = emo or mood.guess(clean)
        self._worker = None
        self.proactive_said.emit(clean, emo)

    def _on_proactive_failed(self, message: str) -> None:
        """
        自己搭话失败不算什么大事：记一条后台提示，退回内置台词，
        免得主人以为程序坏了。
        """
        self._worker = None
        self.note.emit(f"自己搭话没成功：{message}")
        line, emo = offline_proactive(self._role_name())
        self.proactive_said.emit(line, emo)

    def _on_chunk(self, piece: str) -> None:
        self._buffer += piece
        self.text_chunk.emit(self._clean_partial(self._buffer))

    def _accumulate_only(self, piece: str) -> None:
        """主动说话时只攒字，不往对话窗的气泡里填。"""
        self._buffer += piece

    def _on_finished(self, full: str, user_text: str) -> None:
        clean, emo = mood.parse(full or self._buffer)
        clean = (clean or "").strip()
        if not clean:
            clean = "……"
        emo = emo or mood.guess(clean)

        self.history.append(self._stamp("user", user_text))
        self.history.append(self._stamp("assistant", clean))
        self.last_talk_at = now_local()
        self._save_history()

        self.text_chunk.emit(clean)
        self.finished.emit(clean, emo)
        self._worker = None

        self._turn_count += 1
        self._maybe_extract()

    def _on_failed(self, message: str) -> None:
        self._worker = None
        self.failed.emit(message)

    # ---- 后台记忆提炼 -----------------------------------------------------
    def _maybe_extract(self) -> None:
        every = int(self.cfg.get("memory.auto_extract_every", 8) or 0)
        if every <= 0 or self._turn_count % every != 0 or self._extracting or not self.online:
            return
        self._extracting = True
        MemoryExtractor(
            store=self.store,
            base_url=self.cfg.get("api.base_url", "https://api.deepseek.com"),
            api_key=self.cfg.api_key,
            model=self.cfg.get("api.model", "deepseek-chat"),
            turns=self.history[-8:],
            on_done=self._on_extracted,
        ).start()

    def _on_extracted(self, added: list[dict], error: str) -> None:
        """注意：这个回调跑在后台线程里，只做"算向量"这种重活，UI 更新交给信号。"""
        self._extracting = False
        if error:
            self.note.emit(f"记忆提炼失败：{error}")
            return
        if not added:
            return
        try:
            status = self.retriever.sync_index()
            self.note.emit(f"记住了 {len(added)} 件事。" + (f"（{status}）" if status else ""))
        except Exception as exc:  # noqa: BLE001
            self.note.emit(f"记住了 {len(added)} 件事，但向量索引没建成：{exc}")

    # ---- 时间感 -----------------------------------------------------------
    def _time_pair(self):
        """
        给 system prompt 用的 (现在, 上次说话时间)。

        关掉 behavior.time_aware 时返回 (None, None)，人设里就不会出现时间段，
        方便对比"有时间/没时间"两种效果。
        """
        try:
            aware = bool(self.cfg.get("behavior.time_aware", True))
        except Exception:  # noqa: BLE001
            aware = True
        if not aware:
            return None, None
        return now_local(), self.last_talk_at

    @staticmethod
    def _stamp(role: str, content: str) -> dict:
        """历史条目：角色 + 内容 + 本地时间戳（精确到秒，够用来算间隔）。"""
        return {
            "role": role,
            "content": content,
            "at": now_local().isoformat(timespec="seconds"),
        }

    @staticmethod
    def _parse_at(value) -> "datetime | None":
        """把历史里的 'at' 还原成 datetime；坏数据返回 None，不抛。"""
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _find_last_talk(self):
        """从历史里找最后一次"主人说话"的时间；没有就退回 None。"""
        for turn in reversed(self.history):
            if turn.get("role") != "user":
                continue
            stamp = self._parse_at(turn.get("at"))
            if stamp is not None:
                return stamp
        return None

    # ---- 本地历史 ---------------------------------------------------------
    def _load_history(self) -> list[dict]:
        try:
            if HISTORY_FILE.exists():
                data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    rows = [d for d in data if isinstance(d, dict) and "role" in d]
                    if rows:
                        self._fill_missing_at(rows)
                    return rows
        except Exception:  # noqa: BLE001
            pass
        return []

    @staticmethod
    def _fill_missing_at(rows: list[dict]) -> None:
        """
        老版本存的历史没有 at 字段。用文件修改时间兜底补上，
        这样升级后"上次说话多久"不会显示成"从未说过话"。
        """
        try:
            mtime = datetime.fromtimestamp(HISTORY_FILE.stat().st_mtime)
        except OSError:
            return
        fallback = mtime.isoformat(timespec="seconds")
        for row in rows:
            if not row.get("at"):
                row["at"] = fallback

    def _save_history(self) -> None:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            self.history = self.history[-HISTORY_KEEP:]
            HISTORY_FILE.write_text(json.dumps(self.history, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    # ---- 小工具 -----------------------------------------------------------
    @staticmethod
    def _clean_partial(text: str) -> str:
        """
        流式输出时，情绪标记可能只回来一半（比如 "<mood:害"），
        这里把没闭合的尾巴先藏起来，等下一个字到了再显示。
        """
        import re

        cleaned = mood.parse(text)[0]
        cleaned = re.sub(r"[<【\[（(]\s*(mood|情绪)[^>】\])）]*$", "", cleaned, flags=re.I)
        return cleaned
