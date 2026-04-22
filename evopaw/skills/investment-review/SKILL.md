---
name: investment-review
description: Use this skill when the user asks for an investment review, post-mortem analysis, or reflection on a trading decision. Activate whenever the user wants to evaluate what went right, what went wrong, and get a concise one-sentence conclusion.
---

## Purpose
This skill helps users conduct a structured post-investment review. It forces clarity by breaking down the analysis into three distinct, non-overlapping parts.

## Instructions
1.  **Understand the Context**: Carefully read the user's description of the investment scenario, trade, or decision they want to review.
2.  **Generate the Three-Part Output**:
    *   **做对的地方 (What was done right)**: List the specific actions, decisions, or analyses that were correct or beneficial. Be concrete.
    *   **做错的地方 (What was done wrong)**: List the specific mistakes, misjudgments, or missed opportunities. Be honest and direct.
    *   **一句话结论 (One-sentence conclusion)**: Provide a single, powerful sentence that captures the core lesson learned from the entire experience.
3.  **Output Format**: Your response must strictly follow this format with no additional text, preambles, or summaries.

## Output Format
```
1. 做对的地方
[Your analysis here]

2. 做错的地方
[Your analysis here]

3. 一句话结论
[Your one-sentence conclusion here]
```

## Examples
**Example Input:**
"我上周买了XX股票，因为觉得它的财报很好，但没注意到大盘在回调，结果亏了5%。"

**Example Output:**
```
1. 做对的地方
基于财报进行基本面分析是正确的投资方法。

2. 做错的地方
忽略了宏观市场趋势（大盘回调）的风险，没有进行仓位控制或设置止损。

3. 一句话结论
成功的投资需要同时兼顾个股基本面和整体市场环境。
```
