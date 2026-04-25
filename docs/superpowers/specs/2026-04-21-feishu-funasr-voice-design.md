# EvoPaw Feishu Voice Input with Fun-ASR Design

> 文档版本：`2026-04-24 rev2`（切换到 Fun-ASR 实时 WebSocket API，取消 OSS 依赖）
> 基线 commit：`1940522`（彼时 `evopaw/feishu/listener.py`、`evopaw/models.py` 已包含 audio 解析与 `duration_ms` 字段的早期试做）

## 0. Implementation Status

> 最后更新：`2026-04-25`（Phase 4 离线 + 半自动化部分完成），未 commit。所有改造均已在工作树中落地并通过 pytest（**605 总计**，含 6 个 WS mock 集成测试）。

### 图例

- ✅ 已完成并通过单测
- 🚧 部分完成（仍有子项待办）
- ⏳ 尚未开始

### 阶段进度

| 阶段 | 状态 | 主要内容 | 对应章节 |
|------|------|----------|----------|
| Phase 1: Basic Plumbing | ✅ 已完成 | downloader 映射修复、`evopaw/asr/*` 四文件、客户端+服务层单测 | §17.1 |
| Phase 2: Runner Integration | ✅ 已完成 | Runner 语音分支、`语音转写+回答` 格式化、msg_id LRU 去重、main.py 注入、配置模板 | §17.2 |
| Phase 3: Reliability | ✅ 已完成 | 分类失败文案 / 回执 / 客户端重试 / Prometheus 指标 / listener 补测 / WS mock 集成测试 | §17.3 |
| Phase 4: Pre-Production Tuning | 🚧 离线 + 半自动化完成 | 显示配置 / 采样率审计脚本 / 阈值校准脚本 / 模型快照校验 / 4 类样例集成测试 / runbook（含 2026-04-25 检索的最新快照号清单）；只剩"真实凭证下跑生产并执行 runbook"无法本地完成 | §17.4 |

### 已落地的文件

| 文件 | 状态 | 说明 |
|------|------|------|
| `evopaw/feishu/downloader.py` | ✅ | `audio → file` 的 API type 映射已在 `download()` 中生效 |
| `evopaw/asr/__init__.py` | ✅ | 导出 `AsrResult` / `AsrFailure` |
| `evopaw/asr/models.py` | ✅ | `AsrResult`、`AsrFailure`（Exception 子类，frozen dataclass） |
| `evopaw/asr/funasr_realtime_client.py` | ✅（Phase 1+3） | WebSocket one-shot 客户端 + `max_reconnect_retries` 对 `ws_connect`/`submit`/`disconnect` 整次重试 |
| `evopaw/asr/service.py` | ✅（Phase 1+3） | 读本地音频路径 + 调用客户端 + 结构化日志 + `asr_requests_total` / `asr_latency_seconds` 埋点 |
| `evopaw/runner.py` | ✅（Phase 2+3+4） | audio 分支、voice 模板、reply 格式化、Agent 失败保 transcript、msg_id LRU 去重、七种 reason 分类文案、`duration_ms`/`short_wait_s` 回执、`audio_messages_total`/`audio_dedup_hits_total` 埋点；Phase 4 加入 `transcription_title`/`answer_title`/`display_transcript`/`include_audio_path` 四个可覆写参数 |
| `evopaw/main.py` | ✅（Phase 2+3+4） | `_build_speech_service` 工厂、`asr` 配置读取（含全部 Phase 2/3 字段 + Phase 4 显示字段）、`_warn_if_model_is_alias` 启动期校验、注入 Runner |
| `evopaw/observability/metrics.py` | ✅（Phase 3） | 注册 6 个 ASR 指标 + 对应 `record_asr_*` / `record_audio_*` 辅助函数 |
| `config.yaml.template` | ✅（Phase 2+3+4） | `asr:` 段含 §9 全部字段（含 Phase 4 的 `transcription_title`/`answer_title`/`display_transcript`/`include_audio_path`） |
| `scripts/audit_audio_sample_rate.py` | ✅（Phase 4） | OPUS 采样率审计脚本（ffprobe 探测 + §18.2 方案 A/B 推荐） |
| `scripts/calibrate_thresholds.py` | ✅（Phase 4） | 连本地 Prometheus 拉 `evopaw_asr_latency_seconds`，给出 `short_wait_s` / `max_wait_s` 推荐取值 |
| `docs/runbooks/voice-pre-production.md` | ✅（Phase 4） | 预生产 runbook：四步真实联调具体命令 + 2026-04-25 检索的快照号清单 |
| `.env.example` | ✅ | `DASHSCOPE_API_KEY` 条目早已存在 |

### 已落地的单测

| 文件 | 用例数 | 状态 | 对应章节 |
|------|-------|------|----------|
| `tests/unit/test_downloader.py` | +2（`audio→file` 映射断言、`image` 保持原值） | ✅ | §16.1.2 |
| `tests/unit/test_funasr_realtime_client.py` | 21（17 基础 + 4 重试） | ✅ | §16.1.4 |
| `tests/unit/test_asr_service.py` | 10（7 基础 + 3 metrics） | ✅ | §16.1.5 |
| `tests/unit/test_runner.py` | 55（38 原有 + 5 语音 + 5 dedup + 7 分类/回执/指标 + 4 Phase 4 显示配置） | ✅ | §16.1.3 |
| `tests/unit/test_feishu_listener.py` | +3（duration 缺失 / null 降级、audio dispatch 端到端） | ✅ | §16.1.1 |
| `tests/unit/test_audit_audio_sample_rate.py` | 9 | ✅（Phase 4） | §17.4 步骤 A |
| `tests/unit/test_main_asr.py` | 9 | ✅（Phase 4） | §9.1 / §17.4 步骤 C |
| `tests/unit/test_calibrate_thresholds.py` | 17 | ✅（Phase 4 半自动化） | §17.4 步骤 B |
| `tests/integration/test_voice_end_to_end.py` | 6（基础 happy path + task_failed + §16.3 四类样例：短/长/disconnect/timeout） | ✅ | §16.2 / §16.3 mock 部分 |

全量测试：**605 通过**（Phase 1 前 496 → Phase 1 后 527 → Phase 2 后 537 → Phase 3 后 562 → Phase 4 离线 586 → Phase 4 半自动化 605）。

### 收尾说明（2026-04-25）

**当前结论：本轮改造收尾，功能上线可用，剩余 7 项 polish 留作后续上量后处理。**

收尾时点的状态快照：

- 4 个 commit 已 push 到 `hxdflying/EvoPaw`（`a924180` / `0148ec6` / `66256ee` / `0ee3d26`）
- docker compose 重建完成，Phase 1-4 代码在生产容器中运行
- **真实飞书短语音端到端验证通过**（用户已在 1:1 私聊中实测）
- 605 unit + 6 integration 测试全绿
- README / 设计文档 / runbook 全部同步

### 后续待处理项（按可执行时机分组）

#### 🟡 组 A：真实样本验收（10 分钟，随时可做）

需要在飞书发几条真实语音、人工对照回复。代码已经在 mock 层覆盖过，这里是回补真实环境验收。

| # | 验收点 | 预期结果 | 已有 mock 覆盖 |
|---|--------|---------|----------------|
| A1 | 20s 长语音 | 先收到 ack "语音已收到，正在转写和分析"，再收到正式回复 | ✅ `test_long_audio_sends_ack_then_final_reply` |
| A2 | 中英混合 / 口音重 | transcript 非空，Agent 能基于内容回答 | — |
| A3 | 重复发同一条语音 | 第二条被丢弃，`evopaw_audio_dedup_hits_total` +1 | ✅ `test_duplicate_msg_id_is_skipped` |
| A4 | 故意触发失败（如临时改 `DASHSCOPE_API_KEY`） | 用户看到对应分类文案 | ✅ 七种 reason 各有用例 |

操作：照 [docs/runbooks/voice-pre-production.md](../../runbooks/voice-pre-production.md) 步骤 D。

#### 🟢 组 B：上量后才有意义的校准（≥ 1 周后）

| # | 任务 | 触发条件 | 命令 |
|---|------|---------|------|
| B1 | §18.2 采样率审计 | 积累 ≥ 5 条真实飞书录音 | `python3 scripts/audit_audio_sample_rate.py data/workspace/sessions/` |
| B2 | 阈值校准 `short_wait_s` / `max_wait_s` | 积累 ≥ 50 条 ASR 请求 | `python3 scripts/calibrate_thresholds.py` |
| B3 | 飞书 audio 时长分布统计 → `long_audio_threshold_ms` | 同上 | grep 日志中 `duration_ms` 取 P75 |

#### 🔴 组 C：上线日动作

| # | 任务 | 时机 |
|---|------|------|
| C1 | 固定 Fun-ASR 模型为官方快照号 | 正式上线发布前。当前用别名 `fun-asr-realtime` 启动会刷一行 WARN 提醒；查 [阿里云文档](https://help.aliyun.com/zh/model-studio/fun-asr-realtime-websocket-api) 拿当时最新快照号写入 `config.yaml` 的 `model:` 字段。**不要构造推测版本号**。 |

#### 完成后的回写动作

每完成一项，请在本文件对应章节把 ⏳ 改 ✅：

- A1-A4 验收通过 → §16.3 标 ✅、§17.4 Phase 4 整体标 ✅
- B1 结论得出 → §18.2 标 "已实测无影响" 或 "已切方案 A/B"
- B2/B3 写回 `config.yaml` 后 → 在本节加一行 "实际生产值：short_wait_s=X、long_audio_threshold_ms=Y"
- C1 完成 → §9.1 末尾追加 "生产实际使用：fun-asr-realtime-YYYY-MM-DD（YYYY-MM-DD 固定）"

### 下次回到本工作的入口

下次想接着做飞书语音 polish 时，从这个文件 §0 收尾说明开始读，找未打 ✅ 的项即可。具体执行命令在 `docs/runbooks/voice-pre-production.md`，当前已是最新版。

## 1. Summary

本设计为 EvoPaw 增加飞书语音消息处理能力，使用户可以在飞书中直接向 Bot 发送语音，由系统自动完成下载、转写、理解和回复。

目标链路：

`Feishu audio message -> EvoPaw download -> Fun-ASR realtime WebSocket -> enhanced user message -> existing agent -> Feishu reply`

用户侧体验要求已经确认如下：

- 输入来源：飞书 `audio` 消息
- 识别服务：阿里百炼 `Fun-ASR` **实时语音识别 WebSocket API**（以 one-shot 方式送整段语音，不做真正的边说边转）
- 文件中转：**无需公网对象存储**，本地字节流直接送入 WebSocket
- Agent 输入：同时保留 `转写文本 + 原始音频本地路径`
- 回复格式：先展示转写文本，再给出回答
- 交互策略：混合模式
  - 短语音：同一张 "思考中" 卡片内完成
  - 长语音或转写超出短等待窗口：先回执，再发送正式回复

## 2. Context

当前 EvoPaw 已有飞书 WebSocket 接收、消息标准化、附件下载、会话管理和 Agent 执行链路。

**基线现状（commit `1940522`）**：

- `evopaw/feishu/listener.py`
  - `_extract_content` 仅处理 `text` 和 `post`。
  - `_extract_attachment` 已经支持 `image` / `file` / `audio` 三类（audio 解析是早期试做，本设计将把它纳入正式链路）。
- `evopaw/models.py`
  - `Attachment` 已包含 `msg_type: "image" | "file" | "audio"` 和 `duration_ms: int | None` 字段。
- `evopaw/feishu/downloader.py`
  - 已支持通过飞书消息资源接口下载附件到 `workspace/sessions/{sid}/uploads/`。
  - 当前实现用 `.type(attachment.msg_type)` 透传类型到 API —— 这对 `audio` 是错的，详见 §3.1。
- `evopaw/runner.py`
  - 已有"附件下载后再组装用户消息"这条预处理能力。
  - 当前附件处理只覆盖图片和文件，没有音频转写服务层。

因此，本次改造的合理切入点是：

1. 修复 downloader 的 `type` 参数映射（audio → file）。
2. 在现有附件预处理阶段新增 `audio -> transcription` 分支。
3. 新增 `evopaw/asr/*` 一层基础设施（WebSocket 客户端 + 高层编排）。

Agent 主结构不变。**本次改造不引入任何外部对象存储依赖。**

## 3. Verified External Constraints

以下约束已基于截至 `2026-04-24` 的官方文档核实。具体链接见 §19。

### 3.1 Feishu

- "获取消息中的资源文件" 接口 `GET /open-apis/im/v1/messages/:message_id/resources/:file_key`，查询参数 `type` **只接受 `image` 或 `file` 两个值**，音频 / 视频 / 文件统一归属在 `type=file` 下。
- 当前仅支持下载 `100MB` 以内资源文件。
- 资源下载接口通过 `message_id + file_key` 获取文件二进制流。

结论：

- 沿用现有附件下载模式即可获取飞书语音文件，无需新增特殊上传或转码流程。
- **但 `downloader.py` 必须在请求层做 `audio -> file` 的参数映射**，否则飞书 API 直接拒绝请求。这是当前代码的隐性 bug，本次改造顺带修正。

关于 `audio` 消息接收事件的字段契约：

- 飞书"发送 audio 消息"的公开结构仅保证 `file_key`。
- 实际接收 `im.message.receive_v1` 事件时，`message.content` 里通常会带 `duration`（实测单位毫秒），但该字段**未在官方公开契约中稳定保证**。设计不依赖它必然存在，缺失时完全退回 `short_wait_s` 轮询阈值判断（§7.2）。

### 3.2 Fun-ASR Realtime WebSocket

- 连接 URL（北京地域）：`wss://dashscope.aliyuncs.com/api-ws/v1/inference/`（新加坡地域：`wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference/`）
- 握手 Header：
  - 必需：`Authorization: bearer <DASHSCOPE_API_KEY>`
  - 可选：`X-DashScope-WorkSpace`（业务空间 ID）、`X-DashScope-DataInspection`（默认 `enable`）、`user-agent`
- 交互协议为 DashScope 流式事件协议，全部客户端指令和服务端事件均通过 JSON 文本帧传递，音频数据通过 WebSocket 二进制帧传递：
  - 客户端 → 服务端：
    - `run-task`（JSON 指令）
    - 二进制音频帧（裸音频字节，按 `parameters.format` 声明的容器原样发送，官方示例每 100ms 送 1024 字节；文件场景可等速或小幅加速，不需要严格匹配实时速率）
    - `finish-task`（JSON 指令）
  - 服务端 → 客户端：
    - `task-started`
    - `result-generated`（可能多条）
    - `task-finished`
    - `task-failed`
- 模型：
  - 稳定别名：`fun-asr-realtime`（当前等同 `fun-asr-realtime-2025-11-07`）
  - 采样率：`fun-asr-realtime` 要求 `16000`；`fun-asr-flash-8k-realtime` 要求 `8000`
- 支持音频格式：`pcm / wav / mp3 / opus / speex / aac / amr`。飞书下发的语音是 OPUS，原生兼容。
- transcript 在 `result-generated` 事件里的字段路径：
  - `payload.output.sentence.text`（句子文本）
  - `payload.output.sentence.sentence_end`（布尔，句尾才置 `true`）
  - `payload.output.sentence.begin_time` / `end_time`（毫秒）
- 计费（国内）：`0.00033 元/秒` 语音时长；国际：`0.00066 元/秒`。

**run-task 指令结构（准引用官方）**：

```json
{
  "header": {
    "action": "run-task",
    "task_id": "<32位唯一 ID>",
    "streaming": "duplex"
  },
  "payload": {
    "task_group": "audio",
    "task": "asr",
    "function": "recognition",
    "model": "fun-asr-realtime",
    "parameters": {
      "format": "opus",
      "sample_rate": 16000
    },
    "input": {}
  }
}
```

**finish-task 指令结构**：

```json
{
  "header": {
    "action": "finish-task",
    "task_id": "<与 run-task 相同>",
    "streaming": "duplex"
  },
  "payload": { "input": {} }
}
```

结论：

- **不需要任何外部对象存储**：音频字节从 `uploads/` 读出后直接写入 WebSocket 二进制帧。
- one-shot 模式使用：`run-task → 发完整段音频 → finish-task → 等 task-finished`，整段作为一条同步调用处理，不对 `Runner` 的 per-routing_key 串行模型造成冲击。
- 采样率/格式强耦合：飞书 OPUS 实际采样率需 Phase 1 实测确认（Opus 容器内可能是 48k/24k/16k/8k）。若不是 16k，需要在 §18 的风险里承担（详见 §18.2）。

## 4. Goals and Non-Goals

### 4.1 Goals

1. 支持飞书 `audio` 消息进入 EvoPaw 主链路。
2. 将飞书语音下载到本地 `uploads/` 目录。
3. 通过 Fun-ASR 实时 WebSocket API 把整段音频送到百炼并取得完整 transcript（one-shot 使用）。
4. 将 `转写文本 + 原始音频本地路径` 一起交给现有 Agent。
5. 最终在飞书中展示：
   - `语音转写`
   - `回答`
6. 对长语音提供"先回执后正式回复"的用户体验。
7. 在失败、超时、下载错误等场景下提供明确降级行为。
8. 不引入任何公网对象存储或额外的凭证。

### 4.2 Non-Goals

本阶段明确不做以下内容：

1. 本地部署或自托管 Fun-ASR。
2. **真正意义的边说边转**：不做 "用户说话过程中实时向 Agent 推进" 这类增量反馈；WebSocket 协议只作为一次性整段转写的传输通道使用。
3. 说话人分离、情绪识别、事件检测等高级语音分析。
4. 前端直接调用百炼 API。
5. 让 Agent 直接访问 WebSocket 或自行负责转写。
6. 为长语音引入独立后台任务系统或持久化任务表。
7. 不依赖 OSS 或任何其它对象存储。
8. 不自动把飞书 OPUS 转码为别的格式（一期直接按 opus 送，若采样率不匹配进入 §18.2 风险处理）。

## 5. Recommended Architecture

### 5.1 High-Level Flow

```text
Feishu User
  -> FeishuListener
  -> Runner
  -> FeishuDownloader
  -> SpeechRecognitionService
       └── FunASRRealtimeClient (WebSocket)
  -> Runner
  -> Main Agent
  -> FeishuSender
  -> Feishu User
```

### 5.2 Core Design Choice

推荐路线：

`Feishu audio -> local download -> Fun-ASR realtime WebSocket (one-shot) -> enhanced text message -> existing agent`

不采用以下方案作为一期主线：

- 录音文件识别 REST API：需要公网 URL，意味着额外引入 OSS 或等价对象存储层，凭证面 + 运维负担都显著上升；对飞书 100MB 内短语音无长度优势。
- 本地 Fun-ASR：偏离 "调用 API" 目标，模型运维成本过高。
- 边说边转（真正的流式 UX）：改造现有 per-routing-key 串行处理模型，收益 / 复杂度不划算。

### 5.3 Processing Boundary

音频转写属于基础设施预处理能力，不属于 Agent 推理职责。

因此：

- `Runner` 在进入 `agent_fn` 之前完成音频转写
- `session` 历史中写入的是标准化后的增强文本
- `agent_fn` 不感知飞书 `audio` 原始协议，也不直接访问百炼
- Fun-ASR WebSocket 客户端实例仅存在于主进程内存，不通过 Skill / Sub-Agent 工具暴露

## 6. End-to-End Data Flow

### 6.1 Receive

`FeishuListener` 收到 `im.message.receive_v1` 后：

1. 识别 `message_type == "audio"`。
2. 从消息内容 JSON 中提取：
   - `file_key`（契约保证存在）
   - `duration`（契约未明确保证，实测单位毫秒，以 `duration_ms` 填入 `Attachment`；缺失则为 `None`）
3. 构造 `InboundMessage`。
4. 将 `Attachment(msg_type="audio", ...)` 挂入 `InboundMessage.attachment`。

### 6.2 Download

`Runner` 获取或创建 session 后：

1. 调用 `FeishuDownloader.download()`。
2. **downloader 在构造 `GetMessageResourceRequest` 时，对 `msg_type == "audio"` 的附件将 `type` 参数映射为 `"file"`**（§3.1），其它类型保持原值。
3. 将音频落盘到：

   `data/workspace/sessions/{session_id}/uploads/{filename}`

   沙盒内可见路径：`/workspace/sessions/{session_id}/uploads/{filename}`

4. 一期不做扩展名矫正。原因：Fun-ASR 实时 WS API 的格式由 `parameters.format` 显式声明，服务端不依赖文件后缀。listener 写入 `{file_key}.audio` 占位后缀即可保留；Phase 1 真机测试观察到解析失败再在 §18.2 中加固。

### 6.3 Transcribe (WebSocket One-Shot)

`FunASRRealtimeClient` 针对单次语音建立一条短连接 WebSocket，完成后立即关闭：

1. **握手**：
   - URL：`wss://dashscope.aliyuncs.com/api-ws/v1/inference/`
   - Header：`Authorization: bearer ${DASHSCOPE_API_KEY}`
2. **下发 `run-task`**，`payload.parameters` 填：
   - `format: "opus"`（飞书语音默认；若未来加编码探测再改，见 §18.2）
   - `sample_rate: 16000`
   - `model: "fun-asr-realtime"`
   - `task_id`：UUID4 去掉 `-`，32 位十六进制
3. **等待 `task-started` 事件**（`submit_timeout_s` 内未收到视作提交失败）。
4. **流式推送音频二进制帧**：
   - 从本地文件读出全部字节。
   - 每次发送 `chunk_bytes`（默认 1024 字节）并 sleep `chunk_interval_ms`（默认 100ms）。
   - 对"只做 one-shot 转写"的场景，发送节奏允许小幅加速（默认 100ms，可调至 20ms），但不应一次性把整个文件灌进去以免触发服务端接收缓冲限流。
5. **发送 `finish-task`** 指令。
6. **持续收集 `result-generated` 事件**：
   - 只保留 `payload.output.sentence.sentence_end == true` 的条目，避免把增量 partial 结果重复拼接。
   - 以 `begin_time` 升序拼接 `payload.output.sentence.text`，段间用单个空格连接（中文识别结果通常不需要加标点，百炼会自行判定句末标点）。
7. **终态**：
   - 收到 `task-finished` 事件 → 返回 `AsrResult(transcript=...)`
   - 收到 `task-failed` 事件 → 按 §12.5 处理
   - `max_wait_s` 超时 → 按 §12.4 处理（主动关闭 WebSocket）
   - WebSocket 连接异常断开 → 按 §12.3 处理

### 6.4 Enhance

`Runner` 将 transcript 组装成增强后的用户消息：

```text
用户发送了一条语音消息。

语音转写：
{transcript}

原始音频文件已保存到：
`/workspace/sessions/{session_id}/uploads/{filename}`

请优先根据语音转写理解用户意图；如有歧义，可结合原始音频文件路径做进一步处理。
```

### 6.5 Reply

Agent 返回回复后，`Runner` 统一格式化为：

```text
语音转写：
{transcript}

回答：
{agent_reply}
```

## 7. User Interaction Model

### 7.1 Selected Strategy

已确认采用混合策略：

- 短语音：在单条处理链路内等待转写结果
- 长语音或超出短等待窗口：先回执，再正式回复

> **与 §11.1 一致性提示**：回执只改善用户感知，不释放 worker。一期下，同 `routing_key` 的后续消息在本次 `_handle()` 协程结束前仍然排队等待。用户连发语音时，第二条会在第一条完全回复之后才被处理。

### 7.2 Decision Rule

建议使用双条件决定是否先回执：

1. 若飞书消息提供 `duration_ms` 且大于阈值（默认 `15000ms`，见 §9 默认值）；
2. 或者 WebSocket 转写过程已超过 `short_wait_s`（默认 `10s`）。

满足任一条件时：

- 先发送回执：

   `语音已收到，正在转写和分析，请稍候。`

- 后续再发送正式回复。

> 阈值校准：`long_audio_threshold_ms = 15000` 和 `short_wait_s = 10` 为初值。Phase 4 真实联调时应基于实际转写时延的 P50 / P95 校准，不要长期沿用硬编码默认。

`duration_ms` 缺失时，完全退回第 2 条（转写耗时超过 `short_wait_s` 触发回执），不假设固定时长。

### 7.3 Final Response Format

正式回复始终展示转写文本，再给出回答：

```text
语音转写：
{transcript}

回答：
{agent_reply}
```

若 Agent 未返回有效回答，则降级为：

```text
语音转写：
{transcript}

回答：
我已经完成语音转写，但本次未生成有效回答。你可以继续追问，或基于上面的转写文本补充说明。
```

## 8. Module Changes

### 8.1 Existing Files to Modify

#### `evopaw/models.py` — ✅ 已完成（无改动）

已存在 `Attachment.msg_type` 支持 `audio` 与 `duration_ms` 字段；本次无需结构性改动，仅补齐 docstring 与类型注释。

#### `evopaw/feishu/listener.py` — ✅ 已完成（基线已满足）

已有 `audio` 解析逻辑，本次补齐：

- 与 §6.1 一致的 `duration_ms` 提取（加防御性 `int()`）
- 保留 `{file_key}.audio` 占位文件名
- 保留现有文本提取、routing_key、allowed_chats 白名单逻辑

#### `evopaw/feishu/downloader.py` — ✅ Phase 1 已完成

**关键修正**：在 `download()` 内构造请求时，把 `audio` 映射成飞书 API 接受的 `file`：

```python
api_type = "file" if attachment.msg_type == "audio" else attachment.msg_type
req = (
    GetMessageResourceRequest.builder()
    .message_id(msg_id)
    .file_key(attachment.file_key)
    .type(api_type)
    .build()
)
```

#### `evopaw/runner.py` — ✅ Phase 2+3 已完成

这是本次改造的主要编排点，新增：

- ✅ `speech_service` 依赖（Phase 2）
- ✅ `audio` 分支处理（Phase 2）
- ✅ 短等待与回执逻辑（Phase 3）：`duration_ms > long_audio_threshold_ms` 立即发 ack；否则 `short_wait_s` 内拿到结果就同步回复，超时则 ack 后继续等
- ✅ 语音失败降级文案（Phase 3）：按 `AsrFailure.reason` 映射到七种文案（§12.1-§12.5）
- ✅ 最终 "转写 + 回答" 回复格式化（Phase 2）
- ✅ **同 `msg_id` 去重**（Phase 2），audio 专属的 `audio_dedup_hits_total` 指标（Phase 3）
- ✅ `audio_messages_total{status}` 四分类埋点（Phase 3）：success / asr_failed / no_service / download_failed

#### `evopaw/main.py` — ✅ Phase 2+3 已完成

新增：

- ✅ `asr` 配置读取（单节，无 `oss` 段）（Phase 2）
- ✅ `FunASRRealtimeClient` 初始化（持有 `DASHSCOPE_API_KEY` 与 `ws_url`）（Phase 2）
- ✅ `SpeechRecognitionService` 初始化（Phase 2）
- ✅ 注入 `Runner`（Phase 2）
- ✅ 透传 `max_reconnect_retries` / `short_wait_s` / `long_audio_threshold_ms` / `ack_text`（Phase 3）

### 8.2 New Files to Add

#### `evopaw/asr/__init__.py` — ✅ Phase 1 已完成

ASR 包入口。

#### `evopaw/asr/models.py` — ✅ Phase 1 已完成

定义：

- `AsrResult`
- `AsrFailure`

（本方案不存在 `AsrTaskHandle` —— WebSocket one-shot 无对外持有 task 句柄的需要。）

#### `evopaw/asr/funasr_realtime_client.py` — ✅ Phase 1 已完成

职责：

1. 针对单次语音建立短连接 WebSocket（基于 `aiohttp.ClientSession.ws_connect` 或 `websockets` 库；优先选已在依赖里的库）。
2. 发送 `run-task` 并等待 `task-started`。
3. 流式推送音频二进制帧（可配置分片大小 / 间隔）。
4. 发送 `finish-task`。
5. 按 §6.3 步骤 6 聚合 `result-generated.payload.output.sentence.text`（仅 `sentence_end == true`）。
6. 处理 `task-finished` / `task-failed` 终态与 WebSocket 异常。

> ✅ Phase 3 完成：`max_reconnect_retries` 对 `ws_connect`/`submit`/`disconnect` 三类可重试失败做整次转写重试，触发时记录 `asr_ws_reconnect_total` 指标。

#### `evopaw/asr/service.py` — ✅ Phase 1 已完成

高层编排：

1. 接收本地音频路径。
2. 调用 `FunASRRealtimeClient` 完成 one-shot 转写。
3. 返回 `AsrResult` 或抛出标准化失败。
4. 负责 metrics / 日志字段（§15）。

> 一期不引入 `evopaw/storage/*`。若未来补回录音文件 REST API 方案（例如超长会议录音转写）再新增。

## 9. Configuration Design

建议在 `config.yaml.template` 中新增如下配置段。**没有 `oss:` 段**。

```yaml
asr:
  enabled: true                 # ✅ Phase 2 已读并决定是否构建 service
  provider: "aliyun_funasr_realtime"  # ✅ Phase 2
  model: "fun-asr-realtime"     # ✅ Phase 2；稳定别名；上线前固定为阿里云官方发布的快照号
  ws_url: "wss://dashscope.aliyuncs.com/api-ws/v1/inference/"  # ✅ Phase 2
  audio_format: "opus"          # ✅ Phase 2；飞书默认；若 §18.2 判定不兼容再切
  sample_rate: 16000            # ✅ Phase 2；fun-asr-realtime 要求 16kHz
  chunk_bytes: 1024             # ✅ Phase 2；每次发送字节数
  chunk_interval_ms: 100        # ✅ Phase 2；发送间隔
  display_transcript: true      # ✅ Phase 4
  include_audio_path: true      # ✅ Phase 4
  short_wait_s: 10              # ✅ Phase 3 回执判定
  max_wait_s: 120               # ✅ Phase 2；整体转写硬上限
  submit_timeout_s: 10          # ✅ Phase 2
  max_reconnect_retries: 1      # ✅ Phase 3（客户端 transcribe() 对三类可重试 reason 循环）
  dedup_window_size: 256        # ✅ Phase 2（在 runner 段读取）
  long_audio_threshold_ms: 15000 # ✅ Phase 3 回执阈值
  ack_text: "语音已收到，正在转写和分析，请稍候。"  # ✅ Phase 3
  transcription_title: "语音转写"   # ✅ Phase 4
  answer_title: "回答"             # ✅ Phase 4
```

> §9 全部字段在 Phase 4 离线部分完成后已落地到 `config.yaml.template` 与 Runner 构造参数；运营方可改 yaml 即时生效。

语义澄清：

- `submit_timeout_s`：从发完 `run-task` 到收到 `task-started` 的最大等待；超时视为提交失败（§12.2）。
- `max_wait_s`：整次 one-shot 转写的硬上限（握手 + 推流 + 等待 `task-finished` 的总时长）；超时 Runner 主动关闭 WebSocket 并走 §12.4。
- `max_reconnect_retries`：指整次转写失败后从头重试的次数（新建 WebSocket + 重新推流）；默认 `1` 意味着失败最多重试 1 次。
- 对飞书事件的重投递，通过 `dedup_window_size` 做近期 `msg_id` 去重；命中去重直接丢弃，不触发 WebSocket 建联。

### 9.1 Model Versioning Policy

推荐策略：

- 开发联调使用稳定别名 `fun-asr-realtime`。
- 上线前固定为阿里云官方发布的快照版本号（以 [官方文档](https://help.aliyun.com/zh/model-studio/fun-asr-realtime-websocket-api) 当时列出的为准，例如 `fun-asr-realtime-2025-11-07` 或更新的快照）。**不要擅自构造推测版本号**。

理由：

- 开发阶段优先减少人工更新配置成本
- 生产阶段优先保证行为可回溯、可回归

### 9.2 Secrets Policy

**唯一敏感凭证**：`DASHSCOPE_API_KEY`。

- 不写入 `config.yaml`，不写入 `workspace/.config`。
- 由 `FunASRRealtimeClient` 在 `__init__` 里从 `os.environ` 直读。
- 不传入 Skill / Sub-Agent 工具层。
- `.env.example` 需同步新增占位条目，真实值只写 `.env`（已在 `.gitignore`）。

相比 REST + OSS 方案，凭证面从 4 个（`DASHSCOPE_API_KEY` / `OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET` / `OSS_BUCKET`）收敛到 1 个。

## 10. Internal Data Contracts

### 10.1 Attachment

当前实现已落地：

```python
@dataclass(frozen=True)
class Attachment:
    msg_type: str                 # "image" | "file" | "audio"
    file_key: str
    file_name: str                # listener 占位；一期不做扩展名矫正
    duration_ms: int | None = None
```

### 10.2 AsrResult

```python
@dataclass(frozen=True)
class AsrResult:
    transcript: str               # 拼接 sentence_end==true 的 sentence.text（§6.3）
    provider: str                 # "aliyun_funasr_realtime"
    model: str                    # 例如 "fun-asr-realtime"
    task_id: str                  # run-task 请求时的 32 位 ID（仅用于日志）
    duration_ms: int | None = None  # 来自飞书事件，非百炼
```

### 10.3 AsrFailure

实际落地为 `Exception` 的 frozen dataclass 子类（便于在 Runner / service 处 `raise` 与 `except AsrFailure` 统一处理）：

```python
@dataclass(frozen=True)
class AsrFailure(Exception):
    reason: str                   # "download" | "ws_connect" | "submit" | "task_failed"
                                  # | "timeout" | "empty" | "disconnect"
    detail: str | None = None     # 诊断信息，不进入用户回复
    task_id: str | None = None
```

> 实现上新增 `disconnect` reason，覆盖 WebSocket 中途异常关闭 / 错误帧（§12.3），这样七种 reason 与 §15 metrics 的 `status` label 值一一对应。

## 11. Runner Semantics

### 11.1 Queue Model

一期保持现有 `per-routing_key` 串行队列模型不变。

含义：

- 同一会话内，语音转写仍占用当前 worker，直到正式回复结束
- 即便已发送回执，后台也仍由当前 `_handle()` 协程继续处理
- 同一 `routing_key` 下后续消息继续排队等待

### 11.2 Why Not Split Background Jobs in Phase 1

不在一期引入独立后台任务的原因：

1. 避免 session 历史顺序错乱
2. 避免引入任务持久化与恢复机制
3. 控制实现复杂度

代价：

- 长语音会阻塞同会话后续消息吞吐

这是一期接受的 tradeoff。

## 12. Failure Handling

失败需要精细分层，不做笼统"系统错误"。WebSocket 失败必须显式关闭连接，避免半开连接泄漏。

### 12.1 Download Failure

场景：

- 飞书资源下载失败
- 资源超出飞书 100MB 下载限制
- downloader `type` 映射错误（本次设计已修复）

用户回复：

`语音文件下载失败，请重试，或改发文字消息。`

### 12.2 WebSocket Connect / Submit Failure

场景：

- 握手失败（网络 / 401 / 5xx）
- 发送 `run-task` 后 `submit_timeout_s` 内未收到 `task-started`

用户回复：

`语音已收到，但转写服务连接失败，请稍后重试。`

按 `max_reconnect_retries` 策略重试整次转写；仍失败则进入本分支。

### 12.3 WebSocket Unexpected Disconnect

场景：

- 推流过程中或等待结果过程中 TCP 异常关闭、收到非预期 close frame

策略：

- 与 §12.2 同样按 `max_reconnect_retries` 重试整次转写。
- 仍失败时用户回复：`语音转写中断，请稍后重试，或改发文字消息。`

### 12.4 Transcribe Timeout

策略：

- `short_wait_s = 10`
- `max_wait_s = 120`

超过 `max_wait_s` 时：

- 主动关闭 WebSocket 连接。
- 用户回复：`语音转写超时，请稍后重试，或改发文字消息。`
- 不对百炼侧做额外操作（连接一关，服务端任务自然终止）。

### 12.5 Task Failed or Empty Transcript

触发条件：

- 收到 `task-failed` 事件
- 所有 `sentence_end==true` 的 sentence.text 拼接后为空

用户回复：

`语音转写失败，请重试，或改发文字消息。`

`AsrFailure.detail` 记录服务端返回的 `error_code` / `error_message`，仅用于日志 / 指标，不外显。

### 12.6 Agent Failure After Successful ASR

若转写成功但 Agent 出错：

```text
语音转写：
{transcript}

回答：
处理出错，请稍后重试。
```

这样可避免用户完全丢失已经成功得到的 transcript。

## 13. Session History Policy

写入历史的是增强后的标准化文本，而不是飞书原始 `audio` 元消息。

历史中保留：

- transcript
- 本地音频路径

历史中不保留：

- `task_id`
- WebSocket 连接细节

原因：

1. 历史可读性更高
2. 记忆检索更稳定
3. 避免历史依赖临时连接标识

**已知风险**：本地 `uploads/` 由 `CleanupService` 默认保留 7 天；超过保留期后，历史文本里指向音频的路径将失效。Agent 或 pgvector 检索命中远期历史时应视该路径为 "曾经存在" 的弱引用，不做强依赖。

## 14. Cleanup Policy

### 14.1 Local Files

本地 `uploads` 继续沿用当前 `CleanupService` 的保留策略，默认 `7` 天。

不在 ASR 成功后立即删除本地文件，原因：

1. Agent 输入要求保留原始音频本地路径
2. 便于后续排查识别质量问题

### 14.2 Remote Resources

**无**。本方案不往任何远端对象存储写入语音文件，无需远端清理 / Lifecycle 规则。

## 15. Observability — ✅ Phase 3 已完成

指标已全部注册到 `evopaw/observability/metrics.py`，并在对应模块完成埋点：

- ✅ `evopaw_asr_requests_total{provider,status}` — status ∈ {success, ws_connect, submit, disconnect, timeout, task_failed, empty, download}（实际 label 与 `AsrFailure.reason` 一致，比文档初稿多 `download` 分类）
- ✅ `evopaw_asr_latency_seconds{provider}` — service 层记录整次转写耗时的 Histogram
- ✅ `evopaw_asr_timeouts_total{provider}` — `record_asr_request(..., "timeout")` 同步递增
- ✅ `evopaw_asr_ws_reconnect_total{provider}` — `max_reconnect_retries` 真正触发重试时 +1
- ✅ `evopaw_audio_messages_total{status}` — status ∈ {success, asr_failed, no_service, download_failed}
- ✅ `evopaw_audio_dedup_hits_total`（audio 消息重投递去重命中）

关键日志字段（已在 Phase 1/2 的客户端与 service 中打出 `routing_key` / `session_id` / `msg_id` / `task_id` / `asr_elapsed_ms` / `reason` / `detail`）：

- `routing_key`
- `session_id`
- `msg_id`
- `file_key`
- `task_id`
- `asr_status`（started / succeeded / failed / timeout / disconnect）
- `asr_elapsed_ms`

## 16. Test Strategy

### 16.1 Unit Tests

需要补齐以下单测：

1. `tests/unit/test_feishu_listener.py` — ✅ Phase 3 已完成
   - ✅ `audio` 消息识别（Phase 1 即有，端到端 dispatch 测试 Phase 3 新增）
   - ✅ `duration_ms` 提取（含非法值降级为 `None`）
   - ✅ `duration` 字段缺失 / 为 null 时 `duration_ms == None`
2. `tests/unit/test_downloader.py` — ✅ Phase 1 已补
   - ✅ 音频下载成功：断言请求体里的 `type == "file"`（映射生效）
   - ✅ 音频下载失败（测试早已存在）
3. `tests/unit/test_runner.py` — ✅ Phase 2+3 已完成
   - ✅ 短语音同步成功（Phase 2）
   - ✅ 长语音先回执后正式回复（Phase 3 duration 触发 + short_wait_s 触发两类）
   - ✅ 转写超时 → 用户看到 `"语音转写超时"` 文案（Phase 3）
   - ✅ 转写失败 → 按 reason 分类文案（Phase 3 参数化 7 种）
   - ✅ Agent 失败但 ASR 成功 → transcript 仍出现（Phase 2）
   - ✅ 重复 `msg_id` → 去重命中，`audio_dedup_hits_total` +1（Phase 2+3）
4. `tests/unit/test_funasr_realtime_client.py` — ✅ Phase 1+3 已完成 21 个用例
   - ✅ 握手 header 含 `Authorization: bearer xxx`
   - ✅ `run-task` 发送结构完整，`format`/`sample_rate` 与 config 一致
   - ✅ 收到 `task-started` 才开始推流
   - ✅ 只保留 `sentence_end == true` 的句子进 transcript
   - ✅ 多个 `result-generated` 按 `begin_time` 顺序拼接
   - ✅ `task-failed` 事件抛标准化失败
   - ✅ WebSocket 异常关闭抛标准化失败
   - ✅ `max_wait_s` 超时主动关闭连接
   - ✅ `max_reconnect_retries` 对 `ws_connect` / `disconnect` 触发重试并在第二次成功
   - ✅ 重试耗尽沿最后一次失败抛出
   - ✅ `task_failed` 属不可重试类型，只尝试一次
5. `tests/unit/test_asr_service.py` — ✅ Phase 1+3 已完成 10 个用例
   - ✅ 路径传参校验
   - ✅ 失败映射到 `AsrFailure`
   - ✅ 成功递增 `asr_requests_total{status="success"}` + `asr_latency_seconds`
   - ✅ 失败递增 `asr_requests_total{status=<reason>}`（timeout / download 等）

### 16.2 Integration Tests — ✅ Phase 3+4 已完成

`tests/integration/test_voice_end_to_end.py`（6 用例）：

- ✅ 启动本地 aiohttp WebSocket mock server，按 `task-started → result-generated × N → task-finished` 顺序下发事件
- ✅ Runner 完整链路：注入 `audio` 消息 → 下载 stub → service → client → WS mock → transcript → Agent → 回复
- ✅ 断言最终回复以 `"语音转写："` 开头，含 transcript 和 `"回答："` 段
- ✅ 断言 session 历史写入增强文本，且 ack 消息不进历史
- ✅ 覆盖 §16.3 mock 可达的四类样例：
  - 样例 1（短语音）：duration=3000ms 一卡片完成，无 ack
  - 样例 2（长语音）：duration=20000ms 先 ack 后正式回复
  - 样例 4 子项（损坏 / `task_failed`）：用户看到 `"语音转写失败"`
  - 样例 4 子项（中途 disconnect）：用户看到 `"语音转写中断"`
  - 样例 4 子项（整体超时 `max_wait_s`）：用户看到 `"语音转写超时"`
- 真实录音质量、采样率、中英混合识别等 §16.3 内容仍需 runbook 步骤 D

### 16.3 Pre-Production Verification — ⏳ Phase 4 待实施

至少覆盖 4 类真实样例，其中必须包含通过飞书客户端实际录制的真实语音样本（作为测试资产入库或 CI artifact）：

1. `3` 秒普通中文语音（飞书真实录音，OPUS 容器）
2. `20` 秒普通中文语音（飞书真实录音）
3. 英文或中英混合语音
4. 不支持格式或损坏文件

验收标准：

1. 飞书语音能被识别为 `audio`
2. 音频能下载到本地 `uploads`
3. WebSocket 握手成功并收到 `task-started`
4. 短语音在单卡片内完成
5. 长语音能先回执再正式回复
6. 正式回复包含 "语音转写" 和 "回答"
7. 失败场景不导致 worker 崩溃或 WebSocket 半开泄漏
8. `evopaw_asr_requests_total` 指标能如实区分成功 / 失败 / 超时 / 断开

## 17. Rollout Plan

建议分四阶段落地。

### Phase 1: Basic Plumbing

目标：

`audio -> download (with type mapping) -> ws handshake -> run-task -> stream bytes -> finish-task -> transcript`

此阶段不接 Agent，只验证基础链路与日志。关键验证点：Fun-ASR 能对飞书真实 OPUS 样本成功转写；若不成功，走 §18.2 的降级方案判定。

### Phase 1: Basic Plumbing — ✅ 已完成

（标题对应前述内容；Phase 1 通过的单测为 `test_funasr_realtime_client.py` 17 项 + `test_asr_service.py` 7 项 + `test_downloader.py` 2 项断言。）

### Phase 2: Runner Integration — ✅ 已完成

目标：

- ✅ 将 transcript 组装进 `user_content`（`evopaw/runner.py::_build_voice_message`）
- ✅ 跑通正式回复（`_format_voice_reply` + `_VOICE_AGENT_ERROR_REPLY` 兜底）
- ✅ 完成重投递去重（`Runner._is_duplicate_msg`，LRU 窗口 256，`is_cron` 绕过）

Phase 2 新增单测 10 项（`test_runner.py`：5 语音 + 5 dedup），总计 537 passed。

### Phase 3: Reliability — ✅ 已完成

目标：

- ✅ 超时与失败回退：Runner 内按 `AsrFailure.reason` 映射到五种用户文案，覆盖 §12.1–§12.5 的全部七种 reason
- ✅ 回执逻辑：`duration_ms > long_audio_threshold_ms` 立即发 ack；否则 `asyncio.wait_for(..., short_wait_s)` 超时后发 ack 再继续等
- ✅ WebSocket 可重试失败的 `max_reconnect_retries` 整次转写重试（`ws_connect`/`submit`/`disconnect` 三类）；`task_failed`/`empty`/`timeout` 不重试
- ✅ §15 全部 6 个 Prometheus 指标注册 + service/client/runner 三处埋点
- ✅ 集成测试：`tests/integration/test_voice_end_to_end.py` 本地 aiohttp WS mock server 覆盖 happy path + task_failed

Phase 3 新增单测 23 项（client 4 retry / service 3 metrics / runner 13 = 参数化 7 + ack 3 + metrics 3 / listener 2 audio dispatch & 补丁），总计 560 unit + 2 integration。

### Phase 4: Pre-Production Tuning — 🚧 离线 + 半自动化完成

**离线 / 半自动化（已完成 — 不需要真实凭证）**：

- ✅ 显示配置可覆写：`transcription_title` / `answer_title` / `display_transcript` / `include_audio_path` 接入 Runner + config + main，4 个新单测覆盖
- ✅ OPUS 采样率审计脚本：`scripts/audit_audio_sample_rate.py`（ffprobe 探测 + §18.2 方案 A/B 推荐），9 个离线单测
- ✅ 阈值校准脚本：`scripts/calibrate_thresholds.py`（连本地 Prometheus 拉 P50/P80/P95，输出 `short_wait_s` / `max_wait_s` 推荐），17 个离线单测
- ✅ 模型快照号校验：`main._warn_if_model_is_alias` 启动期检测稳定别名并 WARN，9 个单测；`config.yaml.template` 附 2026-04-25 检索的当前快照号清单
- ✅ 集成测试 6 用例覆盖 §16.3 四类样例的 mock 可验证部分（短/长/disconnect/timeout/task_failed）
- ✅ 预生产 runbook：`docs/runbooks/voice-pre-production.md` 把剩余真凭证联调步骤写成可执行命令

**联调部分（必须在真实飞书 app + 百炼 API Key + 真实流量下进行）**：

- ⏳ 跑 EvoPaw 接受真实飞书录音 ≥ 1 周
- ⏳ §18.2：用 `audit_audio_sample_rate.py` 在真实样本上得出方案结论
- ⏳ runbook 步骤 B：用 `calibrate_thresholds.py` 在真实流量上取分位数
- ⏳ runbook 步骤 C：复核阿里云文档当时的最新快照号，写回 `config.yaml`
- ⏳ §16.3 四类真实样例验收（runbook 步骤 D 给出验收矩阵）

## 18. Risks and Tradeoffs

> 风险处置状态概览：18.1 / 18.5 / 18.6 是需接受的设计 tradeoff；18.2 仍需 Phase 4 实测；18.3 / 18.4 已在 Phase 1 按缓解方案落地。

### 18.1 Session Throughput — ✅ 回执已落地（session 串行行为本身是 tradeoff）

风险：

- 长语音会阻塞同会话后续消息

缓解：

- 用回执改善用户感知
- 二期再考虑拆后台 continuation

### 18.2 Audio Format / Sample Rate Mismatch — ⏳ Phase 4 待实测

风险：

- 飞书 OPUS 容器内的实际采样率（48k / 24k / 16k / 8k 之一）未经实测确认。`fun-asr-realtime` 硬性要求 `sample_rate: 16000`。若实际不是 16k，可能出现识别失败或质量下降。
- Fun-ASR 虽声明支持 opus，但不保证所有变种都可识别。

缓解：

- Phase 1 对飞书真实录音做采样率探测（`opusinfo` / `ffprobe`），如发现非 16k：
  - 方案 A：切到 `fun-asr-flash-8k-realtime` 或其它匹配采样率的模型（需重新核实模型能力）。
  - 方案 B：在 downloader 或 asr service 里加一层 `ffmpeg -ar 16000 -f wav` 转码，代价是新增 ffmpeg 依赖与 CPU 开销。
- 方案 A / B 选择延后到 Phase 1 实测结论出来再定，不在一期默认路径中强制引入 ffmpeg。

### 18.3 Credential Scope — ✅ 已按缓解方案落地

风险：

- 若 `DASHSCOPE_API_KEY` 被 Agent 间接读取，可用于任意百炼调用

缓解（全部已落地）：

- ✅ 凭证只留在主进程环境（`main._build_speech_service` 从 `os.environ` 读取）
- ✅ 不写入 `workspace/.config`（`CleanupService` 的凭证写入函数未扩展到 DashScope）
- ✅ Skill / Sub-Agent 工具列表不暴露 Fun-ASR 客户端（`evopaw/asr/*` 未注册为 MCP 工具）

### 18.4 WebSocket Half-Open Connections — ✅ 已按缓解方案落地

风险：

- 转写超时 / 异常时若未显式关闭 WebSocket，会占用连接池并可能导致百炼侧服务限流

缓解：

- ✅ 所有终态分支（成功 / 失败 / 超时 / 断开）强制 `ws.close()`（`_transcribe_once` 的 `finally` 块）
- ✅ 用 `try / finally` 保证释放（同上）
- ✅ 监控 `evopaw_asr_ws_reconnect_total`（Phase 3 已注册并在 `transcribe` 重试循环中递增）

### 18.5 Feishu duration Field Contract Uncertainty — 🚧 接受的 tradeoff

风险：

- 飞书 audio 接收事件的 `duration` 字段无公开稳定契约
- 若飞书后续变更字段名或取消该字段，长短语音判定会退化为仅靠 `short_wait_s` 超时

缓解：

- 设计不强依赖该字段
- 缺失时完全退回 `short_wait_s` 触发回执
- 监控 `duration_ms is None` 的比例

### 18.6 Historical Audio Path Staleness — 🚧 接受的 tradeoff

风险：

- 会话历史中记录的本地音频路径会在 7 天后被 `CleanupService` 清理
- Agent 检索远期历史时路径不可用

缓解：

- 在 Agent system prompt 中说明 "音频路径是短期引用"
- 可选：用 `evopaw_audio_path_stale_total` 指标观测实际影响

## 19. Source Links

### Aliyun

- Fun-ASR 实时语音识别 WebSocket API（**主参考**）:
  - https://help.aliyun.com/zh/model-studio/fun-asr-realtime-websocket-api
- 实时语音识别概览（Fun-ASR / Gummy / Paraformer）:
  - https://help.aliyun.com/zh/model-studio/real-time-speech-recognition
- （备选路径）录音文件识别 REST API，本设计不使用但保留链接便于对比:
  - https://help.aliyun.com/zh/model-studio/fun-asr-recorded-speech-recognition-restful-api

### Feishu

- 获取消息中的资源文件:
  - https://open.feishu.cn/document/server-docs/im-v1/message/get-2
- 消息内容结构描述（含 audio）:
  - https://open.feishu.cn/document/server-docs/im-v1/message-content-description/create_json

## 20. Final Recommendation

推荐按以下原则实施：

1. 将语音识别视为 `Runner` 预处理能力，而不是 Agent 工具能力。
2. 一期使用 `Fun-ASR 实时 WebSocket API` 的 one-shot 模式（`run-task → 推流 → finish-task → task-finished`），不引入 OSS 或任何对象存储。
3. 保持现有队列与 session 语义稳定。
4. 先保守实现 "转写 + 回答"，再在二期考虑更丰富的语音能力。
5. 开发前先修复 `downloader.py` 的 `type` 参数映射（audio → file），这是任何后续联调的前置条件。
6. 仅一把凭证 `DASHSCOPE_API_KEY`，大幅降低凭证与运维面。

该方案与 EvoPaw 当前代码结构兼容性高，改动范围清晰，能以最低系统扰动把飞书语音消息接入现有 Bot 工作流。
