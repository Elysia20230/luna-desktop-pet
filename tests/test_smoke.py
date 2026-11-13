# -*- coding: utf-8 -*-
"""
冒烟测试：不联网、不打开真实窗口，验证核心逻辑没坏。

跑法（在 LunaPet 目录下）：
    .venv\\Scripts\\python.exe -m pytest tests -q
需要 Qt 相关测试时设置环境变量：
    $env:QT_QPA_PLATFORM="offscreen"
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import mood                                     # noqa: E402
from app.config import Config                            # noqa: E402
from app.memory.embedder import HashEmbedder             # noqa: E402
from app.memory.retriever import MemoryRetriever         # noqa: E402
from app.memory.store import MemoryStore                 # noqa: E402
from app.memory.vector_store import SQLiteVectorStore    # noqa: E402
from app.persona import FALLBACK_PERSONA, Persona        # noqa: E402
from app.sprites import SpriteLibrary                    # noqa: E402


# --------------------------------------------------------------------------
# 情绪标记
# --------------------------------------------------------------------------
def test_parse_mood_tag():
    text, emo = mood.parse("过来。\n<mood:害羞>")
    assert text == "过来。"
    assert emo == "害羞"


def test_parse_mood_brackets_and_chinese_colon():
    assert mood.parse("哼。[[mood:生气]]") == ("哼。", "生气")
    assert mood.parse("好吧。【情绪：温柔】") == ("好吧。", "温柔")


def test_parse_mood_unknown_word_falls_back_none():
    text, emo = mood.parse("知道了。<mood:嘿嘿>")
    assert text == "知道了。"
    assert emo is None


def test_guess_emotion_from_text():
    assert mood.guess("真拿你没办法呢") == "温柔"
    assert mood.guess("过来。") in mood.EMOTIONS


# --------------------------------------------------------------------------
# 记忆存取
# --------------------------------------------------------------------------
def test_store_add_dedupe_and_remove(tmp_path: Path):
    store = MemoryStore(path=tmp_path / "memory.json")
    first = store.add_fact("主人喜欢喝黑咖啡")
    assert first is not None
    # 重复提到同一条 -> 只加重权重，不新增，因此返回 None
    again = store.add_fact("主人喜欢喝黑咖啡")
    assert again is None
    assert len(store.facts()) == 1
    assert store.facts()[0]["weight"] > 1.0

    assert store.remove_fact(first["id"]) is True
    assert store.facts() == []


def test_store_persist_and_reload(tmp_path: Path):
    path = tmp_path / "memory.json"
    store = MemoryStore(path=path)
    store.add_fact("主人是学生", tags=["身份"])
    store.save()

    reloaded = MemoryStore(path=path)
    assert reloaded.facts()[0]["text"] == "主人是学生"
    assert reloaded.facts()[0]["tags"] == ["身份"]


def test_store_corrupted_file_is_backed_up(tmp_path: Path):
    path = tmp_path / "memory.json"
    path.write_text("{ 这不是 json", encoding="utf-8")
    store = MemoryStore(path=path)
    assert store.facts() == []
    assert list(tmp_path.glob("memory.json.bak.*")), "坏文件应该被备份，而不是直接删"


# --------------------------------------------------------------------------
# 检索
# --------------------------------------------------------------------------
def test_retriever_finds_relevant_fact(tmp_path: Path):
    store = MemoryStore(path=tmp_path / "memory.json")
    store.add_fact("主人最近在做一个桌宠项目", tags=["项目"])
    store.add_fact("主人昨晚去吃了烤肉")
    store.add_fact("主人喜欢下雨天")

    store_path = tmp_path / "vec.sqlite"
    retriever = MemoryRetriever(
        store,
        HashEmbedder(dim=256),                 # 用本地哈希向量，完全不联网
        SQLiteVectorStore(path=store_path, fingerprint="hash:256"),
        top_k=2,
    )
    retriever.sync_index()

    hits = retriever.retrieve("桌宠做到哪一步了")
    assert hits, "应该至少召回一条"
    assert "桌宠" in hits[0]["text"]


def test_retriever_handles_empty_store(tmp_path: Path):
    store = MemoryStore(path=tmp_path / "memory.json")
    retriever = MemoryRetriever(store, HashEmbedder(dim=64),
                                SQLiteVectorStore(path=tmp_path / "v.sqlite", fingerprint="hash:64"))
    assert retriever.retrieve("随便说点什么") == []
    assert retriever.format_for_prompt([]) == ""


# --------------------------------------------------------------------------
# 提示词拼接
# --------------------------------------------------------------------------
def test_persona_prompt_contains_sections():
    persona = Persona(include_relations=False, extra_note="")
    persona.base_text = FALLBACK_PERSONA
    prompt = persona.system_prompt(
        memory_text="- 主人喜欢黑咖啡", screen_text="她瞄到你正在写代码", max_chars=50)
    assert "主人喜欢黑咖啡" in prompt
    assert "她瞄到你正在写代码" in prompt
    assert "<mood:情绪>" in prompt
    assert "50 字" in prompt


def test_persona_prompt_skips_empty_sections():
    persona = Persona(include_relations=False, extra_note="")
    persona.base_text = "人设"
    prompt = persona.system_prompt()
    assert "你记得的事" not in prompt
    assert "你现在看到的画面" not in prompt


# --------------------------------------------------------------------------
# 立绘：每个情绪在每套服装下都要能解析出一个真实存在的编号
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def library(tmp_path_factory) -> SpriteLibrary:
    cfg = Config(path=tmp_path_factory.mktemp("cfg") / "config.json")
    lib = SpriteLibrary(cfg)
    if not lib.catalog.get("groups"):
        pytest.skip("没有找到 assets/sprite_catalog.json，先运行 tools/scan_sprites.py")
    return lib


def test_every_emotion_resolves_in_every_group(library: SpriteLibrary):
    for group in library.groups:
        for family in library.families:
            available = library.available(family, group)
            if not available:
                continue
            for emotion in mood.EMOTIONS:
                idx = library.resolve_index(emotion, family, group)
                assert idx in available, f"{group}/{family} 的 {emotion} 解析出了不存在的 {idx}"


def test_sprite_path_exists(library: SpriteLibrary):
    group = library.groups[0]
    family = library.families[0]
    idx = library.available(family, group)[0]
    path = library.path(family, group, idx)
    assert path is not None and path.exists()


def test_expressions_file_is_valid_json():
    data = json.loads((ROOT / "assets" / "expressions.json").read_text(encoding="utf-8"))
    for emotion in mood.EMOTIONS:
        assert emotion in data["candidates"], f"expressions.json 缺少 {emotion}"


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
def test_config_defaults_and_dotted_access(tmp_path: Path):
    cfg = Config(path=tmp_path / "config.json")
    assert cfg.get("api.model") == "deepseek-chat"
    cfg.set("api.model", "deepseek-reasoner")
    assert cfg.get("api.model") == "deepseek-reasoner"
    cfg.save()

    again = Config(path=tmp_path / "config.json")
    assert again.get("api.model") == "deepseek-reasoner"
    # 旧配置缺字段时，用默认值补齐
    assert again.get("memory.top_k") == 6


def test_config_broken_json_recovers(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text("not json at all", encoding="utf-8")
    cfg = Config(path=path)
    assert cfg.get("api.model") == "deepseek-chat"
    assert list(tmp_path.glob("config.json.bak.*"))
