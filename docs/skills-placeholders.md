# Skills 占位符规约

日期：2026-04-29（P1-3 扩展版）

本文档记录 EvoPaw `skills_runtime` 当前**实际**支持的占位符。`SKILL.md` 在
被注入到 LLM 上下文（reference 型）或 Sub-Agent system_prompt（task 型）
之前，会在 `evopaw/skills_runtime/instructions.py:_get_skill_instructions()`
→ `evopaw/skills_runtime/placeholders.py:render()` 中做字符串替换。

> 本版本同时支持「新规约」`${EVOPAW_*}` 与「旧 alias」`{skill_base}` 等，
> 旧 alias 至少保留一个版本周期。**新写 SKILL.md 优先使用新规约**——
> `${EVOPAW_*}` 前缀避免和 shell 环境变量混淆，并且能让 grep / 审计更易
> 区分项目内占位符与外部环境变量。

---

## 1. 占位符清单（新规约 `${EVOPAW_*}`）

| 占位符 | 替换值 | 用途 |
|---|---|---|
| `${EVOPAW_SKILL_NAME}`     | skill 名称 | 日志 / 错误文案中的自指 |
| `${EVOPAW_SKILL_BASE}`     | `/mnt/skills/<name>` | 容器内 SKILL 资源挂载点（脚本、模板、字典） |
| `${EVOPAW_SESSION_ID}`     | 当前 session 的纯字符串 ID | 仅在 SKILL.md 内部可见；不暴露给主 Agent |
| `${EVOPAW_SESSION_DIR}`    | `/workspace/sessions/<sid>` | session 工作目录（含 `uploads/`、`outputs/`、`tmp/`） |
| `${EVOPAW_ROUTING_KEY}`    | 当前 routing_key（`p2p:.../group:.../thread:...`） | 飞书消息回写、定向通知 |
| `${EVOPAW_WORKSPACE_ROOT}` | `/workspace` | 容器内全局工作根（凭证、cron 元数据等） |
| `${EVOPAW_TODAY}`          | `YYYY-MM-DD`（Asia/Shanghai） | 报告日期、cron 计划标题 |
| `${EVOPAW_NOW}`            | `YYYY-MM-DD HH:MM:SS TZ`（Asia/Shanghai） | 时间戳；调试用 |

未注入 session_id 的特殊场景下：

- `${EVOPAW_SESSION_ID}` → `<session_id>`
- `${EVOPAW_SESSION_DIR}` → `/workspace/sessions/<session_id>`

未注入 routing_key 时：

- `${EVOPAW_ROUTING_KEY}` → `<routing_key>`

## 1.1 旧 alias（保留一个版本周期）

| 旧 alias | 等价新占位符 |
|---|---|
| `{skill_base}`  | `${EVOPAW_SKILL_BASE}` |
| `{_skill_base}` | `${EVOPAW_SKILL_BASE}`（下划线版历史保留） |
| `{session_id}`  | `${EVOPAW_SESSION_ID}` |
| `{session_dir}` | `${EVOPAW_SESSION_DIR}` |

旧 alias 替换值与新规约完全一致；存量 SKILL.md 无需立即迁移。

---

## 2. 路径常量

实现细节（来自 `evopaw/skills_runtime/instructions.py`）：

```python
_SKILLS_MOUNT = "/mnt/skills"  # 容器内 skill 资源挂载点
# Session workspace 根：
#   /workspace/sessions/<sid>/uploads/    用户上传文件
#   /workspace/sessions/<sid>/outputs/    Skill 产出
#   /workspace/sessions/<sid>/tmp/        临时文件
```

> ⚠️ 旧文档曾把 `{skill_base}` 写成 `/workspace/skills/<name>`，这是
> **错误前提**——容器内 `/workspace/skills` 路径不存在。`docker-compose`
> 把宿主机 `evopaw/skills/` 挂载到容器内 `/mnt/skills`；`/workspace`
> 仅承载 session 工作目录与全局凭证（`/workspace/.config/`）。

---

## 3. 注入位置

```text
SkillDispatcher.dispatch(skill_name, task_context)
  → 命中 reference 型：
      _get_skill_instructions(...) 替换占位符 →
      返回 <skill_instructions>...</skill_instructions> 给主 Agent 上下文
  → 命中 task 型：
      _get_skill_instructions(...) 替换占位符 →
      作为 Sub-Agent (run_skill_agent) 的 system_prompt
```

替换结果会被缓存到 `SkillDispatcher._instruction_cache`，同 dispatcher
实例对相同 skill 复用。

---

## 4. 末尾自动追加的 `<execution_directive>`

替换占位符后，`_get_skill_instructions` 会再追加一段 `<execution_directive>`，
显式告知 LLM / Sub-Agent 当前的资源 / session / routing_key 路径：

```xml
<execution_directive>
Skill 资源目录：/mnt/skills/<name>/
当前 Session 工作目录：/workspace/sessions/<sid>/
  - 用户上传文件：/workspace/sessions/<sid>/uploads/
  - 输出文件目录：/workspace/sessions/<sid>/outputs/
  - 临时文件目录：/workspace/sessions/<sid>/tmp/
当前用户 routing_key：<routing_key 或 "<由系统注入>">
</execution_directive>
```

`<execution_directive>` 是**额外注入**，不替换 SKILL.md 原文中的占位符，
也不消耗占位符替换 cache。

---

## 5. 不变量

以下不变量受 P0-4 / P1-3 测试硬保护，未来调整需要先改测试：

1. `{skill_base}` / `{_skill_base}` 与 `${EVOPAW_SKILL_BASE}` 替换值都是
   `/mnt/skills/<name>`，不是 `/workspace/skills/<name>`。
2. `{session_dir}` / `${EVOPAW_SESSION_DIR}` 替换值都是
   `/workspace/sessions/<sid>`。
3. `${EVOPAW_TODAY}` 与 `${EVOPAW_NOW}` 渲染时区固定 Asia/Shanghai；不读
   服务器系统时区，避免容器 UTC 被误判。
4. 替换是字符串级 `str.replace`，不识别反斜杠转义；如果 SKILL.md 想让
   占位符原文出现，必须改名。

---

## 6. 状态与后续工作

- **P0-4**：占位符基线（`{skill_base}` 等）修正与测试硬保护——已完成。
- **P1-3**：引入 `${EVOPAW_*}` 新规约，保留旧 alias 一个版本周期——本版本
  已落地，新写 SKILL.md 优先用新规约。
- **P2 +**：是否进一步支持反斜杠转义、`${SENDER_NAME}` 等额外占位符待评审。

参考：[hermes-nanobot 借鉴改造计划 §P1-3](./improved_agent/hermes-nanobot-borrow-action-plan-2026-04-29.md)。
