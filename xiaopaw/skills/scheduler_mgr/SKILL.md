---
name: scheduler_mgr
description: "定时任务管理：创建、查看、删除定时任务。支持一次性（at）、周期（every）、cron 表达式三种触发模式。写入 data/cron/tasks.json，CronService 自动热重载生效，无需重启进程。"
type: task
version: "1.0"
---

# scheduler_mgr Skill

所有操作通过沙盒内 `scripts/` 目录下的脚本封装对 `tasks.json` 的读写，主 Agent / 子 Agent
只需要用 `python {_skill_base}/scripts/*.py` 即可完成任务增删改查，无需直接操作 JSON 细节。

**调用方式**：`python {_skill_base}/scripts/<脚本名>.py [参数]`

---

## 功能说明

本 Skill 管理 XiaoPaw 的定时任务，支持：
- **创建**定时任务（三种触发模式：at/every/cron）
- **查看**当前所有任务
- **更新**已有任务（修改 cron 表达式、启用/禁用等）
- **删除**指定任务

⚠️ 本 Skill **只负责写入定时配置，不直接执行业务脚本或命令**。  
触发时的业务处理（例如获取行情、查询新闻、生成报告）由主 Agent
配合其它 Skills（如 `baidu_search`、`web_browse` 等）完成：

- CronService 触发时，会把 `payload.message` 作为 `InboundMessage.content`
  注入主 Agent。
- 因此，`payload.message` 必须是**清晰的自然语言指令**，描述“要做什么”，
  而不是具体的 `python ...` / `bash ...` 等命令。

修改后 CronService 会在下次 tick 时自动感知文件变化（mtime 热重载），无需重启进程。

---

## 数据文件路径

定时任务配置文件通过独立 Docker 卷挂载至沙盒，可直接读写：

```
沙盒路径：/workspace/cron/tasks.json
宿主机路径：data/cron/tasks.json（挂载配置：./data/cron:/workspace/cron:rw）
```

所有读写操作均使用沙盒路径 `/workspace/cron/tasks.json`。

---

## 一、数据结构

```json
{
  "version": 1,
  "jobs": [
    {
      "id": "job-uuid",
      "name": "每日工作摘要",
      "enabled": true,
      "schedule": {
        "kind": "cron",
        "expr": "0 9 * * 1-5",
        "tz": "Asia/Shanghai",
        "at_ms": null,
        "every_ms": null
      },
      "payload": {
        "routing_key": "p2p:ou_xxxx",
        "message": "请生成今日工作摘要"
      },
      "state": {
        "next_run_at_ms": 1738800000000,
        "last_run_at_ms": null,
        "last_status": null,
        "last_error": null
      },
      "created_at_ms": 1736900000000,
      "updated_at_ms": 1736900000000,
      "delete_after_run": false
    }
  ]
}
```

---

## 二、三种触发模式

### 2.1 一次性任务（at）

```json
"schedule": {
  "kind": "at",
  "at_ms": 1738800000000,   // 触发时刻的 Unix 毫秒时间戳
  "every_ms": null,
  "expr": null,
  "tz": null
}
```

- `delete_after_run: true`：触发后自动删除
- `delete_after_run: false`：触发后设为 `enabled: false`（保留记录）

### 2.2 周期任务（every）

```json
"schedule": {
  "kind": "every",
  "every_ms": 3600000,      // 间隔毫秒数（此处为 1 小时）
  "at_ms": null,
  "expr": null,
  "tz": null
}
```

常用换算：
- 1 分钟 = 60000 ms
- 1 小时 = 3600000 ms
- 1 天 = 86400000 ms

### 2.3 Cron 表达式（cron）

```json
"schedule": {
  "kind": "cron",
  "expr": "0 9 * * 1",      // 每周一 09:00
  "tz": "Asia/Shanghai",
  "at_ms": null,
  "every_ms": null
}
```

常用 cron 表达式：
- `0 9 * * 1-5`：周一至周五 09:00
- `0 18 * * 5`：每周五 18:00
- `0 */2 * * *`：每 2 小时整点
- `30 8 1 * *`：每月 1 日 08:30

---

## 三、脚本封装与调用方式

### 3.1 查看任务列表 — `list_tasks.py`

```bash
python {_skill_base}/scripts/list_tasks.py
```

返回：

```json
{"errcode": 0, "errmsg": "success", "data": {"total": 2, "jobs": [ ... ]}}
```

---

### 3.2 创建任务 — `create_task.py`

```bash
python {_skill_base}/scripts/create_task.py \
  --name "阿里巴巴港股行情分析" \
  --routing_key "p2p:ou_xxxx" \
  --message "请查看今天阿里巴巴港股（09988.HK）的当日行情、最近30日K线表现和相关新闻舆情，生成一份行情分析和投资操作建议报告。" \
  --schedule_kind cron \
  --expr "50 18 * * 1-5" \
  --tz "Asia/Shanghai"
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--name` | ✅ | 任务名称，便于在列表中识别 |
| `--routing_key` | ✅ | 触发时消息发送目标，如 `p2p:ou_xxx` / `group:oc_xxx` |
| `--message` | ✅ | 自然语言指令（见下文 Payload 约束） |
| `--schedule_kind` | ✅ | `at` / `every` / `cron` |
| `--expr` | cron 必填 | cron 表达式，如 `"50 18 * * 1-5"` |
| `--tz` | cron 建议 | 时区，通常使用 `"Asia/Shanghai"` |
| `--at_ms` | at 必填 | Unix 毫秒时间戳，精确到毫秒 |
| `--every_ms` | every 必填 | 间隔毫秒数，如 60000 表示 1 分钟 |
| `--delete_after_run` | 否 | 仅 at 模式有意义，传入则触发一次后删除 |

脚本内部会自动：
- 读取 `/workspace/cron/tasks.json`（不存在则创建）
- 生成唯一 `job_id`，补全 `created_at_ms` / `updated_at_ms`
- 将 `state.next_run_at_ms` 设为 `null`，交给 CronService 按最新配置重算
- 原子写回文件

---

### 3.3 更新任务 — `update_task.py`

```bash
python {_skill_base}/scripts/update_task.py \
  --job_id "job-0e0668d6" \
  --expr "50 18 * * 1-5" \
  --schedule_kind cron \
  --tz "Asia/Shanghai"
```

仅会修改传入的字段，其它字段保持不变。  
脚本会自动将该任务的 `state.next_run_at_ms` 重置为 `null`，确保 CronService 使用最新 cron 表达式重新计算下一次触发时间。

常用参数：

| 参数 | 必填 | 说明 |
|------|------|------|
| `--job_id` | ✅ | 要更新的任务 ID |
| `--name` | 否 | 新的任务名称 |
| `--routing_key` | 否 | 新的 routing_key |
| `--message` | 否 | 新的自然语言指令 |
| `--schedule_kind` | 否 | 更新触发模式（at/every/cron） |
| `--expr` / `--tz` | 否 | 更新 cron 表达式与时区 |
| `--at_ms` / `--every_ms` | 否 | 更新 at / every 模式的时间参数 |
| `--enabled` | 否 | `"true"` / `"false"`，启用或禁用任务 |
| `--delete_after_run` | 否 | `"true"` / `"false"`，仅 at 模式有意义 |

---

### 3.4 删除任务 — `delete_task.py`

```bash
python {_skill_base}/scripts/delete_task.py --job_id "job-0e0668d6"
```

成功时返回：

```json
{"errcode": 0, "errmsg": "success", "data": {"action": "deleted", "job_id": "job-0e0668d6"}}
```

---

## 四、payload.routing_key 格式

| 场景 | routing_key 格式 | 示例 |
|------|----------------|------|
| 发给特定用户 | `p2p:{open_id}` | `p2p:ou_abc123` |
| 发到群组 | `group:{chat_id}` | `group:oc_chat456` |
| 发到话题 | `thread:{chat_id}:{thread_id}` | `thread:oc_chat789:ot_xxx` |

routing_key 可从用户当前对话的 session_id 相关信息中获取。

---

## 五、Payload 与输出格式规范

### 5.1 关于 payload.message 的强约束

在任何情况下，都必须遵守以下规则：

- ✅ `payload.message` **只能**是清晰的自然语言描述，面向人类和主 Agent 可读：
  - 例如：`"请每天早上9点帮我整理昨天的工作摘要，并生成一条简要汇报。"`
  - 例如：`"请查看今天阿里巴巴港股（09988.HK）的当日行情、最近30日K线表现和相关新闻舆情，生成一份行情分析和投资操作建议报告。"`
- ❌ 严禁在 `message` 中写入任何**可执行命令或路径**：
  - 禁止：`"python /workspace/sessions/xxx/alibaba_stock_analysis.py"`
  - 禁止：`"bash run.sh"`、`"/usr/bin/python script.py"` 等
  - 禁止写入具体 session 路径（如 `/workspace/sessions/{sid}/...`）

原因说明：

- CronService 触发时，会把 `payload.message` 原样作为 `InboundMessage.content`
  注入主 Agent，由主 Agent 再去调用 `baidu_search`、`web_browse` 等 Skill 来完成真正的业务逻辑。
- 若写成 python/bash 命令，主 Agent 无法正确理解语义，也会破坏当前"配置层（scheduler_mgr）"
  与"执行层（主 Agent + 其它 Skills）"的分层设计。

因此：**scheduler_mgr Skill 永远不要负责“怎么执行脚本”，只描述“想要什么结果”。**

### 5.2 输出格式规范

成功创建：
```json
{
  "errcode": 0,
  "errmsg": "success",
  "data": {
    "action": "created",
    "job_id": "job-xxxxxxxx",
    "name": "每日工作摘要",
    "next_hint": "CronService 将在下次 tick 自动感知并加载新任务，无需重启"
  }
}
```

成功删除：
```json
{
  "errcode": 0,
  "errmsg": "success",
  "data": {
    "action": "deleted",
    "job_id": "job-xxxxxxxx"
  }
}
```

查看列表：
```json
{
  "errcode": 0,
  "errmsg": "success",
  "data": {
    "total": 2,
    "jobs": [
      {"id": "job-xxx", "name": "...", "enabled": true, "schedule": {...}}
    ]
  }
}
```

失败：
```json
{
  "errcode": 1,
  "errmsg": "写入 tasks.json 失败：路径不存在\n建议：确认沙盒挂载路径，或使用 sandbox_execute_bash 创建目录：mkdir -p /workspace/cron",
  "data": {}
}
```
