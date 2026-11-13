# -*- coding: utf-8 -*-
"""
DeepSeek 对话客户端（OpenAI 兼容的 /chat/completions，流式）。

为什么用 QThread + 信号？
    网络请求是阻塞的，不能放在界面线程里，否则窗口会卡住。
    这里把请求放到子线程，收到一个字就 emit 一次 chunk 信号，界面就能"逐字打字"。
    中途按"停止"按钮时调用 stop()，线程会在下一个数据块处自己退出。

对外提供：
    ChatWorker    —— 流式对话线程，配合界面用
    complete_sync —— 同步一次性调用（记忆提炼、屏幕评论这类后台小任务用）
    friendly_error—— 把各种异常翻译成露娜口吻的人话
"""
from __future__ import annotations

import json
import threading
from typing import Callable, Iterator

import httpx
from PySide6.QtCore import QThread, Signal

from .. import providers


def _endpoint(base_url: str, path: str = "") -> str:
    """
    把 base_url 拼成完整的 /chat/completions 地址。

    具体规则交给 app/providers.py 的 endpoint_for()：
    填域名、填到 /v1、填完整地址三种写法都能认（各家文档不一样，用户不该为此踩坑）。
    """
    return providers.endpoint_for(base_url)


def friendly_error(status: int | None, detail: str = "") -> str:
    """把 HTTP 状态码翻译成用户能看懂、且符合人设的一句话。"""
    if status == 401:
        return "钥匙不对哦。去设置 → 对话里把 API Key 填对，再来找我吧。"
    if status == 402:
        return "你的余额好像不够了呢。真是的，连这种小事都要我提醒。"
    if status == 403:
        return "那边不让我说话（HTTP 403）。检查一下这个 Key 有没有该模型的权限。"
    if status == 404:
        return "找不到接口呢。多半是「接口地址」少了 /v1 之类的后缀，去设置 → 对话里换个服务商试试。"
    if status == 429:
        return "一下子说得太急了，被拦住了。等一下再试吧。"
    if status and 500 <= status < 600:
        return "那边好像出了点乱子。稍微等一会儿再来。"
    if status:
        return f"请求失败了（HTTP {status}）。{detail[:120]}"
    return f"信号好像不太好。{detail[:120]}"


class ChatWorker(QThread):
    """一次性对话线程：run() 里发请求，边收边 emit。"""

    chunk = Signal(str)          # 增量文本
    finished_ok = Signal(str)    # 完整文本
    failed = Signal(str)         # 给用户看的错误消息

    def __init__(self, base_url: str, api_key: str, model: str, messages: list[dict],
                 temperature: float = 1.1, timeout_connect: int = 20, timeout_total: int = 120,
                 system_factory: Callable[[], str] | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.messages = messages
        self.temperature = temperature
        self.timeout_connect = timeout_connect
        self.timeout_total = timeout_total
        # 可选的"提示词工厂"：如果给了，就在本线程里调用它生成 system 提示词。
        # 为什么要这么绕：检索记忆可能要联网算向量，放在界面线程会卡窗口。
        self.system_factory = system_factory
        self._stop_flag = threading.Event()

    def stop(self) -> None:
        """界面点"停止"时调用。"""
        self._stop_flag.set()

    def _final_messages(self) -> list[dict]:
        """拼出真正要发出去的 messages（system 在最前面）。"""
        if self.system_factory is None:
            return list(self.messages)
        system = self.system_factory() or ""
        return ([{"role": "system", "content": system}] if system else []) + list(self.messages)

    # ---- 线程主体 ---------------------------------------------------------
    def run(self) -> None:  # noqa: C901 逻辑线性，不必拆
        if not self.api_key:
            # 没有 Key：走离线台词（engine 那边已经分流，这里只是双保险）
            self.failed.emit("还没有填 API Key 呢。现在只能靠我自己的嘴说话了。")
            return

        # 生成提示词也可能失败（检索、读人设文件），失败要给用户一句人话，别静默卡住
        try:
            messages = self._final_messages()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"准备台词的时候出错了：{exc}")
            return

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": True,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(self.timeout_total, connect=self.timeout_connect)
        collected: list[str] = []

        try:
            with httpx.Client(timeout=timeout) as client:
                with client.stream("POST", _endpoint(self.base_url), json=payload,
                                   headers=headers) as resp:
                    if resp.status_code != 200:
                        body = resp.read().decode("utf-8", "ignore")
                        self.failed.emit(friendly_error(resp.status_code, body))
                        return

                    for line in resp.iter_lines():
                        if self._stop_flag.is_set():
                            break
                        if not line:
                            continue
                        if line.startswith("data:"):
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                packet = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            choices = packet.get("choices") or []
                            if not choices:
                                continue
                            delta = choices[0].get("delta") or {}
                            piece = delta.get("content") or ""
                            if piece:
                                collected.append(piece)
                                self.chunk.emit(piece)
        except httpx.ConnectTimeout:
            self.failed.emit("连不上那边呢。检查一下网络，或者换个时间再试。")
            return
        except httpx.ReadTimeout:
            self.failed.emit("等了半天没动静……先算了，等下再聊吧。")
            return
        except Exception as exc:  # noqa: BLE001 网络异常一律转成人话
            self.failed.emit(friendly_error(None, str(exc)))
            return

        text = "".join(collected).strip()
        if not text:
            self.failed.emit("那边什么也没说。再试一次吧。")
            return
        self.finished_ok.emit(text)


def complete_sync(base_url: str, api_key: str, model: str, messages: list[dict],
                  temperature: float = 0.6, timeout: float = 60.0,
                  json_mode: bool = False) -> str:
    """
    同步调用一次，返回完整文本（不做流式）。
    用于后台小任务：从对话里提炼记忆、让模型描述截图内容等。
    失败时抛异常，由调用方决定怎么兜底。

    小坑：`response_format={"type":"json_object"}` 只有部分服务商支持（OpenAI/DeepSeek 支持，
    一些自建或本地模型会直接 400）。所以这里遇到 400 会自动去掉它重试一次——
    调用方的解析本来就是"能解析最好、不能解析就兜底"，少一个参数不影响主流程。
    """
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def _post(body: dict) -> str:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(_endpoint(base_url), json=body, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(friendly_error(resp.status_code, resp.text))
            data = resp.json()
            return (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""

    try:
        return _post(payload)
    except RuntimeError as exc:
        if json_mode and "HTTP 400" in str(exc):
            retry = dict(payload)
            retry.pop("response_format", None)
            return _post(retry)
        raise


def stream_sync(base_url: str, api_key: str, model: str, messages: list[dict],
                temperature: float = 1.1, timeout: float = 120.0) -> Iterator[str]:
    """生成的流式版本（命令行调试用，界面走 ChatWorker）。"""
    payload = {"model": model, "messages": messages, "temperature": temperature, "stream": True}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=timeout) as client:
        with client.stream("POST", _endpoint(base_url), json=payload, headers=headers) as resp:
            if resp.status_code != 200:
                raise RuntimeError(friendly_error(resp.status_code, resp.read().decode("utf-8", "ignore")))
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    packet = json.loads(data)
                except json.JSONDecodeError:
                    continue
                delta = (packet.get("choices") or [{}])[0].get("delta") or {}
                if delta.get("content"):
                    yield delta["content"]
