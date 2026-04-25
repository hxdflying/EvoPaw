# 飞书语音转写 — 预生产联调 Runbook

> 关联设计文档：[2026-04-21-feishu-funasr-voice-design.md](../superpowers/specs/2026-04-21-feishu-funasr-voice-design.md)（§17.4 Phase 4）
>
> 适用范围：Phase 1-3 改造已完成（共 599 unit + 6 integration 测试通过），现在准备让 EvoPaw 在真实飞书 + 百炼环境下"上量验证"。
>
> 本 runbook 把 Phase 4 的四项预生产校准 / 验证拆成可独立执行的步骤，每步都有具体命令、判断标准和需要回写到设计文档的结论。

## 当前状态（2026-04-25 收尾）

**已完成**：

- 代码 4 个 commit 已 push 到 `hxdflying/EvoPaw`
- docker compose 重建并运行
- 真实飞书短语音端到端验证通过（用户实测）

**剩余 7 项（按 [设计文档 §0 收尾说明](../superpowers/specs/2026-04-21-feishu-funasr-voice-design.md) 三组分类）**：

| 组 | 任务 | 触发时机 | 在本 runbook 中位置 |
|---|------|---------|---------------------|
| A1 | 20s 长语音回执 | 随时（10 分钟） | §4 步骤 D 表格第 2 行 |
| A2 | 中英混合 / 口音 | 随时 | §4 步骤 D 表格第 3 行 |
| A3 | 重复发同 msg_id 触发 dedup | 随时 | §4 步骤 D 表格第 4 行 |
| A4 | 故意触发失败 | 随时 | §4 步骤 D 表格第 4 行 |
| B1 | 采样率审计 | 积累 ≥ 5 条真实录音后 | §1 步骤 A |
| B2 | 阈值校准 | 积累 ≥ 50 条 ASR 请求后 | §2 步骤 B |
| C1 | 固定 Fun-ASR 快照号 | 上线日 | §3 步骤 C |

## 0. 前置条件

执行前请确认：

| 资产 | 用途 | 获取途径 |
|------|------|---------|
| 飞书自建应用 `app_id` / `app_secret` | 接收 audio 消息 | 飞书开放平台 → 凭证与基础信息 |
| 百炼 `DASHSCOPE_API_KEY` | Fun-ASR 实时 WebSocket | 阿里云百炼控制台 → API Key |
| 至少一个测试群或 1:1 聊天 | 注入真实飞书录音 | 用真机录制后发送给 Bot |
| `ffmpeg` / `ffprobe`（≥ 4.0） | 采样率探测、可选转码 | `apt install ffmpeg` 等 |

把上面三件凭证写入 `.env`（`.gitignore` 已覆盖），不要提交。

## 1. 步骤 A：OPUS 采样率审计（设计文档 §18.2）

**目的**：决定 §18.2 走方案 A（换模型）还是方案 B（ffmpeg 转码）。

**步骤**：

1. 用真实飞书账号录制至少 5 段语音（覆盖短/长、安静/嘈杂、男/女声）发给 Bot；
2. 让 EvoPaw 正常运行至少 1 轮，确认音频已下载到 `data/workspace/sessions/<sid>/uploads/`；
3. 跑审计脚本：

   ```bash
   python3 scripts/audit_audio_sample_rate.py data/workspace/sessions/
   ```

4. 读末尾 `推荐：` 行做决策。

**预期产出**：以下三种结论之一，回写到设计文档 §18.2：

| 结论 | 行动 |
|------|------|
| 全部 16kHz | 方案 A（无操作）。把 §18.2 风险标记为"已实测无影响"。 |
| 全部 8kHz | 方案 A。`config.yaml` 改 `model: fun-asr-flash-8k-realtime`、`sample_rate: 8000`，重新跑。 |
| 混合或其它 | 方案 B。在 `evopaw/asr/service.py` 加 `ffmpeg -ar 16000 -f wav` 转码层，并新增 ffmpeg 安装文档；保留 §18.2 风险条目。 |

## 2. 步骤 B：延迟阈值校准（`short_wait_s` / `long_audio_threshold_ms`）

**目的**：让回执判定基于真实 P50/P95 延迟，而非默认值（10s / 15000ms）。

**采样**：

1. 跑 EvoPaw 至少 1 周或 ≥ 50 条真实语音；
2. 在 Prometheus（默认 `http://127.0.0.1:9100/metrics`）查询：

   ```promql
   # P50 / P95 转写时延
   histogram_quantile(0.5, rate(evopaw_asr_latency_seconds_bucket[7d]))
   histogram_quantile(0.95, rate(evopaw_asr_latency_seconds_bucket[7d]))
   ```

3. 统计真实音频时长分布（可 grep `data/logs/` 中 `duration_ms`）。

**调参规则**：

- `short_wait_s`：让 ≥ 80% 的请求在不发 ack 的情况下完成 → 取 P80(latency) 的整秒。
- `long_audio_threshold_ms`：取语音时长的 P75，超出此长度的语音多数 ASR 时间也 > short_wait_s，提前发 ack 体验更好。

把新值写到 `config.yaml.template` 并提交。

## 3. 步骤 C：固定 Fun-ASR 模型快照号（设计文档 §9.1）

**目的**：避免阿里云后续把 `fun-asr-realtime` 别名指向行为不同的新模型导致回归。

**步骤**：

1. 启动 EvoPaw，看日志里 `_warn_if_model_is_alias` 是否打了 WARN。打了说明仍在用别名。
2. 打开 [Fun-ASR 实时 WebSocket API 文档](https://help.aliyun.com/zh/model-studio/fun-asr-realtime-websocket-api) 查最新发布的快照版本号。**不要构造推测版本号**。
   - 截至 `2026-04-25` 检索阿里云文档时列出的快照号（按时间倒序）：
     - `fun-asr-realtime-2026-02-28`（最新）
     - `fun-asr-realtime-2025-11-07`（稳定别名 `fun-asr-realtime` 当前指向此版本）
     - `fun-asr-realtime-2025-09-15`
     - `fun-asr-flash-8k-realtime-2026-01-28`（§18.2 方案 A 切到 8kHz 模型时使用）
   - 这些快照号只是当时检索结果，**真实上线前一定要重新打开文档复核**。
   - 通常选择"最新一档" + "上线前已存在 ≥ 1 个月"两项条件都满足的快照号最稳。
3. 改 `config.yaml`：

   ```yaml
   asr:
     model: "fun-asr-realtime-2025-11-07"   # ← 替换为官方实际发布的最新快照
   ```

4. 重启服务，确认 WARN 消失，跑一条真实语音验证转写质量未回退。
5. 在设计文档 §9.1 末尾追加一行：`生产实际使用：fun-asr-realtime-2025-11-07（YYYY-MM-DD 固定）`。

## 4. 步骤 D：四类真实样例联调（设计文档 §16.3）

**目的**：把 mock 单测覆盖不到的真实链路验收一次。

**测试矩阵**：

| # | 样例 | 验收点 |
|---|------|-------|
| 1 | 3 秒中文（飞书真实录音） | 短卡片内一次完成（无 ack 消息），`reply` 含 `语音转写：` + `回答：` 两段 |
| 2 | 20 秒中文（飞书真实录音） | 先收到 ack 文案，后收到正式回复；session 历史只写最终回复 |
| 3 | 中英混合 / 口音 | transcript 非空；Agent 能基于内容回答 |
| 4 | 损坏文件（手动改坏 file_key 对应字节） | 飞书侧返回 `语音转写失败` 类文案，无 worker 崩溃，`evopaw_audio_messages_total{status="asr_failed"}` 递增 |

**观测**：

- `curl http://127.0.0.1:9100/metrics | grep evopaw_asr_` 检查 6 个 ASR 指标都被打上正确的 status label。
- `data/logs/latest.log` 不应出现 `Task was destroyed but it is pending!`（半开连接征兆）。
- 重复发送同一条语音消息（飞书重投递场景）确认 `evopaw_audio_dedup_hits_total` 递增 1。

## 5. 完成判定

把以下结果回写到设计文档 §0 / §17.4 / §18：

- 步骤 A 结论 → §18.2
- 步骤 B 实际选用的两个阈值 → §0 表格 / `config.yaml.template`
- 步骤 C 固定的快照号 → §9.1
- 步骤 D 全部 4 个样例验收通过 → §16.3 标 ✅、§17.4 整阶段标 ✅

> 当四项都完成并回写后，可以把 §0 阶段表的 Phase 4 标记为 ✅，并提交一个标题类似 `feat(voice): pre-production tuning + runbook` 的 PR / commit。
