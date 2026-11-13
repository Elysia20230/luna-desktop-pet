# -*- coding: utf-8 -*-
"""
"看图"能力：把截图交给支持视觉的模型，换回一句画面描述。

DeepSeek 官方的 deepseek-chat 目前不支持图片，所以这里单独配置：
在 settings.json / config.json 的 vision 段填一个 OpenAI 兼容的多模态接口即可，
例如硅基流动的 Qwen/Qwen2.5-VL-7B-Instruct。

没配也能用——那就只根据"前台窗口标题"做反应，不发送任何图像。
"""
from __future__ import annotations

import json

import httpx

from .context import png_to_data_url


class VisionError(Exception):
    """看图失败（配置缺失、网络错误、接口报错都会抛这个）。"""


VISION_PROMPT = (
    "这是一张电脑屏幕截图。请用一句中文（不超过40字）客观描述主人在做什么，"
    "例如：在写 Python 代码、在看番剧、在填表格、在玩某款游戏。"
    "不要评价，不要角色扮演，不要复述界面上的具体文字内容。"
)


def describe_image(png: bytes, cfg) -> str:
    """调用视觉模型描述截图。失败抛 VisionError（带人话原因）。"""
    base_url = (cfg.get("vision.base_url") or "").rstrip("/")
    model = cfg.get("vision.model") or ""
    api_key = cfg.get("vision.api_key") or ""
    if not (base_url and model and api_key):
        raise VisionError("还没有配置视觉模型")

    url = base_url + "/chat/completions" if base_url.endswith("/v1") else base_url + "/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": VISION_PROMPT},
                {"type": "image_url", "image_url": {"url": png_to_data_url(png)}},
            ],
        }],
        "temperature": 0.3,
        "max_tokens": 120,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
            resp = client.post(url, headers=headers, content=json.dumps(payload))
    except httpx.HTTPError as exc:
        raise VisionError(f"网络不通（{exc.__class__.__name__}）") from exc

    if resp.status_code == 401:
        raise VisionError("视觉接口的 Key 不对")
    if resp.status_code >= 400:
        raise VisionError(f"接口返回 {resp.status_code}")

    try:
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        raise VisionError("接口返回的格式看不懂") from exc
    return (text or "").strip().replace("\n", " ")[:160]
