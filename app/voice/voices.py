# -*- coding: utf-8 -*-
"""
情绪 → 音色（参考音频）。

GPT-SoVITS 的"音色"本质上由它听到的那段参考录音决定：
同一句话，给 `[soft]` 那段录音它就用软下来的语气，给 `[asking]` 就用质问的语气。
所以这里做的事很简单——查表，把"她现在是得意还是害羞"换成"用哪段 wav 当参考"。

表在 `assets/voice_emotions.json`，长这样：

    {
      "dir": "…/sakurakouji_luna[v2Pro]",          // 参考音频所在目录
      "refs": { "soft": { "label": "柔和",
                          "file": "[soft]….wav",
                          "prompt": "朝日の悪いところは…" } },
      "emotion_map": { "温柔": "soft", "生气": "asking" },
      "speed": { "生气": 1.06 },
      "default": "normal1"
    }

想加一套新音色：把剪好的人声丢进 dir，在 refs 里加一条（prompt 必须是那段录音的原文），
再在 emotion_map 里指过去即可，不用改代码。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..config import ASSETS_DIR

DEFAULT_MAP_FILE = ASSETS_DIR / "voice_emotions.json"


@dataclass
class RefVoice:
    """一套"音色"：一段参考音频 + 它的原文 + 这一句的语速。"""

    name: str                 # 表里的键，例如 "soft"
    label: str                # 显示用的中文名，例如 "柔和"
    audio: Path               # 参考音频的绝对路径
    prompt: str               # 参考音频的原文（必须和录音内容一致）
    speed: float = 1.0        # 这一句的语速倍率


class VoiceMap:
    """一张"情绪 → 音色"的表，读坏了也不会让程序崩。"""

    def __init__(
        self,
        refs: dict[str, dict],
        emotion_map: dict[str, str],
        speeds: dict[str, float],
        default: str,
        dir_path: Path | None,
        source: Path | None,
        warning: str = "",
    ) -> None:
        self.refs = refs or {}
        self.emotion_map = emotion_map or {}
        self.speeds = speeds or {}
        self.default = default or next(iter(self.refs), "")
        self.dir = dir_path
        self.source = source
        self.warning = warning
        # 哪些参考音频实际不在了（设置页会红字提示）
        self.missing: list[str] = [
            name for name, info in self.refs.items() if not Path(info["audio"]).is_file()
        ]

    # ---- 查询 -------------------------------------------------------------
    @property
    def ok(self) -> bool:
        """至少有一套可用的参考音频。"""
        return any(Path(info["audio"]).is_file() for info in self.refs.values())

    def names(self) -> list[str]:
        return list(self.refs)

    def label(self, name: str) -> str:
        return (self.refs.get(name) or {}).get("label", name)

    def audio_path(self, name: str) -> Path | None:
        info = self.refs.get(name)
        return Path(info["audio"]) if info else None

    def resolve(self, emotion: str = "") -> RefVoice | None:
        """
        按情绪找音色。找不到、音频丢了，都会尽量降级到 default；
        实在一套都没有就返回 None（调用方直接跳过朗读，不影响聊天）。
        """
        name = self.emotion_map.get(emotion or "", self.default)
        info = self.refs.get(name)
        if not info or not Path(info["audio"]).is_file():
            info = self.refs.get(self.default)
            name = self.default
        if not info or not Path(info["audio"]).is_file():
            # 最后再试一次：随便找一套还在的
            for other, other_info in self.refs.items():
                if Path(other_info["audio"]).is_file():
                    info, name = other_info, other
                    break
        if not info:
            return None
        return RefVoice(
            name=name,
            label=info.get("label", name),
            audio=Path(info["audio"]),
            prompt=info.get("prompt", ""),
            speed=float(self.speeds.get(emotion or "", 1.0) or 1.0),
        )


def _abs_refs(raw: dict, dir_path: Path | None) -> dict[str, dict]:
    """把 refs 里的 file 拼成绝对路径。"""
    out: dict[str, dict] = {}
    for name, info in (raw or {}).items():
        if not isinstance(info, dict):
            continue
        file_name = str(info.get("file") or "").strip()
        if not file_name:
            continue
        path = Path(file_name)
        if not path.is_absolute():
            path = (dir_path or Path(".")) / file_name
        out[name] = {
            "label": info.get("label") or name,
            "audio": str(path),
            "prompt": info.get("prompt") or "",
        }
    return out


def load_voice_map(cfg) -> VoiceMap:
    """读取音色表；文件缺失/写坏了就用一份空表 + warning，绝不抛异常。"""
    configured = str(cfg.get("voice.voice_map_file", "") or "").strip()
    path = Path(configured) if configured else DEFAULT_MAP_FILE
    default_voice = str(cfg.get("voice.default_voice", "normal1") or "normal1")

    if not path.is_file():
        return VoiceMap({}, {}, {}, default_voice, None, path,
                        f"没有找到音色表：{path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return VoiceMap({}, {}, {}, default_voice, None, path,
                        f"音色表读取失败（{exc}），先按默认处理")

    if not isinstance(data, dict):
        return VoiceMap({}, {}, {}, default_voice, None, path, "音色表格式不对，先按默认处理")

    dir_path = data.get("dir") or None
    if dir_path:
        dir_path = Path(str(dir_path))
        if not dir_path.is_absolute():
            dir_path = path.parent / dir_path
    refs = _abs_refs(data.get("refs") or {}, dir_path)
    emotion_map = {str(k): str(v) for k, v in (data.get("emotion_map") or {}).items()}
    speeds = {str(k): float(v) for k, v in (data.get("speed") or {}).items()}
    default = str(data.get("default") or default_voice)

    warning = ""
    if default not in refs and refs:
        default = next(iter(refs))
        warning = f"默认音色 {data.get('default')} 不在 refs 里，改用 {default}"
    return VoiceMap(refs, emotion_map, speeds, default, dir_path, path, warning)


def save_emotion_overrides(path: Path | str, mapping: dict[str, str]) -> Path:
    """
    把"情绪 → 音色"的选择写回音色表（设置面板的下拉框保存时走这里）。

    只改 emotion_map 这一项，其它字段（dir/refs/speed/default）原样保留，
    这样用户自己的注释、备注不会被冲掉。写坏了会把原文件备份成 .bak。
    """
    target = Path(path)
    data: dict = {}
    if target.is_file():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:  # noqa: BLE001 原文件坏了就重建，但先留一份备份
            try:
                target.with_suffix(target.suffix + ".bak").write_text(
                    target.read_text(encoding="utf-8"), encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
            data = {}

    current = data.get("emotion_map")
    if not isinstance(current, dict):
        current = {}
    for emotion, name in (mapping or {}).items():
        if name:
            current[str(emotion)] = str(name)
    data["emotion_map"] = current

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
    return target
