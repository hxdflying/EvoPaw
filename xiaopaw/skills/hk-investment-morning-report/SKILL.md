---
name: hk-investment-morning-report
description: Use this skill when the user asks for 早报, 今日行情, or 港股早报. Generate a daily HK investment morning report with HK market summary, position turnover analysis, and moving averages for held stocks.
---

# HK Investment Morning Report Skill

## Overview
This skill generates a structured daily investment morning report focused specifically on the Hong Kong stock market, based on current market data and user's portfolio holdings.

## Input Requirements
- Market data for HK stocks (will be fetched via browser tools)
- User's current HK stock holdings with quantities (assumed to be provided by user or available in context)

## Output Format
The report must follow this exact structure:

### 今日行情
- Summary of major HK indices (恒生指数, 国企指数, 恒生科技指数) with percentage changes
- Top 3 gainers and losers in HK market
- Overall market sentiment assessment

### 持仓观察
- For each HK holding in user's portfolio:
  - Stock symbol and name
  - Current price and daily change %
  - Turnover rate calculation (volume / float shares)
  - Key moving averages (5-day, 20-day, 60-day)
  - Position relative to moving averages (above/below)

### 操作建议
- Clear, actionable recommendations based on:
  - Overall HK market sentiment (bullish/bearish/neutral)
  - Individual holding performance relative to market
  - Turnover rate implications (high turnover may indicate institutional activity)
  - Price position relative to moving averages (trend confirmation/breakout)

## Execution Steps

1. **Fetch HK Market Data**
   - Use browser tools to navigate to financial websites (e.g., sina finance HK, eastmoney HK) to get current HK market data
   - Extract major HK indices (恒生指数, 国企指数, 恒生科技指数) and their changes
   - Get top gainers and losers in HK market

2. **Process Holdings Data**
   - If user provides HK holdings in the request, parse them directly
   - If holdings are not provided, ask the user for their current HK positions
   - For each HK holding, fetch current price, volume, float shares, and historical price data for moving average calculations

3. **Calculate Metrics**
   - Turnover Rate = Trading Volume / Float Shares Outstanding
   - Flag any holdings with turnover rates > 5% as high activity
   - Calculate 5-day, 20-day, and 60-day moving averages from historical price data
   - Determine if current price is above or below each moving average

4. **Generate Report**
   - Format output exactly as specified above
   - Keep language concise and professional
   - Focus on actionable insights rather than raw data
   - Emphasize HK market-specific factors and trends

## Examples

**Example Input:**
"生成今日港股早报，我的持仓包括：腾讯控股(0700.HK) 100股，美团-W(3690.HK) 50股"

**Example Output:**
### 今日行情
恒生指数 +1.2% (18500.32), 国企指数 +0.9% (6500.21), 恒生科技指数 +1.8% (3850.45)
市场整体呈现强势，科技股领涨。

### 持仓观察
腾讯控股(0700.HK): ¥350.20 (+2.1%), Turnover: 0.8% (Normal activity)
- 5日均线: ¥345.50 (价格在均线上方)
- 20日均线: ¥340.20 (价格在均线上方)
- 60日均线: ¥335.80 (价格在均线上方)

美团-W(3690.HK): ¥120.50 (+3.2%), Turnover: 1.2% (Normal activity)
- 5日均线: ¥118.30 (价格在均线上方)
- 20日均线: ¥115.60 (价格在均线上方)
- 60日均线: ¥110.20 (价格在均线上方)

### 操作建议
港股市场今日表现强劲，科技板块领涨。您的持仓腾讯和美团均表现良好，价格位于所有关键均线之上，显示强势上涨趋势。建议继续持有，关注成交量变化和大盘支撑位。