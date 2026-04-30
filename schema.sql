--  pgvector 数据库 schema
-- 运行前先启动：docker compose -f pgvector-docker-compose.yaml up -d
-- 向量作为普通列存储，和标量字段在同一张表内。

CREATE EXTENSION IF NOT EXISTS vector;  -- 启用 pgvector 扩展

-- ─────────────────────────────────────────────────────────────────────────────
-- 记忆主表
-- 每一行 = XiaoPaw 一轮对话（user + assistant 一问一答）
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS memories (
    id              TEXT        PRIMARY KEY,           -- SHA-256 幂等 id（session_id + turn_ts + user_msg 前32字）
    session_id      TEXT        NOT NULL,              -- 来源 session
    routing_key     TEXT        NOT NULL,              -- 用户标识（p2p:ou_xxx / group:oc_xxx）

    -- 原始内容
    user_message    TEXT        NOT NULL,              -- 用户原始消息
    assistant_reply TEXT        NOT NULL,              -- 助手回复

    -- LLM 提取的结构化字段（qwen3-max 提取）
    summary         TEXT        NOT NULL,              -- 一句话摘要（向量化）
    tags            TEXT[]      NOT NULL DEFAULT '{}', -- 领域标签（标量过滤用）

    -- 时间元数据
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    turn_ts         BIGINT      NOT NULL,              -- 原始对话时间戳（毫秒）

    -- 向量列
    summary_vec     vector(1024),                      -- 摘要语义向量（text-embedding-v3 dim=1024）
    message_vec     vector(1024),                      -- 原始对话语义向量

    -- 全文搜索列（BM25 近似，PostgreSQL 内置 GIN 索引）
    search_text     TEXT        NOT NULL DEFAULT '',   -- user_message + tags 拼接，供 LIKE / tsv 检索
    search_tsv      TSVECTOR    GENERATED ALWAYS AS (to_tsvector('simple', search_text)) STORED
    -- search_tsv 自动维护，写入 search_text 即可
);

-- ─────────────────────────────────────────────────────────────────────────────
-- 索引
-- ─────────────────────────────────────────────────────────────────────────────

-- 向量索引（HNSW，近似最近邻，牺牲少量精度换性能）
CREATE INDEX IF NOT EXISTS memories_summary_vec_idx
    ON memories USING hnsw (summary_vec vector_cosine_ops);

CREATE INDEX IF NOT EXISTS memories_message_vec_idx
    ON memories USING hnsw (message_vec vector_cosine_ops);

-- 全文索引（BM25 近似）
CREATE INDEX IF NOT EXISTS memories_search_tsv_idx
    ON memories USING gin (search_tsv);

-- 标量索引（时间范围、用户过滤）
CREATE INDEX IF NOT EXISTS memories_routing_key_idx ON memories (routing_key);
CREATE INDEX IF NOT EXISTS memories_created_at_idx  ON memories (created_at DESC);
CREATE INDEX IF NOT EXISTS memories_tags_idx        ON memories USING gin (tags);
