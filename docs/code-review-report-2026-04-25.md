# EvoPaw 代码 Review 报告

审查日期：`2026-04-25`
审查范围：`evopaw/` 主代码（不含 `evopaw/skills/` 上游 Skills，含本项目自研 Skills 的接入层）+ `tests/` 概览
**Review 过程中按用户要求忽略 `docs/` 目录。**

代码规模：核心 ~4966 LOC，单测+集成 12879 LOC，**测试/代码比 ≈ 2.6**（健康）。

---

## 0. 总评

EvoPaw 是一个**结构清晰、测试覆盖扎实**的 Claude Agent SDK 应用。架构层次分明（Listener → Runner → Agent → SkillLoader → Sub-Agent），关键边界（凭证隔离、session 隔离、cwd 隔离）都有明确的设计意图。615 单测 0 失败，可观测性（Prometheus + JSON 日志）齐全。

主要问题集中在三类：
1. **资源泄漏**：`SessionManager._jsonl_locks` 永久增长、`memory/indexer.py` 全局 LLM client 无关闭路径。
2. **配置过时/硬编码**：`_MODEL_CTX_LIMIT = 32000` 与现役 Sonnet 4.6 / Opus 4.7（≥200k 上下文）严重不匹配（**✅ 2026-04-25 已修复，见 C-1**）；`tick_interval=0.05` 偏激进；多处魔法字符串。
3. **风格/可读性**：runner._handle 单方法 130 行、send/send_text/send_thinking 重试逻辑三份近似副本、教学体长注释残留。

**没有发现严重的安全漏洞或数据丢失风险**。下文按"严重度分类"组织 findings。

---

## 严重度图例

- 🔴 **Critical**：可能造成数据丢失、安全暴露、服务中断
- 🟠 **Major**：明显的正确性/性能/资源问题，应当修
- 🟡 **Minor**：可读性/一致性/规范问题，影响维护成本
- 🟢 **Nit**：风格、注释、命名等小事项

---

## 1. 🔴 Critical Findings

### C-1. ✅ 已完成（2026-04-25，待 commit） — `_MODEL_CTX_LIMIT = 32000` 与现役模型严重不匹配
**位置**：`evopaw/memory/context_mgmt.py:41`

```python
# 修改前
_MODEL_CTX_LIMIT    = 32000
# 修改后
_MODEL_CTX_LIMIT    = 200000
```

**修复动作**：
- 把 `_MODEL_CTX_LIMIT` 从 `32000` 改为 `200000`，对齐 Claude Sonnet 4.6 / Haiku 4.5 现役上下文窗口。
- 同步更新注释，去掉"与 m3l19 演示代码保持一致"这一过时引用；写明当前阈值依据；并说明 Opus 4.7（1M）使用本默认值时仍按 200k 计算（90k token 触发摘要，对长对话仍有意义）。
- 压缩阈值（`_COMPRESS_THRESHOLD = 0.45`）和保留轮数（`_FRESH_KEEP_TURNS = 10`）保持不变——压缩逻辑本身正确，本次只修参数错配。
- `tests/unit/test_context_mgmt.py` 中所有用到的 `model_ctx_limit` 都是测试自带的显式参数（`32000`、`1000`、`100` 等），不依赖默认值，615 单测全部通过。

**未来改进**（不在本次范围）：
- 进一步参数化（按主 Agent `planner_model` 自动推断 ctx 上限），见 `docs/improved_agent/hermes-agent-improvement-plan.md` 已规划的 `model_ctx_limit` 配置项。

---

### 原始描述（保留供历史参考）

**位置**：`evopaw/memory/context_mgmt.py:39`

```python
_MODEL_CTX_LIMIT    = 32000
```

主 Agent 是 `claude-sonnet-4-6`（200k 上下文），子 Agent 是 `claude-haiku-4-5`（200k 上下文），项目甚至支持 `claude-opus-4-7`（1M 上下文）。但压缩阈值仍按 32000 token × 0.45 = 14400 token 触发。

**影响**：远早于必要时机就触发摘要压缩，浪费 LLM 调用、丢失对话细节、引入摘要质量风险。这是从 m3l19 课程演示代码继承下来的常量。

**建议**：把 `_MODEL_CTX_LIMIT` 改成参数化（按主 Agent model 推断），或至少调到 200000。对应阈值也要同步调整。压缩本身的逻辑是对的，参数错了。

---

## 2. 🟠 Major Findings

### M-1. ✅ 已完成（2026-04-25，待 commit） — `SessionManager._jsonl_locks` 永久增长（内存泄漏）

**修复动作**：
- `_jsonl_locks` 从 `dict` 改为 `OrderedDict`，加 `_jsonl_locks_max` 上限（默认 256，可注入）。
- 新增 `_acquire_jsonl_lock(session_id)`：命中时 `move_to_end` 刷新 LRU；未命中时创建并加末尾，超限时按 LRU 顺序踢出最旧的、**未被持有**的 entry（`lock.locked()` 检查）。
- **正在被持有的 Lock 永不踢出**——避免并发写入同一 session 的冲突；极端情况下全部被持有时短暂超限，下次再清理。
- `append` 改为调用 `_acquire_jsonl_lock` 取代 `setdefault`。
- `clear_all` 已存在的 `_jsonl_locks.clear()` 保持不变。

**单测**（`tests/unit/test_session_manager.py::TestJsonlLocksLRU`，5 case）：
- 大量 append 后 dict 不超过上限
- 同一 session 多次 append 复用同一 Lock 实例
- 被持有的 Lock 不被踢出（防并发冲突）
- LRU 踢出最旧的未持有 entry
- 复用刷新 LRU 顺序（最近使用的不被先踢）

---

### 原始描述（保留供历史参考）

**位置**：`evopaw/session/manager.py:27, 142`

```python
self._jsonl_locks: dict[str, asyncio.Lock] = {}
...
lock = self._jsonl_locks.setdefault(session_id, asyncio.Lock())
```

每个新 session 创建一个 `asyncio.Lock` 并加入 dict，**永不清理**。即使 session 通过 `/new` 切换或 cleanup 删除 jsonl 文件，dict 里的 Lock 仍然挂着。

**爆炸面**：长期运行的进程，每天若干用户每个 N 个 session，dict 单调增长。每个 Lock 实例本身很小（~200 字节），但**没有上界**。

**建议**：要么改 LRU（容量上限），要么在 `clear_all` / 切新 session 时清理对应 entry，要么改成 weakref。

### M-2. ✅ 已完成（2026-04-25，待 commit） — `clear_all` 不持有 jsonl 锁就清空 lock dict

**修复动作**：
- `evopaw/session/manager.py:clear_all` 添加详细 docstring，明确"使用前提"是调用方保证当前没有 worker 在 append（TestAPI 串行流程下安全）。
- 实际防护：原本无差别 `_jsonl_locks.clear()` 改为只清理**当前未被持有**（`lock.locked() == False`）的 entry。被持有的 lock 留给正在运行的 append 自行释放，避免产生孤儿 JSONL 文件。
- 单测新增 `TestClearAllProtection`：验证持有中的 lock 在 clear_all 后仍保留、未持有的被清理。

---

### 原始描述（保留供历史参考）

**位置**：`evopaw/session/manager.py:165-173`

```python
async def clear_all(self) -> None:
    async with self._index_lock:
        for f in self._sessions_dir.glob("s-*.jsonl"):
            f.unlink()
        self._write_index({})
        self._jsonl_locks.clear()
```

`clear_all` 只持有 `_index_lock`，没有等待正在运行的 `append` 完成。如果有 worker 在 `append` 路径上：
1. `append` 读到旧的 `_jsonl_locks[sid]` Lock
2. `append` 进入 `async with lock:`
3. `clear_all` 删除 jsonl 文件 + 清空 dict
4. `append` 写入磁盘 → 创建一个**孤儿 jsonl 文件**

虽然 `clear_all` 仅 TestAPI 调用，但仍是隐患。

**建议**：增加文档说明只能在静默期调用，或拒绝时返回错误，或等待所有 worker 落定。

### M-3. ✅ 已完成（2026-04-25，待 commit） — `memory/indexer.py` 全局 LLM client 无关闭路径
**位置**：`evopaw/memory/indexer.py:69-86`

**修复动作**：
- 在 `evopaw/memory/indexer.py` 新增 `shutdown_index_clients()`：遍历两个模块级 client（`_llm_client`、`_embed_client`），各自调用 `client.close()`（OpenAI Python SDK v1+ 提供同步 close 方法关闭 httpx 连接池），异常吞掉（避免阻塞进程退出），最后置 `None` 以支持热重载场景下的再次惰性创建。
- 在 `evopaw/main.py:async_main()` 把 `await asyncio.gather(*tasks)` 包入 `try/finally`，finally 中调用 `shutdown_index_clients()`。任何路径退出（CancelledError、任务异常、SIGTERM）都会触发关闭。
- `evopaw/main.py` 顶部 import `shutdown_index_clients`。

**单测**（`tests/unit/test_indexer.py::TestShutdownIndexClients`，5 case）：
- 两个 client 都实例化时，分别调用 `close()` 且置 None
- 两个都未实例化时不报错
- 仅一个实例化时，另一个不影响
- `client.close()` 抛异常时被静默吞掉，不向上传播
- shutdown 后 `_get_llm_client()` 能再次惰性创建

**未选 per-call 方案的原因**：
- 报告同时建议过 per-call 创建。本次选 shutdown 方案，因为单例复用 httpx 连接池在高频对话场景下仍有性能优势；shutdown hook 是最小改动（约 25 行），不动现有调用路径。

**测试结果**：全量 620 单测通过（新增 5）。

---

### 原始描述（保留供历史参考）

**位置**：`evopaw/memory/indexer.py:69-86`

```python
_llm_client   = None
_embed_client = None
```

模块级单例 + 惰性初始化。但进程关闭时**没有 `close()` 调用**，OpenAI client 内部的 httpx 连接池不会优雅关闭。容器优雅退出（SIGTERM）时可能在 stderr 留 warning。

**建议**：要么改成 per-call 创建（轻量，indexer 已经在 executor 里跑），要么提供 `shutdown_index_clients()` 在 `main` 退出时调用。

### M-4. ✅ 已完成（2026-04-25，待 commit） — 文件 IO 缺少显式 encoding

**修复动作**：5 处全部加 `encoding="utf-8"`：
- `evopaw/session/manager.py:101` `jsonl_path.read_text(encoding="utf-8")`
- `evopaw/session/manager.py:149` `open(jsonl_path, "a", encoding="utf-8")`
- `evopaw/session/manager.py:191` `self._index_path.read_text(encoding="utf-8")`
- `evopaw/session/manager.py:196-198` `tmp_path.write_text(..., encoding="utf-8")`
- `evopaw/session/manager.py:211` `open(jsonl_path, "w", encoding="utf-8")`

**为什么不补单测**：encoding 默认值的差异只在容器/系统 locale 异常时才显现，单元测试环境总是 UTF-8 default，无法稳定 reproduce 出 bug 路径。本质是防御性修改，相关 17 个 session_manager 单测继续通过即可。

---

### 原始描述（保留供历史参考）

**位置**：`evopaw/session/manager.py:97, 145, 187, 192, 207`

```python
for line in jsonl_path.read_text().strip().split("\n"):  # 97
with open(jsonl_path, "a") as f:                         # 145
return json.loads(self._index_path.read_text())          # 187
tmp_path.write_text(json.dumps(...))                     # 192
with open(jsonl_path, "w") as f:                         # 207
```

Python 3 在 Linux 上默认 UTF-8，但显式声明是社区强烈推荐的最佳实践——避免在容器 locale 异常时降级到 ASCII 或者用户改了系统 locale 时静默乱码。

**建议**：全部加 `encoding="utf-8"`（项目其它地方 `bootstrap.py`、`context_mgmt.py` 已经规范地使用了）。

### M-5. ✅ 已完成（2026-04-25，待 commit） — `cron/service.py` tick 间隔过密

**修复动作**：
- `evopaw/cron/service.py:41` 默认 `tick_interval` 从 `0.05` (50ms) 改为 `1.0` (1s)，注释说明"如需亚秒级精度请显式覆盖"。
- `tests/unit/test_cron_service.py` 12 处 `CronService(...)` 构造全部加 `tick_interval=0.05` 显式参数（测试场景需要亚秒级精度验证 `every_ms=100` 等用例）。

**收益**：生产容器从每秒 20 次轮询降到每秒 1 次，长期 CPU 节省可观；测试无回归（12 测试全过）。

---

### 原始描述（保留供历史参考）

**位置**：`evopaw/cron/service.py:41-42`

```python
def __init__(self, ..., tick_interval: float = 0.05) -> None:
```

50ms 一次轮询，每秒 20 次 tick。空 jobs 列表也照样轮询。Cron 任务普遍不需要亚秒级精度。

**影响**：CPU 持续低水位占用，长期运行的容器多一笔不必要的能耗。

**建议**：默认改 1.0s，注释说明"如需亚秒级请显式覆盖"。当前 `_loop` 每 tick 还做 mtime 检查 + ms 比较，纯 Python 调用栈也是开销。

### M-6. ✅ 已完成（2026-04-25，待 commit） — `feishu/listener.py:138` 全异常捕获后只 log

**修复动作**：
- `evopaw/feishu/listener.py` 顶部新增 `_on_dispatch_done(future)` 回调函数：从 `future.result()` 取出异常，记录 `errors_total{component="feishu_listener", error_type=<异常名>}` metric。
- `do_without_validation` 中两处 `asyncio.run_coroutine_threadsafe` 调用（dispatch InboundMessage、dispatch on_bot_added）都接 `fut.add_done_callback(_on_dispatch_done)`，不再静默丢弃 future。
- 顶层 `except Exception as exc` 同步追加 `record_error("feishu_listener", type(exc).__name__)`，事件解析阶段的异常也进 metric。

**单测**（`tests/unit/test_feishu_listener.py::TestHandlerExceptionInBody`，新增 3 case）：
- handler 同步异常时调用 `record_error`
- dispatched coroutine 失败时 done_callback 调用 `record_error`
- dispatched coroutine 正常完成时不调 `record_error`

---

### 原始描述（保留供历史参考）

**位置**：`evopaw/feishu/listener.py:53-139`

```python
def do_without_validation(self, payload: bytes) -> None:
    try:
        ...
    except Exception:
        logger.exception("Failed to handle im.message.receive_v1 websocket event")
```

整个 130 行函数包在一个 try except 里。任何字段解析错误、metric 调用错误、resolve_routing_key 异常、`asyncio.run_coroutine_threadsafe` 失败都被吞。`run_coroutine_threadsafe` 返回的 future **从未被检查**，dispatch 失败也无 metric。

**建议**：
- 保持 try/except（事件循环不应崩溃）但**记录到 errors_total metric**。
- `asyncio.run_coroutine_threadsafe` 返回的 future 应该 add_done_callback 检查异常。

### M-7. `runner._handle` 单方法 130 行，职责过载
**位置**：`evopaw/runner.py:246-377`

dedup → slash → session → 附件下载 → ASR → agent 调用 → 语音格式化 → 历史持久化 → 卡片更新 → 兜底发送，全在一个方法里。

**影响**：测试覆盖虽多，但每个分支都要搭一遍完整 mock 上下文。读代码时心智负担大。

**建议**：拆分至少三个：`_resolve_input`（dedup + slash + session + 附件/语音）、`_invoke_agent`、`_dispatch_reply`。本次不强制做，但下次进入这个文件改东西时顺手拆。

---

## 3. 🟡 Minor Findings

### m-1. ✅ 已完成（2026-04-25，待 commit） — `feishu/sender.py` 三处重试逻辑重复

**修复动作**：
- `evopaw/feishu/sender.py` 新增 `_send_with_retry(*, routing_key, msg_type, content, root_id, label)`：把 retry/backoff 主体抽成共享方法，按 routing_key 分流到 p2p / group / thread。
- `send`：现在只构建 interactive 卡片并调用 `_send_with_retry(label="send")`。
- `send_text`：现在只构建 text JSON 并调用 `_send_with_retry(label="send_text")`。
- 日志/错误信息通过 `label` 区分调用方，行为完全等价。

**测试**：51 个 sender 单测全过（基础 + 卡片版），未新增测试（行为不变只重构）。

---

### 原始描述（保留供历史参考）

**位置**：`evopaw/feishu/sender.py:33-65, 135-169` (`send` 和 `send_text`)

两个方法的 retry/backoff 主体代码几乎完全一样，只在 msg_type/content 构建上不同。

**建议**：抽 `_send_with_retry(routing_key, msg_type, content, root_id)` 共享。

### m-2. ✅ 已完成（2026-04-25，待 commit） — `cleanup/service.py` 在方法内重复 import json

**修复动作**：在 `evopaw/cleanup/service.py` 顶部 import block 加 `import json`，删除 `write_feishu_credentials` 和 `write_tavily_credentials` 内的两处方法内 import。16 个 cleanup 单测全过。

---

### 原始描述（保留供历史参考）

**位置**：`evopaw/cleanup/service.py:155, 179`

```python
def write_feishu_credentials(self, app_id, app_secret):
    import json  # 155

def write_tavily_credentials(self, api_key):
    import json  # 179
```

两次方法内 import。提到模块顶部即可。

### m-3. ✅ 已完成（2026-04-25，待 commit） — 教学体长注释残留

**修复动作**：清理 `evopaw/memory/` 三个文件的教学体注释，保留真正的"为什么"型 docstring：
- `bootstrap.py`：模块 docstring 从 13 行压缩为 5 行；删除 2 处"💡【第19课·...】"前缀的内嵌注释，改为简短中性语句。
- `context_mgmt.py`：模块 docstring 从 22 行压缩为 1 行；`prune_tool_results` / `chunk_by_tokens` / `maybe_compress` / `append_session_raw` 的 docstring 各删除 "💡 【第N课】" 前缀和过度教学化文字，保留本质 WHY（如 tool_call_id 链路完整性、雪崩效应）。
- `indexer.py`：模块 docstring 从 16 行压缩为 7 行；删除 5 处 "💡 核心点"/"💡【第21课】" 注释，保留语义性说明。

**整体收益**：`memory/` 三个文件累计删除约 60 行教学注释；可读性提升、信息密度提高。所有原有"为什么"型说明都保留，无信息丢失。

**测试**：73 个 memory 相关单测全过（context_mgmt + indexer + bootstrap）。

---

### 原始描述（保留供历史参考）

**位置**：
- `evopaw/memory/context_mgmt.py:1-21`（21 行的"三把剪刀"教学）
- `evopaw/memory/indexer.py:1-16`（11 行的课程引用）
- `evopaw/memory/bootstrap.py:1-12`（11 行）
- `evopaw/memory/context_mgmt.py:117-126`（"💡【第19课·三把剪刀】"内嵌教学注释）

CLAUDE.md 的写代码原则是"Default to writing no comments. Only add one when the WHY is non-obvious"。这些注释本质是开发者把课程笔记搬进了源码，对维护者没价值。

**建议**：删除"💡 第N课"风格的教学注释，保留真正的"为什么"型 docstring。

### m-4. `tools/skill_loader.py` 占位符替换写法重复
**位置**：`evopaw/tools/skill_loader.py:150-153`

```python
stripped = stripped.replace("{skill_base}", _skill_base)
stripped = stripped.replace("{_skill_base}", _skill_base)
stripped = stripped.replace("{session_id}", session_id or "<session_id>")
stripped = stripped.replace("{session_dir}", _session_dir)
```

四个 replace 单独调用。可改 dict + 循环。**注意**：我已验证 `{skill_base}` 不是 `{_skill_base}` 的子串（前者第二字符是 `s`，后者是 `_`），所以 replace 顺序无 bug。

### m-5. `_DEFAULT_MAX_HISTORY_TURNS = 20` 重复定义
**位置**：
- `evopaw/agents/main_agent.py:40`
- `evopaw/main.py:148` `cfg.get("session", {}).get("max_history_turns", 20)`

两处独立的 `20`。改一处忘改另一处会出现配置漂移。

**建议**：在 `agents/main_agent.py` 导出常量，main.py 取 `_DEFAULT_MAX_HISTORY_TURNS` 作 fallback。

### m-6. `agents/main_agent.py:122-128` system_prompt 拼接散落
`build_bootstrap_prompt` 已经构建了主 system prompt，但 main_agent 又在调用方追加 `<tool_constraint>` 段。下次有约束要加，会再追加一段，逐渐失控。

**建议**：把 tool_constraint 移入 bootstrap.py 或 build_main_agent_options，集中管理。

### m-7. ✅ 已完成（2026-04-25，待 commit） — 模块级常量硬编码 LLM 模型名

**修复动作**：
- `evopaw/memory/indexer.py`：`_EMBED_MODEL` / `_EMBED_DIM` / `_EXTRACT_MODEL` 改为从环境变量读取 fallback：
  - `EVOPAW_MEMORY_EMBED_MODEL`（默认 `text-embedding-v3`）
  - `EVOPAW_MEMORY_EMBED_DIM`（默认 `1024`）
  - `EVOPAW_MEMORY_EXTRACT_MODEL`（默认 `qwen3-max`）
- `evopaw/memory/context_mgmt.py`：新增 `_SUMMARY_MODEL` 模块常量从 `EVOPAW_MEMORY_SUMMARY_MODEL` 读取（默认 `qwen3-turbo`），`_summarize_chunk` 用此常量代替原硬编码字面值。
- `tests/unit/test_indexer.py:test_uses_correct_model_and_dim` 改为读模块常量而非硬编码字面值，避免运维设置 env 后测试反而失败。
- 新增 `TestModelEnvOverride` 3 case：默认值、env 覆盖 indexer 模型、env 覆盖 summary 模型。

**为什么用 env 而非 config.yaml**：环境变量 fallback 与现有 `_QWEN_API_KEY` 风格一致、最小改动、不破坏现有调用接口。如果未来需要从 config.yaml 注入，可在 main.py 启动时读 config 后 `os.environ.setdefault(...)`，仍然兼容。

---

### 原始描述（保留供历史参考）

**位置**：
- `evopaw/memory/context_mgmt.py:147` `model="qwen3-turbo"`
- `evopaw/memory/indexer.py:33` `_EXTRACT_MODEL = "qwen3-max"`
- `evopaw/memory/indexer.py:31-32` `_EMBED_MODEL`、`_EMBED_DIM`

记忆系统的 LLM 模型名硬编码在源码里，没有通过 config.yaml 暴露。换模型需要改代码。

**建议**：搬到 config.yaml `memory.summary_model` / `memory.extract_model` / `memory.embed_model`。

### m-8. `Runner.__init__` 14 个参数
**位置**：`evopaw/runner.py:136-152`

构造函数接收 14 个参数，其中 9 个是语音相关。signature 长度难维护。

**建议**：把语音相关参数封装到 `VoiceConfig` dataclass。

### m-9. `_ATTACHMENT_PATH_RE` 与 Runner 构造的消息字符串紧耦合
**位置**：
- `evopaw/tools/add_image_tool_local.py:37-39`（提取 regex）
- `evopaw/runner.py:53-60` (`_build_attachment_message`)
- `evopaw/runner.py:63-86` (`_build_voice_message`)
- `evopaw/api/test_server.py:175-179` (`_copy_attachment` 同样格式)

四处独立构造"沙盒路径提示"格式。regex 在一处、构造代码在三处。任一处改格式（如换引号、换段落），其它处会悄悄漂移。

**建议**：抽公共函数 `format_sandbox_path_hint(path, original_text)` 集中。

### m-10. `runner.py:38` AgentFn 用位置参数 Callable
```python
AgentFn = Callable[[str, list[MessageEntry], str, str, str, bool], Awaitable[str]]
```

6 个位置参数，类型说明全靠下方注释。换成 Protocol 会自描述。

### m-11. `memory/indexer.py:213-214` turn_id 生成易碰撞
```python
raw_id  = f"{session_id}_{turn_ts}_{user_message[:32]}"
turn_id = hashlib.sha256(raw_id.encode()).hexdigest()[:16]
```

只取 sha256 前 16 字符（64 bits）。同一 session 同一毫秒同一前 32 字符的输入会被认为同 id，被 ON CONFLICT DO NOTHING 抑制。但实际中两条紧邻消息有不同的 user_message 会落在不同 turn_ts，碰撞概率低。**建议**：注释里说明这是有意的去重设计（不是 bug），并把 turn_id 截断长度提到 24 或保留全长降低误抑制。

---

## 4. 🟢 Nit Findings

### n-1. `evopaw/api/__init__.py`、`evopaw/cleanup/__init__.py` 等多个 `__init__.py` 是空文件
没问题，但 `evopaw/__init__.py` 也是空——若想用 `from evopaw import __version__` 等公共信息会无处可放。

### n-2. `feishu/listener.py:74` 入群事件 chat_type 写死字符串 `"group"`
```python
if not self._is_chat_allowed(chat_id, "group"):
```
不是 enum/常量。`_is_chat_allowed` 内部用 `chat_type == "p2p"` 判断，硬编码字符串散落。

### n-3. `agents/skill_agent.py:30` `model: str = DEFAULT_SUB_AGENT_MODEL`
默认值在 `claude_client` 模块；本模块多处 import `claude_client.DEFAULT_SUB_AGENT_MODEL`。OK，但 `agents/skill_agent.py` 也可以不接 default（永远从 build_skill_loader_server 透传），减少配置点。

### n-4. ✅ 已完成（2026-04-25，待 commit） — logger 没有结构化字段

**修复动作**：在关键执行路径上为 logger 调用补 `extra={...}` 字段，让 JSON 日志可按 `routing_key` / `session_id` / `feishu_msg_id` 结构化查询：

- `evopaw/runner.py`：worker 异常路径（line 232）、handle 子方法（dedup、ASR 失败、agent_after_asr_failed）共 4 处补 extra 字段。
- `evopaw/agents/main_agent.py`：agent_fn 入口 INFO log 补 routing_key + session_id；CLI 错误 / unexpected error 异常路径补字段。

**新增测试**：`tests/unit/test_logging_config.py::TestJsonFormatterExtraFields`（6 case）：
- 无 extra 时只输出基础字段
- routing_key / session_id / feishu_msg_id 各自单独传入
- 三字段同时传入
- 非白名单字段不进 JSON（避免噪音）

**未做的部分**：仅覆盖最高频/最关键的执行路径（runner._handle、agent_fn）。其它低频 log（cron / cleanup / asr 等）未补，等下次进入相关文件时顺手补即可，不强制。

---

### 原始描述（保留供历史参考）

`observability/logging_config.py:JsonFormatter` 支持 `routing_key / session_id / feishu_msg_id` 字段，但代码里 `logger.info(...)` 几乎都是字符串拼接，没用 `extra={"routing_key": ...}`。结果是 JSON 日志只有 `msg`，不可结构化查询。

### n-5. `tools/add_image_tool_local.py:19` 模块级 `_WORKSPACE_ROOT`
```python
_WORKSPACE_ROOT = (Path(__file__).parent.parent.parent / "data" / "workspace").resolve()
```
默认值假设代码在 `evopaw/tools/` 下三层=项目根。容器内 `evopaw/` 实际在 `/app/evopaw/`，三层上去是 `/`——`/data/workspace` 不存在！但实际调用都通过 `workspace_root=workspace_dir` 注入，default 值只在没传时才用。OK 但是个隐藏陷阱。

### n-6. `Dockerfile:14` `npm install -g @anthropic-ai/claude-code` 不带版本号
每次 build 拉最新版，不利于复现。

### n-7. `cleanup/service.py:155-170` write_feishu_credentials 的 `mkdir(parents=True, exist_ok=True)` 后才 `chmod(0o700)`
如果目录已存在但权限是 0o755，第二次启动 chmod 会改回 0o700。OK，但若多个进程并发启动时 chmod 期间另一个进程读，会有微妙窗口。生产单进程，无影响。

### n-8. `agents/skill_agent.py:69-70` 异常文案一样
```python
return f"⚠️ Skill '{skill_name}' 执行失败：{exc}"
return f"⚠️ Skill '{skill_name}' 发生内部错误，请稍后重试。"
```
分别对应 SDK error 和 unexpected error。两段文案区分度不够（"执行失败" vs "内部错误"），用户视角差异不大。可统一。

### n-9. `runner.py` 缺少对 `is_cron` 的 metric
```python
if not inbound.is_cron and self._is_duplicate_msg(inbound.msg_id):
```
cron 触发的消息绕过 dedup，但是没有专门的 metric 区分"用户触发"vs"cron 触发"，在 inbound_messages_total 里看不出来。

### n-10. `feishu/sender.py:30-31` `retry_backoff: tuple[int, ...] = (1, 2, 4)`
dataclass field with mutable default 是 bug 模式，但 tuple 是 immutable 所以 OK——只是看上去吓人，可加注释。

---

## 5. 横切面观察

### 5.1 安全
- ✅ 凭证不进入 LLM context（`cleanup_svc.write_feishu_credentials` 写到 `.config/feishu.json` mode 0600）
- ✅ 路径遍历保护：`add_image_tool_local.py:78` `if not str(path).startswith(str(workspace_root))`
- ✅ skill_loader path traversal 保护（`tools/skill_loader.py:80`）
- ⚠️ `bypassPermissions` mode：主 Agent + Sub-Agent 都用 `permission_mode="bypassPermissions"`（`llm/claude_client.py:59,85`）。这意味着 Claude Code CLI 的所有权限提示都被绕过——结合 Sub-Agent 的 `Bash` 工具，**Sub-Agent 可以执行任意 Bash 命令**。这是 Skill 系统能工作的前提（无需用户确认每个 `python xxx.py`），但意味着 prompt injection 可以让 Sub-Agent 执行 `rm -rf` 之类。
  - 当前防护：cwd 限定到 `/workspace`，skill 资源在 `/mnt/skills:ro`。但 `/workspace` 内的文件可被破坏。
  - **建议**：在文档明确这是接受的风险（Bot 只对 allowed_chats 开放，等价于内部工具），并考虑在 Sub-Agent 入口加一个 prompt-injection 检测层（针对来自外部的 user content）。

### 5.2 并发与资源
- ✅ `SessionManager` 读写有 lock（`_index_lock` + per-session `_jsonl_locks`）
- ✅ `Runner` 用 per-routing_key Queue + worker，串行单 session、并发不同 session
- ✅ `CleanupService` 用 `asyncio.Lock` 防止并发 sweep
- ✅ `FunASRRealtimeClient` 在 `finally` 关闭 ws + session
- ⚠️ 见 M-1, M-2, M-3 的资源泄漏

### 5.3 错误处理
- ✅ Slash command 不进 Agent 的设计避免错误传播
- ✅ `agent_fn` 包了 SDK 异常（CLINotFoundError 等）→ 返回友好文案
- ✅ ASR 失败有详细的 `AsrFailure.reason` 七分类
- ⚠️ 见 M-6 的 listener 全异常吞噬

### 5.4 可观测性
- ✅ Prometheus metric 覆盖：events、inbound、workers、queue、HTTP、errors、ASR、audio
- ✅ JSON 日志 + 控制台双格式（RotatingFileHandler 50MB × 5）
- ⚠️ 见 n-4 日志缺结构化字段

### 5.5 测试
- ✅ 615 单测 + 多个集成测试，按 marker 分级（`llm` / `not llm`）
- ✅ `tests/integration/TEST_CASES.md` 文档化测试场景
- ✅ legacy 测试归档到 `tests/archive/legacy_crewai/`
- ⚠️ 横向拆分（基础版/卡片版）已在 redundancy-audit F9 中标注，暂缓

### 5.6 配置一致性
- ⚠️ 见 m-5 (max_history_turns 重复)、m-7 (LLM 模型硬编码)、C-1 (ctx_limit 过时)

---

## 6. 模块级总结

| 模块 | LOC | 评分 | 主要问题 |
|---|---:|:-:|---|
| `main.py` | 324 | A | 启动逻辑清晰；asr 配置散在 `_make_runner` 调用里（建议封装） |
| `runner.py` | 503 | B | M-7 单方法过长；m-8 init 参数过多 |
| `agents/main_agent.py` | 254 | A | m-6 system_prompt 拼接散落 |
| `agents/skill_agent.py` | 75 | A | n-8 异常文案区分度低 |
| `agents/hooks.py` | 53 | A | 简洁；可在 hook 里把 `block.input.skill_name` 也推送到 verbose |
| `tools/skill_loader.py` | 312 | B+ | m-4 占位符 replace 重复；闭包内 cache 没上限（query 内存活，可接受） |
| `tools/add_image_tool_local.py` | 126 | A | n-5 模块级默认值有陷阱 |
| `feishu/listener.py` | 310 | B | M-6 全异常吞噬 + future 不检查 |
| `feishu/sender.py` | 276 | B+ | m-1 重试逻辑重复 |
| `feishu/downloader.py` | 79 | A | 简洁，错误处理完整 |
| `feishu/session_key.py` | 23 | A+ | 函数式、零依赖、易测试 |
| `session/manager.py` | 235 | C+ | M-1, M-2, M-4 集中出现，是本次 review 最弱模块 |
| `memory/bootstrap.py` | 61 | A | 容错好；memory.md 200 行截断合理 |
| `memory/context_mgmt.py` | 252 | C | C-1 过时阈值；m-3 教学体注释；m-7 模型硬编码 |
| `memory/indexer.py` | 247 | B | M-3 client 不关闭；m-7 模型硬编码；m-11 turn_id 长度 |
| `cron/service.py` | 285 | B+ | M-5 tick 过密；JSON 写入按钮触发频率高（每次 fire 都 save） |
| `cron/models.py` | 49 | A+ | 数据类清晰 |
| `cleanup/service.py` | 197 | A- | m-2 inline import；其它好 |
| `asr/funasr_realtime_client.py` | 387 | A | 错误分类细致；writer/reader 协程协作清晰；finally 资源释放规范 |
| `asr/service.py` | 106 | A | 简洁；metric 覆盖完整 |
| `api/test_server.py` | 179 | A | 改完 #8 B 后 skills_called 接通 |
| `api/capture_sender.py` | 84 | A | 改完 #8 B 后含 record_skills/pop_skills |
| `observability/metrics.py` | 174 | A+ | 覆盖完整、命名规范 |
| `observability/logging_config.py` | 71 | A- | n-4 结构化字段未广泛使用 |
| `llm/claude_client.py` | 86 | A | 简洁；bypassPermissions 见 5.1 安全 |

总评分布：A 类 14、B 类 7、C 类 2（session/manager.py、memory/context_mgmt.py）。

---

## 7. 优先修复清单（按 ROI 排序）

| 优先级 | ID | 工作量 | 预期收益 | 状态 |
|---|---|---|---|---|
| P0 | C-1 | 5 行改动 | 立即生效，避免错误压缩，保留对话细节 | ✅ 完成（待 commit） |
| P0 | M-3 | 30 行 | 优雅退出 + 减少 stderr warning | ✅ 完成（待 commit） |
| P1 | M-1 | 20-50 行 | 长期内存稳定性 | ✅ 完成（待 commit） |
| P1 | M-4 | 5 处加 `encoding="utf-8"` | 容器 locale 鲁棒性 | ✅ 完成（待 commit） |
| P1 | M-6 | 10 行 | 正确的失败可见性 | ✅ 完成（待 commit） |
| P2 | C-1 之后再调 `_FRESH_KEEP_TURNS` 等参数 | 视情况 | 配套优化 | ⏭️ 评估后无需改动 |
| P2 | M-5 tick_interval | 1 行 | 长期 CPU 节省 | ✅ 完成（待 commit） |
| P2 | m-1 重试抽公共 | 20 行 | 改一处生效全发送路径 | ✅ 完成（待 commit） |
| P2 | m-7 LLM 模型可配置 | 30 行 | 换记忆系统 LLM 不需改源码 | ✅ 完成（待 commit） |
| P3 | m-3 删教学注释 | 删 50 行 | 可读性 | ✅ 完成（待 commit） |
| P3 | n-4 logger.extra 结构化字段 | 10-20 行 | 日志可查询性 | ✅ 完成（待 commit） |
| P3 | M-2 clear_all 文档/防护 | 10 行 | 测试场景边角 | ✅ 完成（待 commit） |
| P3 | m-2 inline import 提顶部 | 4 行 | 微小 | ✅ 完成（待 commit） |

**强烈不建议混做**：M-7 (拆 _handle) 和 m-1 (重试抽取)。两者都是结构改动，应单独 commit。

---

## 8. 不建议改动

按 redundancy-audit 同样的态度：

- **`feishu/sender.py` 三个发送方法的存在**（send / send_text / send_thinking）：职责确实不同（卡片 vs 纯文本 vs Loading 卡片），不应合并。但 retry 主体可抽公共（m-1）。
- **`api/CaptureSender` 与 `FeishuSender`**：职责清晰，不要合并。
- **教学风格的 `<context_summary>` XML 标签**：虽然像演示代码风格，但实际作为 LLM-readable 标记是有用的，保留。
- **Runner 的 per-routing_key 队列设计**：正确实现了"同 session 串行、跨 session 并行"。复杂度合理，不要为了简化合一个全局队列。
- **`tools/skill_loader.py` 渐进式披露**：阶段一 description XML、阶段二 SKILL.md 加载，是正确的 token 优化策略，不要图简单合并成一阶段。

---

## 9. 测试相关补充

不建议立刻动，但记下来供后续重构参考：

- **缺乏混沌/压力测试**：高并发同 routing_key 的 message 涌入、长时间 cron 漂移、pgvector 连不上时的批量失败。
- **`TestSkillsCalled`（本次新增）只 mock 了 SDK message stream**：没有跑真实 SDK 的 e2e，但这是合理取舍（e2e 在 `tests/integration/test_e2e_conversation.py`）。
- **`tests/unit/test_runner_card.py` 与 `test_runner.py`**：redundancy-audit F9 已记录，暂缓合并。

---

## 10. 与 redundancy-audit-2026-04-21 的关系

本次 review 与 redundancy-audit 的区别：
- **redundancy-audit** 关注"代码冗余/重复"——可以删什么。
- **本报告** 关注"代码质量/正确性/可维护性"——应该改什么。

两份报告的 finding 不重叠（除了 m-3 教学注释与之前发现的"演示代码痕迹"沾边）。redundancy-audit 已完成的项目（F1-F9 + #8 A/B + F3）不在本报告重复。

---

## 附录 A. 评分维度说明

- **A+/A**：模块级最佳实践，无明显问题
- **A-**：1 处 minor，整体优秀
- **B+/B**：2-3 处 minor 或 1 处 major，整体良好
- **C+/C**：累积多个问题，建议优先修复

评分仅在本项目内部相对意义——所有模块都是可以工作的。

---

## 附录 B. Review 范围确认

- 包含：`evopaw/` 下所有 Python 模块（不含 `evopaw/skills/` Anthropic 上游 Skills）、`tests/` 结构概览、`Dockerfile`、`docker-compose.yaml`。
- 排除：`docs/` 目录全部（按用户要求）、`evopaw/skills/{docx,pptx,xlsx,pdf,...}/` 上游 Skill 内部实现（属于 Anthropic 维护，本项目仅做接入）、`evopaw/skills/_shared/office/` 共享资源（同上）。
- 接入层（自研 Skills）：本次未深入审查 `evopaw/skills/{feishu_ops,scheduler_mgr,tavily_search,arxiv_search,memory-save,search_memory,memory-governance,daily-summary,investment-*}/` 内部脚本——这些是任务型 Sub-Agent 调用的目标，需要独立一轮 review，不在本次范围。
