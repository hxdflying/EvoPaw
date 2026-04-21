# EvoPaw Feishu Voice Input with Fun-ASR Design

## 1. Summary

本设计为 EvoPaw 增加飞书语音消息处理能力，使用户可以在飞书中直接向 Bot 发送语音，由系统自动完成下载、转写、理解和回复。

目标链路：

`Feishu audio message -> EvoPaw download -> OSS temporary object -> Bailian Fun-ASR transcription -> enhanced user message -> existing agent -> Feishu reply`

用户侧体验要求已经确认如下：

- 输入来源：飞书 `audio` 消息
- 识别服务：阿里百炼 `Fun-ASR` 录音文件识别 REST API
- 文件中转：阿里云 OSS
- Agent 输入：同时保留 `转写文本 + 原始音频本地路径`
- 回复格式：先展示转写文本，再给出回答
- 交互策略：混合模式
  - 短语音：同一张 “思考中” 卡片内完成
  - 长语音或转写超出短等待窗口：先回执，再发送正式回复

## 2. Context

当前 EvoPaw 已有飞书 WebSocket 接收、消息标准化、附件下载、会话管理和 Agent 执行链路。

现状关键点：

- `evopaw/feishu/listener.py`
  - 目前只将 `text` 和 `post` 解析为正文。
  - 目前只将 `image` 和 `file` 解析为附件。
- `evopaw/feishu/downloader.py`
  - 已支持通过飞书消息资源接口下载附件到 `workspace/sessions/{sid}/uploads/`。
- `evopaw/runner.py`
  - 已有“附件下载后再组装用户消息”这条预处理能力。
  - 当前附件处理只覆盖图片和文件，没有音频转写服务层。

因此，本次改造的合理切入点不是改 Agent 主结构，而是在现有附件预处理阶段新增 `audio -> transcription` 分支。

## 3. Verified External Constraints

以下约束已基于截至 `2026-04-21` 的官方文档确认：

### 3.1 Feishu

- 飞书“获取消息中的资源文件”接口支持下载音频、视频、图片和文件。
- 当前仅支持下载 `100MB` 以内资源文件。
- 资源下载接口通过 `message_id + file_key` 获取文件二进制流。

这意味着 EvoPaw 可以沿用现有附件下载模式获取飞书语音文件，无需新增飞书侧的特殊上传或转码流程。

### 3.2 Fun-ASR

- Fun-ASR 录音文件识别 REST API 为异步任务模式：
  - 先提交任务
  - 再轮询任务状态
- 输入不支持本地文件直传，也不支持 `base64`
- 输入必须是公网可访问的 `HTTP/HTTPS` 文件 URL，通过 `file_urls` 传入
- 单文件限制：
  - 文件大小不超过 `2GB`
  - 时长不超过 `12` 小时
- 稳定别名 `fun-asr` 截至 `2026-04-21` 当前等同 `fun-asr-2025-08-25`

结论：

- 必须引入一个公网可访问的中转层
- OSS 是最自然的中转方案
- Fun-ASR 不能直接接本地 `workspace` 音频路径

### 3.3 OSS

- OSS 可以为私有对象生成带过期时间的预签名 URL
- 预签名 URL 足以满足 Fun-ASR 对“公网可访问 URL”的要求

结论：

- 生产环境不应把语音文件长期公开为公共读对象
- 应优先使用私有 Bucket + GET 预签名 URL

## 4. Goals and Non-Goals

### 4.1 Goals

1. 支持飞书 `audio` 消息进入 EvoPaw 主链路。
2. 将飞书语音下载到本地 `uploads/` 目录。
3. 将本地音频上传到 OSS 并生成临时 URL。
4. 通过 Fun-ASR 进行异步转写。
5. 将 `转写文本 + 原始音频本地路径` 一起交给现有 Agent。
6. 最终在飞书中展示：
   - `语音转写`
   - `回答`
7. 对长语音提供“先回执后正式回复”的用户体验。
8. 在失败、超时、下载错误等场景下提供明确降级行为。

### 4.2 Non-Goals

本阶段明确不做以下内容：

1. 本地部署或自托管 Fun-ASR。
2. 流式/实时转写 WebSocket 能力。
3. 说话人分离、情绪识别、事件检测等高级语音分析。
4. 前端直接调用百炼 API。
5. 让 Agent 直接读取 OSS URL 或自行负责转写。
6. 为长语音引入新的后台任务系统或持久化任务表。

## 5. Recommended Architecture

### 5.1 High-Level Flow

```text
Feishu User
  -> FeishuListener
  -> Runner
  -> FeishuDownloader
  -> OSSUploader
  -> FunASRClient
  -> SpeechRecognitionService
  -> Runner
  -> Main Agent
  -> FeishuSender
  -> Feishu User
```

### 5.2 Core Design Choice

推荐路线：

`Feishu audio -> local download -> OSS signed URL -> Fun-ASR async transcription -> enhanced text message -> existing agent`

不采用以下方案作为一期主线：

- 流式 ASR
  - 会显著改造现有 per-routing-key 串行处理模型
- 本地 Fun-ASR
  - 偏离“调用 API”目标
  - 带来额外模型运维成本

### 5.3 Processing Boundary

音频转写属于基础设施预处理能力，不属于 Agent 推理职责。

因此：

- `Runner` 在进入 `agent_fn` 之前完成音频转写
- `session` 历史中写入的是标准化后的增强文本
- `agent_fn` 不感知飞书 `audio` 原始协议，也不直接访问百炼或 OSS

## 6. End-to-End Data Flow

### 6.1 Receive

`FeishuListener` 收到 `im.message.receive_v1` 后：

1. 识别 `message_type == "audio"`
2. 从消息内容中提取：
   - `file_key`
   - `duration_ms`（若飞书事件中可取到）
3. 构造 `InboundMessage`
4. 将 `Attachment(msg_type="audio", ...)` 挂入 `InboundMessage.attachment`

### 6.2 Download

`Runner` 获取或创建 session 后：

1. 调用 `FeishuDownloader.download()`
2. 将音频落盘到：

`/workspace/sessions/{session_id}/uploads/{filename}`

3. 本地绝对路径示例：

`data/workspace/sessions/{session_id}/uploads/{filename}`

### 6.3 Upload

`SpeechRecognitionService` 收到本地音频路径后：

1. 生成 OSS object key，例如：

`evopaw/voice/{yyyy}/{mm}/{dd}/{session_id}/{msg_id}-{sanitized_filename}`

2. 上传到私有 Bucket
3. 生成 GET 预签名 URL，默认有效期 `3600s`

### 6.4 Transcribe

`FunASRClient`：

1. 向 Fun-ASR 提交异步转写任务
2. 保存返回的 `task_id`
3. 轮询任务状态直至：
   - `SUCCEEDED`
   - `FAILED`
   - 超时
4. 从结果中抽取最终 transcript

### 6.5 Enhance

`Runner` 将 transcript 组装成增强后的用户消息：

```text
用户发送了一条语音消息。

语音转写：
{transcript}

原始音频文件已保存到：
`/workspace/sessions/{session_id}/uploads/{filename}`

请优先根据语音转写理解用户意图；如有歧义，可结合原始音频文件路径做进一步处理。
```

### 6.6 Reply

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

### 7.2 Decision Rule

建议使用双条件决定是否先回执：

1. 若飞书消息提供 `duration_ms` 且大于阈值，例如 `15000ms`
2. 或者转写轮询已超过 `short_wait_s`，例如 `10s`

满足任一条件时：

- 先发送回执：

`语音已收到，正在转写和分析，请稍候。`

- 后续再发送正式回复

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

扩展 `Attachment`：

- `msg_type`: 从 `image | file` 扩为 `image | file | audio`
- 增加 `duration_ms: int | None = None`
- 保持现有不可变 dataclass 形式

#### `evopaw/feishu/listener.py`

新增：

- `audio` 消息识别
- `audio` 附件解析
- `duration_ms` 提取
- 音频默认文件名占位逻辑

保留：

- 文本正文提取逻辑
- 现有 routing_key 逻辑
- 现有 allowed_chats 白名单逻辑

#### `evopaw/feishu/downloader.py`

扩展说明和测试，支持：

- `audio` 类型资源下载

核心下载实现无需大改，因为飞书消息资源接口本身支持音频下载。

#### `evopaw/runner.py`

这是本次改造的主要编排点，新增：

- `speech_service` 依赖
- `audio` 分支处理
- 短等待与回执逻辑
- 语音失败降级文案
- 最终“转写 + 回答”回复格式化

#### `evopaw/main.py`

新增：

- `asr` 配置读取
- `oss` 配置读取
- `OssUploader` 初始化
- `FunASRClient` 初始化
- `SpeechRecognitionService` 初始化
- 注入 `Runner`

### 8.2 New Files to Add

#### `evopaw/storage/oss_uploader.py`

职责：

1. 上传本地文件到 OSS
2. 生成预签名 URL
3. 删除远端对象

#### `evopaw/asr/models.py`

定义：

- `AsrTaskHandle`
- `AsrResult`
- 可选 `AsrFailure`

#### `evopaw/asr/funasr_client.py`

职责：

1. 提交转写任务
2. 查询任务状态
3. 等待任务完成
4. 解析结果结构

#### `evopaw/asr/service.py`

高层编排：

1. 本地音频上传 OSS
2. 提交 Fun-ASR
3. 轮询结果
4. 返回标准化 transcript 与元数据

## 9. Configuration Design

建议在 `config.yaml.template` 中新增如下配置段。

```yaml
asr:
  enabled: true
  provider: "aliyun_funasr"
  model: "fun-asr"
  display_transcript: true
  include_audio_path: true
  short_wait_s: 10
  max_wait_s: 120
  poll_interval_s: 1.0
  long_audio_threshold_ms: 15000
  submit_timeout_s: 10
  query_timeout_s: 10
  max_submit_retries: 2
  max_query_retries: 2
  ack_text: "语音已收到，正在转写和分析，请稍候。"
  transcription_title: "语音转写"
  answer_title: "回答"

oss:
  enabled: true
  endpoint: "https://oss-cn-beijing.aliyuncs.com"
  region: "cn-beijing"
  bucket: "${OSS_BUCKET}"
  key_prefix: "evopaw/voice"
  use_signed_url: true
  signed_url_ttl_s: 3600
  remote_retention_hours: 24
```

约束：

- `oss.signed_url_ttl_s` 必须显著大于 `asr.max_wait_s`
- 推荐至少满足：
  - `signed_url_ttl_s >= max_wait_s + 300`

这样可以避免队列等待、提交重试或轮询期间 URL 过期，导致 Fun-ASR 侧出现隐性拉取失败。

### 9.1 Model Versioning Policy

推荐策略：

- 开发联调用 `fun-asr`
- 上线前固定为确认时的快照版，例如：
  - 截至 `2026-04-21`，当前稳定别名 `fun-asr` 等同 `fun-asr-2025-08-25`

理由：

- 开发阶段优先减少人工更新配置成本
- 生产阶段优先保证行为可回溯、可回归

### 9.2 Secrets Policy

以下凭证不写入 `config.yaml`，也不写入 `workspace/.config`：

- `DASHSCOPE_API_KEY`
- `OSS_ACCESS_KEY_ID`
- `OSS_ACCESS_KEY_SECRET`
- `OSS_BUCKET`

推荐：

- 优先使用环境变量注入
- 在阿里云环境中优先用 RAM Role / STS

## 10. Internal Data Contracts

### 10.1 Attachment

建议结构：

```python
@dataclass(frozen=True)
class Attachment:
    msg_type: str
    file_key: str
    file_name: str
    duration_ms: int | None = None
```

### 10.2 AsrTaskHandle

```python
@dataclass(frozen=True)
class AsrTaskHandle:
    task_id: str
    audio_url: str
    provider: str
    model: str
```

### 10.3 AsrResult

```python
@dataclass(frozen=True)
class AsrResult:
    transcript: str
    task_id: str
    provider: str
    model: str
    duration_ms: int | None = None
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

失败需要精细分层，不做笼统“系统错误”。

### 12.1 Download Failure

场景：

- 飞书资源下载失败
- 资源超出飞书下载限制

用户回复：

`语音文件下载失败，请重试，或改发文字消息。`

### 12.2 OSS Upload Failure

用户回复：

`语音已收到，但上传转写服务失败，请稍后重试。`

### 12.3 Fun-ASR Submit Failure

用户回复：

`语音已收到，但转写任务提交失败，请稍后重试。`

### 12.4 Fun-ASR Timeout

策略：

- `short_wait_s = 10`
- `max_wait_s = 120`

超过最大等待时间时：

`语音转写超时，请稍后重试，或改发文字消息。`

### 12.5 Empty Transcript or Failed Task

用户回复：

`语音转写失败，请重试，或改发文字消息。`

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

- OSS URL
- `task_id`
- 云端对象 key

原因：

1. 历史可读性更高
2. 记忆检索更稳定
3. 避免历史依赖临时 URL
4. 降低敏感云端对象信息暴露

## 14. Cleanup Policy

### 14.1 Local Files

本地 `uploads` 继续沿用当前 `CleanupService` 的保留策略，默认 `7` 天。

不在 ASR 成功后立即删除本地文件，原因：

1. Agent 输入要求保留原始音频本地路径
2. 便于后续排查识别质量问题

### 14.2 Remote OSS Files

采用双保险：

1. Fun-ASR 完成后立即尝试删除远端 OSS 对象
2. 同时配置 OSS Lifecycle，对 `evopaw/voice/` 前缀做 `24` 小时自动过期

一期不在 `CleanupService` 中做远端对象扫描清理，以降低复杂度。

## 15. Observability

建议新增指标：

- `evopaw_asr_requests_total{provider,status}`
- `evopaw_asr_latency_seconds`
- `evopaw_asr_timeouts_total`
- `evopaw_oss_upload_failures_total`
- `evopaw_audio_messages_total`

关键日志字段：

- `routing_key`
- `session_id`
- `msg_id`
- `file_key`
- `oss_object_key`
- `task_id`

## 16. Test Strategy

### 16.1 Unit Tests

需要补齐以下单测：

1. `tests/unit/test_feishu_listener.py`
   - `audio` 消息识别
   - `duration_ms` 提取
   - 缺失字段时的降级逻辑
2. `tests/unit/test_downloader.py`
   - 音频下载成功/失败
3. `tests/unit/test_runner.py`
   - 短语音同步成功
   - 长语音先回执后正式回复
   - ASR 超时
   - ASR 失败
4. `tests/unit/test_funasr_client.py`
   - 提交
   - 轮询
   - 超时
   - 空结果
5. `tests/unit/test_oss_uploader.py`
   - 上传
   - 生成签名 URL
   - 删除对象

### 16.2 Integration Tests

建议新增伪依赖集成测试：

- mock OSS
- mock Fun-ASR API
- 注入 `audio` 消息事件
- 断言 session 历史写入增强文本
- 断言飞书回复格式为“转写 + 回答”

### 16.3 Pre-Production Verification

至少覆盖 4 类真实样例：

1. `3` 秒普通中文语音
2. `20` 秒普通中文语音
3. 英文或中英混合语音
4. 不支持格式或损坏文件

验收标准：

1. 飞书语音能被识别为 `audio`
2. 音频能下载到本地 `uploads`
3. 音频能上传 OSS 并提交百炼任务
4. 短语音在单卡片内完成
5. 长语音能先回执再正式回复
6. 正式回复包含“语音转写”和“回答”
7. 失败场景不导致 worker 崩溃

## 17. Rollout Plan

建议分四阶段落地。

### Phase 1: Basic Plumbing

目标：

`audio -> download -> oss -> funasr -> transcript`

此阶段不接 Agent，只验证基础链路与日志。

### Phase 2: Runner Integration

目标：

- 将 transcript 组装进 `user_content`
- 跑通正式回复

### Phase 3: Reliability

目标：

- 超时与失败回退
- 回执逻辑
- OSS 删除
- 监控指标

### Phase 4: Pre-Production Tuning

目标：

- 真实飞书与百炼联调
- 调整阈值、文案和日志粒度

## 18. Risks and Tradeoffs

### 18.1 Session Throughput

风险：

- 长语音会阻塞同会话后续消息

缓解：

- 用回执改善用户感知
- 二期再考虑拆后台 continuation

### 18.2 Audio Format Compatibility

风险：

- 飞书语音文件实际编码格式可能与扩展名不一致
- Fun-ASR 虽支持多种格式，但并不保证所有变种都可识别

缓解：

- 联调阶段对真实飞书语音样本做兼容性验证
- 必要时增加轻量转码层，但不作为一期默认设计

### 18.3 Credential Scope

风险：

- 若将 DashScope/OSS 凭证写入 workspace，可能被 Agent 间接读取

缓解：

- 凭证只留在主进程环境
- 不写入 `workspace/.config`

## 19. Source Links

### Aliyun

- Fun-ASR 录音文件识别 REST API:
  - https://help.aliyun.com/zh/model-studio/fun-asr-recorded-speech-recognition-restful-api
- 录音文件识别概览:
  - https://help.aliyun.com/zh/model-studio/recording-file-recognition
- OSS 预签名 URL（Python）:
  - https://help.aliyun.com/zh/oss/developer-reference/python-download-using-a-presigned-url

### Feishu

- 获取消息中的资源文件:
  - https://feishu.apifox.cn/api-58352986
- 消息资源介绍:
  - https://apifox.com/apidoc/docs-site/532425/doc-1945316

## 20. Final Recommendation

推荐按以下原则实施：

1. 将语音识别视为 `Runner` 预处理能力，而不是 Agent 工具能力。
2. 一期使用 `OSS + Fun-ASR REST`，不做流式改造。
3. 保持现有队列与 session 语义稳定。
4. 先保守实现“转写 + 回答”，再在二期考虑更丰富的语音能力。

该方案与 EvoPaw 当前代码结构兼容性高，改动范围清晰，能以最低系统扰动把飞书语音消息接入现有 Bot 工作流。
