"""上下文生命周期管理：剪枝 / 分块 / 压缩 + ctx.json 持久化。"""

from __future__ import annotations

import datetime
import json
import logging
import time
from datetime import timezone
from pathlib import Path
import os
from typing import Mapping

from evopaw.memory._dashscope_clients import make_openai_client, resolved_extra_body
from evopaw.observability.metrics import record_llm_call
from evopaw.provider_runtime import ResolveError, ResolvedRuntime, resolve_runtime

logger = logging.getLogger(__name__)

# 默认参数
# _MODEL_CTX_LIMIT 对齐现役主 Agent 模型（Claude Sonnet 4.6 / Haiku 4.5 均为 200k）。
# 用 Opus 4.7（1M）时本阈值仍按 200k 计算，超过 90k token 时触发摘要——对长对话仍有意义。
_PRUNE_KEEP_TURNS   = 10
_CHUNK_TOKENS       = 2000
_FRESH_KEEP_TURNS   = 10
_COMPRESS_THRESHOLD = 0.45
_MODEL_CTX_LIMIT    = 200000


# ─────────────────────────────────────────────────────────────────
# 1. 剪枝
# ─────────────────────────────────────────────────────────────────


def prune_tool_results(
    messages: list[dict],
    keep_turns: int = _PRUNE_KEEP_TURNS,
) -> None:
    """in-place 剪枝：超出 keep_turns 的 tool 消息内容替换为 [已剪枝]。

    保留占位而不直接删除：tool_call_id 链路必须完整（tool 消息 id 对应
    assistant 的 tool_calls），直接删除会导致 OpenAI/Qwen 格式校验报错。
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

    Token 估算用 ``len(content) // 2`` 保守值（中文 1 字≈1 token、英文 4 字≈1 token）。
    单条消息超过 chunk_tokens 时独立成一个 chunk，不截断内容。
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

# 摘要 LLM 模型名通过环境变量 fallback（m-7），运维可在不改源码情况下切换。
# resolver 接入后（configure_memory_runtime 被 main.py 调用），此值会被覆盖为 roles.memory_summary.model。
_SUMMARY_MODEL = os.getenv("EVOPAW_MEMORY_SUMMARY_MODEL", "qwen3-turbo")

# resolver 注入的 runtime（启动期由 main.py 调一次 configure_memory_runtime 设置）。
# 为 None 时走旧的「环境变量 + DashScope 硬编码 base_url」路径，保持向后兼容。
_resolved_summary: ResolvedRuntime | None = None


def configure_memory_runtime(app_config: Mapping) -> None:
    """启动期注入 app_config，使 _summarize_chunk 通过 resolver 解析 model/base_url/api_key。

    未调用时模块沿用旧行为（env var + DashScope 硬编码端点），保持向后兼容。

    memory_summary 当前仅支持 OpenAI-compatible chat completions 端点。如果 resolver
    解析出其它 runtime_family，此处显式抛 ResolveError。
    """
    global _resolved_summary, _SUMMARY_MODEL
    try:
        resolved = resolve_runtime("memory_summary", app_config)
    except ResolveError as e:
        logger.warning("memory_summary 角色解析失败，沿用旧路径：%s", e)
        _resolved_summary = None
        return
    if resolved.runtime_family != "openai_chat":
        raise ResolveError(
            f"memory_summary 当前仅支持 openai_chat runtime_family（解析为 "
            f"provider={resolved.provider_id} family={resolved.runtime_family}）。"
            "如需其它 provider 接入，需先扩展 _dashscope_clients 的客户端构造逻辑。"
        )
    _resolved_summary = resolved
    _SUMMARY_MODEL = resolved.model
    logger.info(
        "memory_summary resolved: provider=%s model=%s",
        resolved.provider_id, resolved.model,
    )


def _make_summary_client():
    """创建摘要用的 LLM client（OpenAI 兼容格式，通义 DashScope 默认）。

    具体构造逻辑收敛在 `_dashscope_clients.make_openai_client`。
    """
    return make_openai_client(_resolved_summary)


def _summarize_chunk(messages: list[dict]) -> str:
    """用轻量模型生成一段历史的摘要。可在测试中 mock。

    成功 / 失败两路都通过 ``record_llm_call`` 上报到 metrics（role=memory_summary）。
    """
    started_at = time.monotonic()
    provider_id    = _resolved_summary.provider_id    if _resolved_summary else "dashscope"
    runtime_family = _resolved_summary.runtime_family if _resolved_summary else "openai_chat"

    try:
        client = _make_summary_client()
        history = "\n".join(
            f"{m.get('role', '')}: {str(m.get('content', ''))[:300]}"
            for m in messages
        )
        extra_body = resolved_extra_body(_resolved_summary)
        resp = client.chat.completions.create(
            model=_SUMMARY_MODEL,
            messages=[
                {"role": "user", "content": _SUMMARY_PROMPT.format(history=history)},
            ],
            extra_body=extra_body,
        )
        usage = getattr(resp, "usage", None)
        _record_memory_metric(
            provider_id, runtime_family, "memory_summary", "success",
            started_at,
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0,
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0,
        )
        return resp.choices[0].message.content.strip()
    except Exception:  # noqa: BLE001
        logger.warning("_summarize_chunk failed, using fallback", exc_info=True)
        _record_memory_metric(
            provider_id, runtime_family, "memory_summary", "error", started_at,
        )
        return "[压缩失败，内容省略]"


def _record_memory_metric(
    provider_id: str,
    runtime_family: str,
    role: str,
    outcome: str,
    started_at: float,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    """对齐 backend 层 `_record` 的语义；失败仅 warn，不抛。"""
    try:
        record_llm_call(
            provider_id=provider_id,
            runtime_family=runtime_family,
            role=role,
            outcome=outcome,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_seconds=time.monotonic() - started_at,
        )
    except Exception:  # noqa: BLE001
        logger.warning("record_llm_call failed for role=%s", role, exc_info=True)


def maybe_compress(
    messages: list[dict],
    model_ctx_limit: int = _MODEL_CTX_LIMIT,
    fresh_keep_turns: int = _FRESH_KEEP_TURNS,
    chunk_tokens: int = _CHUNK_TOKENS,
    compress_threshold: float = _COMPRESS_THRESHOLD,
) -> None:
    """in-place 压缩：超过 context 使用率阈值时，将旧消息摘要化。

    触发条件：``approx_tokens / model_ctx_limit > compress_threshold``。
    保留策略：system 消息全保留 + 最近 fresh_keep_turns 轮原文 + 旧消息变摘要。
    """
    model_limit = model_ctx_limit
    # 只统计非 system 消息的 token。压缩后插入的 <context_summary> system 消息
    # 若一并计入阈值，会逐轮累积导致更快触发压缩（雪崩）。
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

    两份存储分工：``ctx.json`` 为压缩快照，``raw.jsonl`` 为完整审计日志。
    """
    if not messages:
        return
    ctx_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now(tz=timezone.utc).isoformat()
    with open(ctx_dir / f"{session_id}_raw.jsonl", "a", encoding="utf-8") as f:
        for msg in messages:
            record = {**msg, "ts": ts}
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
