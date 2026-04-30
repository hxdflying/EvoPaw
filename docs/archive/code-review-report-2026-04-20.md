# EvoPaw 项目系统性审查报告

**审查日期**: 2026-04-20
**项目版本**: main 分支 (commit 4263b2c)
**审查范围**: 全量代码、架构、安全、测试、部署

---

## 目录

1. [执行摘要](#1-执行摘要)
2. [架构审查](#2-架构审查)
3. [安全审计](#3-安全审计)
4. [代码质量分析](#4-代码质量分析)
5. [测试覆盖评估](#5-测试覆盖评估)
6. [Skills 系统完整性](#6-skills-系统完整性)
7. [并发与性能](#7-并发与性能)
8. [部署与配置](#8-部署与配置)
9. [优先级修复清单](#9-优先级修复清单)

---

## 1. 执行摘要

### 项目概况

| 指标 | 数值 |
|------|------|
| 核心 Python 文件 | 37 个 |
| 总代码行数 | ~3,944 行 |
| 单元测试数 | 496 个（全部通过） |
| 测试覆盖率 | 22 模块 100%，8 模块 80-99% |
| 内置 Skills | 18 个（13 个完整，5 个仅声明） |
| 迁移状态 | CrewAI → Claude Agent SDK 100% 完成 |

### 总体评价

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构设计 | ★★★★☆ | 分层清晰，MCP 渐进式披露设计优秀，部分模块需拆分 |
| 安全性 | ★★★☆☆ | 架构安全（凭证隔离、路径防护），但存在凭证泄露风险 |
| 代码质量 | ★★★★☆ | 风格一致，类型标注良好，异常处理可改进 |
| 测试覆盖 | ★★★★★ | 496 测试全通过，核心模块 100% 覆盖 |
| 并发安全 | ★★★★★ | Lock + 原子写 + fsync，设计到位 |
| 可维护性 | ★★★★☆ | 模块化好，但 skill_loader.py 需拆分 |

---

## 2. 架构审查

### 2.1 整体架构

```
Feishu WebSocket → FeishuListener → Runner (per-routing_key queue)
     → Main Agent (Claude Sonnet) → SkillLoaderTool (MCP)
       → Sub-Agent (Claude Haiku) → FeishuSender → 用户
```

**优势**:
- 消息流清晰，单向数据流
- Per-routing_key 队列实现同 session 串行、跨 session 并行
- Sub-Agent 短生命周期，防止状态污染
- MCP 渐进式披露——Main Agent 只需一个工具入口

**关注点**:

| 编号 | 问题 | 位置 | 严重度 |
|------|------|------|--------|
| A-1 | `skill_loader.py` (438 行) 承担 5 项职责：Registry 构建、XML 描述生成、指令加载、history 内联、MCP 工厂 | `evopaw/tools/skill_loader.py` | Major |
| A-2 | `runner.py` 的 `_handle()` 方法包含 8 个逻辑步骤，未拆分子方法 | `evopaw/runner.py:149-204` | Major |
| A-3 | `on_bot_added` 回调始终为 `None`，入群欢迎功能缺失 | `evopaw/main.py:184` | Minor |
| A-4 | TestAPI 的 `skills_called` 字段始终为空列表，Skill 调用链路不可追踪 | `evopaw/api/test_server.py:124` | Minor |

**建议重构 `skill_loader.py`**:
```
evopaw/tools/
├── skill_loader/
│   ├── __init__.py       # MCP server 工厂（build_skill_loader_server）
│   ├── registry.py       # _build_skill_registry
│   ├── descriptor.py     # _build_description_xml
│   ├── loader.py         # _get_skill_instructions + 路径替换
│   └── history.py        # _handle_history_reader 内联
```

### 2.2 三层记忆架构

| 层级 | 存储 | 读取时机 | 写入时机 |
|------|------|---------|---------|
| L1 Bootstrap | `soul/user/agent/memory.md` | Agent 启动时注入 system_prompt | Skill 脚本手动编辑 |
| L2 Context | `ctx.json` + `raw.jsonl` | 每轮对话前加载 | 每轮对话后保存 |
| L3 Vector | pgvector | `search_memory` Skill 搜索 | `asyncio.create_task` 异步索引 |

**评价**: 三层设计合理——L1 稳定、L2 实时、L3 语义。异步索引不阻塞主流程是正确的决策。

### 2.3 数据流完整性

```
InboundMessage
  → Runner.dispatch() [入队]
  → Runner._worker() [串行消费]
  → Runner._handle()
      ├─ _handle_slash() [快速路径：/new /verbose /help /status]
      ├─ SessionManager.get_or_create() [并发安全]
      ├─ FeishuDownloader.download() [附件下载]
      ├─ SessionManager.load_history() [JSONL 读取]
      ├─ FeishuSender.send_thinking() [Loading 卡片]
      ├─ agent_fn() [Agent 执行]
      │   ├─ build_bootstrap_prompt() [L1]
      │   ├─ load_session_ctx() [L2]
      │   ├─ build_skill_loader_server() [MCP]
      │   ├─ query() [Claude Agent SDK]
      │   ├─ save_session_ctx() [L2 持久化]
      │   └─ async_index_turn() [L3 异步]
      ├─ SessionManager.append() [历史持久化]
      └─ FeishuSender.update_card() [替换 Loading 卡片]
```

---

## 3. 安全审计

### 3.1 🔴 P0 — 凭证泄露风险

#### 3.1.1 `.env` 文件包含真实凭证

**位置**: `/home/hxd/agent_project/evopaw/.env`

| 行号 | 凭证类型 | 状态 |
|------|---------|------|
| 5-6 | 飞书 APP_ID / APP_SECRET | ⚠ 真实值 |
| 9 | ANTHROPIC_API_KEY (`sk-ant-api03-...`) | ⚠ 真实值 |
| 12 | TAVILY_API_KEY | ⚠ 真实值 |
| 15 | PostgreSQL DSN（含密码 `evopaw123`） | ⚠ 真实值 |

**风险**: 若已提交到 git 历史，凭证可能永久暴露。
**处置**: 立即轮换所有凭证，确认 `.env` 在 `.gitignore` 中（已确认在）。

#### 3.1.2 默认密码硬编码

多处使用默认密码 `evopaw123`：

| 文件 | 行号 | 内容 |
|------|------|------|
| `config.yaml.template` | 39 | `${MEMORY_DB_DSN:-postgresql://evopaw:evopaw123@...}` |
| `docker-compose.yaml` | 25 | `POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-evopaw123}` |
| `pgvector-docker-compose.yaml` | 10 | 同上 |
| `evopaw/skills/search_memory/scripts/search.py` | 27-30 | 默认 DB_DSN 含 `evopaw123` |

**建议**: 移除所有默认密码，强制要求环境变量。

### 3.2 安全亮点（正面评价）

| 机制 | 位置 | 评价 |
|------|------|------|
| 凭证隔离 | `cleanup/service.py` | ✅ 凭证不进 LLM context，写入沙盒 0o600 权限 |
| 路径遍历防护 | `tools/skill_loader.py:77-81` | ✅ `resolve()` + `startswith()` 检查 |
| SQL 参数化 | `memory/indexer.py`, `search_memory/scripts/search.py` | ✅ 全部使用参数化查询 |
| 原子文件操作 | `session/manager.py` | ✅ write-then-rename 模式 |
| 非 root 容器 | `Dockerfile:29-31` | ✅ 创建 `evopaw` 用户 |

### 3.3 🟡 P1 — 附件文件名路径遍历

**位置**: `evopaw/feishu/downloader.py:38-42`

```python
dest_path = dest_dir / attachment.file_name  # file_name 未校验
```

若飞书 API 返回恶意 `file_name`（如 `../../../etc/passwd`），可能导致路径遍历。

**建议**: 校验文件名不含路径分隔符，或使用 UUID 替代：
```python
safe_name = Path(attachment.file_name).name  # 剥离路径前缀
if safe_name != attachment.file_name:
    logger.warning("Blocked suspicious file_name: %r", attachment.file_name)
```

### 3.4 SQL 注入风险（低）

`search_memory/scripts/search.py:90` 使用 f-string 拼接 WHERE 子句：

```python
where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
cur.execute(f"... {where_sql} ...", params)
```

虽然 `where_clauses` 均为硬编码模板（`"tags && %(tags)s"` 等），当前无注入风险，但模式不够安全。建议使用参数构建器。

---

## 4. 代码质量分析

### 4.1 异常处理

**问题**: 大量使用 `except Exception:` 宽泛捕获。

| 文件 | 出现次数 | 说明 |
|------|---------|------|
| `feishu/listener.py` | 4 次 | WebSocket 解析、消息处理、`_extract_post_text`、`run_forever` |
| `feishu/sender.py` | 3 次 | 消息发送重试、`send_thinking`、`send_text` |
| `tools/skill_loader.py` | 2 次 | YAML 解析、JSON 解析 |
| `runner.py` | 2 次 | 消息处理循环、错误消息发送 |
| `memory/context_mgmt.py` | 1 次 | `_summarize_chunk` |
| `memory/indexer.py` | 1 次 | 索引失败 |
| `main.py` | 2 次 | 日志设置、启动清理 |
| `agents/main_agent.py` | 2 次 | Agent 内部异常 |

**减缓因素**: 所有位置都有日志记录且标注 `# noqa: BLE001`。
**建议**: 在可预测的场景使用具体异常类型（`yaml.YAMLError`, `json.JSONDecodeError`, `IOError` 等）。

### 4.2 未使用代码

| 位置 | 问题 | 严重度 |
|------|------|--------|
| `main.py:110` | `sub_agent_model` 读取但未使用 | Major |
| `main.py:112` | `sub_agent_max_turns` 读取但未使用 | Major |
| `tools/skill_loader.py:99` | `_build_description_xml` 参数 `skills_dir` 未使用 | Minor |
| `main.py:184` | `on_bot_added=None` 永不调用 | Minor |

### 4.3 重复代码

**`feishu/sender.py`** — `send()` 和 `send_text()` 的重试逻辑重复（约 30 行），包括：
- routing_key 解析（`p2p:` / `group:` / `thread:`）出现 3 次
- 重试循环（`max_retries=3`, 指数退避）出现 2 次

**建议**: 提取 `_resolve_receive_id(routing_key)` 和 `_send_with_retry(fn)` 公共方法。

### 4.4 类型安全

| 问题 | 位置 | 建议 |
|------|------|------|
| 广泛使用 `dict[str, Any]` | `skill_loader.py` 多处 | 定义 `SkillInfo(TypedDict)` |
| `type: ignore[override]` | `listener.py:52` | 规范子类重写 |
| `Any` 用于 LLM 客户端 | `indexer.py:25` | 定义具体协议类型 |

### 4.5 代码复杂度

| 文件 | 行数 | 职责数 | 评价 |
|------|------|--------|------|
| `tools/skill_loader.py` | 438 | 5 | ⚠ 需拆分 |
| `feishu/listener.py` | ~380 | 3 | ⚠ 提取 `_extract_*` 为模块级函数 |
| `runner.py` | 267 | 2 | ⚠ `_handle()` 需拆分子方法 |
| `main.py` | 254 | 1 | ✅ 单一职责（编排启动） |
| `memory/context_mgmt.py` | ~200 | 1 | ✅ 职责清晰 |

### 4.6 设计问题

**`listener.py` 受保护成员访问**:

```python
# _EvoPawEventHandler 内部访问 FeishuListener 的受保护方法
content = FeishuListener._extract_content(...)   # W0212
attachment = FeishuListener._extract_attachment(...)  # W0212
```

**建议**: 将 `_extract_content`、`_extract_attachment` 提升为模块级函数。

---

## 5. 测试覆盖评估

### 5.1 覆盖率概况

| 覆盖率 | 模块列表 |
|--------|---------|
| **100%** | `agents/main_agent.py`, `agents/skill_agent.py`, `agents/hooks.py`, `memory/bootstrap.py`, `memory/context_mgmt.py`, `memory/indexer.py`, `tools/skill_loader.py`, `tools/add_image_tool_local.py`, `runner.py`, `models.py`, `session/manager.py`, `session/models.py`, `cron/service.py`, `cron/models.py`, `cleanup/service.py`, `llm/claude_client.py` 等 22 个模块 |
| **80-99%** | `feishu/listener.py`, `feishu/sender.py`, `feishu/downloader.py`, `feishu/session_key.py`, `observability/` 等 8 个模块 |
| **0%** | `main.py`（CLI 入口）, `api/test_server.py`, `api/schemas.py` |

### 5.2 测试质量

| 维度 | 评价 |
|------|------|
| 单元测试数量 | 496 个，全部通过 |
| 测试独立性 | ✅ 使用 mock 隔离外部依赖 |
| 边界条件 | ✅ 覆盖空输入、超长输入、并发等场景 |
| 集成测试 | ✅ 包含 memory system、e2e conversation、feishu ops 等 |
| 覆盖率门槛 | `fail_under = 80`（pyproject.toml） |

### 5.3 测试缺口

| 缺口 | 影响 | 建议 |
|------|------|------|
| `main.py` 无测试 | 低（CLI 入口） | 补充启动流程集成测试 |
| `api/test_server.py` 无测试 | 中 | 补充 HTTP 端点集成测试 |
| `api/schemas.py` 无测试 | 低（纯数据模型） | 可忽略 |

---

## 6. Skills 系统完整性

### 6.1 Skills 清单

#### 完整可用（13 个）

| 类别 | Skill 名称 | 类型 | SKILL.md | 脚本 |
|------|-----------|------|---------|------|
| 文件处理 | pdf, docx, pptx, xlsx | task | ✅ | ✅ |
| 平台操作 | feishu_ops | task | ✅ | ✅ |
| 调度管理 | scheduler_mgr | task | ✅ | ✅ |
| 搜索 | tavily_search, arxiv_search | task | ✅ | ✅ |
| 记忆 | search_memory | task | ✅ | ✅ |
| 历史 | history_reader | reference (内联) | ✅ | N/A |
| 开发 | skill-creator | task | ✅ | ✅ |
| 投资 | investment-report | task | ✅ | ✅ |

#### 缺失脚本实现（5 个 ⚠）

| Skill 名称 | SKILL.md | 脚本 | 影响 |
|-----------|---------|------|------|
| memory-save | ✅ (6400 字) | ❌ | 第 22 课核心功能 |
| memory-governance | ✅ (9500 字) | ❌ | 第 22 课核心功能 |
| web_browse | ✅ | ❌ | 网页浏览能力缺失 |
| daily-summary | ✅ | ❌ | 日报生成缺失 |
| investment-review | ✅ | ❌ | 投资复盘缺失 |

**另外 2 个声明但未确认**: `investment-consult`, `hk-investment-morning-report`（需确认是否改为 reference 类型）。

### 6.2 Skills 加载机制

```yaml
# evopaw/skills/load_skills.yaml
skills:
  - name: pdf
    type: task
  - name: history_reader
    type: reference
  # ... 共 18 个
```

**加载流程**: `load_skills.yaml` → `_build_skill_registry()` → 验证 SKILL.md 存在 → 提取 description → 构建 XML 注入工具描述。

---

## 7. 并发与性能

### 7.1 并发安全机制

| 机制 | 位置 | 保护对象 | 评价 |
|------|------|---------|------|
| `asyncio.Lock` | `runner.py:76` | 队列/Worker 创建 | ✅ |
| `asyncio.Lock` | `session/manager.py:26` | `index.json` 读写 | ✅ |
| `dict[str, asyncio.Lock]` | `session/manager.py:27` | 每个 session 的 JSONL | ✅ |
| `asyncio.Lock` | `cleanup/service.py:64` | `sweep()` 操作 | ✅ |
| write-then-rename | `session/manager.py:189` | index.json 原子写 | ✅ |
| write-then-rename | `cleanup/service.py:164` | 凭证原子写 | ✅ |
| `flush() + fsync()` | `session/manager.py:148` | JSONL 追写持久化 | ✅ |

### 7.2 潜在性能关注点

| 关注点 | 位置 | 风险 | 建议 |
|--------|------|------|------|
| 模块全局 LLM 客户端 | `indexer.py:74-86` | `_llm_client` / `_embed_client` 无生命周期管理 | 添加连接超时和显式 cleanup |
| Worker 空闲超时 | `runner.py` | 默认 300s，高并发场景可能创建大量 worker | 添加 max_workers 上限 |
| 日志文件积累 | `logging_config.py:42` | 50MB × 5 = 250MB | 生产环境验证磁盘占用 |
| history 全量加载 | `session/manager.py` | `load_history` 读取整个 JSONL | 大 session 可能较慢，考虑反向读取 |

### 7.3 异步设计评价

- ✅ 所有 I/O 操作使用 `async/await`
- ✅ CPU 密集操作（cleanup sweep）使用 `run_in_executor`
- ✅ pgvector 索引使用 `asyncio.create_task` 异步化，不阻塞主流程
- ✅ WebSocket 线程到事件循环使用 `run_coroutine_threadsafe` 正确跨线程

---

## 8. 部署与配置

### 8.1 Docker 配置

**Dockerfile 评价**:
- ✅ 基于 Python 3.11 + Node.js 22
- ✅ 安装 Claude Code CLI
- ✅ 创建非 root 用户 `evopaw`
- ⚠ 无多阶段构建（镜像较大）

**docker-compose.yaml**:
- ✅ evopaw-main + pgvector 双服务
- ⚠ 默认密码 `evopaw123`（见安全审计）

### 8.2 数据库 Schema

**`schema.sql` 特性**:
- ✅ pgvector HNSW 索引（`summary_vec`, `message_vec`）
- ✅ BM25 全文索引（`search_tsv` 自动维护触发器）
- ✅ 标量过滤索引（`session_id`, `routing_key`, `created_at`）
- ✅ `ON CONFLICT DO NOTHING` 幂等插入

### 8.3 依赖管理

**`requirements.txt` 关键依赖**:

| 依赖 | 版本约束 | 评价 |
|------|---------|------|
| `claude-agent-sdk` | `>=0.1.0` | ⚠ 约束过松，SDK 可能有 breaking change |
| `lark-oapi` | 未锁定 | ⚠ 建议锁定主版本 |
| `psycopg2-binary` | `>=2.9.0` | ✅ |
| `pgvector` | `>=0.2.0` | ✅ |
| `aiohttp` | 未锁定 | ⚠ 建议锁定主版本 |

**建议**: 使用 `pip-compile` 生成 `requirements.lock` 锁定完整依赖树。

### 8.4 环境变量依赖

| 变量 | 必需 | 来源 |
|------|------|------|
| `FEISHU_APP_ID` | 是 | `.env` / 环境 |
| `FEISHU_APP_SECRET` | 是 | `.env` / 环境 |
| `ANTHROPIC_API_KEY` | 是 | `.env` / 环境 |
| `TAVILY_API_KEY` | 否 | `.env` / 环境 |
| `MEMORY_DB_DSN` | 否 | `.env` / 环境 |
| `POSTGRES_PASSWORD` | 否（有默认值） | `.env` / 环境 |

---

## 9. 优先级修复清单

### 🔴 P0 — 立即处理

| # | 问题 | 位置 | 行动 |
|---|------|------|------|
| 1 | `.env` 含真实凭证 | `.env` | 轮换所有凭证，确认不在 git 历史 |
| 2 | 默认密码 `evopaw123` 硬编码 | `config.yaml.template:39`, `docker-compose.yaml:25`, `search.py:27` | 移除默认值，强制环境变量 |
| 3 | `.env.example` 含真实凭证值 | `.env.example` | 替换为占位符 |

### 🟡 P1 — 高优先级

| # | 问题 | 位置 | 行动 |
|---|------|------|------|
| 4 | 附件文件名未校验 | `feishu/downloader.py:38-42` | 添加 `Path(name).name` 校验 |
| 5 | `sub_agent_model` / `sub_agent_max_turns` 读取未使用 | `main.py:110-112` | 传递给 `build_agent_fn` 或删除 |
| 6 | `skill_loader.py` 单文件 438 行 5 项职责 | `tools/skill_loader.py` | 拆分为子包 |
| 7 | `memory-save` / `memory-governance` 缺脚本 | `skills/memory-save/`, `skills/memory-governance/` | 补充实现（第 22 课核心） |
| 8 | 模块全局 LLM 客户端无生命周期管理 | `memory/indexer.py:74-86` | 添加连接超时 + cleanup |

### 🟢 P2 — 中等优先级

| # | 问题 | 位置 | 行动 |
|---|------|------|------|
| 9 | `except Exception:` 过于宽泛（共 17 处） | 多个文件 | 使用具体异常类型 |
| 10 | `sender.py` 重试逻辑 + routing_key 解析重复 | `feishu/sender.py` | 提取公共方法 |
| 11 | `listener.py` 受保护成员跨类访问 | `feishu/listener.py:105,110` | 改为模块级函数 |
| 12 | `runner._handle()` 未拆分 | `runner.py:149-204` | 提取 `_prepare`, `_execute`, `_finalize` |
| 13 | 依赖版本约束过松 | `requirements.txt` | 使用 `pip-compile` 锁定 |
| 14 | `skill_loader.py:99` 参数 `skills_dir` 未使用 | `tools/skill_loader.py` | 删除或使用 |

### ℹ️ P3 — 低优先级

| # | 问题 | 位置 | 行动 |
|---|------|------|------|
| 15 | `on_bot_added` 未实现 | `main.py:184` | 实现入群欢迎卡片 |
| 16 | TestAPI `skills_called` 虚假数据 | `api/test_server.py:124` | 接入 tracing |
| 17 | 广泛使用 `dict[str, Any]` | `skill_loader.py` | 定义 TypedDict |
| 18 | 日志 `JsonFormatter` 不记录完整上下文 | `observability/logging_config.py` | 扩展 record.extra |
| 19 | entry point 无测试 | `main.py`, `api/test_server.py` | 补充集成测试 |
| 20 | `_compat/` 空目录 | `evopaw/_compat/` | 删除 |

---

## 附录 A：模块依赖图

```
main.py
  ├── runner.py
  │     ├── models.py
  │     ├── session/manager.py
  │     └── feishu/downloader.py
  ├── feishu/listener.py
  ├── feishu/sender.py
  ├── agents/main_agent.py
  │     ├── memory/bootstrap.py
  │     ├── memory/context_mgmt.py
  │     ├── memory/indexer.py
  │     ├── tools/skill_loader.py
  │     │     └── agents/skill_agent.py
  │     ├── tools/add_image_tool_local.py
  │     └── agents/hooks.py
  ├── session/manager.py
  ├── cron/service.py
  ├── cleanup/service.py
  ├── observability/
  └── api/ (可选)
```

## 附录 B：凭证轮换检查清单

若 `.env` 中的凭证曾提交到 git 历史：

- [ ] 飞书 APP_SECRET — 在飞书开放平台重新生成
- [ ] ANTHROPIC_API_KEY — 在 Anthropic Console 重新生成
- [ ] TAVILY_API_KEY — 在 Tavily 面板重新生成
- [ ] PostgreSQL 密码 — 更改为强密码（`openssl rand -base64 32`）
- [ ] 使用 `git filter-repo` 或 BFG 清理 git 历史中的凭证

---

*报告生成工具: Claude Code (Opus 4.6)*
