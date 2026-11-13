# 致谢 / Credits

这个桌宠能跑起来，是因为站在一堆人的肩膀上。按"对这个项目影响多大"排序：

## 角色与作品

- **Navel** —— 《月に寄りそう乙女の作法》（近月少女的礼仪）。
  樱小路露娜、朝日、游星这些名字和他们的故事都属于它。
- 人设资料的整理与提示词的蒸馏，是基于原作台本与公开设定的二次研究。

## 语音（可选功能）

- [**GPT-SoVITS**](https://github.com/RVC-Boss/GPT-SoVITS) —— 少样本语音克隆，
  本项目用它把中文回复翻成日语再念出来。它的作者与社区贡献者做了最重的那部分工作。
- [**faster-whisper**](https://github.com/SYSTRAN/faster-whisper) / [**CTranslate2**](https://github.com/OpenNMT/CTranslate2)
  —— 本地识别，让"实时语音通话"不用联网、不花钱。
- **OpenAI Whisper** —— faster-whisper 用的模型源头。

## 记忆与检索

- [**ChromaDB**](https://github.com/chroma-core/chroma) —— 本地向量库。
- [**SiliconFlow 硅基流动**](https://siliconflow.cn/) —— bge-m3 向量接口（也可换成别的兼容服务）。

## 界面与运行时

- [**Qt for Python (PySide6)**](https://doc.qt.io/qtforpython/) —— 透明置顶窗口、
  自绘气泡、设置面板，全都靠它。
- [**Pillow**](https://python-pillow.org/)、[**httpx**](https://github.com/encode/httpx)、
  [**ffmpeg**](https://ffmpeg.org/)。
- [**霞鹜文楷 LXGW WenKai**](https://github.com/lxgw/LxgwWenKai) —— 文档字体建议。

## 灵感与同类项目

- 各种"桌面宠物 / AI 角色陪伴"开源项目与社区讨论，
  让大家意识到"透明窗口 + 大模型 + 语音"这条路是走得通的。

## 特别说明

上面这些项目的**作者并没有为这个项目背书**，也没有参与开发；
如果这个项目有 bug，那是我的问题，请不要去他们的仓库里报。
