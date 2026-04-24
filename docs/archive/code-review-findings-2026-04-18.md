# EvoPaw 代码审查主要发现

审查日期：2026-04-18

审查范围：
- `evopaw/` 主代码
- `tests/` 测试代码
- `README.md`、`CLAUDE.md`、`config.yaml.template`

结论摘要：
- 当前仓库不是不可运行的“全盘失效”状态，但已经出现明显的契约漂移。
- 最主要的问题不在单个语法错误，而在“功能宣称、运行时实现、测试基线”三者不再一致。
- 当前状态不建议直接按“测试已充分覆盖、核心能力稳定”对外宣称完成。

## 严重问题

### 1. `history_reader` 拿不到完整历史，长会话历史被永久截断

问题：
- `Runner` 读取历史时固定调用 `load_history(session.id)`。
- `SessionManager.load_history()` 默认只返回最近 20 条消息。
- `main_agent` 再把这份已经截断的 `history` 传给 `skill_loader`，`history_reader` 只能读到这 20 条。
- 但主提示词明确告诉模型“更早消息可通过 `history_reader` 读取完整历史”，这与实现不符。

影响：
- 会话超过 20 条后，早期消息既不会进入主 prompt，也无法通过 `history_reader` 找回。
- 这会直接破坏需要回溯早期上下文的多轮任务。

证据：
- `evopaw/main.py:95`
- `evopaw/runner.py:181`
- `evopaw/session/manager.py:88`
- `evopaw/agents/main_agent.py:71`
- `evopaw/agents/main_agent.py:143`
- `evopaw/tools/skill_loader.py:208`

### 2. 上传图片的多模态链路存在路径转换错误

问题：
- `Runner` 给模型的附件路径格式为 `/workspace/sessions/{sid}/uploads/...`。
- `main_agent` 用 `image_path.lstrip("/workspace/")` 转本地路径。
- 这里使用了 `str.lstrip()`，会按字符集合剥离，不是按固定前缀去掉 `/workspace/`。
- 实际结果会把路径错误地变成类似 `ions/sid/uploads/...`。

影响：
- 图片附件通常无法被正确映射到真实文件。
- README 中宣称的“直接理解用户发送的图片”在上传图片场景下不可靠。

证据：
- `evopaw/runner.py:165`
- `evopaw/agents/main_agent.py:126`

### 3. 集成测试基线已经失效，当前 README 的测试状态不可信

问题：
- 实际运行 `python3 -m pytest tests/integration -m 'not llm' -q` 时，测试在收集阶段即失败。
- 失败原因不是用例行为差异，而是测试代码仍引用迁移前对象，例如：
  - `SANDBOX_URL`
  - `qwen_api_key`
  - `sandbox_available`
  - `evopaw.agents.main_crew`
- 当前代码树已经迁移为 `main_agent` 路径，这些引用不再存在。

影响：
- 迁移后的跨模块验证网已经断裂。
- README 中“496 单元测试，0 失败”的表述无法作为当前仓库状态的可信证明。

证据：
- `README.md:253`
- `tests/integration/conftest.py:63`
- `tests/integration/test_course22_cases.py:41`
- `tests/integration/test_course22_cases.py:69`
- `tests/integration/test_file_pipeline.py:39`
- `tests/integration/test_lesson22_cases.py:42`
- `evopaw/agents/__init__.py:3`

## 高优先级问题

### 4. `tavily_search` 凭证注入链路未接通

问题：
- 启动流程只调用了 `write_feishu_credentials()`。
- `tavily_search` 脚本硬编码要求 `/workspace/.config/tavily.json` 存在。
- `CleanupService.write_tavily_credentials()` 已实现，但主进程未调用。

影响：
- 即使环境中设置了 `TAVILY_API_KEY`，搜索技能仍可能在脚本入口直接报“凭证文件不存在”。

证据：
- `evopaw/main.py:125`
- `evopaw/cleanup/service.py:171`
- `evopaw/skills/tavily_search/scripts/search.py:18`

### 5. `ctx.json` 压缩逻辑没有接入主流程，长期上下文会线性膨胀

问题：
- `context_mgmt.py` 提供了 `maybe_compress()`。
- 但 `main_agent` 在持久化时只是把旧 `ctx_messages` 和新轮次消息直接拼接后写回。
- README 所说的“压缩后的对话快照”当前并未真正实现。

影响：
- 长会话会不断把未压缩内容重复注入 `<long_term_context>`。
- 会造成 token 膨胀、上下文污染和模型稳定性下降。

证据：
- `evopaw/memory/context_mgmt.py:159`
- `evopaw/agents/main_agent.py:181`
- `README.md:237`

### 6. 工作区根目录配置未统一，非默认 `workspace_dir` 场景会分裂

问题：
- `main.py` 从配置中读取 `memory.workspace_dir`，并把它传给主 agent。
- 但附件下载、凭证写入、目录清理仍然基于 `data_dir / "workspace"`。
- 这会让 bootstrap/session cwd 和 uploads/.config 落在不同根目录。

影响：
- 一旦配置使用非默认工作区，系统会出现“Agent 看一个目录，附件和凭证写到另一个目录”的错位问题。

证据：
- `evopaw/main.py:98`
- `evopaw/feishu/downloader.py:37`
- `evopaw/cleanup/service.py:143`

## 中优先级问题

### 7. TestAPI 无法正确返回 slash 命令结果

问题：
- `Runner` 对 slash 命令只调用 `sender.send_text()`。
- TestAPI 使用的 `CaptureSender.send_text()` 被实现为 no-op，不会 resolve Future。
- `test_server` 又在 HTTP 层无条件等待这个 Future。

影响：
- 通过 TestAPI 调试 `/new`、`/help`、`/status` 等命令时，响应可能卡到超时。
- 这与 TestAPI “本地调试入口”的定位不一致。

证据：
- `evopaw/runner.py:153`
- `evopaw/api/capture_sender.py:58`
- `evopaw/api/test_server.py:106`
- `tests/integration/test_api.py:212`
- `tests/unit/test_capture_sender_card.py:112`

### 8. Cron 任务在 dispatch 失败时仍会被按“已触发”推进状态

问题：
- `_fire()` 捕获 dispatch 异常后，仅记录 `last_status="error"`。
- 外层循环仍把该 job 放进 `fired_ids`。
- `_post_fire()` 会继续删除一次性任务、禁用 `at` 任务或推进 `every/cron` 的下一次运行时间。

影响：
- 一次瞬时下游故障就可能导致定时任务永久丢失执行机会。

证据：
- `evopaw/cron/service.py:90`
- `evopaw/cron/service.py:121`
- `evopaw/cron/service.py:133`

### 9. 飞书发送失败会被静默吞掉，调用方无法感知“消息未送达”

问题：
- `FeishuSender.send()` / `send_text()` 在重试耗尽后只 `break` 并返回，不抛异常。
- `Runner` 将这次调用视为成功完成。

影响：
- 会话历史会正常落盘，但用户侧可能完全收不到回复。
- 这是典型的“系统内成功、用户侧失败”的隐蔽故障。

证据：
- `evopaw/feishu/sender.py:33`
- `evopaw/feishu/sender.py:141`
- `evopaw/runner.py:200`

### 10. 包元数据不完整，`pyproject.toml` 与真实依赖脱节

问题：
- `pyproject.toml` 只声明了极少数依赖。
- 实际代码和测试还依赖 `PyYAML`、`lark-oapi`、`croniter`、`Pillow`、`openai`、`psycopg2-binary`、`tavily-python` 等。
- 这些只在 `requirements.txt` 中出现。

影响：
- `pip install .` 或 `pip install .[dev]` 无法得到完整环境。
- 这会直接导致测试收集或运行期缺包。

证据：
- `pyproject.toml:1`
- `requirements.txt:1`
- `tests/integration/test_memory_system.py:31`

### 11. 配置模板中存在未消费或部分失效的参数

问题：
- `config.yaml.template` 中定义了 `agent.*`、`sender.*`、`runner.max_queue_size` 等参数。
- 启动链路并未消费其中相当一部分，运行时仍使用硬编码默认值。

影响：
- 配置看起来可调，实际却不生效。
- 会误导运维和后续开发人员。

证据：
- `config.yaml.template:17`
- `config.yaml.template:44`
- `config.yaml.template:48`
- `evopaw/main.py:86`
- `evopaw/llm/claude_client.py:36`

## 验证情况

已验证：
- `python3 -m compileall evopaw` 通过。
- `python3 -m pytest tests/unit/test_main_agent.py -q` 通过。
- `python3 -m pytest tests/unit/test_skill_loader.py -q` 通过。
- `python3 -m pytest tests/unit/test_feishu_ops_scripts.py -q` 通过。
- `python3 -m pytest tests/integration -m 'not llm' -q` 在收集阶段失败，暴露出测试基线漂移问题。

说明：
- 当前沙箱禁止创建监听 socket，因此基于 `aiohttp.test_utils` 的部分 HTTP 型集成测试无法在本次环境中完整跑通。
- TestAPI slash 命令问题来自代码路径严格推导，且与当前 `CaptureSender` 语义一致。

## 建议修复顺序

建议优先按以下顺序处理：

1. 修复历史读取链路，保证 `history_reader` 真正可读完整历史。
2. 修复图片路径转换，恢复上传图片多模态能力。
3. 恢复集成测试基线，移除残留的迁移前引用。
4. 接通 Tavily 凭证写入链路。
5. 将 `ctx.json` 压缩逻辑真正接入主流程。
6. 统一 `workspace_dir` 的配置使用边界。
7. 修复 TestAPI 对 slash 命令的响应机制。
8. 修复 Cron 失败后的状态推进策略。
9. 补齐包元数据与配置消费关系。
