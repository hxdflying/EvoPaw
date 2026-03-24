---
name: daily-summary
description: Use this skill when the user says 「收工」 or 「今天完成了」. This skill generates a concise daily summary report listing 3 main completed tasks, 3 priority tasks for tomorrow, and any blockers.
---

## Daily Summary Format

When triggered by phrases like 「收工」 or 「今天完成了」, output a concise daily summary in plain text format (no tables):

今日完成：
1. [事项一]
2. [事项二]
3. [事项三]

明日计划：
1. [事项一]
2. [事项二]
3. [事项三]

Blocker：[有/无]

## Execution Steps

1. Extract or ask the user for their 3 main completed tasks for today
2. Extract or ask the user for their 3 priority planned tasks for tomorrow
3. Determine if there are any blockers (ask if not specified)
4. Format the information according to the template above in plain text (no tables)
5. Output the formatted summary directly to the user

## Examples

**Example Input:**
用户说「收工」，并提供今日完成：完成需求文档、修复登录bug、参加产品评审；明日计划：开始前端开发、编写测试用例、与后端联调；无Blocker

**Example Output:**
今日完成：
1. 完成需求文档
2. 修复登录bug
3. 参加产品评审

明日计划：
1. 开始前端开发
2. 编写测试用例
3. 与后端联调

Blocker：无