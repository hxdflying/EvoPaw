# EvoPaw 能力工具化可行性分析

日期：2026-04-24

## 背景

本分析讨论一个架构方向：将飞书发消息、读文档、调度任务、记忆保存等能力尽量以清晰工具暴露，而不是塞进 Runner、Main Agent 或其它主流程判断逻辑里。

这个方向的核心目标是让 EvoPaw 保持长期可扩展：

- 主流程只处理系统管道和可靠性。
- Main Agent 只负责理解意图、规划步骤、选择工具。
- 平台能力通过稳定工具接口暴露。
- 具体执行落在 Skill 脚本或确定性服务中，而不是散落在主流程分支里。

## 总体结论

这个方向可行，而且 EvoPaw 当前已经大半走在这条路上。

更准确地说，问题不是“要不要工具化”，而是：

1. 哪些能力应该工具化。
2. 工具接口应该细到什么粒度。
3. 哪些系统管道能力必须留在主流程。
4. 现有 Skill 是否已经有足够清晰、稳定、可测试的执行契约。

当前 EvoPaw 已经具备关键基础：

- Main Agent 已被约束为只通过 `skill_loader` 获取外部能力。
- `skill_loader` 是唯一 MCP 工具入口，按 `skill_name` 分发 reference / task / history_reader。
- `feishu_ops`、`scheduler_mgr`、`memory-save`、`search_memory` 已经注册为 Skills。
- 飞书操作已有脚本化封装。
- 调度任务已有配置层 Skill，并由 `CronService` 到点重新注入主 Agent。

因此，这不是从零开始的重构，而是对现有架构的自然强化。

## 应该工具化的能力

适合工具化的是平台能力和业务动作，也就是 Agent 可以按语义选择调用的能力：

- 飞书发送文本、富文本、图片、文件。
- 飞书读取文档、表格、群成员、日历。
- 飞书创建文档、表格、写入表格或多维表。
- 创建、更新、删除、查看定时任务。
- 保存长期记忆。
- 搜索历史记忆。
- 读取历史对话。
- 处理 PDF、DOCX、PPTX、XLSX 等文件。
- 联网搜索和网页浏览。

这些能力不应该逐步堆到 Runner 或 Main Agent 的 Python 分支中。否则主流程会变成意图分类器：

```python
if 用户说发消息:
    ...
elif 用户说读文档:
    ...
elif 用户说记住:
    ...
elif 用户说定时:
    ...
```

这种结构会让核心流程越来越难维护。每增加一种能力都要修改核心代码，风险和耦合都会上升。

## 不应该工具化的能力

有些能力不是 Agent 的业务能力，而是系统管道和可靠性逻辑，应该继续留在主流程中：

- 飞书事件解析。
- `routing_key` 解析。
- 消息去重。
- session 获取与创建。
- 附件下载。
- 语音转写入口。
- Loading 卡片发送和更新。
- 最终回复写入历史。
- 错误兜底。
- CronService 到期触发。

这些逻辑不应该交给 Agent 决定。Agent 不应该判断一条飞书事件如何入队，也不应该负责底层 session 历史是否落盘。

## 当前实现状态

### Main Agent

`evopaw/agents/main_agent.py` 已经明确注入工具约束：

- 唯一外部能力接口是 `skill_loader`。
- 禁止使用 Claude Code CLI 内置 skill。
- 搜索、定时任务、文件处理、记忆管理等都通过对应 Skill 完成。

这说明当前架构已经把 Main Agent 定位为规划者，而不是直接平台操作执行者。

### SkillLoader

`evopaw/tools/skill_loader.py` 目前承担这些职责：

- 读取 `load_skills.yaml` 构建 Skill registry。
- 从 `SKILL.md` frontmatter 生成轻量描述。
- 在调用时按需加载完整 `SKILL.md`。
- 分发 `reference` / `task` / `history_reader`。
- 创建 Claude SDK MCP server。
- 对 task Skill 启动 Sub-Agent。

这个设计方向正确，但文件职责偏多。长期看应拆成更小的模块，例如：

```text
evopaw/tools/skill_loader/
├── registry.py
├── description.py
├── dispatcher.py
├── history.py
└── mcp.py
```

不过这不是第一优先级。更重要的是先补齐关键 Skill 的执行闭环。

### Feishu Ops

`feishu_ops` 已经比较成熟，脚本包括：

- `send_text.py`
- `send_post.py`
- `send_image.py`
- `send_file.py`
- `read_doc.py`
- `read_sheet.py`
- `create_doc.py`
- `create_sheet.py`
- `upload_sheet.py`
- `write_sheet.py`
- `create_bitable.py`
- `create_bitable_table.py`
- `write_bitable_records.py`
- `get_chat_members.py`
- `list_events.py`
- `create_event.py`

这部分可行度很高。它已经基本实现了“飞书能力工具化”。

当前主要问题不是有没有工具，而是工具面偏大。`feishu_ops` 一个 Skill 里包含很多操作，模型选择压力较大。短期可以接受，长期可以考虑拆成：

- `feishu_send`
- `feishu_docs`
- `feishu_sheets`
- `feishu_calendar`

这样可以让 Skill 描述更清楚，减少误用。

### Scheduler Manager

`scheduler_mgr` 是当前最清晰的分层样板。

它只负责写入定时任务配置，不直接执行业务脚本或命令。触发时由 `CronService` 把 `payload.message` 作为自然语言消息重新注入 Runner，再由 Main Agent 结合其它 Skills 完成业务处理。

这个边界很健康：

```text
scheduler_mgr：管理“什么时候做什么”
CronService：到点触发
Main Agent + Skills：决定“怎么做”
```

这种设计比把 `python ...` 或 `bash ...` 命令写进定时任务安全得多，也更符合 Agent 工作方式。

后续其它能力可以参考 `scheduler_mgr` 的模式：工具只负责稳定、确定性的状态变更，复杂业务由 Agent 重新规划。

### Memory Save

`memory-save` 是当前最明显的缺口。

它已经有较完整的 `SKILL.md` 规范，定义了五类写入目标：

- `soul`
- `user`
- `agent`
- `memory_index`
- `topic`

规范还定义了准入控制、阈值门控、更新优于追加、read-back 验证等规则。

但是当前目录里只有 `SKILL.md`，没有确定性执行脚本。也就是说，现在它更像“让 Sub-Agent 按说明自己读写文件”，还不是一个真正强约束的工具接口。

建议补齐：

```text
evopaw/memory/service.py
evopaw/skills/memory-save/scripts/save.py
evopaw/skills/memory-save/scripts/list_targets.py
evopaw/skills/memory-save/scripts/read_target.py
```

推荐职责划分：

- Agent 判断是否应该记、记什么、写到哪个 target。
- `memory-save` 脚本负责校验、去重、阈值门控、原子写入、read-back 验证。
- `memory/service.py` 提供可单测的确定性写入逻辑。

这样才算把记忆保存真正工具化，而不是把文件修改风险交给模型自由操作。

## 推荐架构边界

推荐保留现有 `skill_loader` 单入口，不要马上把每个动作都暴露成 Main Agent 直连 MCP tool。

推荐形态：

```text
Main Agent
  -> skill_loader
     -> feishu_ops scripts
     -> scheduler_mgr scripts
     -> memory-save scripts
     -> search_memory scripts
```

原因：

- Main Agent 的工具面保持很小。
- SkillLoader 的渐进式披露机制可以继续发挥作用。
- 每个 Skill 可以拥有自己的说明、脚本、测试和权限边界。
- 后续如果接入非 Claude runtime，也可以先把 `skill_loader` 核心拆成 provider-neutral 层。

系统边界建议如下：

```text
Runner / FeishuListener / CronService：
只处理系统管道和可靠性。

Main Agent：
只做意图理解、规划和选择 Skill。

SkillLoader：
只做能力注册、披露和分发。

Skill scripts：
做确定性平台操作和持久化写入。
```

## 落地优先级

### P0：补齐 memory-save 执行闭环

这是最大缺口，也是长期助手最核心的能力。

目标：

- 新增 `memory_service`。
- 新增 `memory-save/scripts/save.py`。
- 支持 `target=user|agent|memory_index|topic` 的安全写入。
- 实现 memory.md 行数阈值门控。
- 实现 topic 写入时自动维护索引。
- 写入后 read-back 验证。
- 增加单元测试覆盖。

### P1：收紧 feishu_ops 输入输出契约

目标：

- 统一所有脚本 JSON 输出格式。
- 统一 `errcode` / `errmsg` / `data`。
- 明确路径限制和文件大小限制。
- 把可恢复错误返回给 Agent，而不是只写 stderr。
- 增加关键脚本的单元测试。

### P2：把 scheduler_mgr 模式推广为 Skill 设计规范

目标：

- 明确“配置层 Skill 不执行业务”。
- 定时任务 payload 始终是自然语言意图。
- 禁止 `payload.message` 中出现可执行命令和 session 路径。
- 让新增 Skill 都遵循“Agent 决策，脚本执行”的模式。

### P3：拆分 skill_loader 内部职责

目标：

- 降低 `skill_loader.py` 文件复杂度。
- 为 provider-neutral tool layer 做准备。
- 保持现有行为不变，先做结构拆分。

## 风险与缓解

### 风险一：工具过细导致模型选择困难

如果把每个小动作都暴露为一个顶层 Skill，Main Agent 会面对过多选择。

缓解：

- 保留 `skill_loader` 单入口。
- 按领域聚合 Skill。
- 在 Skill 内部用脚本细分操作。

### 风险二：工具过粗导致误用

例如 `feishu_ops` 一个 Skill 包含发送、读取、创建、日历、表格等很多能力，说明过长时模型可能选错脚本。

缓解：

- 短期保持现状。
- 当误用频繁或说明膨胀时再拆分领域 Skill。

### 风险三：把系统管道误交给 Agent

例如让 Agent 决定是否写 session 历史、如何去重、如何解析飞书事件，会破坏可靠性。

缓解：

- Runner / Listener / CronService 继续负责系统确定性管道。
- Agent 只接收已经规范化的 `InboundMessage.content`。

### 风险四：记忆写入被 prompt injection 污染

如果让模型直接把外部工具输出写进长期记忆，可能把不可信内容持久化。

缓解：

- `memory-save` 保持 admission control。
- 禁止直接写入外部工具原始输出。
- 写入脚本限制 target、路径和格式。
- 必要时对 memory 写入增加审计记录。

## 最终判断

能力工具化方向非常适合 EvoPaw。

但正确路线不是把所有逻辑都交给 Agent，而是明确分层：

- 系统管道保持确定性。
- Agent 负责语义规划。
- SkillLoader 负责能力分发。
- Skill 脚本负责稳定执行。

短期最值得做的是补齐 `memory-save` 的确定性执行层。它能直接提升 EvoPaw 作为长期个人工作助手的可靠性，也能形成一套可复用的 Skill 工具化范式。
