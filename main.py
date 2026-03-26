import akshare as ak
import pandas as pd
import os
import requests
from datetime import datetime

# 环境配置
STOCK_LIST = os.getenv("STOCK_LIST", "600519").split(",")
FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL", "")

# 获取股票名称
def get_name(ts_code):
    code = ts_code.split(".")[0]
    try:
        stock_info = ak.stock_info_a_code_name()
        name = stock_info[stock_info["code"] == code]["name"].values[0]
        return name
    except:
        return ts_code

# 核心指标计算（用 akshare 获取免费日线数据）
def stock_analysis(ts_code):
    code = ts_code.split(".")[0]
    try:
        # 获取近60个交易日复权后数据
        df = ak.stock_zh_a_daily(symbol=code, adjust="hfq")
        df = df.tail(60)
        if len(df) < 20:
            return None
    except Exception as e:
        print(f"获取 {ts_code} 数据失败: {e}")
        return None

    last = df.iloc[-1]
    pre = df.iloc[-2]

    # 均线趋势判断
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma10"] = df["close"].rolling(10).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    
    if last["close"] > df["ma5"].iloc[-1] > df["ma10"].iloc[-1]:
        trend = "多头趋势"
    elif last["close"] < df["ma5"].iloc[-1] < df["ma10"].iloc[-1]:
        trend = "空头趋势"
    else:
        trend = "震荡整理"

    # MACD 多空判断
    df["ema12"] = df["close"].ewm(span=12, adjust=False).mean()
    df["ema26"] = df["close"].ewm(span=26, adjust=False).mean()
    df["dif"] = df["ema12"] - df["ema26"]
    df["dea"] = df["dif"].ewm(span=9, adjust=False).mean()
    macd = "偏多" if df["dif"].iloc[-1] > df["dea"].iloc[-1] else "偏空"

    # 量能状态
    vol_avg = df["volume"].iloc[-5:].mean()
    vol_rate = last["volume"] / vol_avg if vol_avg > 0 else 1
    vol = "放量" if vol_rate > 1.5 else "缩量" if vol_rate < 0.7 else "平量"

    # RSI 超买超卖
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, 0.0001)
    rsi = 100 - (100 / (1 + rs))
    rsi_v = round(rsi.iloc[-1], 1)
    if rsi_v > 70:
        rsi_s = "超买"
    elif rsi_v < 30:
        rsi_s = "超卖"
    else:
        rsi_s = "正常"

    # 支撑压力 & 涨跌幅
    support = round(df["low"].iloc[-10:].min(), 2)
    pressure = round(df["high"].iloc[-10:].max(), 2)
    pct = round((last["close"] / pre["close"] - 1) * 100, 2)

    return {
        "name": get_name(ts_code),
        "code": ts_code,
        "price": round(last["close"], 2),
        "pct": pct,
        "trend": trend,
        "macd": macd,
        "vol": vol,
        "rsi": f"{rsi_v}({rsi_s})",
        "support": support,
        "pressure": pressure
    }

# 生成四时段报告
def make_report():
    now = datetime.now().strftime("%H:%M")
    if "09:30" in now:
        title = "早盘监测"
    elif "11:30" in now:
        title = "午盘综述"
    elif "15:30" in now:
        title = "收盘总结"
    elif "17:30" in now:
        title = "盘后分析"
    else:
        title = "个股监测"

    lines = [title, "------------------"]
    for code in STOCK_LIST:
        code = code.strip()
        res = stock_analysis(code)
        if not res:
            continue

        lines.append(f"{res['name']} {res['code']}")
        lines.append(f"价格：{res['price']}  涨跌幅：{res['pct']}%")
        lines.append(f"趋势：{res['trend']}")
        lines.append(f"MACD：{res['macd']}  量能：{res['vol']}")
        lines.append(f"RSI：{res['rsi']}")
        lines.append(f"支撑：{res['support']}  压力：{res['pressure']}")
        lines.append("")

    return "\n".join(lines)

# 飞书纯文字推送
def push(content):
    if not FEISHU_WEBHOOK_URL:
        return
    try:
        requests.post(FEISHU_WEBHOOK_URL, json={
            "msg_type": "text",
            "content": {"text": content}
        }, timeout=8)
    except Exception as e:
        print(f"推送失败: {e}")

if __name__ == "__main__":
    report = make_report()
    print(report)
    push(report)
