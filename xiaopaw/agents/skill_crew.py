"""Sub-Crew 工厂 — XiaoPaw 任务型 Skill 执行层

💡【第03课·上下文隔离】Sub-Crew 是 XiaoPaw 实现 Multi-Agent 上下文隔离的核心机制：
   - 主 Crew 的历史对话不传入 Sub-Crew（Sub-Crew 只看到当前任务指令）
   - Sub-Crew 的执行过程不污染主 Crew（主 Crew 只看到 Sub-Crew 的摘要输出）
   - 这就是课程中"Agent 的数字化职能部门"理念的工程实现：
     主 Agent = PMO（项目管理），Sub-Agent = 各职能部门执行具体工作

💡【第14课·MCP 协议】Sub-Crew 通过 MCPServerHTTP 接入 AIO-Sandbox，
   开放全部 MCP 工具（无白名单过滤）

每次 SkillLoaderTool 触发任务型 Skill 时，调用 build_skill_crew() 构建
一个全新的 Sub-Crew 实例，在 AIO-Sandbox 中执行 Skill 逻辑。

设计要点：
- 每次调用返回新实例，防止 CrewAI 内部状态污染
- Sub-Crew 不注入 step_callback（verbose 只推主 Agent 的推理，避免话题噪音）
- session 工作目录和用户 routing_key 通过 SkillLoaderTool 的 sandbox_execution_directive 注入，不经过主 LLM
- 💡【第07课·人设工程】Agent 的 role/goal/backstory 从 agents.yaml 加载，
  运行时占位符（skill_name/session_dir/skill_instructions）由 _format_cfg() 替换——
  与主 Crew 保持一致的 YAML+Python 分离惯例
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from crewai import Agent, Crew, Process, Task
from crewai.mcp import MCPServerHTTP

from xiaopaw.llm.aliyun_llm import AliyunLLM

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parent / "config"

# ── AIO-Sandbox MCP 配置 ────────────────────────────────────────────────────

# 默认端口：sandbox-docker-compose.yaml 映射 8022:8080
_DEFAULT_SANDBOX_MCP_URL = "http://localhost:8022/mcp"


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _format_cfg(cfg: dict, **kwargs) -> dict:
    """对配置字典中的字符串值做 Python format 替换，非字符串值原样保留。"""
    return {k: v.format(**kwargs) if isinstance(v, str) else v for k, v in cfg.items()}


def build_skill_crew(
    skill_name: str,
    skill_instructions: str,
    session_id: str = "",
    sandbox_mcp_url: str = _DEFAULT_SANDBOX_MCP_URL,
    sub_agent_model: str = "qwen3-max",
    max_iter: int = 20,
) -> Crew:
    """
    Sub-Crew 工厂：为指定 Skill 构建一个在 AIO-Sandbox 中执行的独立 Crew。

    Args:
        skill_name: Skill 名称，用于 Agent role 和日志
        skill_instructions: 完整 SKILL.md 正文（已剥离 frontmatter + 注入沙盒路径指令）
        session_id: 当前会话 ID，Agent 使用此 ID 确定沙盒工作目录
        sandbox_mcp_url: AIO-Sandbox MCP 端点 URL
        sub_agent_model: Sub-Agent 使用的 LLM 模型名
        max_iter: Sub-Agent 最大迭代次数，防止无限循环

    Returns:
        已配置好的 Crew 实例，可直接调用 kickoff() / akickoff()
    """
    # 💡【第14课·MCP 接入】MCPServerHTTP 是 CrewAI 原生 MCP 接入方式
    # framework 自动将 MCP 工具转换为 Agent 可用工具，无需手动封装
    # 💡【第03课·工厂模式】每次构建新实例 → 新的 MCP 连接 → 状态完全隔离
    sandbox_mcp = MCPServerHTTP(
        url=sandbox_mcp_url,
    )

    skill_llm = AliyunLLM(model=sub_agent_model, region="cn", temperature=0.3)

    session_dir = (
        f"/workspace/sessions/{session_id}" if session_id else "/workspace/sessions/<session_id>"
    )

    agents_cfg = _load_yaml(_CONFIG_DIR / "agents.yaml")
    tasks_cfg = _load_yaml(_CONFIG_DIR / "tasks.yaml")

    # 💡【第07课·动态人设】skill_name_upper 供 role 用，skill_name 供 goal/backstory 用
    # skill_instructions 已由 _get_skill_instructions() 转义 {var} → {{var}}，
    # Python format 时其内容作为 VALUE 传入，不会被二次解析
    agent_fmt_vars = dict(
        skill_name=skill_name,
        skill_name_upper=skill_name.upper(),
        session_dir=session_dir,
        skill_instructions=skill_instructions,
    )
    skill_agent_cfg = _format_cfg(dict(agents_cfg["skill_agent"]), **agent_fmt_vars)
    # max_iter 是运行时参数，不在 YAML 中定义，直接注入
    skill_agent_cfg["max_iter"] = max_iter

    # 💡【第07课·Agent 三要素】role/goal/backstory 从 YAML 加载，工具/LLM 绑定在 Python 层
    skill_agent = Agent(
        **skill_agent_cfg,
        llm=skill_llm,
        # 💡【第14课·MCP 接入】mcps 参数接收 MCPServerHTTP 列表，框架自动管理连接
        mcps=[sandbox_mcp],
        verbose=True,
    )

    # 💡【第08课·Task 契约】description/expected_output 从 YAML 加载
    # {{task_context}} 经 Python format 变为 {task_context}，
    # 由 SkillLoaderTool 通过 akickoff(inputs={"task_context": ...}) 注入
    skill_task_cfg = _format_cfg(dict(tasks_cfg["skill_task"]), session_dir=session_dir)

    # 💡【第08课·Task 契约两要素】description 明确执行环境约束，expected_output 定义 JSON 格式
    # 注意：{task_context} 由 akickoff(inputs=...) 显式注入（第09课·显式上下文传递）
    skill_task = Task(
        **skill_task_cfg,
        agent=skill_agent,
    )

    # 💡【第09课·Sequential Process】Sub-Crew 同样使用顺序执行
    # 单 Agent 单 Task 天然顺序，此处显式声明让代码意图清晰
    # 注意：Sub-Crew 不传入 step_callback——Sub-Crew 的推理过程不推送到飞书，
    # 避免在 verbose 模式下产生对用户来说难以理解的底层执行噪音
    return Crew(
        agents=[skill_agent],
        tasks=[skill_task],
        process=Process.sequential,
        verbose=True,
    )
