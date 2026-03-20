> 本文档是 [DESIGN.md](../DESIGN.md) §13 的详细内容
> 最后更新：2026-03-10

## 13. 可观测性设计（Logging & Metrics）

### 13.1 日志规范

**目标**：统一 XiaoPaw 全链路的日志格式，方便本地排查 & 接入集中日志系统。

#### 13.1.1 日志后端

- 使用 Python 标准库 `logging`，在 `main.py` 中统一调用 `setup_logging()` 初始化：
  - **控制台**：人类可读格式（`[时间] [级别] [模块] 消息`），便于开发调试时直接查看
  - **文件**：结构化 JSON 行日志，写入 `data/logs/xiaopaw.log`，便于分析与收集
- 日志文件位置：`data/logs/xiaopaw.log`（滚动日志，单文件 50MB，保留 5 个）
- Root logger 默认级别：`INFO`（`setup_logging()` 显式设置，避免 Python 默认 WARNING 级别导致日志丢失）
- 避免重复 handler：`setup_logging()` 检测 handler 类型，多次调用安全（如测试环境）

**调试选项**：

| 环境变量 | 说明 |
|---------|------|
| `QWEN_DEBUG_PAYLOAD=1` | 将 LLM 请求完整 payload 以 INFO 级别写入日志（默认关闭，避免日志过大） |

#### 13.1.2 JSON 行日志格式

标准字段（示例）：

```json
{
  "ts": "2026-03-05T15:20:00.123Z",
  "level": "INFO",
  "logger": "xiaopaw.runner",
  "msg": "处理完成",
  "routing_key": "p2p:ou_xxx",
  "session_id": "s-abc123",
  "feishu_msg_id": "om_xxx",
  "event_type": "im.message.receive_v1",
  "chat_type": "p2p",
  "chat_id": "oc_yyy",
  "thread_id": null,
  "sender_open_id": "ou_xxx",
  "api_path": "/api/test/message",
  "http_method": "POST",
  "status_code": 200,
  "skill_name": null,
  "is_sub_crew": false,
  "error_type": null,
  "error_message": null
}
```

关键上下文字段分类：

| 维度 | 字段 |
|------|------|
| 消息维度 | `routing_key`、`session_id`、`feishu_msg_id` |
| 飞书维度 | `event_type`、`chat_type`、`chat_id`、`thread_id`、`sender_open_id` |
| HTTP 维度（TestAPI）| `api_path`、`http_method`、`status_code` |
| Agent 维度 | `skill_name`、`is_sub_crew` |
| 错误维度 | `error_type`、`error_message`（必要时附上简化 stack） |

#### 13.1.3 模块级日志约定

**FeishuListener**（`xiaopaw.feishu.listener`）：
- 成功连接/重连：`level=INFO`，记录连接地址、`conn_id`
- 处理 `im.message.receive_v1`：记录 `event_type`、`chat_type`、`chat_id`、`thread_id`、`sender_open_id`
- 解析失败：`level=ERROR`，带简化 payload 片段

**Runner**（`xiaopaw.runner`）：
- `dispatch` 入口：记录 `routing_key`、`feishu_msg_id`，标记是否 slash 命令
- `_handle`：调用 Agent 前后记录耗时、history 长度、session_id
- 出现异常时：记录 `error_type`、`error_message`、`routing_key`、`session_id`、`feishu_msg_id`

**SessionManager**（`xiaopaw.session.manager`）：
- `create_new_session`：记录 `routing_key` → `session_id` 映射
- `append`：`DEBUG` 级别，记录 `session_id`、新增 `message_count`

**CronService**（`xiaopaw.cron.service`）：
- Job 触发/失败：记录 `job_id`、`schedule.kind`、`routing_key`、`next_run_at_ms`、`last_status`

**TestAPI**（`xiaopaw.api.test_server`）：
- 每个 HTTP 请求：记录 `api_path`、`http_method`、`status_code`、`duration_ms`、`routing_key`、`session_id`

---

### 13.2 Metrics（Prometheus）

**目标**：提供 Prometheus 友好的 `/metrics` 接口，覆盖事件流量 / HTTP 请求 / Session 数量 / Runner 并发 / Agent 执行等指标。

#### 13.2.1 技术选型

- 使用 `prometheus_client`，在主进程注册所有指标
- 实现方式：方案 B（推荐）— 复用 TestAPI 的 `aiohttp` app，将 `/metrics` 一并挂载，端口通过 `config.yaml.debug` 控制

#### 13.2.2 指标列表

**1）飞书事件与消息流量**

| 指标名 | 类型 | Labels | 埋点位置 |
|-------|------|--------|---------|
| `xiaopaw_feishu_events_total` | Counter | `event_type`, `chat_type` | FeishuListener 解析事件时 |
| `xiaopaw_inbound_messages_total` | Counter | `routing_key_type`, `has_attachment` | Runner.dispatch 收到 InboundMessage 时 |

**2）HTTP API**

| 指标名 | 类型 | Labels | 埋点位置 |
|-------|------|--------|---------|
| `xiaopaw_http_requests_total` | Counter | `path`, `method`, `status_code` | TestAPI 请求处理逻辑 |
| `xiaopaw_http_request_duration_seconds` | Histogram | `path`, `method` | time.monotonic() 测量请求耗时 |

**3）Session 与 Runner 并发**

| 指标名 | 类型 | Labels | 埋点位置 |
|-------|------|--------|---------|
| `xiaopaw_sessions_active` | Gauge | `routing_key_type` | SessionManager.create_new_session / 清理逻辑 |
| `xiaopaw_runner_workers_active` | Gauge | `routing_key_type` | Runner._worker 创建/退出时 inc()/dec() |
| `xiaopaw_runner_queue_size` | Gauge | `routing_key_type` | dispatch / _worker 中根据 queue.qsize() 更新 |

**4）Agent / Sub-Crew（预留）**

| 指标名 | 类型 | Labels | 埋点位置 |
|-------|------|--------|---------|
| `xiaopaw_agent_requests_total` | Counter | `kind`（main/sub_crew）, `skill_name` | main_crew / skill_crew 启动前 |
| `xiaopaw_agent_duration_seconds` | Histogram | `kind`, `skill_name` | Agent 执行结束后记录耗时 |
| `xiaopaw_sub_crews_running` | Gauge | `skill_name` | Sub-Crew 启动/结束时 inc()/dec() |

**5）错误与异常**

| 指标名 | 类型 | Labels | 说明 |
|-------|------|--------|------|
| `xiaopaw_errors_total` | Counter | `component`, `error_type` | 各模块捕获异常时 inc()，与日志中 error_type 对应 |

component 取值：`feishu_listener` / `feishu_sender` / `runner` / `cron` / `session_mgr` / `test_api` / `agent`

---

### 13.3 /metrics 接口

```
GET /metrics        # Prometheus 拉取指标
```

- 响应内容遵循 Prometheus 文本暴露格式，由 `prometheus_client.generate_latest()` 生成
- 监听地址与端口：默认 `127.0.0.1:9100`（仅本地采集），可通过 `config.yaml.debug` 字段调整

---

### 13.4 与 Trace / Session 的联动

**Session 维度**：`session_id` 在日志中统一出现，可与以下文件交叉排查：
- `data/sessions/{sid}.jsonl`（对话历史）
- `data/traces/{sid}/{ts}_{msg_id}/`（Trace）

**消息维度**：`feishu_msg_id` 同时出现在：
- 飞书原始消息
- XiaoPaw 日志
- Trace `meta.json` 中的 `feishu_msg_id`

通过 `feishu_msg_id` 可以将「飞书消息 → Runner 日志 → Agent Trace → 回复内容」完整串联排查。
