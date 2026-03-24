---
name: search_memory
description: >
  当需要回忆任何历史对话内容时，主动使用此 skill——不需要用户说"去搜索"，
  只要话语中隐含对历史信息的依赖，就立即触发：

  - 用户引用过去的结论或操作（"上次你说的"、"之前的分析"、"上周五操作时的结论"、"根据我们之前讨论的"）
  - 用户询问过去做过的事（"上次那个航班"、"之前帮我查的 XX"、"你还记得吗"）
  - 需要找到用户在历史对话中透露的事实（持仓、成本、操作记录、项目状态）
  - 用户说"复盘"、"回顾"、"对比上次"等需要历史上下文才能回答的请求
  - 回答"该不该 XX"类决策问题，且决策所需的背景信息（成本/偏好/历史操作）未在当前对话中出现

  支持语义搜索（模糊意图）、关键字搜索（精确词）、混合搜索（推荐）。
type: task
version: "1.0"
---

# search_memory Skill

## 功能说明

在 pgvector 数据库中搜索 XiaoPaw 的历史对话记忆，支持三种搜索模式的混合使用。

---

## 数据库结构

连接信息（从环境变量读取）：
- DSN：`MEMORY_DB_DSN`，默认 `postgresql://xiaopaw:xiaopaw123@localhost:5432/xiaopaw_memory`

`memories` 表字段说明：

| 字段 | 类型 | 说明 | 搜索方式 |
|------|------|------|---------|
| `id` | TEXT | 主键 | — |
| `session_id` | TEXT | 来源 session | 标量过滤 |
| `routing_key` | TEXT | 用户标识（p2p:ou_xxx） | 标量过滤 |
| `user_message` | TEXT | 用户原始消息 | 全文搜索 |
| `assistant_reply` | TEXT | 助手回复 | 展示用 |
| `summary` | TEXT | 一句话摘要 | 向量搜索 |
| `tags` | TEXT[] | 领域标签（工作/文件处理/日程/搜索/代码等） | 标量过滤 |
| `created_at` | TIMESTAMPTZ | 写入时间 | 标量过滤 |
| `turn_ts` | BIGINT | 对话时间戳（毫秒） | 标量过滤 |
| `summary_vec` | vector(1024) | 摘要语义向量 | 向量搜索 |
| `message_vec` | vector(1024) | 原始对话语义向量 | 向量搜索 |
| `search_tsv` | TSVECTOR | 全文索引（自动维护） | BM25近似 |

---

## 搜索策略选择

根据用户问题类型选择搜索策略：

| 场景 | 策略 | 示例 |
|------|------|------|
| 语义模糊查询 | 纯向量搜索 | "上次那个航班"、"之前聊过的投资建议" |
| 精确关键字 | 纯全文搜索 | "PDF转换"、"错误码500" |
| 有时间/标签限制 | 混合搜索 | "上周的工作文件"、"代码相关的历史" |
| 通用查询 | 混合搜索（推荐） | 大多数情况下混合搜索效果最好 |

---

## 调用方式

调用 `scripts/search.py`，通过命令行参数传入搜索条件：

```bash
python /mnt/skills/search_memory/scripts/search.py \
  --query "用户的搜索意图" \
  --tags "工作,文件处理" \
  --days 7 \
  --limit 5 \
  --mode hybrid
```

### 参数说明

| 参数 | 必填 | 说明 |
|------|------|------|
| `--query` | 是 | 搜索意图（自然语言） |
| `--tags` | 否 | 标签过滤，逗号分隔（如 `工作,文件处理`） |
| `--days` | 否 | 时间范围，最近N天（如 `7` 表示最近一周） |
| `--routing_key` | 否 | 限定用户（默认不限） |
| `--limit` | 否 | 返回条数，默认 5 |
| `--mode` | 否 | 搜索模式：`hybrid`（默认）/ `vector` / `fulltext` |

### 输出格式

JSON 数组，每条记录包含：
```json
[
  {
    "id": "abc123",
    "summary": "帮用户转换了一个PDF文件",
    "user_message": "帮我把这个 PDF 转成 Word",
    "assistant_reply": "转换完成，文件已保存到 outputs/result.docx",
    "tags": ["工作", "文件处理"],
    "created_at": "2026-01-20T14:00:25Z",
    "score": 0.92
  }
]
```

---

## 搜索示例

### 示例1：语义搜索（用户问"上次那个航班"）
```bash
python /mnt/skills/search_memory/scripts/search.py --query "航班查询" --mode vector --limit 3
```

### 示例2：混合搜索（"上周帮我处理的文件"）
```bash
python /mnt/skills/search_memory/scripts/search.py --query "文件处理" --tags "文件处理" --days 7 --mode hybrid
```

### 示例3：全文搜索（精确关键字）
```bash
python /mnt/skills/search_memory/scripts/search.py --query "PDF转换" --mode fulltext --limit 5
```

---

## 注意事项

- 搜索结果按相关性得分降序排列，取 topK
- 混合搜索得分 = 向量得分 × 0.7 + 全文得分 × 0.3
- 如果搜索结果为空，按以下顺序放宽条件重试：
  1. 先去掉 `--days` 限制（时间范围可能太窄）
  2. 再去掉 `--tags` 限制（标签可能不匹配）
  3. 最后切换到 `--mode vector` 纯语义搜索（关键字可能不准）
- 如果需要查看完整回复内容，`assistant_reply` 字段已包含

