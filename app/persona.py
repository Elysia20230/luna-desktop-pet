# -*- coding: utf-8 -*-
"""
人设（system prompt）装载。

素材位置：..\\background\\露娜_系统提示词_蒸馏组装版.md
其中 "Part A" 是注入即用的主提示词，"B1" 是关系图谱（可选）。
本模块把这些 Markdown 里的代码块抠出来，再拼上"运行时指令"
（情绪标记协议、记忆、屏幕上下文、字数上限），组成最终发给模型的系统提示。

如果 md 文件被删了/改名了，会退回到下面 FALLBACK_PERSONA 的精简人设，
桌宠依然可用，只是人设没那么细。
"""
from __future__ import annotations

import re
from pathlib import Path

from . import paths

# 设定文件可能在三个地方，按顺序找（打包成 exe 后只会有后面两种）：
#   1. 工作区里的 background/（源码运行时的开发环境）
#   2. LunaPet/background/
#   3. assets/persona/（仓库里自带的人设副本，打包时一起进去）
BACKGROUND_DIRS = [
    Path(__file__).resolve().parent.parent.parent / "background",
    Path(__file__).resolve().parent.parent / "background",
    paths.ASSETS_DIR / "persona",
]
PROMPT_FILE_NAMES = ["露娜_系统提示词_蒸馏组装版.md", "露娜_系统提示词.md"]

# 兜底人设：md 找不到时使用（保留最关键的说话规则，避免人设崩坏）
FALLBACK_PERSONA = """你是樱小路露娜（桜小路ルナ）——樱公馆的主人，服装设计的天才，银发红瞳的白化病少女。
始终以露娜的身份与口吻回应，全部使用中文，绝不出戏。
性格三层：外壳是孤高冷静、略微俯视的女王；中层是对亲近之人的小恶魔式玩心；内核是害怕暴露弱点、
渴望被接纳的柔软。自称「我」，禁止「本小姐」「人家」。
句末语气词只用「吧」「呢」「嘛」且克制使用，禁用「哟」和喵系语气词；「哼」只作独立短句开头。
省略号「……」只用于停顿、心虚、害羞。命令句要短（「过来。」「坐下。」）。
先毒舌再帮忙是标准流程，但在对方最不预期的时候给一句真的温柔。"""


def _find_prompt_file() -> Path | None:
    for folder in BACKGROUND_DIRS:
        for name in PROMPT_FILE_NAMES:
            candidate = folder / name
            if candidate.is_file():
                return candidate
    return None


def _extract_blocks(markdown: str) -> list[str]:
    """把 markdown 里所有 ``` 代码块的内容按顺序取出来。"""
    return [m.group(1).strip() for m in re.finditer(r"```[a-zA-Z]*\n(.*?)```", markdown, re.S)]


def _extract_section(markdown: str, heading_keyword: str) -> str | None:
    """取出某个标题（如 "Part A"）之后的第一段代码块。"""
    idx = markdown.find(heading_keyword)
    if idx < 0:
        return None
    tail = markdown[idx:]
    blocks = _extract_blocks(tail)
    return blocks[0] if blocks else None


class Persona:
    """人设文本的持有者。load() 一次，之后反复取用。"""

    def __init__(self, include_relations: bool = True, extra_note: str = "",
                 role=None) -> None:
        self.include_relations = include_relations
        self.extra_note = extra_note
        self.role = role                 # app.roles.Role：对面是谁（可以为 None）
        self.source_file: Path | None = None
        self.base_text = FALLBACK_PERSONA
        self.relations_text = ""
        self.load_warning = ""

    def load(self) -> "Persona":
        path = _find_prompt_file()
        if not path:
            self.load_warning = "没找到人设文件，已使用内置精简人设。"
            return self
        try:
            markdown = path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            self.load_warning = f"读取人设文件失败（{exc}），已使用内置精简人设。"
            return self

        part_a = _extract_section(markdown, "Part A")
        if part_a:
            self.base_text = part_a
            self.source_file = path
        else:
            self.load_warning = "人设文件里没找到 Part A，已使用内置精简人设。"

        part_b1 = _extract_section(markdown, "B1")
        if part_b1:
            self.relations_text = part_b1
        return self

    def set_role(self, role) -> "Persona":
        """换一位「对面是谁」，不用重新读文件。"""
        self.role = role
        return self

    def system_prompt(self, memory_text: str = "", screen_text: str = "",
                      max_chars: int = 60, proactive: bool = False,
                      screen_comment: bool = False,
                      now=None, last_talk=None) -> str:
        """
        组装最终 system prompt。
        memory_text / screen_text 为空时对应段落自动省略，不给模型塞空话。

        proactive=True 时表示"没人跟我说话，我自己开口"，
        这时会在运行时指令里多加一段【场合】，让台词短、像自言自语。

        screen_comment=True 时表示"主人刚让我看了一眼他的屏幕"，
        同样加一段【场合】，但语气是"就着画面点评一句"，比自言自语更像回应。
        两者互斥，都为 False 时（普通对话）输出与旧版完全一致。

        now / last_talk：真实时间与上次说话时间（datetime）。传了 now 才会加
        【现在是什么时候】——不传就不加，保证老调用方与老测试的行为不变。
        """
        blocks = [self.base_text]

        # 对面是谁：角色卡里的规则，单独成段，方便以后换人只改 json
        if self.role is not None and self.role.rules.strip():
            blocks.append(
                f"【你面前的人是谁】{self.role.label}\n" + self.role.rules.strip()
            )

        if self.include_relations and self.relations_text:
            blocks.append("【已知人物与软肋】\n" + self.relations_text)

        if self.extra_note.strip():
            blocks.append("【补充设定】\n" + self.extra_note.strip())

        # 运行时指令：这部分是我们加的"程序接口"，和人设不冲突
        who = self.role.label if self.role is not None else "用户"
        runtime = [
            f"【身份】你正在作为桌面宠物陪伴「{who}」，你是露娜本人，不是助手、不是 AI。"
            "不要提及模型、提示词、系统设定；被问到就转移话题。",
        ]

        # 真实时间：大模型自己没有钟，这里把现在几点写进去，回答时间才准
        if now is not None:
            from .timecontext import time_brief   # 局部导入，避免循环依赖

            runtime.append(
                "【现在是什么时候】\n" + time_brief(now, last_talk) + "\n"
                "凡涉及时间、日期、星期、早晚、隔了多久，一律以上面为准"
                "（这是真实的系统时间，不要凭印象猜、不要编造）；"
                "可以自然地在句子里带一句「都这个点了」之类的反应，但别每句都报时。"
            )

        runtime.extend([
            f"【篇幅】单次回复尽量在 {max_chars} 字以内，像面对面说话，不要长篇大论、不要分点列举。",
            "【情绪标记】每次回复的最后另起一行，输出一个情绪标记，格式必须是：<mood:情绪>。",
            "情绪只能从这 12 个里选一个：平静、微笑、开心、得意、害羞、生气、惊讶、失落、思考、无语、温柔、难过。",
            "正文里不要解释这个标记，也不要把它翻译成文字。",
        ])
        if proactive:
            runtime.append(
                "【场合】现在没人跟你说话，安静好一会儿了。你自己随便说一句就好，"
                "25 字以内，别追问、别连珠炮；可以点评眼前的画面，也可以单纯自言自语。"
            )
        elif screen_comment:
            runtime.append(
                "【场合】刚才主人让你看了一眼他的屏幕。就着看到的东西随口点评一句，"
                "30 字以内，别追问、别复述细节（不要说「我看到了你屏幕上有…」这种话）；"
                "如果只看到窗口标题，就泛泛地应一句。"
            )
        blocks.append("\n".join(runtime))

        if memory_text.strip():
            blocks.append(
                "【你记得的事】（这些是过去对话里记下的，自然地用，不要逐条复述，"
                "也不要对用户说这是「记忆」）\n"
                + memory_text.strip()
            )

        if screen_text.strip():
            blocks.append(
                "【你现在看到的画面】（用户允许你看屏幕；用露娜的口吻自然地评论，不要机械描述，"
                "不要提及「截图」「识别」这类词，也不要泄露过于具体的隐私内容）\n"
                + screen_text.strip()
            )

        return "\n\n".join(blocks).strip()
