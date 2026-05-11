---
name: investment-report
description: 生成 A 股 + 港股每日投资早报。覆盖主要指数行情、用户持仓换手率/量价、操作建议三段。触发关键词：早报 / 今日行情 / 投资报告 / 投资早报 / 每日复盘。如果用户只想要港股早报，请改用 hk-investment-morning-report；只要纯格式日报请改用 daily-summary。
type: task
version: "1.1"
requires:
  bins: ["python3"]
---

# investment-report Skill

## 数据来源

由 `${EVOPAW_SKILL_BASE}/scripts/generate_report.py` 调用 [`akshare`](https://akshare.akfamily.xyz/) 抓取实时行情，无需浏览器，也不要尝试调用 `browser_*` / `sandbox_*` 等子 Agent 不具备的工具。

脚本默认采集：
- A 股指数：上证指数、深证成指、创业板指、科创 50（涨跌幅 %）。
- 港股指数：恒生指数、国企指数（涨跌幅 %）。
- 用户持仓：换手率、量价。

## 调用方式

```bash
python ${EVOPAW_SKILL_BASE}/scripts/generate_report.py \
    --positions ${EVOPAW_SESSION_DIR}/tmp/positions.json \
    --output ${EVOPAW_SESSION_DIR}/outputs/report-${EVOPAW_TODAY}.txt
```

参数：

| 参数 | 是否必填 | 说明 |
|---|---|---|
| `--positions` | 否 | 持仓 JSON 文件路径；缺省时仅生成行情概要 + 通用建议 |
| `--output` | 否 | 报告写入路径；缺省 `/dev/stdout`。建议落到 `${EVOPAW_SESSION_DIR}/outputs/` 便于飞书回传 |

stdout 同时打印一行 JSON：`{"report": "...完整报告文本..."}`。

## 持仓 JSON 格式

```json
[
  {"symbol": "600519", "name": "贵州茅台", "shares": 50},
  {"symbol": "0700",   "name": "腾讯控股", "shares": 100},
  {"symbol": "sh000001"}
]
```

A 股代码用 6 位数字（如 `600519`），港股用 4–5 位数字（如 `0700`）。`shares` 字段可选，仅用于人类可读输出，不影响计算。

## 标准执行流程

1. 解析 `task_context`，提取用户提到的持仓（symbol + 数量）。
2. 把持仓写到 `${EVOPAW_SESSION_DIR}/tmp/positions.json`（用 Bash + `cat <<EOF` 即可，确保 UTF-8）。
3. 调用上面的命令生成报告，并把 stdout JSON `report` 字段读回。
4. 把报告原样作为 Sub-Agent 的最终输出文本（不要二次改写格式，主 Agent 才决定怎么发飞书）。

## 错误处理

- 脚本对单个 symbol / 单个指数失败会标 `N/A`，不抛异常；遇到大面积 `N/A` 时在最终输出顶部用一行说明数据源异常，并建议用户晚些再试。
- 用户没说持仓 → 跳过 `--positions`，只产出指数行情和通用建议（不要假设持仓）。
- akshare 包未安装 → 沙盒里通常已经预装；若 `ImportError`，向上层回报"投资数据库依赖未就绪，请联系管理员补 akshare"，不要自己 `pip install`。

## 不要做的事

- 不要让 Sub-Agent 自己复述行情数字之外的"宏观判断/政策解读"；脚本输出的"操作建议"是兜底文案，需要时让主 Agent 改写。
- 不要为了"更全面"而调用 web_browse / tavily_search 抓新闻；本 skill 的合同就是「用 akshare 出今日行情 + 持仓简报」，扩展范围请用户另起一轮。
- 不要硬编码任何日期；用 `${EVOPAW_TODAY}` 拼输出文件名。
