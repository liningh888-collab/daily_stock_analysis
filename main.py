import requests
import json
import logging
import os
from datetime import datetime, timedelta
import time
import akshare as ak
import pandas as pd
import numpy as np

# ======================== 1. 配置初始化 ========================
config_content = os.environ.get("CONFIG_CONTENT")
if not config_content:
    raise Exception("请在 GitHub Secrets 配置 CONFIG_CONTENT")

CONFIG = json.loads(config_content)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ======================== 重要：读取你手动关注的股票 ========================
MANUAL_STOCKS = CONFIG.get("stock", {}).get("symbols", [])
FEISHU_WEBHOOK = CONFIG.get("channels", {}).get("feishu", {}).get("webhook", {}).get("url", "")
EMPTY_DATA_MSG = "今日无符合条件的股票交易数据"

# 仓位规则
TOTAL_CAPITAL = 10000
MAX_SINGLE = 3000
MAX_TOTAL = 8000

# ======================== 指标工具库 ========================
def calc_rsi(series, n=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = -delta.clip(upper=0).rolling(n).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calc_macd(series):
    ema12 = series.ewm(span=12, adjust=False).mean()
    ema26 = series.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return macd, signal

def calc_kdj(high, low, close):
    low_list = low.rolling(9).min()
    high_list = high.rolling(9).max()
    rsv = (close - low_list) / (high_list - low_list) * 100
    k = rsv.ewm(span=3, adjust=False).mean()
    d = k.ewm(span=3, adjust=False).mean()
    j = 3 * k - 2 * d
    return k, d, j

def calc_atr(high, low, close):
    tr = pd.DataFrame()
    tr['h-l'] = high - low
    tr['h-pc'] = abs(high - close.shift(1))
    tr['l-pc'] = abs(low - close.shift(1))
    tr['tr'] = tr.max(axis=1)
    return tr['tr'].rolling(14).mean()

def check_macd_divergence(close, macd):
    last = min(10, len(close)-1)
    if last < 5:
        return False, False
    price_high = close.iloc[-1] > close.iloc[-last:-1].max()
    macd_high = macd.iloc[-1] > macd.iloc[-last:-1].max()
    price_low = close.iloc[-1] < close.iloc[-last:-1].min()
    macd_low = macd.iloc[-1] > macd.iloc[-last:-1].min()
    top_div = price_high and not macd_high
    bot_div = price_low and not macd_low
    return top_div, bot_div

# ======================== 股票分析函数 ========================
def analyze_stock(code, pool_type="自选"):
    try:
        df_spot = ak.stock_zh_a_spot_em()
        row = df_spot[df_spot["代码"] == code].iloc[0]
        current = round(float(row["最新价"]), 2)
        change = round(float(row["涨跌幅"]), 2)
        name = row["名称"]

        hist = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=(datetime.now()-timedelta(days=180)).strftime("%Y%m%d"),
            end_date=datetime.now().strftime("%Y%m%d"),
            adjust="qfq"
        )
        if len(hist) < 60:
            return None

        close = hist["收盘"].astype(float)
        high = hist["最高"].astype(float)
        low = hist["最低"].astype(float)
        vol = hist["成交量"].astype(float)

        ma5 = round(close.rolling(5).mean().iloc[-1], 2)
        ma10 = round(close.rolling(10).mean().iloc[-1], 2)
        ma20 = round(close.rolling(20).mean().iloc[-1], 2)
        ma60 = round(close.rolling(60).mean().iloc[-1], 2)

        vol_status = "量价偏弱"
        if vol.iloc[-1] > vol.rolling(5).mean().iloc[-1] * 1.2:
            vol_status = "量价健康"
        elif vol.iloc[-1] < vol.rolling(5).mean().iloc[-1] * 0.8:
            vol_status = "量能萎缩"

        rsi = round(calc_rsi(close).iloc[-1], 1)
        k, d, j = calc_kdj(high, low, close)
        macd, signal = calc_macd(close)
        atr = round(calc_atr(high, low, close).iloc[-1], 2)
        top_div, bot_div = check_macd_divergence(close, macd)

        trend = "震荡"
        if ma5 > ma10 > ma20 and current > ma20:
            trend = "上升"
        elif ma5 < ma10 < ma20 and current < ma20:
            trend = "下跌"

        # 综合评级
        score = 0
        if rsi < 35: score += 1
        if rsi > 65: score -= 1
        if macd.iloc[-1] > signal.iloc[-1]: score += 1
        if bot_div: score += 2
        if top_div: score -= 2
        if current > ma20: score += 1

        grade = "观望"
        if score >= 4: grade = "🔥 强烈买入"
        elif score >= 2: grade = "✅ 买入"
        elif score <= -3: grade = "❌ 卖出"
        elif score <= -1: grade = "⚠️ 减仓"

        stop = round(current - 1.6 * atr, 2)
        if stop < current * 0.93:
            stop = round(current * 0.94, 2)

        return {
            "code": code, "name": name, "price": current, "change": change,
            "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
            "vol_status": vol_status, "atr": atr, "rsi": rsi,
            "k": round(k.iloc[-1],1), "d": round(d.iloc[-1],1), "j": round(j.iloc[-1],1),
            "macd": round(macd.iloc[-1],2), "signal": round(signal.iloc[-1],2),
            "top_div": top_div, "bot_div": bot_div,
            "trend": trend, "grade": grade, "stop": stop,
            "pool": pool_type, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        logger.error(f"{code} 分析失败: {e}")
        return None

# ======================== 操作建议 ========================
def get_operation(s):
    p = s["price"]
    pool = s["pool"]
    cash = 3000 if pool in ["低位","趋势","自选"] else 2000
    if cash > 3000: cash = 3000

    buy = round(p * 0.97, 2)
    profit1 = round(buy * 1.10, 2)
    profit2 = round(buy * 1.15, 2)
    stop = s["stop"]

    conclusion = "📌 分析结论：观望为主，回踩企稳再低吸"
    if s["grade"] in ["🔥 强烈买入","✅ 买入"]:
        conclusion = "📌 分析结论：趋势健康，可分批建仓"
    elif s["grade"] in ["❌ 卖出","⚠️ 减仓"]:
        conclusion = "📌 分析结论：趋势走弱，控制风险"

    return f"""
{conclusion}
✅ 操作建议（15天波段）
- 买点：{buy} 元附近
- 仓位：{cash} 元（{cash//1000}成）
- 止盈：{profit1} ~ {profit2} 元
- 止损：{stop} 元（亏损≥5% 严格离场）"""

# ======================== 全市场选股 ========================
def get_auto_stocks():
    try:
        df = ak.stock_zh_a_spot_em()
        df = df[(df["最新价"].astype(float) <= 15) & (~df["名称"].str.contains("ST|退", na=False))]
        codes = df["代码"].tolist()[:30]
        auto = []
        for c in codes:
            if len(auto) >= 3: break
            s = analyze_stock(c, pool_type="自动优选")
            if s: auto.append(s)
        return auto
    except:
        return []

# ======================== 推送 ========================
def send_feishu(text):
    if not FEISHU_WEBHOOK: return False
    try:
        requests.post(FEISHU_WEBHOOK, json={"msg_type":"text","content":{"text":text}}, timeout=15)
        return True
    except:
        return False

# ======================== 主程序 ========================
def main():
    selected = []

    # 1. 加入你手动关注的票（海油发展、蓝焰控股、中国石化）
    for code in MANUAL_STOCKS:
        s = analyze_stock(code, pool_type="自选关注")
        if s: selected.append(s)

    # 2. 加入自动优选票（低位+趋势+超跌）
    auto_list = get_auto_stocks()
    for a in auto_list:
        if a["code"] not in [x["code"] for x in selected]:
            selected.append(a)

    if not selected:
        send_feishu(EMPTY_DATA_MSG)
        return

    msg = "OpenClaw 专业量化分析报告\n" + "="*54 + "\n"
    for s in selected:
        op = get_operation(s)
        msg += f"""【{s['code']} {s['name']}】({s['pool']})
💵 现价：{s['price']} 元  |  涨跌幅：{s['change']}%
📊 均线：MA5:{s['ma5']} MA10:{s['ma10']} MA20:{s['ma20']} MA60:{s['ma60']}
📈 量价：{s['vol_status']}  |  ATR波动：{s['atr']}
🧪 RSI：{s['rsi']}  |  KDJ：{s['k']}/{s['d']}/{s['j']}
📐 MACD：{s['macd']} / {s['signal']}
📌 背离：顶背离{'是' if s['top_div'] else '否'} 底背离{'是' if s['bot_div'] else '否'}
📊 趋势：{s['trend']}
🔥 综合评级：{s['grade']}
🛡️ 动态止损：{s['stop']} 元
{op}
⏰ {s['time']}
""" + "-"*54 + "\n"

    msg += f"""
💰 整体资金规划（本金 10000 元）
- 单只最高：3000 元（3成）
- 总持仓不超过：8000 元（8成）
- 配置：自选股 + 自动优选 = 安全分散
"""
    msg += "\n⚠️ 本分析由AI量化生成，仅供学习，不构成投资建议。"
    send_feishu(msg)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        send_feishu(f"系统异常：{str(e)}")
