---
name: history_reader
description: 读取当前 Session 的完整历史对话，支持分页，用于在历史被截断时查阅早期记录
type: reference
version: "2.0"
---

# history_reader — 历史对话读取

## 功能概述

读取当前 Session 的历史对话记录，支持分页，返回结构化的消息列表。
当主 Agent 上下文中的历史对话被截断时（通常保留最近 20 条），
可通过此 Skill 查询更早期的内容。

> **注意**：此 Skill 由系统内联处理，无需沙盒执行。
> Session 上下文由系统自动管理，调用时只需传递分页参数。

## 使用场景

- 用户询问"你之前说的 xxx 是什么意思？"但那条消息已超出上下文窗口
- 需要汇总/回顾历史对话内容
- 统计历史对话中的关键决策或行动

## 输入参数（task_context 中传入）

```json
{
  "page": 1,
  "page_size": 20
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `page` | int | 否 | 页码，从 1 开始，默认 1（最旧的消息） |
| `page_size` | int | 否 | 每页条数，1-50，默认 20 |

**分页规则**：page=1 返回最旧的 page_size 条，page 越大越新。
如需最新消息，使用较大 page 或直接从主 Agent 上下文获取。

## 输出格式（SkillResult）

```json
{
  "errcode": 0,
  "message": "成功读取第 1 页，共 35 条消息，本页 20 条",
  "data": {
    "messages": [
      {"role": "user", "content": "..."},
      {"role": "assistant", "content": "..."}
    ],
    "total": 35,
    "page": 1,
    "page_size": 20,
    "total_pages": 2
  }
}
```

## 示例调用

主 Agent 在 task_context 中传入：
```json
{
  "page": 1,
  "page_size": 20
}
```

系统直接返回分页结果，主 Agent 可根据 `total_pages` 决定是否继续翻页。
