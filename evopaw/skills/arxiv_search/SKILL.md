---
name: arxiv_search
description: 搜索 arXiv 论文库，支持按关键词搜索、按 arXiv ID 获取论文信息、下载 PDF 并提取文本。适用于学术研究、论文调研、技术追踪等场景。
type: task
version: "1.0"
---

# arXiv Search Skill — 论文搜索与阅读

## 概述

通过 arXiv API 搜索学术论文，支持关键词搜索和按 ID 精确获取。
可下载 PDF 并提取文本内容，适用于论文调研和技术追踪。

---

## 使用脚本

脚本路径：`{skill_base}/scripts/search.py`

### 按关键词搜索

```bash
python {skill_base}/scripts/search.py search --query "multi-object tracking" --max_results 10
```

### 按 arXiv ID 获取论文信息

```bash
python {skill_base}/scripts/search.py get --arxiv_id "2301.07041"
```

### 下载 PDF 并提取文本

```bash
python {skill_base}/scripts/search.py pdf --arxiv_id "2301.07041" --output_dir {session_dir}/outputs
```

---

## 命令说明

### search — 关键词搜索

| 参数 | 说明 | 示例 |
|------|------|------|
| `--query` | 搜索关键词（必填） | `"transformer attention mechanism"` |
| `--max_results` | 返回结果数，默认 10，最大 50 | `--max_results 5` |
| `--sort_by` | 排序方式 | `relevance`（默认）/ `lastUpdatedDate` / `submittedDate` |

### get — 按 ID 获取

| 参数 | 说明 | 示例 |
|------|------|------|
| `--arxiv_id` | arXiv 论文 ID（必填） | `"2301.07041"` |

### pdf — 下载 PDF 并提取文本

| 参数 | 说明 | 示例 |
|------|------|------|
| `--arxiv_id` | arXiv 论文 ID（必填） | `"2301.07041"` |
| `--output_dir` | PDF 保存目录（必填） | `{session_dir}/outputs` |

---

## 输出格式

### search/get 成功时

```json
{
  "errcode": 0,
  "errmsg": "success",
  "total": 10,
  "papers": [
    {
      "arxiv_id": "2301.07041",
      "title": "论文标题",
      "authors": ["Author1", "Author2"],
      "summary": "论文摘要...",
      "published": "2023-01-17",
      "updated": "2023-03-05",
      "pdf_url": "https://arxiv.org/pdf/2301.07041",
      "categories": ["cs.CV", "cs.AI"]
    }
  ]
}
```

### pdf 成功时

```json
{
  "errcode": 0,
  "errmsg": "success",
  "arxiv_id": "2301.07041",
  "title": "论文标题",
  "pdf_path": "/workspace/sessions/.../outputs/2301.07041.pdf",
  "text_preview": "前2000字符的文本内容..."
}
```

### 失败时

```json
{
  "errcode": 1,
  "errmsg": "错误说明\n建议：解决方法"
}
```

---

## 注意事项

1. **无需 API Key**：arXiv API 免费开放，无需认证
2. **请求频率**：arXiv 要求请求间隔不低于 3 秒，脚本已内置延迟
3. **PDF 提取**：依赖 `PyPDF2` 库，需先安装 `pip install PyPDF2`
4. **依赖**：`arxiv`、`PyPDF2`、`requests` 库
