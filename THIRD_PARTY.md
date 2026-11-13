# 第三方素材与权利声明 / Third-Party Notices

这个仓库里**只有代码和自制资源**才是 MIT 许可的。下面这些内容各有各的权利人，
用之前请自己确认清楚。**本仓库不包含任何游戏立绘、CG、语音文件。**

---

## 一、角色与世界观（版权归原权利人）

- **樱小路露娜**（桜小路ルナ）、朝日、游星、八千代、尤希尔、里想奈等角色，
  以及《月に寄りそう乙女の作法》（中译《近月少女的礼仪》，Navel，2012）的世界观，
  版权归 **Navel 及原权利人**所有。
- 仓库里的 `assets/persona/*.md`（人设资料、蒸馏后的系统提示词）是**为让大模型扮演该角色
  而整理的研究笔记**，其中引用的角色设定与台词语感属于原作品。它们不是官方文本的再分发，
  而是二次整理；如权利人认为不妥，请开 issue，会立刻删除。
- `assets/expressions.json`、`assets/wardrobe.json` 里的**表情编号与服装分组**，来自对
  立绘文件名的整理（`lun_<尺寸>_<服装>_<动作>_<表情>.png`），本身不含图像。

## 二、立绘 / CG / 语音（不进 git 仓库）

- 程序运行时读取 `Sprite/` 目录下的立绘（`LunaPet.exe` 同级、或上一级、或在设置里指定）。
  **这些 PNG 不在本仓库里**，`.gitignore` 已排除。
- **可选素材包**：为了让人下载完就能看见露娜，项目在 Release 里额外挂了一个
  `luna-sprites-v*.zip`（顶层是 `Sprite/` + 素材说明）。它：
  - 不是 git 仓库的一部分，也不在程序包（`luna-desktop-pet-v*-win64.zip`）里；
  - 与代码的 MIT 许可**无关**，版权归 Navel 及相关权利人；
  - 仅限个人学习研究使用，禁止商用与二次分发；
  - **权利人一句话即可撤下**（项目会删除该 Release 资产，仓库不受影响）。
- `assets/voice/taihen_ni_kibun_ga_ii.wav`（"心情大好～"彩蛋）是从游戏语音里截的片段，
  同样**不入库**，需要的话自己放进去（见 `docs/修改指南.md` §9.3）。
- `docs/preview_*.png` 是立绘的裁切演示图，也不入库。

## 三、依赖的第三方开源项目（这里只列关键的）

| 项目 | 作用 | 许可 |
|---|---|---|
| [PySide6 / Qt for Python](https://doc.qt.io/qtforpython/) | 窗口、透明立绘、界面控件 | LGPL-3.0（Qt 部分为 LGPL/GPL 双许可） |
| [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) | 日语语音合成（可选的"念出来"功能） | MIT（其模型与参考音频另行约定） |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | 本地语音识别（实时通话用，免费离线） | MIT |
| [CTranslate2](https://github.com/OpenNMT/CTranslate2) | faster-whisper 的推理后端 | MIT |
| [Whisper 模型](https://huggingface.co/Systran/faster-whisper-base) | 识别模型权重 | MIT（OpenAI 发布） |
| [sounddevice / PortAudio](https://github.com/spatialaudio/python-sounddevice) | 麦克风录音 | MIT / PortAudio MIT-like |
| [ChromaDB](https://github.com/chroma-core/chroma) | 记忆的向量库 | Apache-2.0 |
| [httpx](https://github.com/encode/httpx) | 调模型接口 | BSD-3-Clause |
| [Pillow](https://python-pillow.org/) | 读立绘尺寸、生成对比图 | HPND |
| [ffmpeg](https://ffmpeg.org/)（可选） | 语音转码 | LGPL/GPL（视编译选项） |
| [霞鹜文楷 LXGW WenKai](https://github.com/lxgw/LxgwWenKai) | 文档与界面备选字体 | SIL OFL 1.1 |

完整依赖清单见 `requirements.txt`；各包的具体许可请以它们自己的 LICENSE 为准。

## 四、"RVC" 之类的换声

本项目**没有集成 RVC**，也不分发任何声音模型。如果你自己拿 GPT-SoVITS / RVC 训练了
声音模型，那份模型和训练数据的使用权限由你自己负责。

## 五、免责

- 本项目仅用于**个人学习与技术研究**，不用于商业目的。
- 请勿把角色立绘、语音等素材随你的二次分发一起打包；想公开自己的改版，
  只发代码，把素材留给使用者自备。
- 如你是权利人并认为某处使用不当，请开 issue 或邮件联系，我会在被通知后尽快移除相关内容。
