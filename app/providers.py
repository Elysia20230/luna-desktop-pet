# -*- coding: utf-8 -*-
"""
对话模型的"服务商预设"。

为什么要这张表：
    绝大多数国产/国外模型都提供 OpenAI 兼容的 /chat/completions，
    所以本程序只写一套请求逻辑；差别集中在「接口地址」和「模型名」。
    用户不该为了换个模型去查文档、抄 base_url——这里预置好，设置里选一下就行。

对外提供：
    PROVIDERS         —— 预设列表（设置里的下拉）
    get(pid)          —— 按 id 取预设，认不出给自定义
    guess_provider()  —— 从用户填的地址反查是哪家
    endpoint_for()    —— 把各种写法的地址统一成 .../chat/completions
    models_of()       —— 某家的常用模型名（下拉候选）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

# 没填地址时的兜底（和 config.py 的默认值保持一致）
DEFAULT_BASE = "https://api.deepseek.com"


@dataclass(frozen=True)
class Provider:
    """一家服务商的预设。"""

    id: str
    label: str
    base_url: str                    # 接口根地址（不含 /chat/completions）
    models: tuple[str, ...] = field(default_factory=tuple)
    docs: str = ""                   # 官方文档/开 Key 的地方，设置里给个链接用
    note: str = ""                   # 一句话提醒（比如"本地服务，要先跑 ollama serve"）

    @property
    def models_list(self) -> list[str]:
        return list(self.models)


# 顺序就是设置里下拉的顺序：国内常用的排前面
PROVIDERS: tuple[Provider, ...] = (
    Provider(
        "deepseek",
        "DeepSeek（默认）",
        "https://api.deepseek.com",
        ("deepseek-chat", "deepseek-reasoner"),
        "https://platform.deepseek.com/api_keys",
        "最便宜好用的起点；deepseek-chat 适合聊天，reasoner 会先想再答",
    ),
    Provider(
        "openai",
        "OpenAI",
        "https://api.openai.com/v1",
        ("gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"),
        "https://platform.openai.com/api-keys",
        "国内直连可能需要代理",
    ),
    Provider(
        "siliconflow",
        "硅基流动 SiliconFlow",
        "https://api.siliconflow.cn/v1",
        ("deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-R1",
         "Qwen/Qwen2.5-72B-Instruct", "Qwen/Qwen3-8B"),
        "https://cloud.siliconflow.cn/account/ak",
        "和记忆用的 bge-m3 是同一家的 Key，可以共用",
    ),
    Provider(
        "moonshot",
        "月之暗面 Kimi",
        "https://api.moonshot.cn/v1",
        ("moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"),
        "https://platform.moonshot.cn/console/api-keys",
        "",
    ),
    Provider(
        "zhipu",
        "智谱 GLM",
        "https://open.bigmodel.cn/api/paas/v4",
        ("glm-4-flash", "glm-4-air", "glm-4-plus"),
        "https://bigmodel.cn/usercenter/apikeys",
        "glm-4-flash 有免费额度，适合先试试",
    ),
    Provider(
        "dashscope",
        "阿里通义千问",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ("qwen-plus", "qwen-turbo", "qwen-max"),
        "https://bailian.console.aliyun.com/",
        "走的是「兼容模式」接口，地址不能少 compatible-mode/v1",
    ),
    Provider(
        "ollama",
        "本地 Ollama（免费离线）",
        "http://127.0.0.1:11434/v1",
        ("qwen2.5:7b", "qwen2.5:3b", "llama3.1:8b", "gemma3:4b"),
        "https://ollama.com/download",
        "先在命令行跑 ollama serve 或开着 Ollama 应用；Key 随便填一个非空值即可",
    ),
    Provider(
        "custom",
        "自定义 / 自建反代",
        "",
        (),
        "",
        "自己填接口地址与模型名，只要是 OpenAI 兼容的 /chat/completions 就能用",
    ),
)

_BY_ID: dict[str, Provider] = {p.id: p for p in PROVIDERS}


def get(provider_id: str | None) -> Provider:
    """按 id 取预设；认不出返回「自定义」。"""
    return _BY_ID.get((provider_id or "").strip() or "custom", _BY_ID["custom"])


def label_of(provider_id: str | None) -> str:
    return get(provider_id).label


def models_of(provider_id: str | None) -> list[str]:
    return get(provider_id).models_list


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except Exception:  # noqa: BLE001 用户手抖写成半截地址也不能崩
        return ""


def guess_provider(base_url: str | None) -> str:
    """从接口地址反查服务商 id；认不出来返回 custom。"""
    host = _host(base_url or "")
    if not host:
        return "custom"
    # 先按预设里的 host 精确比，再退回"包含"匹配（应付子域名/反代）
    for p in PROVIDERS:
        if p.base_url and _host(p.base_url) == host:
            return p.id
    table = {
        "api.deepseek.com": "deepseek",
        "api.openai.com": "openai",
        "api.siliconflow.cn": "siliconflow",
        "api.moonshot.cn": "moonshot",
        "open.bigmodel.cn": "zhipu",
        "dashscope.aliyuncs.com": "dashscope",
    }
    for h, pid in table.items():
        if host == h or host.endswith("." + h):
            return pid
    if host in ("127.0.0.1", "localhost") or host.endswith(".local"):
        return "ollama" if "11434" in (base_url or "") else "custom"
    return "custom"


def endpoint_for(base_url: str | None) -> str:
    """
    把用户填的地址统一成能 POST 的完整地址。

    认得的三种写法都照顾到：
        https://api.deepseek.com                → https://api.deepseek.com/chat/completions
        https://api.openai.com/v1               → https://api.openai.com/v1/chat/completions
        https://x.com/v1/chat/completions       → 原样（别重复拼）
    另外：只填了域名（路径为空）会先补上预设里的版本前缀，避免 OpenAI 这类必须带 /v1 的服务 404。
    """
    base = (base_url or "").strip() or DEFAULT_BASE
    base = base.rstrip("/")
    if base.lower().endswith("/chat/completions"):
        return base

    # 只填了域名：先用预设的完整根地址（带上 /v1 之类）
    pid = guess_provider(base)
    preset = _BY_ID.get(pid)
    if preset is not None and preset.base_url:
        parts = urlsplit(base)
        if parts.path in ("", "/"):
            return preset.base_url.rstrip("/") + "/chat/completions"

    return base + "/chat/completions"
