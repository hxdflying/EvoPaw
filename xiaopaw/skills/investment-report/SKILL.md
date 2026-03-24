---
name: investment-report
description: Use this skill when the user asks for 早报, 今日行情, or 投资报告. Generate a daily investment report with A-share/HK market summary, position turnover analysis, and actionable recommendations.
---

# Investment Report Skill

## Overview
This skill generates a structured daily investment report based on current market data and user's portfolio holdings.

## Input Requirements
- Market data for A-share and HK stocks (will be fetched via browser tools)
- User's current stock holdings with quantities (assumed to be provided by user or available in context)

## Output Format
The report must follow this exact structure:

### 今日行情
- Summary of major A-share indices (上证指数, 深证成指, 创业板指) with percentage changes
- Summary of major HK indices (恒生指数, 国企指数) with percentage changes
- Top 3 gainers and losers in each market

### 持仓观察
- For each holding in user's portfolio:
  - Stock symbol and name
  - Current price and daily change %
  - Turnover rate calculation (volume / float shares)
  - Notable price movements or volume spikes

### 操作建议
- Clear, actionable recommendations based on:
  - Overall market sentiment (bullish/bearish/neutral)
  - Individual holding performance relative to market
  - Turnover rate implications (high turnover may indicate institutional activity)

## Execution Steps

1. **Fetch Market Data**
   - Use browser tools to navigate to financial websites (e.g., sina finance, eastmoney) to get current A-share market data
   - Use browser tools to get HK market data from reliable sources
   - Extract major indices and their changes

2. **Process Holdings Data**
   - If user provides holdings in the request, parse them directly
   - If holdings are not provided, ask the user for their current positions
   - For each holding, fetch current price, volume, and float shares data

3. **Calculate Turnover Rates**
   - Turnover Rate = Trading Volume / Float Shares Outstanding
   - Flag any holdings with turnover rates > 5% as high activity

4. **Generate Report**
   - Format output exactly as specified above
   - Keep language concise and professional
   - Focus on actionable insights rather than raw data

## Examples

**Example Input:**
"生成今日投资早报，我的持仓包括：腾讯控股(0700.HK) 100股，贵州茅台(600519.SH) 50股"

**Example Output:**
### 今日行情
上证指数 +0.5% (3250.21), 深证成指 -0.2% (11200.45), 创业板指 +0.8% (2350.67)
恒生指数 +1.2% (18500.32), 国企指数 +0.9% (6500.21)

### 持仓观察
腾讯控股(0700.HK): ¥350.20 (+2.1%), Turnover: 0.8% (Normal activity)
贵州茅台(600519.SH): ¥1850.50 (-0.3%), Turnover: 0.2% (Low activity)

### 操作建议
市场整体偏强，港股表现优于A股。腾讯控股表现良好，可继续持有；贵州茅台小幅回调但基本面稳定，建议持有观望。