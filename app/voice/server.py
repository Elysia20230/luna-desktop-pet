# -*- coding: utf-8 -*-
"""
把 GPT-SoVITS 的语音服务管起来：需要时拉起，用完关掉。

调用的是它自带的 `api_v2.py`（FastAPI，默认端口 9880，我们默认用 9888 避开冲突）。
几个必须记住的细节：
1. **工作目录必须是 GPT-SoVITS 的根目录**，否则 api_v2.py 里 sys.path 找不到 GPT_SoVITS 包；
2. 用仓库自己的 `runtime\\python.exe`（Python 3.9，torch 等都是配好的，别用项目的 .venv）；
3. 纯 CPU 加载模型约 14 秒，所以是"启动后轮询，好了再报 ready"，不阻塞界面；
4. 就绪探测只要"HTTP 有响应"就算活着——`GET /control` 不带参数会返回 400，那是正常的；
5. 端口上已经有服务（比如你手动开的）就直接复用，不再拉第二个进程。
"""
from __future__ import annotations

import threading
import urllib.parse
from pathlib import Path

import httpx
from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from ..config import ROOT
from .synthesize import probe_alive

LOG_FILE = ROOT / "logs" / "tts_server.log"

# 状态文字（界面直接显示）
S_STOPPED = "未启动"
S_STARTING = "启动中…"
S_LOADING = "加载模型…"
S_READY = "已就绪"
S_ATTACHED = "已连接现有服务"


class TTSServer(QObject):
    """管理 api_v2.py 进程；界面只看 `state` 和 `state_changed`。"""

    state_changed = Signal(str)
    _probe_done = Signal(bool)      # 后台探测结束（内部用，回到主线程处理）

    def __init__(self, cfg, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.state: str = S_STOPPED
        self.note: str = ""               # 出错时的原因，界面用红字显示
        self._proc: QProcess | None = None
        self._mine = False                # 进程是不是我们拉起来的（不是就不许杀）
        self._poll = QTimer(self)
        self._poll.setInterval(1000)
        self._poll.timeout.connect(self._on_tick)
        self._waited = 0.0
        self._weights_loaded = False
        self._probe_busy = False
        self._probe_done.connect(self._on_probe)

    # ---- 基本信息 ---------------------------------------------------------
    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{int(self.cfg.get('voice.port', 9888) or 9888)}"

    @property
    def root(self) -> Path:
        return Path(str(self.cfg.get("voice.root", "") or "")).expanduser()

    @property
    def python(self) -> Path:
        configured = str(self.cfg.get("voice.python", "") or "").strip()
        if configured:
            return Path(configured).expanduser()
        return self.root / "runtime" / "python.exe"

    @property
    def alive(self) -> bool:
        return probe_alive(self.base_url, timeout=self._probe_timeout)

    @property
    def _probe_timeout(self) -> float:
        """探测超时：默认 0.4 秒。以前是 2 秒、还在界面线程里，启动期会明显卡顿。"""
        try:
            return float(self.cfg.get("voice.probe_timeout", 0.4) or 0.4)
        except Exception:  # noqa: BLE001
            return 0.4

    # ---- 启动 / 停止 ------------------------------------------------------
    def ensure_started(self) -> None:
        """需要说话时调用：没起就起，起过就复用。"""
        if self.state in (S_READY, S_ATTACHED, S_STARTING, S_LOADING):
            return
        if probe_alive(self.base_url):
            # 端口上已经有服务（可能是用户自己开的 webui/api），直接复用
            self._set_state(S_ATTACHED)
            self._start_weights_thread()
            return
        if not (self.root / "api_v2.py").is_file():
            self._fail(f"没找到语音服务：{self.root / 'api_v2.py'}")
            return
        if not self.python.is_file():
            self._fail(f"没找到 Python 运行时：{self.python}")
            return

        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        proc = QProcess(self)
        proc.setWorkingDirectory(str(self.root))
        proc.setStandardOutputFile(str(LOG_FILE))
        proc.setStandardErrorFile(str(LOG_FILE))
        proc.finished.connect(lambda *_: self._on_proc_finished())
        proc.start(str(self.python), ["api_v2.py", "-p", str(self.cfg.get("voice.port", 9888))])
        if not proc.waitForStarted(5000):
            self._fail("语音服务启动失败（runtime 的 python 没能运行 api_v2.py）")
            return
        self._proc = proc
        self._mine = True
        self._waited = 0.0
        self._weights_loaded = False
        self._set_state(S_STARTING)
        self._poll.start()

    def stop(self) -> None:
        """退出程序时调用：只关我们自己拉起来的那个进程。"""
        self._poll.stop()
        if self._mine and self._proc is not None:
            try:
                self._proc.terminate()
                if not self._proc.waitForFinished(6000):
                    self._proc.kill()
                    self._proc.waitForFinished(3000)
            except Exception:  # noqa: BLE001
                pass
        self._proc = None
        self._mine = False
        self._set_state(S_STOPPED)

    # ---- 内部 -------------------------------------------------------------
    def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.state_changed.emit(state)

    def _fail(self, reason: str) -> None:
        self.note = reason
        self._poll.stop()
        self._set_state(S_STOPPED)
        self.state_changed.emit("failed")

    def _on_proc_finished(self) -> None:
        if self.state in (S_STARTING, S_LOADING):
            self._fail("语音服务进程退出了，详情看 logs/tts_server.log")
        elif self.state == S_READY:
            self._set_state(S_STOPPED)

    def _on_tick(self) -> None:
        """每秒探测一次是否已就绪。探测本身放后台线程，别卡住界面。"""
        if self.state not in (S_STARTING, S_LOADING):
            self._poll.stop()
            return
        if self._probe_busy:
            return
        self._probe_busy = True
        threading.Thread(target=self._probe_thread, daemon=True).start()

    def _probe_thread(self) -> None:
        alive = probe_alive(self.base_url, timeout=self._probe_timeout)
        self._probe_busy = False
        self._probe_done.emit(alive)      # 跨线程发信号，Qt 会自动排队回主线程

    def _on_probe(self, alive: bool) -> None:
        if self.state not in (S_STARTING, S_LOADING):
            return
        if alive:
            self._poll.stop()
            self._set_state(S_LOADING)
            self._start_weights_thread()
            return
        self._waited += 1.0
        if self._waited > 90:
            self._fail("语音服务 90 秒还没起来，详情看 logs/tts_server.log")

    def _start_weights_thread(self) -> None:
        if self._weights_loaded:
            self._set_state(S_READY)
            return
        threading.Thread(target=self._load_weights, daemon=True).start()

    def _load_weights(self) -> None:
        """把露娜的音色权重挂上去（后台线程，避免卡界面）。"""
        try:
            for kind, key in (("gpt", "voice.gpt_weights"), ("sovits", "voice.sovits_weights")):
                rel = str(self.cfg.get(key, "") or "").strip()
                if not rel:
                    continue
                url = f"{self.base_url}/set_{kind}_weights?weights_path=" + urllib.parse.quote(rel)
                resp = httpx.get(url, timeout=180)
                if resp.status_code != 200:
                    raise RuntimeError(f"{kind} 权重加载失败（HTTP {resp.status_code}）")
            self._weights_loaded = True
            self.note = ""
            self._set_state(S_READY)
        except Exception as exc:  # noqa: BLE001
            self._fail(f"音色权重没挂上：{exc}")
