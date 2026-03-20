---
name: feishu_ops
description: "飞书操作：向用户/群组发送消息（文字/富文本/图片/文件）、读取云文档/表格内容、查询群成员、管理日历事件。适合推送通知、发送处理结果文件、读取共享文档、批量发送报告等场景。"
type: task
version: "2.0"
---

# feishu_ops Skill

所有操作通过沙盒内的 `scripts/` 目录下的独立脚本执行。脚本自动从 `/workspace/.config/feishu.json` 读取凭证，无需手动处理鉴权。

**调用方式**：`python {_skill_base}/scripts/<脚本名>.py [参数]`

---

## 一、发送消息

### send_text.py — 发送纯文字消息

```
python {_skill_base}/scripts/send_text.py \
    --routing_key <routing_key> \
    --text "消息内容"
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--routing_key` | ✅ | `p2p:ou_xxx`（私聊）或 `group:oc_xxx`（群组） |
| `--text` | ✅ | 纯文本消息内容 |

---

### send_post.py — 发送富文本消息（带标题 + 多段落）

```
python {_skill_base}/scripts/send_post.py \
    --routing_key <routing_key> \
    --title "消息标题" \
    --paragraphs '["第一段内容", "第二段，含[链接](https://example.com)"]'
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--routing_key` | ✅ | 同上 |
| `--title` | 否 | 消息标题，可为空 |
| `--paragraphs` | ✅ | JSON 字符串数组，每项为一段文字；支持 `[文字](URL)` 格式内嵌链接 |

---

### send_image.py — 发送图片

```
python {_skill_base}/scripts/send_image.py \
    --routing_key <routing_key> \
    --image_path /workspace/sessions/{session_id}/outputs/chart.png
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--routing_key` | ✅ | 同上 |
| `--image_path` | ✅ | 沙盒内图片绝对路径（jpg/png/gif/webp，≤30MB） |

脚本自动完成：上传图片 → 获取 image_key → 发送 image 消息。

---

### send_file.py — 发送文件（处理结果回传核心场景）

```
python {_skill_base}/scripts/send_file.py \
    --routing_key <routing_key> \
    --file_path /workspace/sessions/{session_id}/outputs/report.pdf
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--routing_key` | ✅ | 同上 |
| `--file_path` | ✅ | 沙盒内文件绝对路径（pdf/doc/xls/ppt/mp4/opus 等，≤30MB） |

脚本自动完成：上传文件 → 获取 file_key → 发送 file 消息。

**典型用法**：用户上传文件 → pdf/docx/xlsx 等 Skill 处理 → 结果保存到 `outputs/` → 调用本脚本将结果文件发回给用户。

---

## 二、读取飞书云文档

### read_doc.py — 读取飞书文档纯文本

```
python {_skill_base}/scripts/read_doc.py \
    --doc "https://xxx.feishu.cn/docx/doccnXXXXXX"
# 或直接传 token：
python {_skill_base}/scripts/read_doc.py --doc doccnXXXXXX
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--doc` | ✅ | 飞书文档 URL 或 doc_token，脚本自动解析 |

返回 `data.content`（纯文本字符串）。

---

### read_sheet.py — 读取飞书电子表格数据

```
python {_skill_base}/scripts/read_sheet.py \
    --sheet "https://xxx.feishu.cn/sheets/shtcnXXXXXX" \
    --sheet_id Sheet1 \
    --range A1:D10
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--sheet` | ✅ | 电子表格 URL 或 spreadsheet_token |
| `--sheet_id` | 否 | Sheet 的 sheetId（非 Sheet 名称），不填则读第一个 Sheet |
| `--range` | 否 | 读取范围如 `A1:D10`，不填则读整表 |

返回 `data.values`（二维数组）。

---

## 三、查询群成员

### get_chat_members.py — 获取群组成员列表

```
python {_skill_base}/scripts/get_chat_members.py --chat_id oc_xxxxx
```

返回 `data.members`（含 open_id、name 等字段的数组）。

---

## 四、日历操作

> 仅支持应用已订阅的共享日历，不支持用户个人 primary 日历（需 user_access_token）。

### list_events.py — 查询日历事件

```
python {_skill_base}/scripts/list_events.py \
    --calendar_id feishu_xxxxxx \
    --start_time 2026-03-01T00:00:00+08:00 \
    --end_time 2026-03-31T23:59:59+08:00
```

### create_event.py — 创建日历事件

```
python {_skill_base}/scripts/create_event.py \
    --calendar_id feishu_xxxxxx \
    --summary "周例会" \
    --start_time 2026-03-09T10:00:00+08:00 \
    --end_time 2026-03-09T11:00:00+08:00 \
    --description "本周进度同步" \
    --attendees '["ou_aaa", "ou_bbb"]'
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--calendar_id` | ✅ | 日历 ID |
| `--summary` | ✅ | 事件标题 |
| `--start_time` / `--end_time` | ✅ | RFC3339 格式时间 |
| `--description` | 否 | 事件描述 |
| `--attendees` | 否 | JSON 数组，元素为与会者 open_id |

---

## 五、创建飞书文档 / 电子表格

### create_doc.py — 创建飞书文档（Docx），可写入 Markdown 内容

```
python {_skill_base}/scripts/create_doc.py \
    --title "季度报告" \
    [--folder_token <token>] \
    [--content "# 一季度\n\n正文内容..."] \
    [--content_file /workspace/report.md]
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--title` | ✅ | 文档标题 |
| `--folder_token` | 否 | 目标文件夹 token；留空则放在应用云空间根目录 |
| `--content` | 否 | Markdown 字符串，直接写入文档正文 |
| `--content_file` | 否 | Markdown 文件路径（与 `--content` 二选一，文件优先） |

**支持的 Markdown 语法**：`# 标题1`～`###### 标题6`、`- 无序列表`、`1. 有序列表`、` ``` 代码块 ``` `、普通段落。

返回 `data.document_id`、`data.url`、`data.blocks_written`（写入块数）。

---

### create_sheet.py — 创建飞书电子表格（Spreadsheet）

```
python {_skill_base}/scripts/create_sheet.py \
    --title "销售数据" \
    [--folder_token <token>]
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--title` | ✅ | 表格标题 |
| `--folder_token` | 否 | 目标文件夹 token；留空则放在应用云空间根目录 |

返回 `data.spreadsheet_token` 和 `data.url`。

---

### upload_sheet.py — 将本地 Excel 文件导入为飞书电子表格

```
python {_skill_base}/scripts/upload_sheet.py \
    --file_path /workspace/sessions/{session_id}/outputs/report.xlsx \
    [--title "销售报告"] \
    [--folder_token <token>]
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--file_path` | ✅ | 本地 `.xlsx` / `.xls` 文件绝对路径（≤20MB） |
| `--title` | 否 | 导入后的表格名称；留空则使用文件名 |
| `--folder_token` | 否 | 目标文件夹 token；留空则放在应用云空间根目录 |

三步导入流程：上传文件 → 创建导入任务 → 轮询等待完成（最长 60s）。

返回 `data.spreadsheet_token`、`data.url`、`data.title`、`data.file_size`。

---

### write_sheet.py — 向电子表格写入数据

```
python {_skill_base}/scripts/write_sheet.py \
    --sheet "https://xxx.feishu.cn/sheets/shtcnXXXX" \
    --values '[["姓名","年龄","部门"],["Alice",30,"工程"],["Bob",25,"设计"]]' \
    [--start_cell A1] \
    [--sheet_id Sheet1]
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--sheet` | ✅ | 电子表格 URL 或 spreadsheet_token |
| `--values` | ✅ | JSON 二维数组，第一行通常为表头 |
| `--start_cell` | 否 | 写入起始单元格，默认 `A1` |
| `--sheet_id` | 否 | Sheet ID，不填则写入第一个 Sheet |

返回 `data.range`（实际写入范围）和 `data.rows_written`。

---

## 六、多维表格（Bitable）操作

### create_bitable.py — 创建多维表格应用

```
python {_skill_base}/scripts/create_bitable.py \
    --name "项目管理" \
    [--folder_token <token>]
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--name` | ✅ | 多维表格名称 |
| `--folder_token` | 否 | 目标文件夹 token；留空则放在应用云空间根目录 |

返回 `data.app_token` 和 `data.url`（后续创建数据表时需要此 token）。

---

### create_bitable_table.py — 在多维表格内创建数据表

```
python {_skill_base}/scripts/create_bitable_table.py \
    --app "https://xxx.feishu.cn/base/BxxXXXX" \
    --name "任务清单" \
    --fields '[
        {"name": "任务名称", "type": "text"},
        {"name": "优先级", "type": "select", "options": ["高","中","低"]},
        {"name": "标签", "type": "multiselect", "options": ["前端","后端","设计"]},
        {"name": "截止日期", "type": "date"},
        {"name": "完成", "type": "checkbox"},
        {"name": "工时(h)", "type": "number"},
        {"name": "参考链接", "type": "url"}
    ]'
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--app` | ✅ | 多维表格 URL 或 app_token |
| `--name` | ✅ | 数据表名称 |
| `--fields` | 否 | JSON 数组，定义字段结构（见下表） |

**字段类型（`type` 值）**：

| type | 说明 | 是否需要 options |
|------|------|----------------|
| `text` | 多行文本 | 否 |
| `number` | 数字 | 否 |
| `select` | 单选 | ✅（options 数组） |
| `multiselect` | 多选 | ✅（options 数组） |
| `date` | 日期 | 否 |
| `checkbox` | 勾选框 | 否 |
| `url` | 超链接 | 否 |

返回 `data.table_id`（后续写入记录时需要）和 `data.fields_created`（已创建字段列表）。

---

### write_bitable_records.py — 批量写入多维表格记录

```
python {_skill_base}/scripts/write_bitable_records.py \
    --app "https://xxx.feishu.cn/base/BxxXXXX" \
    --table_id tblXXXXXX \
    --records '[
        {"任务名称": "完成 API 文档", "优先级": "高", "完成": false},
        {"任务名称": "代码 Review", "优先级": "中", "完成": true}
    ]'
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--app` | ✅ | 多维表格 URL 或 app_token |
| `--table_id` | ✅ | 数据表 ID（`create_bitable_table.py` 返回的 `table_id`） |
| `--records` | ✅ | JSON 数组，每项为一条记录（键为字段名称） |

- 每批最多 500 条，超过自动分批；字段键直接用字段名称（无需字段 ID）。
- 返回 `data.record_count`（成功写入数量）和 `data.record_ids`。

---

## 输出格式

所有脚本统一输出 JSON 到 stdout，exit 0：

```json
{"errcode": 0, "errmsg": "success", "data": {...}}
{"errcode": 1, "errmsg": "错误说明\n建议：...", "data": {}}
```

`errcode=0` 表示成功，`errcode=1` 表示失败（`errmsg` 包含具体原因和建议）。

