# EvoPaw 冗余审查报告（未完成项）

审查日期：`2026-04-21`
最近更新：`2026-04-22`（已完成项目已删除，仅保留未完成项）

---

## 已完成（摘要，详情见 git log）

| 项目 | Commit |
|---|---|
| F1 归档 CrewAI/AIO-Sandbox 时代设计文档 | `1eee888` |
| F2 归档依赖 main_crew / AIO-Sandbox 的旧集成测试 | `a5d28ec` |
| F6 README 对齐当前架构（18→19、补投资技能、死配置警示） | `2d551d6` |
| F7 统一历史术语（Sub-Crew / AIO-Sandbox / baidu_search） | `3e41040` |
| F8 清理工作树垃圾（`.bak` / `_compat/` / `tests/logs/`）+ 补 `.gitignore` | `448b746` |
| 清单 #1 提交 293 处 pending 删除 | `66dfded` / `efd1dbd` / `6fd5a6d` |
| 清单新增 首次提交 `evopaw/` 模块（之前整个目录 untracked） | `3e41040` |

---

## 未完成的 Findings

### F3. 高优先级：Office 技能资源存在三份完全重复拷贝

**判断**

`docx`、`pptx`、`xlsx` 三个技能目录下的 `office/` 资源不是"部分相似"，而是存在可验证的大块完全重复内容。这是仓库中最明确、最可量化的结构性冗余。

**证据**

- 目录体积：
  - `evopaw/skills/docx/scripts/office`：约 `1.4M`
  - `evopaw/skills/pptx/scripts/office`：约 `1.4M`
  - `evopaw/skills/xlsx/scripts/office`：约 `1.4M`
- `schemas/` 目录体积：
  - 三者均约 `1.1M`
- `validators/` 目录体积：
  - 三者均约 `188K`
- `schemas/` 文件数量：
  - 三者均为 `39` 个文件，文件名集合完全一致
- `validators/` 文件数量：
  - 三者均为 `10` 个文件，文件名集合完全一致
- 哈希校验显示下列文件在三个技能目录中内容完全相同：
  - `validators/base.py`
  - `validators/__init__.py`
  - `validators/docx.py`
  - `validators/pptx.py`
  - `validators/redlining.py`

**为什么这是冗余**

- 相同资源被复制三次，放大仓库体积，也放大未来任何修复的修改面。
- 一旦其中一份被单独修补，三份资源极易产生漂移，后续行为会变得不可预测。
- 这类"复制共享库"对技能开发体验也不好，因为维护者无法判断哪个目录才是共享逻辑的真正源头。

**建议动作**

- 抽出一个共享目录，例如 `evopaw/skills/_shared/office/`。
- 三个技能只保留各自真正差异化的入口脚本。
- 抽取时先做"只读共享资源"第一步：
  - 先统一 `schemas/`
  - 再统一 `validators/`
  - 最后再评估 `soffice.py` 和其它公共脚本

**风险提醒**

- 这是高收益但中风险改动，不建议和其他清理混在同一轮做。
- 最好先补共享资源回归测试，再抽取。
- Claude Code Skills 的约定是每个 Skill 自包含（SKILL.md + 脚本同目录），Sub-Agent 的 cwd 指向 session workspace 而非 skill 目录，跨目录相对引用需先验证可行性，不能直接 `import ../../_shared/office/`。

---

### F4. 高优先级：存在已写入配置和 README 的死配置项

**判断**

至少两项配置已经在主程序中读取，也在模板和 README 中公开，但当前运行链路并不会消费它们，属于明确的死配置。目前 README 已加 ⚠️ 警示（`2d551d6`），但代码层仍未处理。

**证据**

- `evopaw/main.py:108-112` 读取：
  - `sub_agent_model`
  - `sub_agent_max_turns`
- `evopaw/main.py:151-159` 构建主 Agent 时只传入：
  - `planner_model`
  - `agent_max_turns`
- `evopaw/agents/skill_agent.py:51-54` 直接调用 `build_sub_agent_options(system_prompt=..., cwd=...)`，并没有使用来自配置的 `sub_agent_model` 或 `sub_agent_max_turns`。
- `config.yaml.template` 仍暴露这两个配置项。

**为什么这是冗余**

- 使用者会误以为这些配置可控，实际上修改后无效。
- 每增加一个死配置，就增加一层"看起来能配、实际上没接线"的认知负担。

**建议动作（需决策）**

两条路径择一：

- **接通**（推荐）：在 `main.py:151` 构建时一并传入，`build_sub_agent_options(system_prompt, cwd, model=..., max_turns=...)`。改动面 ~5 行。
- **明确放弃**：三处（`main.py` / `config.yaml.template` / `README.md`）同步移除，并在 release notes 标注。

**不建议直接删除**：`build_sub_agent_options()` 当前很可能硬编码了 Haiku，删配置项等于彻底移除使用者对 Sub-Agent 模型的控制权。

---

### F5. 中高优先级：存在"文档和接口已承诺，但代码仍是存根"的功能点

**判断**

项目中有几处功能看起来已经对外暴露，但实际仍是占位或 TODO。这类内容不是传统重复代码，但属于明显的功能冗余和维护噪音。

**证据**

- `evopaw/main.py:191-193`
  - `on_bot_added` 仍为 `None`
  - 注释写明 `TODO: 实现 on_bot_added`
- `evopaw/api/test_server.py:119-125` 中 `skills_called=[]  # TODO: 从 Trace 获取`
- `README.md` 的 TestAPI 响应示例里仍展示 `skills_called: []`

（说明：F1 后 `DESIGN.md` 已归档，因此原证据中"DESIGN.md 已把 Bot 入群欢迎事件写成已实现功能"不再作为现役误导源。）

**为什么这是冗余**

- 它们在接口层和文档层已经占据位置，但并没有真正形成稳定能力。
- 这种状态最容易让后续维护者误判"功能已经有了，只是偶发不工作"。

**建议动作（需决策）**

- `on_bot_added`：
  - 如果不打算近期实现，就从接口声明里删掉这个可选回调参数，避免继续暗示"马上会有"。
  - 如果计划保留，就明确排期实现。
- `skills_called`：
  - 要么真正从 Trace 提取并返回。
  - 要么先从 `TestResponse` 模型和 README 示例中移除。

---

### F9. 低优先级：部分代码结构存在轻度重复与可合并点

**判断**

这部分不属于必须立即处理的问题，但如果你准备顺手做一轮内部收束，可以纳入后续精简计划。

**证据**

- `evopaw/tools/skill_loader.py:96-100`
  - `_build_description_xml()` 接收 `skills_dir` 参数，但函数体未使用该参数。
- `evopaw/main.py:162-168` 和 `evopaw/main.py:217-223`
  - 分别构建 `runner` 和 `test_runner`
  - 依赖装配高度相似，存在轻度装配重复。
- 单元测试按"基础版 / 卡片版"平行拆分：
  - `tests/unit/test_feishu_sender.py:1`
  - `tests/unit/test_feishu_sender_card.py:1-9`
  - `tests/unit/test_runner.py:1`
  - `tests/unit/test_runner_card.py:1-8`

**为什么这是冗余**

- 这些点不会直接误导业务功能，但会增加局部维护摩擦。
- 特别是测试按"功能演进阶段"横向复制文件，后续继续扩展时容易把同一组件的断言分散到多个文件里。

**建议动作**

- 删除 `_build_description_xml()` 的无效 `skills_dir` 参数，减少 API 表面噪音（~2 行，极低风险）。
- 抽出 Runner 装配 helper，避免主程序里重复 wiring。
- 评估是否按组件合并测试文件，而不是按"基础版/卡片版"继续平行增长（保守起见，短期不动）。

---

## 剩余待办清单

按风险/收益比保留排序。#1–#6 已完成。

### #7. 处理死配置 sub_agent_model / sub_agent_max_turns（需先决策）

对应 F4。接通 vs 放弃，择一执行。

### #8. 功能存根状态澄清（需先决策）

对应 F5。

- `on_bot_added`：删除 / 实现，二选一。
- `skills_called`：接 Trace 取值 / 移除字段，二选一。

### #9. 清除 API 表面噪音（极低风险，建议顺手做）

对应 F9 第一条。`skill_loader.py:_build_description_xml()` 去掉未用的 `skills_dir` 参数（形参 + 所有调用点），改动面约 2 行。

### F3 Office 共享资源抽取（中风险，需单独一轮）

对应 F3。建议与 #7–#9 分开进行：

- 先补共享资源回归测试
- 先验证 Sub-Agent cwd 下跨目录相对引用的可行性
- 再按 schemas → validators → 公共脚本 的顺序逐步抽取

---

## 不建议现在误删的内容

有些部分看起来"像重复"，但目前没有足够证据表明它们应该被删除：

- `CaptureSender` 与 `FeishuSender`
  - 前者明显是 TestAPI/测试环境用 sender，职责边界仍然清晰。
- `history_reader` 的内联分支
  - 这是有意绕开 Sub-Agent 的特殊路径，不属于冗余。
- 投资类多个 Skill（`investment-report` / `investment-review` / `investment-consult` / `hk-investment-morning-report`）
  - 名称相近，但本次没有逐一核实它们是否功能重叠，不建议仅凭命名合并。
