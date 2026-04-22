---
name: tavily_search
description: 使用 Tavily API 搜索互联网，获取最新信息、新闻、技术文档等。支持搜索深度控制和结果数量设置，返回标题、URL 和内容摘要。
type: task
version: "1.0"
---

# Tavily Search Skill — 网络搜索

## 概述

通过 Tavily Search API 进行网络搜索，返回标题、URL 和内容摘要。
凭证由系统在启动时写入，Agent 无需处理认证。

---

## 使用脚本

脚本路径：`{skill_base}/scripts/search.py`

### 基本搜索

```bash
python {skill_base}/scripts/search.py --query "搜索关键词"
```

### 完整参数

```bash
python {skill_base}/scripts/search.py \
  --query "搜索词"          # 必填：搜索内容
  --max_results 10         # 可选：返回结果数（1-20，默认10）
  --search_depth basic     # 可选：搜索深度（basic/advanced，默认basic）
  --include_answer true    # 可选：是否包含AI生成的答案摘要（默认true）
```

### 参数说明

| 参数 | 说明 | 示例 |
|------|------|------|
| `--query` | 搜索关键词或自然语言问题（必填） | `"Python 异步编程最佳实践"` |
| `--max_results` | 返回结果数，默认 10，最大 20 | `--max_results 5` |
| `--search_depth` | 搜索深度 | `basic`（快速）/ `advanced`（深度，更准确但更慢）|
| `--include_answer` | 是否返回AI摘要 | `true`（默认）/ `false` |

---

## 典型场景

### 场景 1：搜索最新资讯

```bash
python {skill_base}/scripts/search.py \
  --query "2026年大模型发展动态" \
  --max_results 10
```

### 场景 2：技术问题深度搜索

```bash
python {skill_base}/scripts/search.py \
  --query "Python asyncio 死锁排查" \
  --max_results 5 \
  --search_depth advanced
```

---

## 输出格式

成功时（stdout JSON）：

```json
{
  "errcode": 0,
  "errmsg": "success",
  "query": "搜索词",
  "answer": "AI 生成的答案摘要（如果 include_answer=true）",
  "total": 10,
  "results": [
    {
      "title": "页面标题",
      "url": "https://example.com/article",
      "content": "页面内容摘要..."
    }
  ]
}
```

失败时：

```json
{
  "errcode": 1,
  "errmsg": "错误说明\n建议：解决方法"
}
```

---

## 注意事项

1. **不要手动处理认证**：API Key 由系统注入 `/workspace/.config/tavily.json`，脚本自动读取
2. **max_results 选择策略**：
   - 精准信息 → `--max_results 3~5`
   - 一般调研 → `--max_results 10`（默认）
   - 全面调研 → `--max_results 20`
3. **search_depth 选择**：大多数场景用 `basic` 即可；需要高精度时用 `advanced`
4. **依赖**：`tavily-python` 库，需先安装 `pip install tavily-python`
