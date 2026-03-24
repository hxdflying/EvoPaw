---
name: investment-consult
description: Use this skill when the user asks to analyze stock investment value, evaluate whether a stock is worth buying, or requests a comprehensive investment consultation. Activate whenever phrases like "分析一下", "帮我看看", or "这只股值得买吗" appear in the query.
---

# Investment Consultation Skill

This skill provides a structured investment analysis for stocks based on fundamental and technical indicators, along with risk assessment and a final recommendation.

## Allowed Tools

- `sandbox_execute_bash`: Execute shell commands to run analysis scripts
- `sandbox_file_operations`: Read/write files for data processing
- `browser_navigate`, `browser_get_markdown`: Fetch current market data from financial websites
- `sandbox_convert_to_markdown`: Convert web content to markdown format

## Workflow

1. **Identify the stock**: Extract the stock symbol or company name from the user query
2. **Gather fundamental data**: Collect P/E ratio, P/B ratio, revenue growth metrics
3. **Analyze technical indicators**: Identify trends, support levels, and resistance levels
4. **Assess risks**: Identify up to three key risk factors
5. **Generate conclusion**: Provide a one-sentence investment recommendation

## Output Format

The analysis must follow this exact structure:

# [Stock Name] Investment Analysis

## 基本面（市盈率/市净率/营收增长）
- 市盈率 (P/E): [value]
- 市净率 (P/B): [value] 
- 营收增长: [value]

## 技术面（趋势/支撑位/压力位）
- 趋势: [bullish/bearish/sideways]
- 支撑位: [price level]
- 压力位: [price level]

## 风险提示
1. [Risk factor 1]
2. [Risk factor 2] 
3. [Risk factor 3]

## 综合结论
[One-sentence investment recommendation]

## Implementation Notes

- Use browser tools to fetch real-time data from financial websites like Yahoo Finance, Google Finance, or local market data providers
- For Chinese stocks, prioritize data from East Money, Xueqiu, or similar platforms
- Always verify data currency and source reliability
- If real-time data is unavailable, clearly state assumptions and use latest available data
- The analysis should be objective and balanced, highlighting both opportunities and risks
