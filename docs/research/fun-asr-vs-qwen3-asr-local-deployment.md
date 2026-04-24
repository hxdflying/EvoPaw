# Fun-ASR vs Qwen3-ASR：本地部署选型分析

> 更新时间：2026-04-22
> 结论先行：如果你的目标是“现在就选一个模型做本地部署”，默认优先选 **Qwen3-ASR**；如果你的核心场景高度偏向 **中文/方言/行业热词定制**，并且你更看重 **显式 hotword 能力** 而不是时间戳、OpenAI/vLLM 服务化和一体化多语言体验，再考虑 **Fun-ASR-Nano**。

---

## 1. 先说结论

### 我的总推荐

- **默认推荐：Qwen3-ASR-0.6B**
  - 适合大多数本地部署场景。
  - 原因是它在官方资料里给出了更完整的部署闭环：`pip` 包、`vLLM`、流式推理、OpenAI 兼容接口、Docker、Web Demo、Forced Aligner 时间戳、语言识别。
- **高质量优先：Qwen3-ASR-1.7B**
  - 如果你有更充足的 GPU，且主要追求识别质量、方言/多语言鲁棒性和更成熟的服务化能力，优先上它。
- **中文行业词/热词优先：Fun-ASR-Nano**
  - 如果你明确是中文主场景，且更重视显式 `hotwords`、方言/口音、歌词/说唱等特化能力，Fun-ASR 很有吸引力。
  - 但它当前公开仓库在“时间戳、说话人、完整部署工具链”这几个点上明显不如 Qwen3-ASR 完整。

### 一句话决策树

- 你要 **多语言 + 时间戳 + 服务化部署 + 后续扩展性**：选 **Qwen3-ASR**
- 你要 **中文/方言/热词优化**，而且可以接受工具链没那么完整：选 **Fun-ASR-Nano**
- 你只有一张中等显存卡，想要稳妥落地：先试 **Qwen3-ASR-0.6B**
- 你有 24GB 级别 GPU，追求上线效果：优先 **Qwen3-ASR-1.7B**

---

## 2. 两者到底是什么

### Fun-ASR

Fun-ASR 是通义实验室开源的端到端语音识别大模型方案。官方仓库当前公开出来、面向你真正可下载部署的，主要是：

- **Fun-ASR-Nano-2512**
- **Fun-ASR-MLT-Nano-2512**

仓库 README 里提到的最强 **Fun-ASR 7.7B** 出现在论文和评测表中，但并不是当前仓库给你直接下载部署的公开模型；仓库模型表实际列出的公开模型是两个 **800M Nano** 版本。这个点很关键，因为很多人会误把论文里的 7.7B 结果当成自己马上能本地部署的版本。

### Qwen3-ASR

Qwen3-ASR 是 Qwen 团队开源的 ASR 系列，当前官方公开核心模型包括：

- **Qwen3-ASR-0.6B**
- **Qwen3-ASR-1.7B**
- **Qwen3-ForcedAligner-0.6B**

它的定位更像“一套完整的 ASR 产品化开源方案”，而不只是一个模型权重：官方同时给了 Python 包、vLLM、流式推理、时间戳对齐、Docker、本地 Web Demo 和 OpenAI 兼容服务接口。

---

## 3. 最核心的差异

| 维度 | Fun-ASR | Qwen3-ASR |
| --- | --- | --- |
| 开源可部署主力模型 | 公开主力是 800M Nano/MLT-Nano | 公开主力是 0.6B 和 1.7B |
| 语言覆盖方式 | 分成偏中文的 Nano 和偏多语的 MLT-Nano | 单模型 all-in-one，30 语言 + 22 中文方言 |
| 语言识别（LID） | README 未突出成体系能力 | 官方明确支持 LID |
| 时间戳 | README 里明确还在 TODO | 官方有独立 ForcedAligner |
| 流式推理 | 论文强调做了流式优化，README 也说低时延 | 官方公开支持流式，并给出 demo 和 vLLM 路径 |
| 服务化部署 | Python 推理为主 | Python、vLLM、Docker、OpenAI API、Web Demo 都有 |
| 热词/上下文偏置 | 显式 `hotwords` 很友好 | 更偏 prompt/context 方式，没看到同等显式 hotword API |
| 包装形态 | 依赖 `trust_remote_code`，模型包更定制化 | `qwen-asr` + `safetensors`，更标准 |
| 本地部署默认推荐 | 中文特化场景 | 通用部署场景 |

---

## 4. 架构差异：它们为什么会表现不同

### 4.1 Fun-ASR 的架构思路

Fun-ASR 论文给出的核心结构是：

- 音频编码器
- 音频适配器
- CTC 解码器
- 基于 LLM 的解码器

其中一个很重要的工程点是：**CTC 一遍结果会被用来做热词定制和上下文增强**。这解释了为什么 Fun-ASR 在“行业词、热词、复杂业务文本”这类场景里很强调可定制性，也解释了它为什么在 README 里直接暴露了 `hotwords` 参数。

### 4.2 Qwen3-ASR 的架构思路

Qwen3-ASR 是基于 **Qwen3-Omni** 后训练出来的 ASR 系列。论文给出的结构重点是：

- **AuT encoder**
- projector
- Qwen3 语言模型主体

它的一个很强的点是：**同一模型支持 streaming / offline unified inference**。也就是官方从一开始就在同一模型内考虑了流式和离线两种推理形态，而不是把流式当成额外补丁。

### 4.3 对本地部署意味着什么

- **Fun-ASR** 的设计更像“把 ASR 做成强行业工具”，尤其强调热词、噪声、代码混说和业务可控性。
- **Qwen3-ASR** 的设计更像“把 ASR 做成标准化平台能力”，尤其强调语言识别、长音频、时间戳、流式、服务化接口和统一部署框架。

如果你是做应用落地，后者通常更省工程成本。

---

## 5. 能力对比：本地部署最该关心什么

### 5.1 语言与方言覆盖

#### Fun-ASR

- **Fun-ASR-Nano**：偏中文/英文/日文，同时强调中文 7 大方言、26 地域口音、歌词/说唱。
- **Fun-ASR-MLT-Nano**：官方模型卡给出 31 种语言，总体上是多语言版，但训练重点仍明显偏东亚/东南亚及相关语言扩展。

#### Qwen3-ASR

- **Qwen3-ASR-0.6B / 1.7B**：官方明确写的是 **30 种语言 + 22 种中文方言/方言类变体**。
- 这是 **单模型 all-in-one**，不需要像 Fun-ASR 那样在“中文特化版”和“多语版”之间做一次额外选择。

#### 判断

- 如果你是 **中文主场景**，Fun-ASR 的方言/口音导向很强。
- 如果你是 **中英混合、多语言、跨地区产品**，Qwen3-ASR 的单模型覆盖更省心。

---

### 5.2 时间戳、字幕、对齐能力

这是两者当前最明显的分水岭。

#### Fun-ASR

- 仓库 README 的 TODO 里直接写着：
  - `Support returning timestamps`
  - `Support speaker diarization`

也就是说，**当前这个新仓库版本并没有把时间戳和说话人作为现成能力完整交付出来**。

#### Qwen3-ASR

- 官方同时发布了 **Qwen3-ForcedAligner-0.6B**
- 支持 **11 种语言**
- 支持 **词级/字符级/更灵活粒度** 的对齐
- 官方 demo、Python API 和 vLLM 路径都已经把这件事打通

#### 判断

- 你如果要做字幕、切句、逐词对齐、可视化时间轴、后处理检索，**Qwen3-ASR 明显更成熟**。
- 你如果完全不需要时间戳，只要转写文本，Fun-ASR 才不会在这里吃亏。

---

### 5.3 流式推理与实时场景

#### Fun-ASR

- README 写的是“low-latency real-time transcription”
- 论文也强调它做了流式架构优化，并给了 streaming decoding 结果
- 但官方仓库在“如何把这件事变成标准服务接口”上，公开文档没有 Qwen3-ASR 完整

#### Qwen3-ASR

- 官方明确写了：
  - 支持 **streaming inference**
  - 当前流式模式走 **vLLM backend**
  - 提供 **streaming web demo**
  - 同时明确指出：**流式模式不支持 batch，也不返回 timestamps**

#### 判断

- 如果你要做浏览器麦克风、会议实时字幕、边说边出字，**Qwen3-ASR 的公开交付明显更工程化**。
- Fun-ASR 不是不能做，而是你更可能要自己补工程层。

---

### 5.4 热词、行业词、上下文定制

这是 Fun-ASR 最值得重视的优势点。

#### Fun-ASR

- README 示例直接暴露了 `hotwords=["开放时间"]`
- 论文中也明确说：
  - CTC 初始识别结果会用于 **hotword customization**
  - 还会用于上下文增强

这说明它在设计上就把“业务词表”和“行业专有名词”当成生产问题来处理。

#### Qwen3-ASR

- 论文提到模型会利用 system prompt 里的 context tokens 作为背景知识
- 这更像 **prompt/context biasing**
- 但官方 README 没给出 Fun-ASR 那种同等显式、直接可用的 `hotwords` 参数体验

#### 判断

- **医疗、金融、政企、制造、客服质检** 这类专有名词很多的场景，Fun-ASR 的显式 hotword 体验更直接。
- 如果你更关注“标准多语言服务能力”，Qwen3-ASR 还是整体更强。

---

### 5.5 说话人分离、VAD、扩展任务

#### Fun-ASR

- README 中在 `AutoModel` 里示例了 `vad_model="fsmn-vad"`
- 同时 README 提到更大的 **FunASR** 生态在 2024 年就包含：
  - ASR
  - VAD
  - 标点
  - 说话人验证
  - 说话人分离
  - 多说话人 ASR

但要注意：**那是 FunASR 生态，不等于这个新 `Fun-ASR` 仓库已经把这些能力全部无缝集成好了。**

#### Qwen3-ASR

- 官方重点是：
  - ASR
  - LID
  - Forced alignment
  - Streaming / vLLM / serving

它不是一个“全语音工具箱”定位，而是“高质量 ASR + 对齐 + 服务化”定位。

#### 判断

- 如果你未来可能会深度进入 FunAudio/FunASR 生态，Fun 这条路的扩展空间不差。
- 但如果你只看当前这个 repo 的成熟交付，Qwen3-ASR 更完整。

---

## 6. 本地部署视角下的工程成熟度

### 6.1 安装与运行路径

#### Fun-ASR

官方给的是很直白的路径：

```bash
git clone https://github.com/FunAudioLLM/Fun-ASR.git
cd Fun-ASR
pip install -r requirements.txt
```

然后通过 `funasr.AutoModel(...)` 或直接 `FunASRNano.from_pretrained(...)` 跑。

#### Qwen3-ASR

官方给了更标准的发布方式：

```bash
pip install -U qwen-asr
pip install -U qwen-asr[vllm]
```

并且配套：

- `qwen-asr-demo`
- `qwen-asr-demo-streaming`
- `qwen-asr-serve`
- `vllm serve`
- Docker
- OpenAI SDK / cURL 调用方式

#### 判断

- 想尽快把能力嵌入自己 Python 工程：**两者都能做到**
- 想尽快形成 **可服务化、可对外暴露 API、可灰度** 的部署形态：**Qwen3-ASR 优势很明显**

---

### 6.2 权重打包与安全/维护性

这是我很看重，但很多人容易忽略的一点。

#### Fun-ASR

官方 HF 模型仓库是更定制化的结构，至少可以看到：

- 主目录里有一个 **`model.pt`**
- HF 页面明确提示了 **pickle imports**
- 同时目录里还有一个嵌套的 **`Qwen3-0.6B`** 子目录
- 推理代码要求 `trust_remote_code=True`

这意味着：

- 权重与代码耦合更强
- 加载链更依赖自定义实现
- 对安全审计、供应链合规、离线封装来说，不如纯 `safetensors` 路线干净

#### Qwen3-ASR

- 官方模型仓库是标准 **`safetensors`**
- 官方 Python 包也更标准化
- vLLM / OpenAI 兼容接口路径更清晰

#### 判断

- 如果你是个人项目，Fun-ASR 的这种定制化问题不一定构成阻碍。
- 如果你是企业内网、生产环境、要做制品管理和安全审计，**Qwen3-ASR 的包装更利于长期维护**。

---

### 6.3 部署工具链完整度

#### Fun-ASR

强项：

- Python 推理简单
- 显式 hotword
- 可接 VAD

短板：

- 当前新 repo 没有像 Qwen 那样完整的一套 demo / serve / Docker / OpenAI API 叙事
- 时间戳、speaker diarization 仍在 TODO

#### Qwen3-ASR

强项：

- `qwen-asr` PyPI 包
- transformers backend
- vLLM backend
- streaming demo
- gradio demo
- Docker image
- OpenAI SDK / cURL 调用
- forced aligner

#### 判断

**如果你问的是“本地部署”而不是“学术效果”，Qwen3-ASR 的工程交付成熟度更高。**

---

## 7. 性能与基准：应该怎么看

## 7.1 一个容易踩的坑

Fun-ASR 官方展示里，很多最亮眼的结果来自 **Fun-ASR 7.7B**；但你当前能直接开源部署的是 **Fun-ASR-Nano / MLT-Nano 800M**。  
所以选型时应该拿：

- **Fun-ASR-Nano / Fun-ASR-MLT-Nano**
- 对比
- **Qwen3-ASR-0.6B / 1.7B**

而不是拿“论文里的闭源 7.7B”去对比“你实际要部署的开源模型”。

---

### 7.2 Qwen3-ASR 对 Fun-ASR-MLT-Nano 的官方对比

Qwen 官方在模型卡里直接给了和 **Fun-ASR-MLT-Nano** 的多语言对比。结果很清楚：

| 基准 | Fun-ASR-MLT-Nano | Qwen3-ASR-0.6B | Qwen3-ASR-1.7B |
| --- | ---: | ---: | ---: |
| MLS | 28.70 | 13.19 | 8.55 |
| CommonVoice | 17.25 | 12.75 | 9.18 |
| MLC-SLM | 29.94 | 15.84 | 12.74 |
| Fleurs | 10.03 | 7.57 | 4.90 |
| Fleurs† | 31.89 | 10.37 | 6.62 |
| Fleurs†† | 47.84 | 21.80 | 12.60 |
| News-Multilingual（内部） | 65.07 | 17.39 | 12.80 |

这个表说明：

- **Qwen3-ASR-0.6B 已经明显压过 Fun-ASR-MLT-Nano**
- **Qwen3-ASR-1.7B 则进一步拉开差距**

如果你的重点是 **多语言通用部署**，这组数据已经足够说明方向。

---

### 7.3 Qwen3-ASR 的语言识别能力

Qwen 官方还给出了 LID 准确率：

| 数据集 | Whisper-large-v3 | Qwen3-ASR-0.6B | Qwen3-ASR-1.7B |
| --- | ---: | ---: | ---: |
| MLS | 99.9 | 99.3 | 99.9 |
| CommonVoice | 92.7 | 98.2 | 98.7 |
| MLC-SLM | 89.2 | 92.7 | 94.1 |
| Fleurs | 94.6 | 97.1 | 98.7 |
| 平均 | 94.1 | 96.8 | 97.9 |

如果你的输入语言不固定、用户会混用语言或地区口音，Qwen 的 LID 是非常有价值的部署特性。

---

### 7.4 Fun-ASR 的强项应该怎样理解

Fun-ASR 的官方材料里，最值得关注的不是“它是否全面胜过 Qwen”，而是它在这些方向上明确投入很多：

- 中文/方言/口音
- 远场和高噪音
- 歌词/说唱
- 热词和行业词
- 代码混说

而且论文给出的 streaming、noise robustness、production-oriented optimization 叙事非常强。这意味着：

- 如果你的业务数据高度贴近 **中文真实业务场景**
- 识别里经常出现 **专有名词**
- 或者你的音频质量比较差

那 Fun-ASR 不应被简单看成“弱于 Qwen 的备选项”，它更像 **偏场景化最优** 的选项。

---

## 8. 本地部署成本：磁盘、显存、复杂度

## 8.1 官方模型文件大小

### Qwen3-ASR

- **Qwen3-ASR-0.6B**：HF 仓库主权重大约 **1.88 GB**
- **Qwen3-ASR-1.7B**：HF 仓库总大小约 **4.7 GB**
- **Qwen3-ForcedAligner-0.6B**：大约 **1.84 GB**

如果你需要 **时间戳**，Qwen 的总模型资产大致要按：

- `0.6B + aligner ≈ 3.7 GB`
- `1.7B + aligner ≈ 6.5 GB`

来预留磁盘。

### Fun-ASR

- **Fun-ASR-Nano-2512**：HF 模型树页面显示主仓库约 **1.99 GB**
- 同时该仓库还能看到：
  - 一个约 **1.97 GB** 的 `model.pt`
  - 一个嵌套的 `Qwen3-0.6B` 目录

这反映出 Fun-ASR 的模型打包方式更定制，不像 Qwen3-ASR 那样一眼就是标准 `safetensors` 权重布局。

---

### 8.2 显存建议（以下为基于参数规模和 BF16 推理的经验估算，不是官方硬门槛）

| 模型 | 建议起步显存 | 更稳妥显存 |
| --- | --- | --- |
| Qwen3-ASR-0.6B | 8 GB 级 | 12 GB 级 |
| Qwen3-ASR-1.7B | 16 GB 级 | 24 GB 级 |
| Qwen3-ForcedAligner-0.6B | 8 GB 级 | 12 GB 级 |
| Fun-ASR-Nano / MLT-Nano | 8 GB 级 | 12 GB 级 |

说明：

- 如果你用 Qwen3-ASR + ForcedAligner 同机部署，最好往更高显存档位走。
- 长音频、批量推理、流式并发都会推高显存压力。
- Qwen 官方还专门推荐了 **FlashAttention 2** 来降显存、提速，这说明它对长音频和批量场景确实做了工程优化。

---

### 8.3 CPU-only 值不值得

我不建议把这两个模型当成“CPU-first”的部署方案。

- Fun-ASR README 虽然示例参数允许 `device="cpu"`，但这更像可运行，不等于适合作为生产吞吐方案。
- Qwen3-ASR 的官方部署叙事明显是 GPU/vLLM 优先。

如果你是 **纯 CPU 环境**，这两个都不是我会优先推荐的路线。

---

## 9. 你真正部署时，会遇到的实际问题

### 9.1 如果你需要时间戳

直接选 **Qwen3-ASR**。

Fun-ASR 当前公开仓库在这一项上处于明显劣势，原因不是论文不先进，而是 **公开可用交付还没补齐**。

### 9.2 如果你需要 OpenAI 兼容接口

直接选 **Qwen3-ASR**。

官方文档已经给出了：

- OpenAI SDK
- `/v1/chat/completions`
- `/v1/audio/transcriptions`
- `vllm serve`

这能显著降低你接应用层的成本。

### 9.3 如果你需要显式热词

优先看 **Fun-ASR**。

因为它不是让你“自己想办法 prompt 一下”，而是显式给了 `hotwords` 参数，使用路径更直接。

### 9.4 如果你要做浏览器实时识别

优先看 **Qwen3-ASR**。

因为它的 streaming demo、HTTPS 注意事项、vLLM 路径和命令行工具都是官方文档的一部分。

### 9.5 如果你做中文客服/会议/政企内网

这里就要按优先级取舍：

- **优先质量与功能完整性**：Qwen3-ASR-1.7B
- **优先热词和中文业务场景定制**：Fun-ASR-Nano
- **优先成本和通用性平衡**：Qwen3-ASR-0.6B

---

## 10. 我的分场景建议

### 场景 A：做一个通用语音转写服务

需求特点：

- 要 API 化
- 可能以后要接前端、字幕、导出时间轴
- 中英混合甚至多语言
- 希望后面能平滑扩容

建议：

- **首选：Qwen3-ASR-0.6B**
- 预算够再升 **Qwen3-ASR-1.7B**

原因：

- 模型本身更强
- 时间戳方案现成
- 服务化路径更成熟
- 长期维护成本更低

---

### 场景 B：中文会议/客服/行业转写

需求特点：

- 中文占绝对主导
- 行业词多
- 方言、口音、噪声重要

建议：

- 如果你最关注 **热词/行业词**：先试 **Fun-ASR-Nano**
- 如果你最关注 **整体稳定性 + 工程成熟度**：先试 **Qwen3-ASR-1.7B**

我的实际倾向：

- **PoC 第一轮**：同时测这两个
- **只允许先选一个**：我仍然更倾向 **Qwen3-ASR-1.7B**

因为：

- Fun-ASR 的特化方向很诱人
- 但从“今天就要部署”这个角度看，Qwen 更完整、更少坑

---

### 场景 C：多语言产品或海外业务

建议：

- **Qwen3-ASR-0.6B 或 1.7B**

不建议：

- 把 Fun-ASR 当成多语言通用主力

原因：

- 官方对比表里，Qwen 在多语言公开基准上对 Fun-ASR-MLT-Nano 是明显领先的。

---

### 场景 D：要词级时间戳/字幕/检索

建议：

- **Qwen3-ASR + Qwen3-ForcedAligner-0.6B**

没有悬念。

---

### 场景 E：资源一般，先跑起来再说

建议：

- **Qwen3-ASR-0.6B**

理由：

- 0.6B 体量相对友好
- 官方效率数据非常强
- 包装、接口、部署工具链比 Fun-ASR 更规整

---

## 11. 最终推荐

## 推荐方案 1：通用默认方案

**Qwen3-ASR-0.6B**

适合：

- 先上线一个可维护的本地 ASR 服务
- 后面可能要接时间戳、字幕、前端 Demo、OpenAI 兼容 API
- 输入语言不完全固定

这是我认为最稳妥的默认选择。

---

## 推荐方案 2：质量优先方案

**Qwen3-ASR-1.7B**

适合：

- 你有更充足 GPU
- 你愿意为质量付出更多显存和磁盘
- 你要更强的多语言、方言和复杂音频鲁棒性

这是我认为“只选一个且希望少走弯路”的最佳方案。

---

## 推荐方案 3：中文热词特化方案

**Fun-ASR-Nano**

适合：

- 业务强中文
- 你很重视热词/专有名词
- 你愿意接受时间戳、服务化工具链不如 Qwen 完整

它不是通用部署首选，但在某些中文行业场景下可能比你预期更合适。

---

## 12. 一句话版建议

- **只能选一个做本地部署**：我建议你选 **Qwen3-ASR**
- **资源有限**：先上 **Qwen3-ASR-0.6B**
- **追求效果**：上 **Qwen3-ASR-1.7B**
- **中文行业词是第一优先级**：再认真评估 **Fun-ASR-Nano**

---

## 13. 参考来源

### 官方仓库

- Fun-ASR GitHub: https://github.com/FunAudioLLM/Fun-ASR
- Qwen3-ASR GitHub: https://github.com/QwenLM/Qwen3-ASR

### 官方论文

- Fun-ASR Technical Report: https://arxiv.org/abs/2509.12508
- Qwen3-ASR Technical Report: https://arxiv.org/abs/2601.21337

### 官方模型卡

- Fun-ASR-Nano-2512: https://huggingface.co/FunAudioLLM/Fun-ASR-Nano-2512
- Fun-ASR-MLT-Nano-2512: https://huggingface.co/FunAudioLLM/Fun-ASR-MLT-Nano-2512
- Qwen3-ASR-0.6B: https://huggingface.co/Qwen/Qwen3-ASR-0.6B
- Qwen3-ASR-1.7B: https://huggingface.co/Qwen/Qwen3-ASR-1.7B
- Qwen3-ForcedAligner-0.6B: https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B

---

## 14. 备注

- 文中“显存建议”属于基于参数规模、权重体积和官方推理方式的工程估算，不是官方硬性门槛。
- 文中涉及的“更适合本地部署”判断，重点看的是 **你今天能否快速、稳定、低风险地落地**，而不是只看单一榜单分数。
- 如果你后续愿意，我可以继续给你补一版：
  - **按你的实际硬件配置**（CPU/GPU/显存/内存）给出精确选型
  - 或者直接写 **Qwen3-ASR / Fun-ASR 的本地部署步骤文档**
