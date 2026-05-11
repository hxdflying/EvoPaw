"""对话记忆写入 pipeline（pgvector）。

每轮对话结束后通过 ``asyncio.create_task(async_index_turn(...))`` 异步触发，
不阻塞主流程：``extract_summary_and_tags → embed_texts → upsert_memory``。

设计要点：``db_dsn`` 通过参数注入便于测试 / 多实例；为空时静默跳过；
DB 异常只 log warning，不传播给 Runner。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from typing import Any, Mapping

from evopaw.memory._dashscope_clients import make_openai_client, resolved_extra_body
from evopaw.observability.metrics import record_llm_call
from evopaw.provider_runtime import ResolveError, ResolvedRuntime, resolve_runtime

logger = logging.getLogger(__name__)

# 通义 API 配置。启动期调用 configure_memory_runtime 后，模型名会被 roles.memory_*
# 解析结果覆盖；未配置 resolver 时保留环境变量 fallback。
_QWEN_API_KEY  = os.getenv("QWEN_API_KEY", "")
_EMBED_MODEL   = os.getenv("EVOPAW_MEMORY_EMBED_MODEL", "text-embedding-v3")
_EMBED_DIM     = int(os.getenv("EVOPAW_MEMORY_EMBED_DIM", "1024"))
_EXTRACT_MODEL = os.getenv("EVOPAW_MEMORY_EXTRACT_MODEL", "qwen3-max")

# resolver 注入的 runtime（启动期由 main.py 调一次 configure_memory_runtime 设置）。
_resolved_extract: ResolvedRuntime | None = None
_resolved_embed:   ResolvedRuntime | None = None


def configure_memory_runtime(app_config: Mapping) -> None:
    """启动期注入 app_config，使 indexer 通过 resolver 解析 model/base_url/api_key。

    未调用时模块沿用旧行为（env var + DashScope 硬编码端点），保持向后兼容。
    解析失败（ResolveError）的角色不阻断启动，仅 warn 并保留旧路径。

    memory_extract / memory_embedding 当前仅支持 OpenAI-compatible 端点。如果 resolver
    解析出其它 runtime_family，此处显式抛 ResolveError。
    """
    global _resolved_extract, _resolved_embed, _EXTRACT_MODEL, _EMBED_MODEL, _llm_client, _embed_client
    try:
        resolved = resolve_runtime("memory_extract", app_config)
    except ResolveError as e:
        logger.warning("memory_extract 角色解析失败，沿用旧路径：%s", e)
        _resolved_extract = None
    else:
        if resolved.runtime_family != "openai_chat":
            raise ResolveError(
                f"memory_extract 当前仅支持 openai_chat runtime_family（解析为 "
                f"provider={resolved.provider_id} family={resolved.runtime_family}）。"
                "如需其它 provider 接入，需先扩展 _dashscope_clients 的客户端构造逻辑。"
            )
        _resolved_extract = resolved
        _EXTRACT_MODEL = resolved.model

    try:
        resolved = resolve_runtime("memory_embedding", app_config)
    except ResolveError as e:
        logger.warning("memory_embedding 角色解析失败，沿用旧路径：%s", e)
        _resolved_embed = None
    else:
        if resolved.runtime_family != "openai_chat":
            raise ResolveError(
                f"memory_embedding 当前仅支持 openai_chat runtime_family（解析为 "
                f"provider={resolved.provider_id} family={resolved.runtime_family}）。"
                "如需其它 provider 接入，需先扩展 _dashscope_clients 的客户端构造逻辑。"
            )
        _resolved_embed = resolved
        _EMBED_MODEL = resolved.model

    # 失效模块级单例，下次调用时按新 runtime 重建
    _llm_client = None
    _embed_client = None
    logger.info(
        "indexer runtime configured: extract=%s embed=%s",
        _EXTRACT_MODEL, _EMBED_MODEL,
    )

_EXTRACT_PROMPT = """\
分析以下一轮对话，提取结构化信息，以 JSON 格式返回：

{{
  "summary": "一句话摘要，描述这轮对话做了什么（20字以内）",
  "tags": ["标签1", "标签2"]
}}

只返回 JSON，不要其他内容。

用户：{user_message}
助手：{assistant_reply}
"""


# ── 可测试的注入点 ───────────────────────────────────────────────


def _connect_db(db_dsn: str):
    """封装 DB 连接，方便 mock"""
    import psycopg2  # noqa: PLC0415
    return psycopg2.connect(db_dsn)


def _make_llm_client(resolved: ResolvedRuntime | None = None):
    """惰性创建 LLM client（避免模块导入时就初始化）。

    具体构造逻辑收敛在 `_dashscope_clients.make_openai_client`。
    """
    return make_openai_client(resolved)


# 模块级 client（惰性初始化）
_llm_client   = None
_embed_client = None


def _get_llm_client():
    global _llm_client
    if _llm_client is None:
        _llm_client = _make_llm_client(_resolved_extract)
    return _llm_client


def _get_embed_client():
    global _embed_client
    if _embed_client is None:
        # 通义 embedding 与 chat 共用 base_url 和 OpenAI 兼容格式，复用同一构造函数。
        _embed_client = _make_llm_client(_resolved_embed)
    return _embed_client


def shutdown_index_clients() -> None:
    """关闭模块级 LLM/embedding client；进程优雅退出时调用。

    - OpenAI Python SDK v1+ 内部使用 httpx 连接池；不显式 close 会在 GC
      时打印 ResourceWarning。
    - 关闭后将单例置 None，下次调用 _get_*_client() 会重新惰性创建，
      不影响在线热重载场景。
    """
    global _llm_client, _embed_client
    for name, client in (("_llm_client", _llm_client), ("_embed_client", _embed_client)):
        if client is None:
            continue
        try:
            client.close()
        except Exception:  # noqa: BLE001
            logger.debug("close %s failed", name, exc_info=True)
    _llm_client = None
    _embed_client = None


# ── Step 2：LLM 提取摘要 + 标签 ─────────────────────────────────


def extract_summary_and_tags(
    user_message: str,
    assistant_reply: str,
) -> tuple[str, list[str]]:
    """调 LLM 提取一句话摘要 + 领域标签。

    每轮只调用一次模型；malformed JSON 时兜底为 ``(user[:50], [])``，不抛异常。
    成功路径通过 ``record_llm_call`` 上报到 metrics；调用方 ``_index_single_turn``
    用 try/except 兜底，HTTP 异常会传播出去并在那里 warn——故失败打点交给上层。
    """
    started_at = time.monotonic()
    provider_id    = _resolved_extract.provider_id    if _resolved_extract else "dashscope"
    runtime_family = _resolved_extract.runtime_family if _resolved_extract else "openai_chat"

    prompt = _EXTRACT_PROMPT.format(
        user_message    = user_message[:500],
        assistant_reply = assistant_reply[:500],
    )
    extra_body = resolved_extra_body(_resolved_extract)
    try:
        resp = _get_llm_client().chat.completions.create(
            model    = _EXTRACT_MODEL,
            messages = [{"role": "user", "content": prompt}],
            extra_body = extra_body,
        )
    except Exception:
        _record_memory_metric(
            provider_id, runtime_family, "memory_extract", "error", started_at,
        )
        raise

    usage = getattr(resp, "usage", None)
    _record_memory_metric(
        provider_id, runtime_family, "memory_extract", "success", started_at,
        input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0,
        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0,
    )

    raw = resp.choices[0].message.content.strip()

    # 剥离 markdown 代码块
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        data = json.loads(raw)
        return data.get("summary", ""), data.get("tags", [])
    except json.JSONDecodeError:
        return user_message[:50], []


# ── Step 3：向量化 ───────────────────────────────────────────────


def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量向量化，返回 dim=1024 的 float[] 列表。

    成功 / 失败两路均通过 ``record_llm_call`` 上报。embedding 接口
    output_tokens 恒为 0，token 用量记到 input_tokens（与 chat 调用对齐）。
    """
    if not texts:
        return []
    started_at = time.monotonic()
    provider_id    = _resolved_embed.provider_id    if _resolved_embed else "dashscope"
    runtime_family = _resolved_embed.runtime_family if _resolved_embed else "openai_chat"

    try:
        resp = _get_embed_client().embeddings.create(
            model      = _EMBED_MODEL,
            input      = texts,
            dimensions = _EMBED_DIM,
        )
    except Exception:
        _record_memory_metric(
            provider_id, runtime_family, "memory_embedding", "error", started_at,
        )
        raise

    usage = getattr(resp, "usage", None)
    _record_memory_metric(
        provider_id, runtime_family, "memory_embedding", "success", started_at,
        input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0,
    )
    return [item.embedding for item in resp.data]


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


# ── Step 4：写入 pgvector ────────────────────────────────────────


def upsert_memory(conn: Any, record: dict[str, Any]) -> None:
    """写入一条记忆记录，id 相同时跳过（ON CONFLICT DO NOTHING 幂等）。

    ``search_text = user_message + tags``，供 GIN 全文索引使用。
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO memories (
                id, session_id, routing_key,
                user_message, assistant_reply,
                summary, tags,
                turn_ts,
                summary_vec, message_vec,
                search_text
            ) VALUES (
                %(id)s, %(session_id)s, %(routing_key)s,
                %(user_message)s, %(assistant_reply)s,
                %(summary)s, %(tags)s,
                %(turn_ts)s,
                %(summary_vec)s::vector, %(message_vec)s::vector,
                %(search_text)s
            )
            ON CONFLICT (id) DO NOTHING
            """,
            {
                **record,
                "summary_vec": str(record["summary_vec"]),
                "message_vec": str(record["message_vec"]),
            },
        )
    conn.commit()


# ── 主入口：异步单轮建索引 ───────────────────────────────────────


async def async_index_turn(
    session_id:      str,
    routing_key:     str,
    user_message:    str,
    assistant_reply: str,
    turn_ts:         int,
    db_dsn:          str,
) -> None:
    """每轮对话结束后后台触发的异步索引入口。

    ``asyncio.create_task()`` 调用，不阻塞主流程；``db_dsn`` 为空静默跳过；
    DB 连接失败只 log warning，不抛异常。
    """
    if not db_dsn:
        return  # db_dsn 未配置，静默跳过

    await asyncio.get_running_loop().run_in_executor(
        None,
        _index_single_turn,
        session_id, routing_key, user_message, assistant_reply, turn_ts, db_dsn,
    )


def _index_single_turn(
    session_id:      str,
    routing_key:     str,
    user_message:    str,
    assistant_reply: str,
    turn_ts:         int,
    db_dsn:          str,
) -> None:
    """同步内核，在 run_in_executor 线程池中运行。"""
    # 生成稳定幂等 id：hash 完整 user_message 而不是前 32 字，避免长前缀相同
    # 的两条消息（"复习一下今天的会议纪要 1"/"...2"）共用 turn_id 被
    # ON CONFLICT DO NOTHING 静默丢弃。
    raw_id  = f"{session_id}_{turn_ts}_{user_message}"
    turn_id = hashlib.sha256(raw_id.encode()).hexdigest()[:16]

    conn = None
    try:
        conn = _connect_db(db_dsn)

        summary, tags    = extract_summary_and_tags(user_message, assistant_reply)
        message_text     = f"用户：{user_message}\n助手：{assistant_reply}"
        vecs             = embed_texts([summary, message_text])
        search_text      = user_message + " " + " ".join(tags)

        upsert_memory(conn, {
            "id":              turn_id,
            "session_id":      session_id,
            "routing_key":     routing_key,
            "user_message":    user_message,
            "assistant_reply": assistant_reply,
            "summary":         summary,
            "tags":            tags,
            "turn_ts":         turn_ts,
            # str(list) 与 pgvector 期望格式一致；如改用 numpy array 需 .tolist()。
            "summary_vec":     vecs[0] if vecs else [],
            "message_vec":     vecs[1] if len(vecs) > 1 else [],
            "search_text":     search_text,
        })
    except Exception:
        logger.warning(
            "async_index_turn failed for session=%s turn_ts=%s",
            session_id, turn_ts, exc_info=True,
        )
    finally:
        if conn is not None:
            conn.close()
