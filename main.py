import requests
import json
import logging
import os
import time
import random
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import yfinance as yf

# ======================== 日志 & 配置 ========================
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

config_content = os.environ.get("CONFIG_CONTENT")
if not config_content:
    raise Exception("未配置 CONFIG_CONTENT")
CONFIG = json.loads(config_content)
FEISHU_WEBHOOK = CONFIG.get("channels", {}).get("feishu", {}).get("webhook", {}).get("url", "")

# 自选股 + 扫描池
MY_STOCKS = {
    "000968.SZ": "蓝焰控股",
    "600028.SS": "中国石化",
    "600968.SS": "海油发展"
}

SCAN_POOL = {
    "000858.SZ": "五粮液", "000333.SZ": "美的集团", "601899.SS": "紫金矿业",
    "600519.SS": "贵州茅台", "601318.SS": "中国平安", "601288.SS": "农业银行",
    "601988.SS": "中国银行", "601857.SS": "中国石油", "601088.SS": "中国神华"
}

# ======================== 指标 + 量价 ========================
def analyze_stock(symbol, name):
    try:
        tk = yf.Ticker(symbol)
        df = tk.history(period="120d", timeout=10)
        if len(df) < 60:
            return None

        close  = df["Close"]
        openp  = df["Open"]
        high   = df["High"]
        low    = df["Low"]
        volume = df["Volume"]

        # 均线
        ma5  = close.rolling(5).mean()
        ma20 = close.rolling(20).mean()

        # RSI
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta).clip(lower=0).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi  = 100 - (100 / (1 + rs))

        # MACD
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd  = ema12 - ema26
        sig   = macd.ewm(span=9, adjust=False).mean()

        # KDJ
        low9  = low.rolling(9).min()
        high9 = high.rolling(9).max()
        rsv   = (close - low9) / (high9 - low9).replace(0,1) * 100
        k     = rsv.ewm(span=3, adjust=False).mean()
        d     = k.ewm(span=3, adjust=False).mean()
        j     = 3 * k - 2 * d

        # 量价判断
        vol5     = volume.rolling(5).mean()
        vol10    = volume.rolling(10).mean()
        latest_vol = volume.iloc[-1]
        vol_condition = "量价健康"
        if close.iloc[-1] > close.iloc[-2] and latest_vol > vol5:
            vol_condition = "放量上涨"
        elif close.iloc[-1] < close.iloc[-2] and latest_vol < vol5:
            vol_condition = "缩量调整"
        elif latest_vol > vol10 * 1.5:
            vol_condition = "异常放量"

        # 条件
        cond_rsi   = rsi.iloc[-1] < 40
        cond_kdj   = j.iloc[-1] < 40
        cond_macd  = macd.iloc[-1] > sig.iloc[-1] and macd.iloc[-2] <= sig.iloc[-2]
        cond_trend = close.iloc[-1] > ma20.iloc[-1]

        # 满足条件文案（你要的风格）
        reasons = []
        if cond_rsi:
            reasons.append("RSI超跌")
        if cond_kdj:
            reasons.append("KDJ低位")
        if cond_macd:
            reasons.append("MACD金叉")
        if cond_trend:
            reasons.append("趋势向上")
        reason_str = " + ".join(reasons) if reasons else "无明显信号"

        # 得分 & 信号
        score = sum([cond_rsi, cond_kdj, cond_macd, cond_trend])
        signal = "🔥 买入信号" if score >= 3 else "⚠️ 观望"

        # 条件单
        price = close.iloc[-1]
        buy_p = round(price * 0.97, 2)
        vol   = max(int(3000 / buy_p // 100 * 100), 100)

        return {
            "code": symbol.replace(".SS","").replace(".SZ",""),
            "name": name,
            "price": round(price,2),
            "rsi": round(rsi.iloc[-1],1),
            "j": round(j.iloc[-1],1),
            "vol_condition": vol_condition,
            "signal": signal,
            "reason": reason_str,
            "buy": buy_p,
            "vol": vol,
            "profit10": round(buy_p*1.1,2),
            "stop": round(price*0.94,2)
        }
    except Exception as e:
        return None

# ======================== 扫描 & 推送 ========================
def main():
    result = []
    pool = {**MY_STOCKS, **SCAN_POOL}
    for symbol, name in pool.items():
        data = analyze_stock(symbol, name)
        if data:
            result.append(data)
        time.sleep(0.6)

    if not result:
        return

    msg = f"🚀 A股量化选股报告 {datetime.now().strftime('%m-%d %H:%M')}\n" + "="*42 + "\n"
    for r in result[:6]:
        msg += f"""
【{r['code']} {r['name']}】{r['signal']}
现价：{r['price']} 元  |  量价：{r['vol_condition']}
满足条件：{r['reason']}

RSI：{r['rsi']}   KDJ-J：{r['j']}
买入价：{r['buy']}  股数：{r['vol']}
止盈10%：{r['profit10']}  止损：{r['stop']}
------------------------------------------
"""
    msg += "\n⚠️ 仅供学习参考，不构成投资建议"

    if FEISHU_WEBHOOK:
        requests.post(FEISHU_WEBHOOK, json={
            "msg_type": "text",
            "content": {"text": msg}
        }, timeout=10)

if __name__ == "__main__":
    main()
