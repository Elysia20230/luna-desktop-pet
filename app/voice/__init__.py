# -*- coding: utf-8 -*-
"""
`app/voice` —— 让露娜用日语开口说话。

分工：
    voices.py      情绪 → 用哪段参考音频（音色）
    translate.py   中文台词 → 日语台词（交给 DeepSeek）
    synthesize.py  日语台词 + 音色 → wav 文件（交给 GPT-SoVITS 的 HTTP 接口）
    player.py      播放 wav（标准库 winsound，不额外装东西）
    server.py      负责把 GPT-SoVITS 的 api_v2.py 拉起来 / 关掉
    service.py     把上面几步串成一个"说一句"的动作，不卡界面

所有语音相关的设置都在 config.json 的 voice 段，想改行为不用动这里的代码。
"""
from __future__ import annotations

__all__ = ["voices", "translate", "synthesize", "player", "server", "service"]
