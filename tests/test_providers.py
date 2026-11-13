# -*- coding: utf-8 -*-
"""
服务商预设与地址拼接的测试。

这些点最容易在"换一家 API"时踩坑：
  · 用户填 https://api.deepseek.com（没 /v1）→ 要能拼对
  · 用户填 https://api.openai.com/v1（有 /v1）→ 不能拼成 /v1/v1
  · 用户直接贴完整 .../chat/completions → 不能再叠一层
  · 切服务商时 Key 要分开存，切回来还能用
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import providers  # noqa: E402
from app.config import Config  # noqa: E402


# ---- 地址拼接 -------------------------------------------------------------
def test_endpoint_empty_falls_back_to_default():
    assert providers.endpoint_for("") == "https://api.deepseek.com/chat/completions"
    assert providers.endpoint_for(None) == "https://api.deepseek.com/chat/completions"


def test_endpoint_bare_domain():
    assert providers.endpoint_for("https://api.deepseek.com") == \
        "https://api.deepseek.com/chat/completions"


def test_endpoint_with_v1():
    assert providers.endpoint_for("https://api.openai.com/v1") == \
        "https://api.openai.com/v1/chat/completions"


def test_endpoint_strips_trailing_slash():
    assert providers.endpoint_for("https://api.deepseek.com/") == \
        "https://api.deepseek.com/chat/completions"
    assert providers.endpoint_for("https://api.openai.com/v1/") == \
        "https://api.openai.com/v1/chat/completions"


def test_endpoint_full_url_untouched():
    full = "https://example.com/v1/chat/completions"
    assert providers.endpoint_for(full) == full


def test_endpoint_local_ollama():
    assert providers.endpoint_for("http://127.0.0.1:11434/v1") == \
        "http://127.0.0.1:11434/v1/chat/completions"


def test_endpoint_unknown_host_keeps_path():
    """不认识的域名：原样补 /chat/completions，不擅自塞 /v1。"""
    assert providers.endpoint_for("https://my-proxy.example.com/openai") == \
        "https://my-proxy.example.com/openai/chat/completions"


# ---- 服务商目录 -----------------------------------------------------------
def test_all_providers_have_models_and_docs():
    for provider in providers.PROVIDERS:
        assert provider.id and provider.label
        if provider.id == "custom":      # 「自定义」就是留给用户自填的，没有预设模型与文档
            assert provider.base_url == ""
            continue
        assert provider.models_list, f"{provider.id} 没有候选模型"
        assert provider.docs.startswith("http")


def test_get_unknown_returns_custom():
    assert providers.get("nope").id == "custom"
    assert providers.get("deepseek").id == "deepseek"


def test_guess_provider_by_host():
    assert providers.guess_provider("https://api.deepseek.com") == "deepseek"
    assert providers.guess_provider("https://api.openai.com/v1") == "openai"
    assert providers.guess_provider("https://api.moonshot.cn/v1") == "moonshot"
    # 认不出来的地址按"自定义"处理，而不是猜错成某一家
    assert providers.guess_provider("https://my-proxy.example.com") == "custom"


def test_models_of_returns_copy():
    models = providers.models_of("deepseek")
    assert isinstance(models, list) and models
    models.append("x")  # 改返回值不能污染预设
    assert "x" not in providers.models_of("deepseek")


# ---- Key 分服务商保存 -----------------------------------------------------
def test_api_key_prefers_provider_slot(tmp_path):
    cfg = Config(path=tmp_path / "config.json")
    cfg.set("api.provider", "openai")
    cfg.set("api.keys", {"openai": "sk-openai"})
    cfg.set("api.api_key", "sk-legacy")
    assert cfg.api_key == "sk-openai"


def test_set_api_key_writes_both_slots(tmp_path):
    cfg = Config(path=tmp_path / "config.json")
    cfg.set("api.provider", "moonshot")
    cfg.set_api_key("  sk-moon  ")
    assert cfg.api_key == "sk-moon"
    assert cfg.get("api.keys")["moonshot"] == "sk-moon"
    # 老字段也一起更新，记忆提炼/翻译那些只读 cfg.api_key 的地方才不会失联
    assert cfg.get("api.api_key") == "sk-moon"


def test_switching_provider_keeps_other_keys(tmp_path):
    cfg = Config(path=tmp_path / "config.json")
    cfg.set("api.provider", "deepseek")
    cfg.set_api_key("sk-deep")
    cfg.set("api.provider", "openai")
    cfg.set_api_key("sk-open")
    assert cfg.get("api.keys") == {"deepseek": "sk-deep", "openai": "sk-open"}

    cfg.set("api.provider", "deepseek")
    assert cfg.api_key == "sk-deep"


def test_default_provider_is_deepseek(tmp_path):
    cfg = Config(path=tmp_path / "config.json")
    assert cfg.get("api.provider") == "deepseek"
    assert cfg.get("api.keys") == {}


@pytest.mark.parametrize("provider", [p.id for p in providers.PROVIDERS])
def test_endpoint_for_each_preset(provider):
    """每家预设的地址都要能拼出 /chat/completions 结尾的完整 URL。"""
    base = providers.get(provider).base_url
    if not base:
        pytest.skip("custom 家没有预设地址")
    url = providers.endpoint_for(base)
    assert url.endswith("/chat/completions")
    assert "//chat" not in url.replace("https://", "").replace("http://", "")
