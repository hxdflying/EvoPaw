> ⚠️ 归档：CrewAI / AIO-Sandbox 时代的集成测试

这些测试引用已移除的 `evopaw.agents.main_crew` 模块，或依赖 AIO-Sandbox
（`localhost:8022`）这一不再存在的基础设施。

归档日期：2026-04-22（按 `docs/redundancy-audit-2026-04-21.md` 重排清单 #5 处理）

## 归档文件

- `test_course22_cases.py` — 第 22 课演示 Case，全文以 AIO-Sandbox 为前提
- `test_lesson22_cases.py` — 第 22 课预置场景，依赖 `main_crew.build_agent_fn`
- `test_file_pipeline.py` — 依赖 `pipeline_client` fixture 与 `main_crew` 和 `sandbox_available`，当前无法运行

## 如何重用

这些测试仍描述了有价值的场景（第 22 课记忆链路、文件上传全链路等）。
若要重写为当前架构下的测试，参考：

- `evopaw/agents/main_agent.py`（当前主 Agent 入口）
- `tests/integration/conftest.py` 中的 `memory_client` / `memory_client_pgvector` fixture
- `docs/message-flow.md`（当前消息流完整说明）

**不要**试图就地修复归档测试——它们只是场景参考资料，不属于现役测试套件。
