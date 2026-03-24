"""上下文生命周期管理

💡【第19课·三把剪刀】Context 不加控制会无限膨胀，拖慢推理、撑爆 token 限制。
三种手术刀各司其职：

1. prune_tool_results  — 剪枝：把过时的 tool result 替换为占位符
   场景：tool result 通常是大段 JSON/HTML，但 agent 早已不需要
   策略：保留最近 keep_turns 轮，更早的内容替换为 [已剪枝]

2. chunk_by_tokens     — 分块：将消息列表按近似 token 数切片
   用于 maybe_compress 内部，每块独立压缩

3. maybe_compress      — 压缩：超过 context 使用率阈值时，把旧消息摘要化
   场景：长时间对话，即使剪枝后还是太长
   策略：保留 system 消息 + 最近 fresh_keep_turns 轮，旧消息分块摘要

4. ctx.json 持久化    — 跨 session 恢复：save / load / append_raw
   save_session_ctx   → 覆盖写 {session_id}_ctx.json（压缩快照）
   load_session_ctx   → 读取，不存在时返回 []
   append_session_raw → 追加写 {session_id}_raw.jsonl（完整审计日志）
"""

from __future__ import annotations

import datetime
import json
import logging
from datetime import timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from crewai.hooks import LLMCallHookContext

logger = logging.getLogger(__name__)

# 默认参数（与 m3l19 演示代码保持一致）
_PRUNE_KEEP_TURNS   = 10
_CHUNK_TOKENS       = 2000
_FRESH_KEEP_TURNS   = 10
_COMPRESS_THRESHOLD = 0.45
_MODEL_CTX_LIMIT    = 32000


# ─────────────────────────────────────────────────────────────────
# 1. 剪枝
# ─────────────────────────────────────────────────────────────────


def prune_tool_results(
    messages: list[dict],
    keep_turns: int = _PRUNE_KEEP_TURNS,
) -> None:
    """in-place 剪枝：超出 keep_turns 的 tool 消息内容替换为 [已剪枝]。

    💡 为什么保留占位而不直接删除消息：
    tool_call_id 链路必须完整（tool 消息 id 对应 assistant 的 tool_calls）。
    直接删除会导致 OpenAI/Qwen 格式校验报错，保留消息结构但清空内容更安全。

    Args:
        messages: LLM 消息列表（in-place 修改）
        keep_turns: 保留最近 N 轮的完整 tool result
    """
    user_indices = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    if len(user_indices) <= keep_turns:
        return  # 轮数不足，无需剪枝

    # 保留点：倒数第 keep_turns 个 user 消息之前的 tool 消息全部剪枝
    cutoff_idx = user_indices[-keep_turns]
    for i in range(cutoff_idx):
        if messages[i].get("role") == "tool":
            messages[i]["content"] = "[已剪枝]"


# ─────────────────────────────────────────────────────────────────
# 2. 分块
# ─────────────────────────────────────────────────────────────────


def chunk_by_tokens(
    messages: list[dict],
    chunk_tokens: int = _CHUNK_TOKENS,
) -> list[list[dict]]:
    """按近似 token 数切分消息列表。

    💡 token 估算：中文 1 字 ≈ 1 token，英文 4 字 ≈ 1 token，
    取保守值 len(content) // 2。宁多算不少算，防止实际超限。

    单条消息超过 chunk_tokens 时独立成一个 chunk（不截断消息内容）。
    """
    if not messages:
        return []

    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_tokens = 0

    for msg in messages:
        msg_tokens = len(str(msg.get("content", ""))) // 2
        if current_tokens + msg_tokens > chunk_tokens and current:
            chunks.append(current)
            current = [msg]
            current_tokens = msg_tokens
        else:
            current.append(msg)
            current_tokens += msg_tokens

    if current:
        chunks.append(current)

    return chunks


# ─────────────────────────────────────────────────────────────────
# 3. 压缩
# ─────────────────────────────────────────────────────────────────


_SUMMARY_PROMPT = """\
将以下对话历史压缩为结构化摘要，只保留关键信息：
1. 用户目标：这段对话要完成什么
2. 关键事实：重要的结论、文件路径、操作结果
3. 未完成事项：尚未完成的任务（如有）

禁止包含：中间过程、失败尝试、重复内容。

对话历史：
{history}
"""


def _summarize_chunk(messages: list[dict]) -> str:
    """用轻量模型生成一段历史的摘要。可在测试中 mock。"""
    try:
        from crewai import LLM  # noqa: PLC0415
        summary_llm = LLM(model="qwen3-turbo")
        history = "\n".join(
            f"{m.get('role', '')}: {str(m.get('content', ''))[:300]}"
            for m in messages
        )
        return summary_llm.call([
            {"role": "user", "content": _SUMMARY_PROMPT.format(history=history)}
        ])
    except Exception:  # noqa: BLE001
        # 💡 宽泛捕获是设计决策：任何 LLM 调用失败都不应阻塞压缩流程。
        #    压缩是优化，不是必要条件；降级为占位符比让整个对话崩溃要好。
        logger.warning("_summarize_chunk failed, using fallback", exc_info=True)
        return "[压缩失败，内容省略]"


def maybe_compress(
    messages: list[dict],
    context: "LLMCallHookContext",
    fresh_keep_turns: int = _FRESH_KEEP_TURNS,
    chunk_tokens: int = _CHUNK_TOKENS,
    compress_threshold: float = _COMPRESS_THRESHOLD,
) -> None:
    """in-place 压缩：超过 context 使用率阈值时，将旧消息摘要化。

    💡 触发条件：approx_tokens / model_ctx_limit > compress_threshold
    保留策略：system 消息全保留 + 最近 fresh_keep_turns 轮原文 + 旧消息变摘要

    Args:
        messages: LLM 消息列表（in-place 修改）
        context: LLMCallHookContext，用于读取 llm.context_window_size
        fresh_keep_turns: 压缩时保留最近 N 轮不压缩
        chunk_tokens: 每个压缩块的近似 token 数
        compress_threshold: 上下文使用率超过此值才触发
    """
    model_limit = getattr(
        getattr(context, "llm", None), "context_window_size", _MODEL_CTX_LIMIT
    )
    # 💡 只统计非 system 消息的 token。
    #    system 消息（含压缩后插入的 <context_summary>）由框架/本函数自行控制，
    #    若一并计入阈值，每轮压缩后插入的摘要 system 消息会累积，导致下轮更快触发压缩——"雪崩效应"。
    non_system = [m for m in messages if m.get("role") != "system"]
    approx_tokens = sum(len(str(m.get("content", ""))) // 2 for m in non_system)
    if approx_tokens / model_limit < compress_threshold:
        return  # 未超阈值，不压缩

    system_msgs = [m for m in messages if m.get("role") == "system"]
    # non_system 已在上方计算，此处复用

    user_indices = [i for i, m in enumerate(non_system) if m.get("role") == "user"]
    if len(user_indices) <= fresh_keep_turns:
        return  # 轮数不足，无法划分"新鲜区"，跳过

    cutoff     = user_indices[-fresh_keep_turns]
    old_msgs   = non_system[:cutoff]
    fresh_msgs = non_system[cutoff:]

    # 分块摘要
    chunks = chunk_by_tokens(old_msgs, chunk_tokens)
    summary_msgs = [
        {
            "role":    "system",
            "content": f"<context_summary>\n{_summarize_chunk(chunk)}\n</context_summary>",
        }
        for chunk in chunks
    ]

    messages.clear()
    messages.extend(system_msgs + summary_msgs + fresh_msgs)


# ─────────────────────────────────────────────────────────────────
# 4. ctx.json 持久化
# ─────────────────────────────────────────────────────────────────


def load_session_ctx(session_id: str, ctx_dir: Path) -> list[dict]:
    """读取压缩 context 快照；文件不存在时返回空列表。"""
    p = ctx_dir / f"{session_id}_ctx.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def save_session_ctx(session_id: str, messages: list[dict], ctx_dir: Path) -> None:
    """覆盖写入当前压缩 context 快照。"""
    ctx_dir.mkdir(parents=True, exist_ok=True)
    (ctx_dir / f"{session_id}_ctx.json").write_text(
        json.dumps(messages, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_session_raw(
    session_id: str,
    messages: list[dict],
    ctx_dir: Path,
) -> None:
    """追加消息到原始完整历史（append-only JSONL，保留所有中间过程）。

    💡 两份存储职责分工：
    - ctx.json   → 压缩后快照，用于跨 session 快速恢复
    - raw.jsonl  → 完整审计日志，用于 debug 和分析
    """
    if not messages:
        return
    ctx_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now(tz=timezone.utc).isoformat()
    with open(ctx_dir / f"{session_id}_raw.jsonl", "a", encoding="utf-8") as f:
        for msg in messages:
            record = {**msg, "ts": ts}
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
