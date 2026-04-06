import requests
import json
import logging
import os
from datetime import datetime, timedelta
import time
import pandas as pd
import numpy as np
import yfinance as yf

# ======================== 配置 ========================
config_content = os.environ.get("CONFIG_CONTENT")
if not config_content:
    raise Exception("请配置 CONFIG_CONTENT")
CONFIG = json.loads(config_content)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

FEISHU_WEBHOOK = CONFIG.get("channels", {}).get("feishu", {}).get("webhook", {}).get("url", "")

# A股对应 yfinance 代码
STOCK_MAP = {
    "000968.SZ": "蓝焰控股",
    "600028.SS": "中国石化",
    "600968.SS": "海油发展"
}

CAPITAL = 10000
SINGLE_MAX = 3000

# ======================== 指标计算 ========================
def calc_indicators(df):
    df = df.sort_index()
    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    # 均线
    ma5  = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()

    # RSI(14)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()

    # KDJ(9)
    low9 = low.rolling(9).min()
    high9 = high.rolling(9).max()
    tr = high9 - low9
    tr = tr.replace(0, 1)
    rsv = (close - low9) / tr * 100
    k = rsv.ewm(span=3, adjust=False).mean()
    d = k.ewm(span=3, adjust=False).mean()
    j = 3 * k - 2 * d

    # 最新值
    last_i = -1
    return {
        "price": round(close.iloc[last_i], 2),
        "ma5": round(ma5.iloc[last_i], 2),
        "ma20": round(ma20.iloc[last_i], 2),
        "rsi": round(rsi.iloc[last_i], 1),
        "macd": round(macd_line.iloc[last_i], 2),
        "signal": round(signal_line.iloc[last_i], 2),
        "k": round(k.iloc[last_i], 1),
        "d": round(d.iloc[last_i], 1),
        "j": round(j.iloc[last_i], 1),
        # 金叉判断：MACD线上穿信号线
        "macd_gold": macd_line.iloc[last_i] > signal_line.iloc[last_i] and macd_line.iloc[last_i-1] <= signal_line.iloc[last_i-1],
        "trend_up": close.iloc[last_i] > ma20.iloc[last_i]
    }

# ======================== 核心量化策略 ========================
def strategy_buy(ind):
    """
    严格买入策略：
    1. RSI < 40（超跌）
    2. MACD 金叉
    3. J 值 < 40（KDJ低位）
    4. 站稳 20 日均线
    """
    cond1 = ind["rsi"] < 40
    cond2 = ind["macd_gold"]
    cond3 = ind["j"] < 40
    cond4 = ind["trend_up"]

    score = sum([cond1, cond2, cond3, cond4])
    buy_signal = score >= 3

    reason = []
    if cond1: reason.append("RSI超跌")
    if cond2: reason.append("MACD金叉")
    if cond3: reason.append("KDJ低位")
    if cond4: reason.append("趋势向上")

    return {
        "buy": buy_signal,
        "score": score,
        "reason": " + ".join(reason) if reason else "无满足条件"
    }

# ======================== 获取K线 ========================
def get_hist(symbol):
    try:
        tk = yf.Ticker(symbol)
        df = tk.history(period="90d", timeout=15)
        if len(df) < 30:
            return None
        return calc_indicators(df)
    except Exception as e:
        logger.warning(f"{symbol} 获取失败: {str(e)}")
        return None

# ======================== 条件单 ========================
def calc_order(price):
    buy_price = round(price * 0.97, 2)
    volume = int(SINGLE_MAX / buy_price // 100 * 100)
    volume = max(volume, 100)
    return {
        "buy_price": buy_price,
        "volume": volume,
        "profit10": round(buy_price * 1.1, 2),
        "profit15": round(buy_price * 1.15, 2),
        "stop": round(price * 0.94, 2)
    }

# ======================== 推送 ========================
def send_feishu(text):
    if not FEISHU_WEBHOOK:
        return False
    try:
        requests.post(FEISHU_WEBHOOK, json={
            "msg_type": "text",
            "content": {"text": text}
        }, timeout=10)
        return True
    except Exception as e:
        logger.error(f"推送失败: {e}")
        return False

# ======================== 主程序 ========================
def main():
    logger.info("开始严格量化策略分析")
    result = []

    for symbol, name in STOCK_MAP.items():
        ind = get_hist(symbol)
        if not ind:
            continue

        strategy = strategy_buy(ind)
        order = calc_order(ind["price"])

        result.append({
            "code": symbol.replace(".SS","").replace(".SZ",""),
            "name": name,
            **ind,
            **strategy,
            **order
        })
        time.sleep(0.8)

    if not result:
        send_feishu("今日行情获取失败")
        return

    msg = f"🚀 OpenClaw 严格量化策略报告\n{datetime.now().strftime('%Y-%m-%d %H:%M')}\n" + "="*50 + "\n"

    for r in result:
        signal = "🔥 买入信号" if r["buy"] else "⚠️ 观望"
        msg += f"""
【{r['code']} {r['name']}】 {signal}
现价：{r['price']} 元 | 得分：{r['score']}/4
条件满足：{r['reason']}

📊 指标
RSI：{r['rsi']}   MACD金叉：{'是' if r['macd_gold'] else '否'}
KDJ：{r['k']}/{r['d']}/{r['j']}
MA5：{r['ma5']}  MA20：{r['ma20']}  趋势向上：{'是' if r['trend_up'] else '否'}

📋 条件单
买入 ≤ {r['buy_price']} 元，{r['volume']} 股
止盈10%：{r['profit10']}  止盈15%：{r['profit15']}
止损：{r['stop']}
""" + "-"*50 + "\n"

    msg += "\n⚠️ 仅量化学习参考，不构成投资建议"
    send_feishu(msg)
    logger.info("策略执行完成")

if __name__ == "__main__":
    main()
