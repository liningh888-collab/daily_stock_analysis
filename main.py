import requests
import json
import logging
import os
from datetime import datetime, timedelta
import time
import akshare as ak
import pandas as pd
import numpy as np

# ======================== 配置初始化 ========================
config_content = os.environ.get("CONFIG_CONTENT")
if not config_content:
    raise Exception("请在 GitHub Secrets 配置 CONFIG_CONTENT")

CONFIG = json.loads(config_content)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ======================== 股票列表 ========================
MANUAL_STOCKS = CONFIG.get("stock", {}).get("symbols", [])
FEISHU_WEBHOOK = CONFIG.get("channels", {}).get("feishu", {}).get("webhook", {}).get("url", "")

# 仓位规则
TOTAL_CAPITAL = 10000
MAX_SINGLE = 3000
MAX_TOTAL = 8000

# ======================== 指标工具库 ========================
def calc_rsi(series, n=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = -delta.clip(upper=0).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    rs = rs.fillna(0)
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
    rsv = (close - low_list) / (high_list - low_list).replace(0, np.nan) * 100
    rsv = rsv.fillna(0)
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

# ======================== 股票分析（自选强制推送，不过滤） ========================
def analyze_stock_manual(code):
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

        vol_ma5 = vol.rolling(5).mean().iloc[-1]
        vol_status = "量价偏弱"
        if vol.iloc[-1] > vol_ma5 * 1.2:
            vol_status = "量价健康"
        elif vol.iloc[-1] < vol_ma5 * 0.8:
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

        score = 0
        if rsi < 35: score += 1
        if macd.iloc[-1] > signal.iloc[-1]: score += 1
        if bot_div: score += 2
        if current > ma20: score += 1

        grade = "观望"
        if score >= 4:
            grade = "🔥 强烈买入"
        elif score >= 2:
            grade = "✅ 买入"
        elif score <= -3:
            grade = "❌ 卖出"
        elif score <= -1:
            grade = "⚠️ 减仓"

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
            "pool": "自选关注", "rps": "-",
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        logger.error(f"{code} 分析失败: {e}")
        return None

# ======================== 操作建议 ========================
def get_operation(s):
    p = s["price"]
    cash = min(3000, MAX_SINGLE)
    buy = round(p * 0.97, 2)
    profit1 = round(buy * 1.10, 2)
    profit2 = round(buy * 1.15, 2)
    stop = s["stop"]

    return f"""
✅ 操作建议
- 买点：{buy} 元
- 仓位：{cash} 元
- 止盈：{profit1} ~ {profit2}
- 止损：{stop} 元
"""

# ======================== 飞书推送（修复超长+失败无提示） ========================
def send_feishu(text):
    if not FEISHU_WEBHOOK:
        logger.warning("飞书 Webhook 未配置")
        return False

    max_bytes = 19000
    text_bytes = text.encode('utf-8')
    if len(text_bytes) > max_bytes:
        text = text_bytes[:max_bytes].decode('utf-8', 'ignore') + "\n...内容过长已截断"

    try:
        res = requests.post(
            FEISHU_WEBHOOK,
            json={"msg_type": "text", "content": {"text": text}},
            timeout=20
        )
        logger.info(f"飞书推送结果：{res.status_code} {res.text}")
        return True
    except Exception as e:
        logger.error(f"飞书推送失败：{e}")
        return False

# ======================== 主程序（确保自选股一定推送） ========================
def main():
    selected = []

    # 自选股强制加入
    for code in MANUAL_STOCKS:
        stock = analyze_stock_manual(code)
        if stock:
            selected.append(stock)
        time.sleep(0.3)

    # 没有任何自选数据
    if not selected:
        send_feishu("⚠️ 自选股数据获取失败，今日无分析内容")
        return

    # 拼接报告
    msg = "🚀 OpenClaw 每日量化分析\n" + "="*50 + "\n"

    for s in selected:
        msg += f"""【{s['code']} {s['name']}】
💵 现价：{s['price']} 元  |  涨跌幅：{s['change']}%
📈 均线：MA5:{s['ma5']} MA10:{s['ma10']} MA20:{s['ma20']}
📊 量价：{s['vol_status']}
🧪 RSI：{s['rsi']}  |  KDJ：{s['k']}/{s['d']}/{s['j']}
📐 MACD：{s['macd']} / {s['signal']}
🔥 评级：{s['grade']}
🛡️ 止损：{s['stop']} 元
"""
        msg += get_operation(s)
        msg += "-"*50 + "\n"

    # 末尾提示
    msg += "\n📌 今日无额外优质标的，仅展示自选股"
    msg += "\n⚠️ 分析仅供学习，不构成投资建议。"

    send_feishu(msg)

if __name__ == "__main__":
    main()
