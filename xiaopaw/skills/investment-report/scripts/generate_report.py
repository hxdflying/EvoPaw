#!/usr/bin/env python3
"""
Generate daily investment report with market data and position analysis.
Uses akshare for market data retrieval.
"""

import json
import sys
import argparse
from datetime import datetime, timedelta
import pandas as pd
import akshare as ak


def get_market_indices():
    """Get major A-share and HK stock indices."""
    indices = {}
    
    # A-share indices
    try:
        a_indices = {
            "上证指数": "sh000001",
            "深证成指": "sz399001",
            "创业板指": "sz399006",
            "科创50": "sh000688"
        }
        
        for name, code in a_indices.items():
            try:
                df = ak.stock_zh_index_daily(symbol=code)
                if not df.empty:
                    latest = df.iloc[-1]
                    prev = df.iloc[-2] if len(df) > 1 else latest
                    change_pct = ((latest['close'] - prev['close']) / prev['close']) * 100
                    indices[f"A股-{name}"] = round(change_pct, 2)
            except Exception as e:
                indices[f"A股-{name}"] = "N/A"
                
    except Exception as e:
        pass
    
    # HK indices
    try:
        hk_indices = {
            "恒生指数": "HSI",
            "国企指数": "HSCEI",
            "恒生科技指数": "HSTECH"
        }
        
        for name, symbol in hk_indices.items():
            try:
                df = ak.index_hk_hist(symbol=symbol)
                if not df.empty:
                    latest = df.iloc[-1]
                    prev = df.iloc[-2] if len(df) > 1 else latest
                    change_pct = ((latest['close'] - prev['close']) / prev['close']) * 100
                    indices[f"港股-{name}"] = round(change_pct, 2)
            except Exception as e:
                indices[f"港股-{name}"] = "N/A"
                
    except Exception as e:
        pass
        
    return indices


def analyze_positions(positions_file):
    """Analyze user positions from JSON file."""
    if not positions_file or positions_file == "None":
        return {}
    
    try:
        with open(positions_file, 'r', encoding='utf-8') as f:
            positions = json.load(f)
        
        analysis = {}
        
        for stock in positions.get('stocks', []):
            symbol = stock.get('symbol')
            if not symbol:
                continue
                
            try:
                # Get stock data
                if symbol.endswith('.HK'):
                    # HK stock
                    df = ak.stock_hk_daily(symbol=symbol.replace('.HK', ''))
                else:
                    # A-share stock
                    df = ak.stock_zh_a_hist(symbol=symbol[:6], period="daily")
                
                if df.empty:
                    analysis[symbol] = {"turnover_rate": "N/A", "volume_price": "N/A"}
                    continue
                
                latest = df.iloc[-1]
                
                # Calculate turnover rate (simplified)
                if 'turnover_rate' in df.columns:
                    turnover_rate = latest['turnover_rate']
                else:
                    # Estimate turnover rate if not available
                    turnover_rate = "N/A"
                
                # Volume-price analysis
                if len(df) >= 2:
                    prev_close = df.iloc[-2]['close'] if 'close' in df.columns else df.iloc[-2]['收盘']
                    current_close = latest['close'] if 'close' in df.columns else latest['收盘']
                    volume = latest['volume'] if 'volume' in df.columns else latest['成交量']
                    
                    price_change_pct = ((current_close - prev_close) / prev_close) * 100
                    volume_str = f"量: {volume:,.0f}, 价: {price_change_pct:+.2f}%"
                else:
                    volume_str = "N/A"
                
                analysis[symbol] = {
                    "turnover_rate": turnover_rate if turnover_rate != "N/A" else "N/A",
                    "volume_price": volume_str
                }
                
            except Exception as e:
                analysis[symbol] = {"turnover_rate": "N/A", "volume_price": "N/A"}
                
        return analysis
        
    except Exception as e:
        return {}


def generate_report(indices, positions_analysis):
    """Generate formatted report."""
    report = []
    
    # Market行情 section
    report.append("【今日行情】")
    for index_name, change in indices.items():
        if change != "N/A":
            direction = "↑" if change > 0 else "↓" if change < 0 else "→"
            report.append(f"- {index_name}: {direction}{abs(change):.2f}%")
        else:
            report.append(f"- {index_name}: N/A")
    report.append("")
    
    # Position observation section
    report.append("【持仓观察】")
    if positions_analysis:
        for symbol, analysis in positions_analysis.items():
            report.append(f"- {symbol}: 换手率 {analysis['turnover_rate']}, {analysis['volume_price']}")
    else:
        report.append("- 无持仓数据")
    report.append("")
    
    # Operation suggestions section
    report.append("【操作建议】")
    report.append("- 市场波动较大，建议关注持仓个股基本面")
    report.append("- 如持仓出现异常放量，需重点关注")
    report.append("- 保持合理仓位，控制风险")
    
    return "\n".join(report)


def main():
    parser = argparse.ArgumentParser(description='Generate investment report')
    parser.add_argument('--positions', type=str, help='Path to positions JSON file')
    parser.add_argument('--output', type=str, default='/dev/stdout', help='Output file path')
    
    args = parser.parse_args()
    
    # Get market indices
    indices = get_market_indices()
    
    # Analyze positions
    positions_analysis = analyze_positions(args.positions)
    
    # Generate report
    report = generate_report(indices, positions_analysis)
    
    # Output report
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(json.dumps({"report": report}, ensure_ascii=False))


if __name__ == "__main__":
    main()