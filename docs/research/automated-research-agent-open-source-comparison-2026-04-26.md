# 自动化科研 Agent 开源库分析与选型报告

生成日期：2026-04-26  
对象仓库：

- ClawPhD: <https://github.com/ZhihaoAIRobotic/ClawPhD>
- ARIS / Auto-claude-code-research-in-sleep: <https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep>
- Dr. Claw: <https://github.com/OpenLAIR/dr-claw>
- AutoResearchClaw: <https://github.com/aiming-lab/AutoResearchClaw>

## 0. 结论先行

如果你的身份是“LLM/agent 方向在读博士生”，并且关注“自动化科研 agent”本身，而不是只想找一个写论文/画图工具，我的建议排序是：

1. **AutoResearchClaw**：最适合作为“自动科研系统”的研究对象和实验基线。它有明确的 23-stage state machine、实验执行、文献、论文、引用校验、HITL、MetaClaw 等模块，代码实体最多，也最适合做系统性评测。但它的 README 宣传强、集成面宽、版本元数据滞后，需要强审计。
2. **ARIS**：最适合研究 agent workflow、cross-model review、prompt protocol 和科研流程方法论。它不是传统 Python 包，而是大量 `SKILL.md` 组成的流程协议库。透明、可改、适合你快速改造和做对照实验，但工程执行可靠性很依赖 Claude Code/Codex/MCP/本地环境。
3. **Dr. Claw**：最适合作为“科研 agent 工作台 / HCI / 多 agent CLI 编排平台”。它有 React/Vite + Express + Electron + NPM 发布 + CI，产品化程度最高。适合研究 human-agent collaboration 和 project management，而不是直接作为科研发现算法的核心。
4. **ClawPhD**：更像“论文产物工具链”，重点是 PDF->Markdown、图表/网页/论文评审/参考图抽取等。它对科研输出很有用，但不构成完整的 idea->experiment->paper 闭环。适合作为你未来系统里的工具模块，而不是主研究对象。

一句话：**想做自动科研 agent 研究，先读 ARIS 的 workflow 设计，再用 AutoResearchClaw 做可执行基线；Dr. Claw 用来研究交互和平台化；ClawPhD 作为论文产物生成与评审工具补充。**

## 1. 本次审查方法与局限

我在 2026-04-26 对四个仓库的 `main` 分支做了浅克隆与静态检查，重点看了 README、包元数据、入口文件、目录结构、关键 workflow 文件、测试数量、许可证和最近提交。

本报告没有完整跑通这些系统的端到端科研流程。原因是这些流程通常需要 API key、联网文献搜索、GPU、Docker/SSH、Claude/Codex/Gemini/OpenRouter 等外部服务。下面关于“能力”的判断区分两类：

- **已在仓库中看到的实现痕迹**：目录、入口、配置、state machine、测试、workflow 文件。
- **README 声称或示例展示的能力**：需要额外运行验证，不能等同于已被严格基准证明。

## 2. 仓库快照

| 项目 | 本地审查 commit | 最近提交时间 | 规模与结构信号 | 包/入口 | 测试/CI | 许可证 |
|---|---:|---:|---|---|---|---|
| ClawPhD | `b3bb1ed` | 2026-03-26 | 209 个 tracked files，86 个 Python 文件，17 个 `clawphd/skills` | `clawphd` Typer CLI；`pyproject.toml` 包名 `clawphd-ai` | 6 个 test files；未见 `.github/workflows` | MIT |
| ARIS | `97e0eb1` | 2026-04-25 | 312 个 files，68 个顶层 skills，131 个总 `SKILL.md`（含 Codex/Claude/Gemini 变体） | 非传统包；核心是 `skills/`、`tools/`、`mcp-servers/` | 12 个 test files；未见 `.github/workflows` | MIT |
| Dr. Claw | `1bec91a` | 2026-04-23 | 1217 个 files，338 个 JS/TS 文件，86 个顶层 skills，171 个总 `SKILL.md` | NPM 包 `dr-claw` v1.1.4；React/Vite + Express + Electron | 30 个 test files；有 CI、npm publish、desktop release workflows | GPL-3.0 + AGPL-3.0 |
| AutoResearchClaw | `84dad0a` | 2026-04-23 | 512 个 files，363 个 Python 文件，28 个总 `SKILL.md`，完整 `researchclaw/` 模块树 | `researchclaw` CLI；`pyproject.toml` 版本 0.3.1 | 96 个 test files；未见 `.github/workflows` | MIT |

注意两个版本一致性信号：

- ClawPhD 的 `pyproject.toml` 写的是 `0.1.3.post4`，但 `clawphd/__init__.py` 写的是 `0.1.4.post3`。
- AutoResearchClaw README 宣称 v0.4.0 HITL，但 `pyproject.toml` 和 `researchclaw/__init__.py` 仍是 `0.3.1`。

这不一定代表功能不可用，但说明项目处在快速迭代期，做研究基线时要锁 commit、记录配置、保留产物。

## 3. 总体对比矩阵

评分范围 1-5，5 表示该维度最强。这里的评分是基于仓库静态结构、文档完整性、可研究价值与潜在工程风险的综合判断。

| 维度 | ClawPhD | ARIS | Dr. Claw | AutoResearchClaw |
|---|---:|---:|---:|---:|
| 自动科研闭环完整度 | 2.0 | 4.0 | 3.5 | 5.0 |
| Agent workflow 透明度 | 3.5 | 5.0 | 3.5 | 4.0 |
| 可执行代码/系统工程实体 | 3.0 | 2.5 | 4.5 | 4.5 |
| 实验执行与结果真实性意识 | 1.5 | 3.5 | 3.0 | 4.0 |
| Cross-model critique / reviewer 设计 | 3.0 | 5.0 | 3.5 | 4.0 |
| 文献/引用/claim grounding | 3.0 | 4.0 | 3.5 | 4.0 |
| 人机协作与交互体验 | 2.5 | 3.5 | 5.0 | 4.0 |
| 可作为博士课题基线/研究对象 | 3.0 | 4.5 | 4.0 | 5.0 |
| 第一周上手成本 | 4.0 | 3.5 | 3.0 | 2.5 |
| 商业/闭源复用友好度 | 4.5 | 4.5 | 2.0 | 4.5 |

## 4. 四类系统的本质差异

这四个项目虽然都围绕“AI 科研”，但实际上站在不同抽象层：

| 类型 | 项目 | 本质 |
|---|---|---|
| 论文产物工具链 | ClawPhD | 把 paper/PDF/section 转成图、网页、review、结构化 Markdown 等产物 |
| Workflow skill pack | ARIS | 用 `SKILL.md` 定义科研流程协议，强调 cross-model review 和 artifact contracts |
| Research workspace | Dr. Claw | 给 Claude/Codex/Gemini/OpenRouter 等 agent 提供 UI、项目、文件、Git、任务和 Auto Research Hub |
| Code-native autonomous pipeline | AutoResearchClaw | Python 代码实现的 23-stage idea->literature->experiment->paper state machine |

这意味着它们不完全是同类竞品。更合理的组合方式是：

- **AutoResearchClaw** 做自动科研流水线主干。
- **ARIS** 提供 workflow protocol、reviewer independence、paper writing/audit 等方法论。
- **Dr. Claw** 提供工作台和 human-agent 操作界面。
- **ClawPhD** 提供图表、PDF 解析、网页、论文评审等工具能力。

## 5. ClawPhD 详细分析

### 5.1 定位

ClawPhD README 将其描述为基于 Nanobot/OpenClaw 的研究 agent，目标是把学术论文转成 publication-ready diagrams、posters、videos、paper websites 等。已勾选功能包括图生成、参考图抽取、PDF->Markdown、AI paper review、paper discovery、paper websites；poster、video、code synthesis 仍是待办。

从代码结构看，它是一个 Python CLI/agent 框架，核心目录包括：

- `clawphd/agent/tools/`：`paperbanana.py`、`pdf2md.py`、`paper_review.py`、`figureref.py`、`autopage.py`、`arxiv_pipeline.py` 等。
- `clawphd/skills/`：17 个技能，包括 `diagram-gen`、`figure-ref`、`pdf2md`、`ai-review`、`page-gen`、`paper-scout` 等。
- `clawphd/providers/`：LiteLLM、多 provider registry、OpenAI Codex、Azure OpenAI、自定义 OpenAI-compatible endpoint。
- `clawphd/channels/`：多聊天/消息渠道集成痕迹。

### 5.2 优点

1. **论文产物生成能力集中**  
   它覆盖了很多科研后处理任务：论文图、参考图、PDF 结构化、paper website、AI review。这些任务相对边界清晰，比“全自动科研发现”更容易落地。

2. **工具粒度清晰，适合被别的 agent 调用**  
   `clawphd/agent/tools` 下的工具较适合被上层科研 agent 作为 callable tools 使用。例如 AutoResearchClaw 或 ARIS 完成实验后，可以调用 ClawPhD 生成图和网页。

3. **多 provider 支持较完整**  
   `pyproject.toml` 依赖 LiteLLM，并支持 OpenRouter、Anthropic、OpenAI、Gemini、DeepSeek 等配置。对于国内/海外不同 API 环境比较友好。

4. **MIT 许可证**  
   对个人研究、fork、二次集成都比较友好。

### 5.3 缺点与风险

1. **不是完整自动科研闭环**  
   它并没有像 AutoResearchClaw 那样提供明确的 idea->literature->experiment->analysis->paper state machine，也没有 ARIS 那样完整的 workflow skill graph。它更偏“论文产物 agent”。

2. **工程成熟度仍偏 alpha**  
   `pyproject.toml` classifier 是 Alpha；测试文件只有 6 个；未见 GitHub Actions CI；版本号存在元数据不一致。

3. **科学有效性要人工审查**  
   AI paper review、diagram generation、paper website generation 对科研表达有帮助，但不能证明方法正确、实验真实或引用可靠。

4. **图生成依赖外部服务**  
   PaperBanana/Gemini/Replicate/OpenRouter image generation 等路径可能涉及额外 API key、成本、服务稳定性和版权/复现问题。

### 5.4 适用范围

适合：

- 已有论文/PDF，需要生成图、网页、结构化 Markdown、review。
- 需要把科研 agent 的最终产物做得更好看、更可展示。
- 做“paper artifact generation agent”或“academic visual generation”方向研究。

不适合：

- 直接作为自动科研闭环主系统。
- 直接验证一个 idea 的 novelty、实验有效性、claim-grounding。

### 5.5 对你的匹配度

对 LLM/agent 博士生来说，ClawPhD 的价值是**工具模块价值高，主课题价值中等**。如果你研究自动科研 agent 的输出质量、图文生成、paper review 或学术表达自动化，它值得读；如果你研究“agent 如何提出假设并跑实验验证”，它不是第一选择。

建议你把它当作“产物层工具箱”，而不是自动科研系统主干。

## 6. ARIS / Auto-claude-code-research-in-sleep 详细分析

### 6.1 定位

ARIS 的核心不是一个传统软件包，而是一组面向 Claude Code/Codex/Cursor/Trae/Antigravity 等 agent 环境的科研 workflow skills。README 的口号是让 Claude Code 在你睡觉时做研究，`AGENT_GUIDE.md` 更准确地定义了它：**一个由 composable Markdown skills 组成的 research harness，通过 cross-model adversarial collaboration 编排 ML research lifecycle**。

核心结构：

- `skills/`：68 个顶层技能，包括 `research-pipeline`、`idea-discovery`、`experiment-bridge`、`auto-review-loop`、`paper-writing`、`citation-audit`、`rebuttal`、`research-wiki` 等。
- `skills/skills-codex/` 及 reviewer 变体：为 Codex CLI、Claude-review、Gemini-review 等提供适配。
- `mcp-servers/`：`llm-chat`、`minimax-chat`、`gemini-review`、`claude-review`、`codex-image2`、`feishu-bridge`。
- `tools/`：arXiv/Semantic Scholar/DeepXiv/Exa 搜索、实验队列、Research Wiki、审计验证、trace 保存等工具。

### 6.2 工作流机制

ARIS 的典型 full pipeline 是：

1. `/idea-discovery`：文献检索、idea 生成、novelty check、research review。
2. `/experiment-bridge`：读取实验计划，实现代码，cross-model code review，部署实验。
3. `/auto-review-loop`：评审、修改、复跑、再评审。
4. `/paper-writing`：paper-plan、paper-figure、paper-write、paper-compile、auto-paper-improvement-loop。
5. `/citation-audit`、`/paper-claim-audit`、`/proof-checker` 等作为提交前 audit gate。

它最重要的设计不是“有多少功能”，而是：

- Executor 和 Reviewer 分属不同模型家族。
- Reviewer independence：尽量让 reviewer 直接读文件，而不是读 executor 的二手总结。
- Artifact contracts：不同 skill 通过 `IDEA_REPORT.md`、`EXPERIMENT_PLAN.md`、`AUTO_REVIEW.md`、`NARRATIVE_REPORT.md`、`paper/main.tex` 等文件通信。
- Plain Markdown first：技能本身可读、可改、可做版本对比。

### 6.3 优点

1. **非常适合研究 workflow protocol**  
   对博士生来说，ARIS 是一个很好的“方法论语料库”。你可以直接研究它如何拆分科研流程、如何设计 reviewer、如何设 gate、如何处理 artifact handoff。

2. **Cross-model review 设计有研究价值**  
   ARIS 明确反对单模型 self-play，把 executor 和 reviewer 分离。这非常适合做对照实验：single-model self-reflection vs cross-model adversarial review。

3. **透明且可改**  
   大量逻辑写在 `SKILL.md`，不是深藏在框架内部。你可以很快改 protocol、加审计、调 effort、换模型。

4. **工具链丰富**  
   它已经覆盖 idea、literature、experiment、review、writing、rebuttal、overleaf、citation audit、wiki、meta-optimize 等完整科研生命周期。

5. **MIT 许可证，低集成阻力**  
   如果你要把其中的 workflow 思想迁移到自己的 agent 框架，许可证上较友好。

### 6.4 缺点与风险

1. **不是强类型/强约束的执行引擎**  
   ARIS 主要依赖 agent 读 `SKILL.md` 后执行。好处是灵活，坏处是执行一致性取决于模型、上下文、工具权限和运行环境。

2. **很难自动化回归测试 workflow 质量**  
   prompt/skill 的行为测试比普通函数测试难得多。仓库有一些工具/MCP 测试，但对完整 research pipeline 的科学质量并没有标准 CI。

3. **依赖复杂 agent 环境**  
   Claude Code、Codex MCP、各种 reviewer bridge、GPU/SSH、web search、Overleaf、Feishu 等能力都可能成为失败点。

4. **README 更新速度很快，宣传密度高**  
   项目活跃，但也意味着很多功能可能还在快速磨合。用于论文实验时必须锁 commit 和技能版本。

5. **“睡觉时科研”容易被误用**  
   它适合作为 research copilot，而不是无需审查的 paper factory。真正提交前仍需人工核验实验、引用、claim 和写作。

### 6.5 适用范围

适合：

- 研究自动科研 agent 的 workflow/protocol/skill design。
- 研究 cross-model critique、reviewer independence、artifact contracts。
- 快速把自己的科研项目接入自动化流程。
- 做 prompt engineering、agent workflow evaluation、human checkpoint policy 研究。

不适合：

- 需要一个完全 deterministic、可控、可复现实验平台。
- 不熟悉 Claude Code/Codex/MCP 的低成本新手用户。

### 6.6 对你的匹配度

ARIS 与你的背景高度匹配。它不是最“工程完备”的系统，但它最适合作为**研究 idea 的来源**。你可以从它抽象出很多可发表的问题：

- cross-model reviewer 是否真的降低 hallucination 和 local minima？
- artifact contract 能否提高 long-horizon agent 的恢复能力？
- auto-review-loop 的改进是否能被客观指标捕捉？
- 人类 checkpoint 应该放在哪些阶段最划算？

建议你把 ARIS 当作“科研 agent workflow 设计库”来读，而不是只把它当作现成工具。

## 7. Dr. Claw 详细分析

### 7.1 定位

Dr. Claw 是一个完整的 AI research workspace。README 称它是 full-stack research workspace，支持 Research Lab、Auto Research、100+ research skills、多 agent backend、OpenClaw integration、desktop app、npx 启动等。

从代码结构看，它是一个产品化应用：

- 前端：React 18、Vite、Tailwind、CodeMirror、xterm。
- 后端：Express、WebSocket、SQLite、auth、project/session/file/git/skills/routes。
- CLI/NPM：`dr-claw` 和 legacy alias `vibelab`。
- Desktop：Electron build、macOS/Windows installer workflow。
- Agent backends：Claude Code、Codex、Gemini、OpenRouter。
- Python harness：`agent-harness/cli_anything/drclaw`。

它还内置大量 skills，并集成 ARIS、Autoresearch、DeepScientist 等 tool packs。

### 7.2 核心机制

Dr. Claw 的 Research Lab pipeline 不是把科研逻辑硬编码在一个 Python state machine 里，而是：

1. 用户在 Chat 里描述课题。
2. `inno-pipeline-planner` 生成 `.pipeline/docs/research_brief.json` 和 `.pipeline/tasks/tasks.json`。
3. Research Lab 展示 Survey、Ideation、Experiment、Publication、Promotion 阶段。
4. Auto Research 顺序执行 tasks，调用 Claude/Codex/Gemini/OpenRouter。
5. 生成产物写入项目目录。

它的强项是“科研 agent 项目管理和执行界面”，不是单独某个 discovery algorithm。

### 7.3 优点

1. **产品化程度最高**  
   它有 NPM package、desktop app、CI、publish workflow、Electron release workflow。四个项目里它最像可给团队使用的工作台。

2. **多 agent backend 支持实际有价值**  
   支持 Claude Code、Codex、Gemini、OpenRouter，适合比较不同 agent executor 的行为。

3. **Research Lab 对 human-agent collaboration 很有用**  
   它把 research brief、task list、artifacts、project files、Git、skills、compute 放到一个界面里，适合长期项目管理。

4. **适合做 HCI / agent workspace 研究**  
   如果你的问题是“研究者如何监督和纠偏自动科研 agent”，Dr. Claw 是最好的起点。

5. **可作为其他系统的外壳**  
   它可以把 ARIS、Autoresearch、DeepScientist 等 tool packs 包进 Auto Research Hub。

### 7.4 缺点与风险

1. **不是最纯粹的自动科研算法库**  
   它更多是平台/工作台。科研能力很大程度来自内置或接入的 skills，而不是一个独立可评测的核心 agent algorithm。

2. **系统复杂度和攻击面大**  
   Web server、auth、database、file explorer、git、embedded terminal、agent permission、OpenRouter key、Auto Research bypass permissions 等都需要安全配置。

3. **许可证约束更强**  
   Dr. Claw 是 GPL-3.0 + AGPL-3.0 组合。学术研究没问题，但如果你要闭源商用或嵌入商业平台，需要谨慎。

4. **UI 可能掩盖科学质量问题**  
   更好的界面不代表更可靠的科研结论。对自动科研来说，UI 应该服务于 provenance、audit 和 human correction，而不是只展示流程跑完。

5. **依赖 native Node 模块**  
   `node-pty`、`better-sqlite3`、Electron 等可能增加安装成本，尤其在不同 OS 上。

### 7.5 适用范围

适合：

- 搭建个人/团队 AI research workspace。
- 做 human-agent interaction、agent observability、workflow dashboard、multi-agent backend comparison。
- 管理多个科研项目、长期 session、文件/Git/任务。

不适合：

- 直接作为“自动发现新科学知识”的核心算法基线。
- 只想轻量跑一条命令生成实验和论文的用户。

### 7.6 对你的匹配度

Dr. Claw 对你的匹配度取决于你的研究问题：

- 如果你研究 **agent workflow execution + HCI + observability**，匹配度很高。
- 如果你研究 **自动科研 agent 的 reasoning/evaluation/experiment validity**，它更适合作为平台层，而不是主模型。

建议你把 Dr. Claw 当作“研究者如何控制自动科研 agent”的平台，而不是把它和 AutoResearchClaw/ARIS 直接当作同一类系统比较。

## 8. AutoResearchClaw 详细分析

### 8.1 定位

AutoResearchClaw 是四个项目里最接近“从 idea 到 paper 的 code-native autonomous research pipeline”的系统。README 称其可以从一个 topic 生成论文、BibTeX、实验代码、图表、review、deliverables，并支持 full-auto 和 co-pilot。

本地代码结构显示，它确实有一个较完整的 Python 系统：

- `researchclaw/pipeline/stages.py`：定义 23 个 stage、gate、rollback、pivot/refine。
- `researchclaw/pipeline/runner.py`：checkpoint、heartbeat、resume、summary、diagnosis 等。
- `researchclaw/literature/`：arXiv、OpenAlex、Semantic Scholar、novelty、verify。
- `researchclaw/experiment/`：sandbox、docker、ssh、runner、validator、visualize。
- `researchclaw/hitl/`：SmartPause、cost guard、intervention、session、notify、learning。
- `researchclaw/metaclaw_bridge/`：lesson-to-skill、PRM gate、skill feedback。
- `researchclaw/templates/`：conference templates、LaTeX converter。
- `researchclaw/skills/builtin/`：domain、experiment、tooling skills。

### 8.2 23-stage pipeline

代码里的 stage sequence 是：

- Phase A: `TOPIC_INIT`, `PROBLEM_DECOMPOSE`
- Phase B: `SEARCH_STRATEGY`, `LITERATURE_COLLECT`, `LITERATURE_SCREEN`, `KNOWLEDGE_EXTRACT`
- Phase C: `SYNTHESIS`, `HYPOTHESIS_GEN`
- Phase D: `EXPERIMENT_DESIGN`, `CODE_GENERATION`, `RESOURCE_PLANNING`
- Phase E: `EXPERIMENT_RUN`, `ITERATIVE_REFINE`
- Phase F: `RESULT_ANALYSIS`, `RESEARCH_DECISION`
- Phase G: `PAPER_OUTLINE`, `PAPER_DRAFT`, `PEER_REVIEW`, `PAPER_REVISION`
- Phase H: `QUALITY_GATE`, `KNOWLEDGE_ARCHIVE`, `EXPORT_PUBLISH`, `CITATION_VERIFY`

Gate stages 是 5、9、20；Stage 15 支持 `pivot` 和 `refine` rollback；citation verification 被明确标为不能作为 noncritical skip。

这说明 AutoResearchClaw 至少在架构层面认真考虑了长流程 agent 的状态、回滚、暂停、恢复和失败处理。

### 8.3 优点

1. **最适合作为自动科研系统基线**  
   它有明确 stage machine、CLI、config、artifact directory、checkpoint、sandbox、literature、paper generation、citation verification。

2. **代码实体充分，便于二次开发和实验插桩**  
   363 个 Python 文件、96 个 test files，模块划分比 ARIS 更接近传统研究系统。你可以插入 evaluator、logger、ablation、cost tracker。

3. **实验真实性意识较强**  
   config 明确区分 `sandbox`、`docker`、`ssh_remote`、`simulated`；并提示 simulated 是假数据，仅用于框架开发调试。这个意识对自动科研很关键。

4. **HITL 设计适合研究**  
   full-auto、gate-only、checkpoint、step-by-step、co-pilot、自定义 stage policy 等模式，很适合做 human intervention budget 的研究。

5. **Claim/citation verification 是必要方向**  
   它内置 citation verification、verified registry、paper verifier 等模块。虽然效果需要验证，但方向是对的。

6. **MIT 许可证**  
   适合作为你自己系统的参考或 fork 基线。

### 8.4 缺点与风险

1. **宣传语非常强，需要严格实证验证**  
   “Chat an Idea. Get a Paper.” 容易被误解成论文工厂。真实科研质量必须由实验、引用、claim、novelty、人类专家审查共同验证。

2. **版本元数据滞后**  
   README 宣称 v0.4.0，但 Python package version 仍是 0.3.1。这对复现实验不是致命问题，但需要锁 commit。

3. **集成面很宽，失败点多**  
   OpenCode、Docker、SSH、ACP agent、OpenAlex、Semantic Scholar、arXiv、Gemini/Nano Banana、MetaClaw、Overleaf 等都可能导致运行不稳定。

4. **没有看到 GitHub Actions CI**  
   虽然 test files 多，但没有 `.github/workflows` 意味着外部贡献/主分支质量门槛需要额外确认。

5. **自动生成实验仍可能“形式正确但科学无效”**  
   例如 baseline 不合理、数据泄漏、metric 选择错误、toy dataset 不能支撑 claim、统计检验不充分。这是所有自动科研 agent 的核心风险。

6. **setup 成本最高**  
   真正运行强模式可能要 API、Docker/GPU、外部 agent CLI、LaTeX、网络文献检索。第一周不要试图直接 full-auto 生成完整 paper，应先跑 toy topic。

### 8.5 适用范围

适合：

- 做自动科研 agent 的系统论文、评测论文、failure analysis。
- 构建 idea->experiment->paper 的可执行 baseline。
- 研究 HITL policy、state machine orchestration、experiment repair、claim verification。
- 和 ARIS 做“skill-based vs code-native state-machine”对照。

不适合：

- 无审查地生成投稿论文。
- 完全不愿配置本地环境/API/GPU 的用户。
- 对每一步都要求 deterministic reproducibility 的严格科学计算场景，除非你额外加锁环境和审计。

### 8.6 对你的匹配度

AutoResearchClaw 是四个里最适合你深入研究的主对象。它足够复杂，有真实系统问题；又足够模块化，可以做 ablation。建议你围绕它建立自己的 benchmark harness，而不是只用它生成论文。

## 9. 按研究问题选型

| 你的目标 | 首选 | 次选 | 不建议作为主选 |
|---|---|---|---|
| 研究自动科研 agent 的端到端可靠性 | AutoResearchClaw | ARIS | ClawPhD |
| 研究 workflow/prompt/skill protocol | ARIS | AutoResearchClaw | Dr. Claw |
| 研究 cross-model reviewer 是否有效 | ARIS | AutoResearchClaw | ClawPhD |
| 研究 human-agent collaboration / 可观察性 | Dr. Claw | AutoResearchClaw | ClawPhD |
| 搭建个人科研自动化工作台 | Dr. Claw | ARIS | AutoResearchClaw |
| 自动生成论文图、网页、PDF 解析 | ClawPhD | ARIS paper-illustration | AutoResearchClaw |
| 做可控评测和 ablation | AutoResearchClaw | ARIS | Dr. Claw |
| 快速改 prompt 和研究 protocol | ARIS | ClawPhD | AutoResearchClaw |

## 10. 给你的具体建议

### 10.1 第一阶段：先拆解系统，不急着跑 full-auto

建议你先花 3-5 天读：

1. ARIS 的 `AGENT_GUIDE.md`、`skills/research-pipeline/SKILL.md`、`skills/experiment-bridge/SKILL.md`、`skills/paper-writing/SKILL.md`。
2. AutoResearchClaw 的 `researchclaw/pipeline/stages.py`、`runner.py`、`config.researchclaw.example.yaml`。
3. Dr. Claw 的 `skills/inno-pipeline-planner/SKILL.md`、`server/routes/auto-research.js`、`docs/pipeline-outputs.md`。
4. ClawPhD 的 `clawphd/agent/tools/paperbanana.py`、`pdf2md.py`、`paper_review.py`。

你会很快看到四种不同设计哲学：

- ARIS：prompt/skill protocol first。
- AutoResearchClaw：state machine first。
- Dr. Claw：workspace/UI/orchestration first。
- ClawPhD：academic artifact tools first。

### 10.2 第二阶段：做一个小型对照实验

选一个你熟悉且低成本的 LLM/agent toy topic，例如：

- “改进一个小型 RAG reranker 的消融实验”
- “比较两种 prompt compression 策略”
- “在公开小数据集上验证 agent planning memory 的简单假设”

对 ARIS 和 AutoResearchClaw 设置同样约束：

- 最大预算：例如 2 小时 wall-clock、固定 API budget、无远程 GPU或单 GPU。
- 固定文献源：只允许 arXiv + Semantic Scholar 前 N 篇。
- 固定实验规模：toy dataset + 2 baselines + 3 seeds。
- 固定输出：idea report、experiment plan、code、result summary、claim-evidence table、paper draft。

然后评估：

- idea 是否具体、可检验、非平凡？
- experiment code 是否真的实现了 claim？
- baseline 是否合理？
- metric 是否有数据泄漏或错用 ground truth？
- paper 中每个 numerical claim 是否能追溯到 raw result？
- citation 是否真实且支持上下文？
- human intervention 次数和总时间是多少？
- 失败后恢复能力如何？

### 10.3 第三阶段：形成博士研究问题

你可以从这些项目中抽出几个很好的研究方向：

1. **自动科研 agent 的评测基准**  
   不只评估最终论文分数，而是评估 idea novelty、实验真实性、claim-grounding、citation contextual correctness、human correction cost。

2. **Skill-based workflow vs state-machine workflow**  
   ARIS 代表技能协议，AutoResearchClaw 代表代码状态机。比较它们在 long-horizon reliability、recoverability、cost、透明度上的差异。

3. **Cross-model review 的有效性**  
   比较同模型 self-review、同家族不同模型 review、跨家族 adversarial review。ARIS 是很好的实验起点。

4. **Human checkpoint policy / SmartPause**  
   研究哪些阶段最值得人类介入：idea selection、experiment design、claim writing、citation audit、quality gate。

5. **自动科研 agent 的 provenance system**  
   设计一个统一 artifact graph：每个 claim 对应 evidence、code、run config、raw output、citation 和 reviewer decision。

6. **反论文工厂机制**  
   如何让自动科研 agent 默认输出“研究草稿 + 不确定性 + audit report”，而不是伪装成 submission-ready paper。

## 11. 最推荐的组合路线

如果你要做一个自己的自动科研 agent 原型，我建议采用：

- **主执行框架**：AutoResearchClaw 的 23-stage pipeline 思想。
- **工作流协议**：借鉴 ARIS 的 artifact contracts 和 reviewer independence。
- **交互层**：借鉴 Dr. Claw 的 Research Lab 和 task dashboard。
- **产物工具**：接入 ClawPhD 的 PDF->Markdown、figure generation、paper website、paper review。
- **你自己的贡献点**：统一 provenance/evaluation harness，明确衡量“自动科研是否可信”。

这样你的系统不会只是“又一个会写 paper 的 agent”，而是一个可以回答科学问题的研究平台：哪些自动化步骤可靠，哪些必须人类介入，哪些输出不能信。

## 12. 风险清单

使用这些库做科研时，必须警惕：

1. **引用真实不等于引用正确**  
   Paper 真实存在，但可能不支持当前上下文 claim。

2. **实验能跑不等于实验有效**  
   自动生成代码最危险的是 silent logic bug：错误 split、错误 label、错误 metric、baseline 不公平。

3. **Reviewer 分数不等于审稿质量**  
   LLM reviewer 会被措辞、summary、上下文污染影响。需要 fresh thread、直接读文件、隐藏 executor 自我解释。

4. **Paper draft 不是投稿稿**  
   自动生成稿件应视为 draft，需要作者人工确认贡献、实验、引用、伦理和 venue policy。

5. **权限与安全**  
   这些系统经常需要 shell、文件写入、联网、Docker、SSH、API key。务必在隔离环境中运行，避免把高权限 agent 接到敏感目录。

6. **许可证**  
   ClawPhD、ARIS、AutoResearchClaw 是 MIT；Dr. Claw 是 GPL/AGPL 组合。闭源产品化时尤其要注意 Dr. Claw。

## 13. 最终推荐

对你当前阶段，我建议：

1. **必读 ARIS**：它能给你最多 workflow/protocol 层面的灵感。
2. **重点复现 AutoResearchClaw**：它最适合成为你论文里的 baseline 或研究对象。
3. **选择性使用 Dr. Claw**：如果你想做交互系统、demo、团队工作台，它很有价值。
4. **把 ClawPhD 当工具箱**：用于图、网页、PDF 解析、AI review，不要把它当完整自动科研 agent。

如果你最终目标是发一篇关于 automated research agents 的论文，我会把题目收敛到：

> “Auditable Autonomous Research Agents: Evaluating Long-Horizon Scientific Workflows through Claim-Evidence Provenance and Cross-Model Review”

在这个题目下，ARIS 和 AutoResearchClaw 都是非常好的研究对象，Dr. Claw 可以作为交互层对照，ClawPhD 可以提供 artifact-generation 子模块。

## 14. 主要参考源

ClawPhD:

- README: <https://github.com/ZhihaoAIRobotic/ClawPhD/blob/main/README.md>
- `pyproject.toml`: <https://github.com/ZhihaoAIRobotic/ClawPhD/blob/main/pyproject.toml>
- `clawphd/cli/commands.py`: <https://github.com/ZhihaoAIRobotic/ClawPhD/blob/main/clawphd/cli/commands.py>

ARIS:

- README: <https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep/blob/main/README.md>
- `AGENT_GUIDE.md`: <https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep/blob/main/AGENT_GUIDE.md>
- `skills/research-pipeline/SKILL.md`: <https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep/blob/main/skills/research-pipeline/SKILL.md>
- `tools/install_aris.sh`: <https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep/blob/main/tools/install_aris.sh>

Dr. Claw:

- README: <https://github.com/OpenLAIR/dr-claw/blob/main/README.md>
- `package.json`: <https://github.com/OpenLAIR/dr-claw/blob/main/package.json>
- `docs/pipeline-outputs.md`: <https://github.com/OpenLAIR/dr-claw/blob/main/docs/pipeline-outputs.md>
- `server/routes/auto-research.js`: <https://github.com/OpenLAIR/dr-claw/blob/main/server/routes/auto-research.js>
- `skills/inno-pipeline-planner/SKILL.md`: <https://github.com/OpenLAIR/dr-claw/blob/main/skills/inno-pipeline-planner/SKILL.md>

AutoResearchClaw:

- README: <https://github.com/aiming-lab/AutoResearchClaw/blob/main/README.md>
- `pyproject.toml`: <https://github.com/aiming-lab/AutoResearchClaw/blob/main/pyproject.toml>
- `config.researchclaw.example.yaml`: <https://github.com/aiming-lab/AutoResearchClaw/blob/main/config.researchclaw.example.yaml>
- `researchclaw/pipeline/stages.py`: <https://github.com/aiming-lab/AutoResearchClaw/blob/main/researchclaw/pipeline/stages.py>
- `researchclaw/pipeline/runner.py`: <https://github.com/aiming-lab/AutoResearchClaw/blob/main/researchclaw/pipeline/runner.py>
