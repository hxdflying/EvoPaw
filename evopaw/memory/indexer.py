"""对话记忆写入 pipeline（pgvector）

💡【第21课·搜索记忆】每轮对话结束后异步触发，不阻塞主流程：
  asyncio.create_task(async_index_turn(...))

pipeline：
  1. extract_summary_and_tags — 调 LLM 提取一句话摘要 + 领域标签
  2. embed_texts              — 调 text-embedding-v3 向量化
  3. upsert_memory            — 写入 pgvector（ON CONFLICT DO NOTHING 幂等）

与 m3l21 演示版的主要区别：
  - db_dsn 作为参数传入（不读全局环境变量），方便测试和多实例部署
  - db_dsn 为空时静默跳过，不报错
  - DB 连接失败时只 log warning，不把异常传播给 Runner
  - 新增 _connect_db() 作为可 mock 的注入点
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# 通义 API 配置
_QWEN_API_KEY  = os.getenv("QWEN_API_KEY", "")
_EMBED_MODEL   = "text-embedding-v3"
_EMBED_DIM     = 1024
_EXTRACT_MODEL = "qwen3-max"

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


def _make_llm_client():
    """惰性创建 LLM client（避免模块导入时就初始化）"""
    from openai import OpenAI  # noqa: PLC0415
    return OpenAI(
        api_key  = _QWEN_API_KEY,
        base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )


# 模块级 client（惰性初始化）
_llm_client   = None
_embed_client = None


def _get_llm_client():
    global _llm_client
    if _llm_client is None:
        _llm_client = _make_llm_client()
    return _llm_client


def _get_embed_client():
    global _embed_client
    if _embed_client is None:
        # 💡 通义的 embedding API 与 chat API 共用同一 base_url（兼容 OpenAI 格式），
        #    无需单独客户端类，_make_llm_client() 对两者通用。
        _embed_client = _make_llm_client()
    return _embed_client


# ── Step 2：LLM 提取摘要 + 标签 ─────────────────────────────────


def extract_summary_and_tags(
    user_message: str,
    assistant_reply: str,
) -> tuple[str, list[str]]:
    """调 LLM 提取一句话摘要 + 领域标签。

    💡 核心点：每轮只调用一次模型；malformed JSON 时兜底，不抛异常
    """
    prompt = _EXTRACT_PROMPT.format(
        user_message    = user_message[:500],
        assistant_reply = assistant_reply[:500],
    )
    resp = _get_llm_client().chat.completions.create(
        model    = _EXTRACT_MODEL,
        messages = [{"role": "user", "content": prompt}],
        extra_body = {"enable_thinking": False},
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
    """批量向量化，返回 dim=1024 的 float[] 列表。"""
    if not texts:
        return []
    resp = _get_embed_client().embeddings.create(
        model      = _EMBED_MODEL,
        input      = texts,
        dimensions = _EMBED_DIM,
    )
    return [item.embedding for item in resp.data]


# ── Step 4：写入 pgvector ────────────────────────────────────────


def upsert_memory(conn: Any, record: dict[str, Any]) -> None:
    """写入一条记忆记录，id 相同时跳过（ON CONFLICT DO NOTHING 幂等）。

    💡 核心点：search_text = user_message + tags，供 GIN 全文索引使用
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

    💡 核心点：asyncio.create_task() 调用此函数，不阻塞主流程返回
    db_dsn 为空时静默跳过；DB 连接失败只 log，不抛异常。
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
    # 生成稳定幂等 id
    raw_id  = f"{session_id}_{turn_ts}_{user_message[:32]}"
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
            # 💡 str(list) 输出 "[0.1, 0.2, ...]"，与 pgvector 期望格式一致。
            #    若 embed_texts 返回 numpy array，需先调用 .tolist()。
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
