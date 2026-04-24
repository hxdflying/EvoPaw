# EvoPaw 冗余审查报告（未完成项）

审查日期：`2026-04-21`
最近更新：`2026-04-24`（已完成项目已删除，仅保留未完成项）

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
| F4 接通死配置 `sub_agent_model` / `sub_agent_max_turns`（4 层穿透 + README 撤警告） | `<待 commit>` |
| F9 第 1 项 去掉 `_build_description_xml` 的未用 `skills_dir` 参数 | `<待 commit>` |
| F9 第 2 项 `main.py` 用 `functools.partial` 统一 Runner / test_runner 装配 | `<待 commit>` |

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
- `schemas/` 文件数量（递归）：
  - 三者均为 `39` 个文件，文件名集合完全一致
- `validators/` 文件数量（递归）：
  - 三者均为 `10` 个文件，文件名集合完全一致
- 实际 `diff -rq` 显示：整个 `scripts/office/` 目录（除 `__pycache__/*.pyc` 字节码时间戳差异外）三份完全相同，不只是 `schemas/` 和 `validators/`，还包括：
  - `helpers/`
  - `pack.py` / `unpack.py` / `validate.py` / `soffice.py`

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
- 三份 SKILL.md 里大量使用裸相对路径 `scripts/office/soffice.py`（docx 7 处、pptx 5 处、xlsx 1 处）。抽取后需要同步改写这些路径引用，并在 `skill_loader._build_skill_registry` 里把 `_shared` 目录排除在 registry 之外。

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

### F9. 低优先级：测试文件按"基础版 / 卡片版"平行拆分

**判断**

单元测试按"功能演进阶段"横向复制了文件，同一组件的断言会分散到多个文件里。

**证据**

- `tests/unit/test_feishu_sender.py:1`
- `tests/unit/test_feishu_sender_card.py:1-9`
- `tests/unit/test_runner.py:1`
- `tests/unit/test_runner_card.py:1-8`

**为什么这是冗余**

- 不会直接误导业务功能，但会增加局部维护摩擦。
- 未来继续扩展时容易把同一组件的断言分散到更多文件里。

**建议动作**

- 评估是否按组件合并测试文件，而不是按"基础版/卡片版"继续平行增长（保守起见，短期不动）。

---

## 剩余待办清单

按风险/收益比保留排序。

### #8. 功能存根状态澄清（需先决策）

对应 F5。

- `on_bot_added`：删除 / 实现，二选一。
- `skills_called`：接 Trace 取值 / 移除字段，二选一。

### F3 Office 共享资源抽取（中风险，需单独一轮）

对应 F3。建议与 #8 分开进行：

- 先补共享资源回归测试
- 先验证 Sub-Agent cwd 下跨目录相对引用的可行性
- 再按 schemas → validators → 公共脚本 的顺序逐步抽取

### F9 测试文件合并（可选，保守起见短期不动）

对应现 F9。现阶段不计入必做清单，待实际触发重复扩展时再评估。

---

## 不建议现在误删的内容

有些部分看起来"像重复"，但目前没有足够证据表明它们应该被删除：

- `CaptureSender` 与 `FeishuSender`
  - 前者明显是 TestAPI/测试环境用 sender，职责边界仍然清晰。
- `history_reader` 的内联分支
  - 这是有意绕开 Sub-Agent 的特殊路径，不属于冗余。
- 投资类多个 Skill（`investment-report` / `investment-review` / `investment-consult` / `hk-investment-morning-report`）
  - 名称相近，但本次没有逐一核实它们是否功能重叠，不建议仅凭命名合并。
