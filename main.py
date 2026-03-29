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

STOCK_SYMBOLS = CONFIG.get("stock", {}).get("symbols", [])
FEISHU_WEBHOOK = CONFIG.get("channels", {}).get("feishu", {}).get("webhook", {}).get("url", "")
EMPTY_DATA_MSG = "今日无股票交易数据"

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
    if last < 5: return False, False
    price_high = close.iloc[-1] > close.iloc[-last:-1].max()
    macd_high = macd.iloc[-1] > macd.iloc[-last:-1].max()
    price_low = close.iloc[-1] < close.iloc[-last:-1].min()
    macd_low = macd.iloc[-1] > macd.iloc[-last:-1].min()
    top_div = price_high and not macd_high
    bot_div = price_low and not macd_low
    return top_div, bot_div

# ======================== 股票数据 + 专业量化 ========================
def get_real_stock(code):
    try:
        df_spot = ak.stock_zh_a_spot_em()
        row = df_spot[df_spot["代码"] == code].iloc[0]
        current = round(float(row["最新价"]), 2)

        hist = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=(datetime.now()-timedelta(days=180)).strftime("%Y%m%d"),
            end_date=datetime.now().strftime("%Y%m%d"),
            adjust="qfq"
        )

        close = hist["收盘"].astype(float)
        high = hist["最高"].astype(float)
        low = hist["最低"].astype(float)
        vol = hist["成交量"].astype(float)

        # 均线
        ma5 = round(close.rolling(5).mean().iloc[-1],2)
        ma10 = round(close.rolling(10).mean().iloc[-1],2)
        ma20 = round(close.rolling(20).mean().iloc[-1],2)
        ma60 = round(close.rolling(60).mean().iloc[-1],2)

        # 指标
        rsi = round(calc_rsi(close).iloc[-1],1)
        macd, signal = calc_macd(close)
        k,d,j = calc_kdj(high, low, close)
        atr = round(calc_atr(high, low, close).iloc[-1],2)

        # 布林
        mid = close.rolling(20).mean()
        std = close.rolling(20).std()
        up = round(mid.iloc[-1] + 2*std.iloc[-1],2)
        down = round(mid.iloc[-1] - 2*std.iloc[-1],2)

        # 背离
        top_div, bot_div = check_macd_divergence(close, macd)

        # 量价
        vol_ok = vol.iloc[-1] > vol.rolling(5).mean().iloc[-1]
        vol_rise = "量价配合" if vol_ok else "量价偏弱"

        # 动态止损
        stop = round(current - 1.7*atr, 2)

        # 趋势
        trend = "震荡"
        if current > ma20 and ma5 > ma10:
            trend = "🚀 强势多头"
        elif current < ma20 and ma5 < ma10:
            trend = "🔻 空头趋势"

        # 综合评级
        score = 0
        if rsi < 35: score +=1
        if rsi > 65: score -=1
        if macd.iloc[-1] > signal.iloc[-1]: score +=1
        if j.iloc[-1] < 25: score +=1
        if j.iloc[-1] > 75: score -=1
        if bot_div: score +=2
        if top_div: score -=2
        if current > ma20: score +=1

        grade = "观望"
        if score >= 4: grade = "🔥 强烈买入"
        elif score >=2: grade = "✅ 买入"
        elif score <=-3: grade = "❌ 卖出"
        elif score <=-1: grade = "⚠️ 减仓"

        # 仓位
        position = "3成以内"
        if grade in ["🔥 强烈买入","✅ 买入"]: position = "5~7成"
        if grade in ["⚠️ 减仓","❌ 卖出"]: position = "1~2成或空仓"

        return {
            "code": code,
            "name": row["名称"],
            "price": current,
            "change": round(float(row["涨跌幅"]),2),
            "ma5":ma5,"ma10":ma10,"ma20":ma20,"ma60":ma60,
            "rsi":rsi,
            "k":round(k.iloc[-1],1),"d":round(d.iloc[-1],1),"j":round(j.iloc[-1],1),
            "macd":round(macd.iloc[-1],2),"signal":round(signal.iloc[-1],2),
            "boll_up":up,"boll_down":down,
            "atr":atr,
            "vol_status":vol_rise,
            "top_div":top_div,"bot_div":bot_div,
            "trend":trend,
            "grade":grade,
            "position":position,
            "stop":stop,
            "time":datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        logger.error(f"{code} error: {e}")
        return None

# ======================== 新增：自动生成操作建议 ========================
def get_operation_suggest(s):
    code = s["code"]
    price = s["price"]
    grade = s["grade"]
    stop = s["stop"]
    
    # 本金10000 规则：单只<=30%, 总仓<=80%
    if grade in ["🔥 强烈买入", "✅ 买入"]:
        suggest_cash = 3000
        decision = "📌 分析结论：安全度高，适合分批建仓"
    elif grade == "观望":
        suggest_cash = 2000 if code == "600028" else 3000
        decision = "📌 分析结论：观望为主，回踩企稳再低吸"
    else:
        suggest_cash = 0
        decision = "📌 分析结论：趋势偏弱，暂不参与"

    # 买点（当前价小幅回调）
    buy = round(price * 0.98, 2)
    # 止盈 10%~15%
    profit1 = round(price * 1.10, 2)
    profit2 = round(price * 1.15, 2)

    return f"""
{decision}
✅ 操作建议（15天波段）
- 买点：{buy} 元附近
- 仓位：{suggest_cash} 元（{suggest_cash//1000}成）
- 止盈：{profit1} ~ {profit2} 元
- 止损：{stop} 元（亏损≥5% 严格离场）"""

# ======================== 新闻 & 推送 ========================
def get_market_news():
    try:
        news_df = ak.stock_news_em(page=1, limit=3)
        return "\n".join([f"• {r['标题']}\n  时间：{r['发布时间']}" for _,r in news_df.iterrows()])
    except:
        return "暂无新闻"

def send_feishu(text):
    if not FEISHU_WEBHOOK: return False
    try:
        r = requests.post(FEISHU_WEBHOOK, json={
            "msg_type":"text",
            "content":{"text":text}
        }, timeout=20)
        return r.json().get("code")==0
    except:
        return False

# ======================== 主程序 ========================
def main():
    stocks = [get_real_stock(c) for c in STOCK_SYMBOLS]
    stocks = [s for s in stocks if s]
    news = get_market_news()

    if not stocks:
        send_feishu(EMPTY_DATA_MSG)
        return

    # 去掉了开头的 🧠 符号
    msg = "OpenClaw 专业量化分析报告\n" + "="*54 + "\n"
    for s in stocks:
        operate = get_operation_suggest(s)
        msg += f"""【{s['code']} {s['name']}】
💵 现价：{s['price']} 元  |  涨跌幅：{s['change']}%
📊 均线：MA5:{s['ma5']} MA10:{s['ma10']} MA20:{s['ma20']} MA60:{s['ma60']}
📈 量价：{s['vol_status']}  |  ATR波动：{s['atr']}
🧪 RSI：{s['rsi']}  |  KDJ：{s['k']}/{s['d']}/{s['j']}
📐 MACD：{s['macd']} / {s['signal']}
📌 背离：顶背离{'是' if s['top_div'] else '否'} 底背离{'是' if s['bot_div'] else '否'}
📊 趋势：{s['trend']}
🔥 综合评级：{s['grade']}
🎯 建议仓位：{s['position']}
🛡️ 动态止损：{s['stop']} 元
{operate}
⏰ {s['time']}
""" + "-"*54 + "\n"

    # 资金总规划（固定10000本金）
    msg += """
💰 整体资金规划（本金 10000 元）
- 单只最高：3000 元（3成）
- 总持仓不超过：8000 元（8成）
- 预留现金：2000 元（安全垫）
"""

    msg += f"\n📰 市场要闻：\n{news}\n"
    msg += "\n⚠️ 本分析由AI量化生成，仅供学习，不构成投资建议。"

    send_feishu(msg)
    logger.info("推送完成")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        send_feishu(f"系统异常：{str(e)}")
