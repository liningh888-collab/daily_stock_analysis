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
    format='%(asctime)s - %(levelname)s - %(funcName)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ======================== 核心配置 ========================
FIXED_STOCKS = ["000968", "600028", "600968"]
EXTRA_RECOMMEND_COUNT = 4
FEISHU_WEBHOOK = CONFIG.get("channels", {}).get("feishu", {}).get("webhook", {}).get("url", "")

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
    tr = (high_list - low_list).replace(0, np.nan)
    rsv = (close - low_list) / tr * 100
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

# ======================== 单只股票分析 ========================
def analyze_one_stock(code):
    max_retry = 2
    for retry in range(max_retry):
        try:
            time.sleep(0.1 * (retry + 1))
            df_spot = ak.stock_zh_a_spot_em()
            if df_spot.empty:
                raise Exception("实时行情为空")
            df_spot = df_spot[df_spot['代码'].str.match(r'^\d{6}$')]
            row = df_spot[df_spot["代码"] == code]
            if row.empty:
                raise Exception("未找到代码")
            row = row.iloc[0]
            current = round(float(row["最新价"]), 2)
            change = round(float(row["涨跌幅"]), 2)
            name = row["名称"]

            hist = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=(datetime.now()-timedelta(days=120)).strftime("%Y%m%d"),
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
            if macd.iloc[-1] > signal.iloc[-1]: score +=1
            if bot_div: score +=2
            if current > ma20: score +=1
            if j.iloc[-1] < 20: score +=1
            try:
                pe = float(row.get('市盈率-动态', 1000))
                if 0 < pe < 30: score +=1
                if pe < 15: score +=1
            except: pass
            if vol_status == "量价健康": score +=1

            grade = "观望"
            if score >=5: grade = "🔥 强烈买入"
            elif score>=3: grade = "✅ 买入"
            elif score<=-2:grade = "❌ 卖出"
            elif score<=-1:grade = "⚠️ 减仓"

            stop = round(current - 1.6*atr, 2)
            if stop < current*0.93: stop = round(current*0.94, 2)
            buy_price = round(current*0.97, 2)
            volume = int(MAX_SINGLE / buy_price //100 *100)
            if volume <100: volume=100

            return {
                "code":code, "name":name, "price":current, "change":change,
                "ma5":ma5,"ma10":ma10,"ma20":ma20,"ma60":ma60,
                "vol_status":vol_status, "atr":atr, "rsi":rsi,
                "k":round(k.iloc[-1],1),"d":round(d.iloc[-1],1),"j":round(j.iloc[-1],1),
                "macd":round(macd.iloc[-1],2),"signal":round(signal.iloc[-1],2),
                "top_div":top_div,"bot_div":bot_div,
                "trend":trend,"grade":grade,"stop":stop,
                "buy_price":buy_price,"volume":volume,
                "profit10":round(buy_price*1.10,2),
                "profit15":round(buy_price*1.15,2),
                "score":score
            }
        except Exception as e:
            if retry == max_retry-1: return None
            continue
    return None

# ======================== 全市场扫描（超快版） ========================
def scan_market_for_recommend(exclude_codes, top_n=4):
    logger.info("开始全市场快速扫描...")
    try:
        df = ak.stock_zh_a_spot_em()
        df = df[df['代码'].str.match(r'^(60|00|30)\d{4}$')]
        df = df[~df['名称'].str.contains('ST|退|XD|XR', na=False)]
        df['总市值'] = pd.to_numeric(df['总市值'], errors='coerce')
        df = df[(df['总市值'] >= 100) & (df['总市值'] <= 5000)]
        codes = df['代码'].tolist()[:100]  # 🔥 只扫100只，速度超快
    except:
        return []

    pool = []
    for code in codes:
        stock = analyze_one_stock(code)
        if stock and stock['score'] >=3:
            pool.append(stock)
        time.sleep(0.1) # 更短延迟

    pool = sorted(pool, key=lambda x: x['score'], reverse=True)
    return pool[:top_n]

# ======================== 推送 & 主程序 ========================
def get_operation(s):
    return f"""
📋 同花顺条件单
├─ 股票：{s['code']} {s['name']}
├─ 买入：≤ {s['buy_price']} 元，{s['volume']}股
├─ 止盈10%：{s['profit10']} 元
├─ 止盈15%：{s['profit15']} 元
└─ 止损：{s['stop']} 元
"""

def send_feishu(text):
    if not FEISHU_WEBHOOK:
        logger.warning("飞书Webhook未配置")
        return False
    max_bytes = 19000
    text_bytes = text.encode('utf-8')
    if len(text_bytes) > max_bytes:
        text = text_bytes[:max_bytes].decode('utf-8','ignore') + "\n...（过长截断）"
    for retry in range(2):
        try:
            res = requests.post(FEISHU_WEBHOOK, json={"msg_type":"text","content":{"text":text}}, timeout=15)
            res.raise_for_status()
            return True
        except:
            time.sleep(1)
    return False

def main():
    selected = []
    logger.info("固定分析：蓝焰控股、中国石化、海油发展")

    # 固定3只
    for code in FIXED_STOCKS:
        stock = analyze_one_stock(code)
        if stock:
            selected.append(stock)
        time.sleep(0.2)

    # 市场扫描（超快版）
    exclude = FIXED_STOCKS + [s['code'] for s in selected]
    extra_stocks = scan_market_for_recommend(exclude, EXTRA_RECOMMEND_COUNT)
    selected += extra_stocks

    if not selected:
        send_feishu("⚠️ 今日无符合条件股票")
        return

    msg = "🚀 OpenClaw 能源股+全市场精选报告\n" + "="*60 + "\n"
    msg += f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
    msg += "="*60 + "\n\n"

    msg += "🔹 固定关注股\n"
    for s in [x for x in selected if x['code'] in FIXED_STOCKS]:
        msg += f"【{s['code']} {s['name']}】\n💵 {s['price']}元 | 评级：{s['grade']}\n📉 止损：{s['stop']}\n"
        msg += get_operation(s) + "\n------------------------\n\n"

    if extra_stocks:
        msg += "🔹 全市场精选推荐\n"
        for s in extra_stocks:
            msg += f"【{s['code']} {s['name']}】\n💵 {s['price']}元 | 评级：{s['grade']}\n"
            msg += get_operation(s) + "\n------------------------\n\n"

    msg += "⚠️ 分析仅供学习，不构成投资建议"
    send_feishu(msg)
    logger.info("推送完成！全程仅需十几秒～")

if __name__ == "__main__":
    main()
