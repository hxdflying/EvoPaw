---
name: baidu_search
description: Use this skill to search the internet using Baidu when the user asks about recent events, current information, news, prices, people, companies, technical documentation, or any topic that may require up-to-date data beyond the AI's training knowledge. Supports filtering by time range (week/month/semiyear/year) and specific websites. Returns titles, URLs, and content summaries for each result.
license: Proprietary. LICENSE.txt has complete terms
---

# Baidu Search Skill — 百度搜索

## 概述

通过百度千帆搜索 API 进行网络搜索，返回标题、URL 和内容摘要。
凭证由系统在启动时写入沙盒，Agent 无需处理认证。

---

## 使用脚本

脚本路径：`./scripts/search.py`（沙盒挂载路径，由 sandbox_execution_directive 提供）

### 基本搜索

```bash
python ./scripts/search.py --query "搜索关键词"
```

### 完整参数

```bash
python ./scripts/search.py \
  --query "搜索词"          # 必填：搜索内容
  --top_k 10               # 可选：返回结果数（1-50，默认20）
  --recency week           # 可选：时间过滤（week/month/semiyear/year）
  --sites "zhihu.com,csdn.net"  # 可选：限定站点，逗号分隔，最多20个
```

### 参数说明

| 参数 | 说明 | 示例 |
|------|------|------|
| `--query` | 搜索关键词或自然语言问题（必填） | `"Python 异步编程最佳实践"` |
| `--top_k` | 返回结果数，默认 20，最大 50 | `--top_k 5`（精准搜索）/ `--top_k 30`（广泛调研）|
| `--recency` | 时间范围过滤 | `week`近7天 / `month`近30天 / `semiyear`近半年 / `year`近一年 |
| `--sites` | 指定站点（逗号分隔） | `"github.com,stackoverflow.com"` |

---

## 典型场景

### 场景 1：搜索最新资讯

```bash
python ./scripts/search.py \
  --query "2025年大模型发展动态" \
  --recency month \
  --top_k 10
```

### 场景 2：技术问题搜索（限定高质量站点）

```bash
python ./scripts/search.py \
  --query "Python asyncio 死锁排查" \
  --top_k 5 \
  --sites "stackoverflow.com,docs.python.org,github.com"
```

### 场景 3：精准事实查询

```bash
python ./scripts/search.py \
  --query "特斯拉 Model 3 2025款价格" \
  --top_k 3 \
  --recency month
```

---

## 输出格式

成功时（stdout JSON）：

```json
{
  "errcode": 0,
  "errmsg": "success",
  "query": "搜索词",
  "total": 10,
  "results": [
    {
      "id": 1,
      "title": "页面标题",
      "url": "https://example.com/article",
      "summary": "页面内容摘要..."
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

1. **不要手动处理认证**：API Key 由系统注入 `/workspace/.config/baidu.json`，脚本自动读取
2. **top_k 选择策略**：
   - 精准信息（如价格、日期）→ `--top_k 3~5`
   - 一般调研 → `--top_k 10~20`（默认）
   - 全面调研 → `--top_k 30~50`
3. **结果需要进一步阅读**：如需获取完整页面内容，配合 `web_browse` Skill 使用 `sandbox_convert_to_markdown` 抓取具体 URL
4. **依赖**：`requests` 库，在沙盒中通常已预装；若报错可先执行 `pip install requests`

---

## 任务结果格式要求

```json
{
  "errcode": 0,
  "errmsg": "success",
  "query": "实际搜索词",
  "total": 10,
  "results": [
    {"id": 1, "title": "标题", "url": "URL", "summary": "摘要"}
  ],
  "summary": "对搜索结果的综合分析，回答用户的原始问题"
}
```
