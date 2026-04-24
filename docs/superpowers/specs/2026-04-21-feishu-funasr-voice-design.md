# EvoPaw Feishu Voice Input with Fun-ASR Design

> 文档版本：`2026-04-24 rev2`（切换到 Fun-ASR 实时 WebSocket API，取消 OSS 依赖）
> 基线 commit：`1940522`（彼时 `evopaw/feishu/listener.py`、`evopaw/models.py` 已包含 audio 解析与 `duration_ms` 字段的早期试做）

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

#### `evopaw/models.py`

已存在 `Attachment.msg_type` 支持 `audio` 与 `duration_ms` 字段；本次无需结构性改动，仅补齐 docstring 与类型注释。

#### `evopaw/feishu/listener.py`

已有 `audio` 解析逻辑，本次补齐：

- 与 §6.1 一致的 `duration_ms` 提取（加防御性 `int()`）
- 保留 `{file_key}.audio` 占位文件名
- 保留现有文本提取、routing_key、allowed_chats 白名单逻辑

#### `evopaw/feishu/downloader.py`

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

#### `evopaw/runner.py`

这是本次改造的主要编排点，新增：

- `speech_service` 依赖
- `audio` 分支处理
- 短等待与回执逻辑
- 语音失败降级文案
- 最终 "转写 + 回答" 回复格式化
- **同 `msg_id` 去重**：维护最近 N 条（默认 256）已处理 `msg_id` 的滚动集合，防止飞书重投递导致对同一语音重复建立 WebSocket 连接。

#### `evopaw/main.py`

新增：

- `asr` 配置读取（单节，无 `oss` 段）
- `FunASRRealtimeClient` 初始化（持有 `DASHSCOPE_API_KEY` 与 `ws_url`）
- `SpeechRecognitionService` 初始化
- 注入 `Runner`

### 8.2 New Files to Add

#### `evopaw/asr/__init__.py`

ASR 包入口。

#### `evopaw/asr/models.py`

定义：

- `AsrResult`
- `AsrFailure`

（本方案不存在 `AsrTaskHandle` —— WebSocket one-shot 无对外持有 task 句柄的需要。）

#### `evopaw/asr/funasr_realtime_client.py`

职责：

1. 针对单次语音建立短连接 WebSocket（基于 `aiohttp.ClientSession.ws_connect` 或 `websockets` 库；优先选已在依赖里的库）。
2. 发送 `run-task` 并等待 `task-started`。
3. 流式推送音频二进制帧（可配置分片大小 / 间隔）。
4. 发送 `finish-task`。
5. 按 §6.3 步骤 6 聚合 `result-generated.payload.output.sentence.text`（仅 `sentence_end == true`）。
6. 处理 `task-finished` / `task-failed` 终态与 WebSocket 异常。

#### `evopaw/asr/service.py`

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
  enabled: true
  provider: "aliyun_funasr_realtime"
  model: "fun-asr-realtime"     # 稳定别名；上线前固定为阿里云官方发布的快照号
  ws_url: "wss://dashscope.aliyuncs.com/api-ws/v1/inference/"
  audio_format: "opus"          # 飞书默认；若 §18.2 判定不兼容再切
  sample_rate: 16000            # fun-asr-realtime 要求 16kHz
  chunk_bytes: 1024             # 每次发送字节数
  chunk_interval_ms: 100        # 发送间隔
  display_transcript: true
  include_audio_path: true
  short_wait_s: 10              # 短等待上限（触发回执的软阈值）
  max_wait_s: 120               # 整体转写硬上限
  submit_timeout_s: 10          # run-task → task-started 超时
  max_reconnect_retries: 1      # 握手/连接级重试次数（整次转写失败才重试）
  dedup_window_size: 256        # 最近 msg_id 去重窗口
  long_audio_threshold_ms: 15000
  ack_text: "语音已收到，正在转写和分析，请稍候。"
  transcription_title: "语音转写"
  answer_title: "回答"
```

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

```python
@dataclass(frozen=True)
class AsrFailure:
    reason: str                   # "download" | "ws_connect" | "submit" | "task_failed" | "timeout" | "empty"
    detail: str | None = None     # 诊断信息，不进入用户回复
    task_id: str | None = None
```

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

## 15. Observability

建议新增指标：

- `evopaw_asr_requests_total{provider,status}` — status ∈ {success, ws_connect_fail, submit_fail, disconnect, timeout, failed, empty}
- `evopaw_asr_latency_seconds{provider}` — 整次 one-shot 转写耗时（握手到 task-finished）
- `evopaw_asr_timeouts_total{provider}`
- `evopaw_asr_ws_reconnect_total{provider}` — `max_reconnect_retries` 实际触发的次数
- `evopaw_audio_messages_total{status}`
- `evopaw_audio_dedup_hits_total`（消息重投递去重命中）

关键日志字段：

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

1. `tests/unit/test_feishu_listener.py`
   - `audio` 消息识别
   - `duration_ms` 提取（含非法值降级为 `None`）
   - `duration` 字段缺失时 `duration_ms == None`
2. `tests/unit/test_downloader.py`
   - 音频下载成功：断言请求体里的 `type == "file"`（映射生效）
   - 音频下载失败
3. `tests/unit/test_runner.py`
   - 短语音同步成功
   - 长语音先回执后正式回复
   - 转写超时 → 用户回复 + WebSocket 被关闭
   - 转写失败 → 用户回复
   - Agent 失败但 ASR 成功 → transcript 仍出现
   - 重复 `msg_id` → 去重命中，不触发 WebSocket 建联
4. `tests/unit/test_funasr_realtime_client.py`（新增文件）
   - 握手 header 含 `Authorization: bearer xxx`
   - `run-task` 发送结构完整，`format`/`sample_rate` 与 config 一致
   - 收到 `task-started` 才开始推流
   - 只保留 `sentence_end == true` 的句子进 transcript
   - 多个 `result-generated` 按 `begin_time` 顺序拼接
   - `task-failed` 事件抛标准化失败
   - WebSocket 异常关闭抛标准化失败
   - `max_wait_s` 超时主动关闭连接
5. `tests/unit/test_asr_service.py`（新增文件）
   - 路径传参校验
   - 失败映射到 `AsrFailure`

### 16.2 Integration Tests

建议新增伪依赖集成测试：

- 启动一个本地 WebSocket mock server，按官方事件顺序下发 `task-started` → `result-generated` × N → `task-finished`
- 注入 `audio` 消息事件
- 断言 session 历史写入增强文本
- 断言飞书回复格式为 "转写 + 回答"

### 16.3 Pre-Production Verification

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

### Phase 2: Runner Integration

目标：

- 将 transcript 组装进 `user_content`
- 跑通正式回复
- 完成重投递去重

### Phase 3: Reliability

目标：

- 超时与失败回退
- 回执逻辑
- WebSocket 异常断开重试
- 监控指标

### Phase 4: Pre-Production Tuning

目标：

- 真实飞书与百炼联调
- 基于 P50 / P95 延迟校准 `short_wait_s` 与 `long_audio_threshold_ms`
- 调整阈值、文案和日志粒度
- 固定 Fun-ASR 版本为上线快照号（§9.1）

## 18. Risks and Tradeoffs

### 18.1 Session Throughput

风险：

- 长语音会阻塞同会话后续消息

缓解：

- 用回执改善用户感知
- 二期再考虑拆后台 continuation

### 18.2 Audio Format / Sample Rate Mismatch

风险：

- 飞书 OPUS 容器内的实际采样率（48k / 24k / 16k / 8k 之一）未经实测确认。`fun-asr-realtime` 硬性要求 `sample_rate: 16000`。若实际不是 16k，可能出现识别失败或质量下降。
- Fun-ASR 虽声明支持 opus，但不保证所有变种都可识别。

缓解：

- Phase 1 对飞书真实录音做采样率探测（`opusinfo` / `ffprobe`），如发现非 16k：
  - 方案 A：切到 `fun-asr-flash-8k-realtime` 或其它匹配采样率的模型（需重新核实模型能力）。
  - 方案 B：在 downloader 或 asr service 里加一层 `ffmpeg -ar 16000 -f wav` 转码，代价是新增 ffmpeg 依赖与 CPU 开销。
- 方案 A / B 选择延后到 Phase 1 实测结论出来再定，不在一期默认路径中强制引入 ffmpeg。

### 18.3 Credential Scope

风险：

- 若 `DASHSCOPE_API_KEY` 被 Agent 间接读取，可用于任意百炼调用

缓解：

- 凭证只留在主进程环境
- 不写入 `workspace/.config`
- Skill / Sub-Agent 工具列表不暴露 Fun-ASR 客户端

### 18.4 WebSocket Half-Open Connections

风险：

- 转写超时 / 异常时若未显式关闭 WebSocket，会占用连接池并可能导致百炼侧服务限流

缓解：

- 所有终态分支（成功 / 失败 / 超时 / 断开）强制 `ws.close()`
- 用 `async with` 语法或 `try / finally` 保证释放
- 监控 `evopaw_asr_ws_reconnect_total`

### 18.5 Feishu duration Field Contract Uncertainty

风险：

- 飞书 audio 接收事件的 `duration` 字段无公开稳定契约
- 若飞书后续变更字段名或取消该字段，长短语音判定会退化为仅靠 `short_wait_s` 超时

缓解：

- 设计不强依赖该字段
- 缺失时完全退回 `short_wait_s` 触发回执
- 监控 `duration_ms is None` 的比例

### 18.6 Historical Audio Path Staleness

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
