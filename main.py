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
# 1. 固定必须分析的3只
FIXED_STOCKS = ["000968", "600028", "600968"]
# 2. 全市场扫描后额外推荐数量
EXTRA_RECOMMEND_COUNT = 4
# 3. 飞书
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
    max_retry = 3
    for retry in range(max_retry):
        try:
            time.sleep(0.5 * (retry + 1))
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

            # 历史K线
            hist = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=(datetime.now()-timedelta(days=180)).strftime("%Y%m%d"),
                end_date=datetime.now().strftime("%Y%m%d"),
                adjust="qfq"
            )
            if len(hist) < 60:
                logger.warning(f"{code} 数据不足")
                return None

            close = hist["收盘"].astype(float)
            high = hist["最高"].astype(float)
            low = hist["最低"].astype(float)
            vol = hist["成交量"].astype(float)

            # 均线
            ma5 = round(close.rolling(5).mean().iloc[-1], 2)
            ma10 = round(close.rolling(10).mean().iloc[-1], 2)
            ma20 = round(close.rolling(20).mean().iloc[-1], 2)
            ma60 = round(close.rolling(60).mean().iloc[-1], 2)

            # 量价
            vol_ma5 = vol.rolling(5).mean().iloc[-1]
            vol_status = "量价偏弱"
            if vol.iloc[-1] > vol_ma5 * 1.2:
                vol_status = "量价健康"
            elif vol.iloc[-1] < vol_ma5 * 0.8:
                vol_status = "量能萎缩"

            # 指标
            rsi = round(calc_rsi(close).iloc[-1], 1)
            k, d, j = calc_kdj(high, low, close)
            macd, signal = calc_macd(close)
            atr = round(calc_atr(high, low, close).iloc[-1], 2)
            top_div, bot_div = check_macd_divergence(close, macd)

            # 趋势
            trend = "震荡"
            if ma5 > ma10 > ma20 and current > ma20:
                trend = "上升"
            elif ma5 < ma10 < ma20 and current < ma20:
                trend = "下跌"

            # 综合评分（基本面+技术面）
            score = 0
            # 技术面加分
            if rsi < 35:          score += 1
            if macd.iloc[-1] > signal.iloc[-1]: score +=1
            if bot_div:           score +=2
            if current > ma20:    score +=1
            if j.iloc[-1] < 20:   score +=1
            # 基本面加分（简化：低估值+盈利）
            pe = row.get('市盈率-动态', 1000)
            try:
                pe = float(pe)
                if 0 < pe < 30:   score +=1
                if pe < 15:       score +=1
            except:
                pass
            # 量能加分
            if vol_status == "量价健康": score +=1

            # 评级
            grade = "观望"
            if score >=5:  grade = "🔥 强烈买入"
            elif score>=3: grade = "✅ 买入"
            elif score<=-2:grade = "❌ 卖出"
            elif score<=-1:grade = "⚠️ 减仓"

            # 止损/买入/仓位
            stop = round(current - 1.6*atr, 2)
            if stop < current*0.93:
                stop = round(current*0.94, 2)
            buy_price = round(current*0.97, 2)
            volume = int(MAX_SINGLE / buy_price //100 *100)
            if volume <100:
                volume=100

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
                "score":score,
                "time":datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        except Exception as e:
            logger.error(f"{code} 第{retry+1}次失败: {str(e)[:100]}")
            if retry == max_retry-1:
                return None
    return None

# ======================== 全市场扫描筛选 ========================
def scan_market_for_recommend(exclude_codes, top_n=4):
    logger.info("开始全市场扫描选股...")
    try:
        df = ak.stock_zh_a_spot_em()
        df = df[df['代码'].str.match(r'^\d{6}$')]
        # 过滤：排除ST、退市、科创板（可自行调整）
        df = df[~df['名称'].str.contains('ST|退|科|XD|XR', na=False)]
        # 只看A股
        df = df[df['代码'].str.match(r'^(60|00|30)')]
        # 排除已固定的
        df = df[~df['代码'].isin(exclude_codes)]
        # 市值过滤（100亿～5000亿，稳健）
        df['总市值'] = pd.to_numeric(df['总市值'], errors='coerce')
        df = df[(df['总市值'] >= 100) & (df['总市值'] <= 5000)]
        codes = df['代码'].tolist()[:800] # 取前800只扫描
    except Exception as e:
        logger.error(f"获取股票列表失败: {e}")
        return []

    pool = []
    for idx, code in enumerate(codes):
        if idx % 100 ==0:
            logger.info(f"扫描进度：{idx}/{len(codes)}")
        stock = analyze_one_stock(code)
        if stock and stock['score'] >=3: # 评分≥3才入选
            pool.append(stock)
        time.sleep(0.2)

    # 按评分排序
    pool = sorted(pool, key=lambda x: x['score'], reverse=True)
    return pool[:top_n]

# ======================== 操作建议 ========================
def get_operation(s):
    return f"""
📋 同花顺条件单
├─ 股票：{s['code']} {s['name']}
├─ 买入：价格 ≤ {s['buy_price']} 元，{s['volume']}股
├─ 止盈10%：{s['profit10']} 元
├─ 止盈15%：{s['profit15']} 元
└─ 止损：{s['stop']} 元（严格执行）
"""

# ======================== 飞书推送 ========================
def send_feishu(text):
    if not FEISHU_WEBHOOK:
        logger.warning("飞书Webhook未配置")
        return False
    max_bytes = 19000
    text_bytes = text.encode('utf-8')
    if len(text_bytes) > max_bytes:
        text = text_bytes[:max_bytes].decode('utf-8','ignore') + "\n...（过长截断）"
    max_retries=2
    for retry in range(max_retries):
        try:
            res = requests.post(
                FEISHU_WEBHOOK,
                json={"msg_type":"text","content":{"text":text}},
                timeout=25
            )
            res.raise_for_status()
            logger.info("飞书推送成功")
            return True
        except Exception as e:
            logger.error(f"推送失败{retry+1}: {e}")
            time.sleep(1)
    logger.error("飞书推送失败")
    return False

# ======================== 主程序 ========================
def main():
    selected = []
    logger.info(f"固定分析3只：{FIXED_STOCKS}")

    # 1. 分析固定3只
    for code in FIXED_STOCKS:
        stock = analyze_one_stock(code)
        if stock:
            selected.append(stock)
            logger.info(f"{code} {stock['name']} | {stock['grade']}")
        else:
            logger.warning(f"{code} 分析失败")
        time.sleep(0.3)

    # 2. 全市场扫描额外推荐
    exclude = FIXED_STOCKS + [s['code'] for s in selected]
    extra_stocks = scan_market_for_recommend(exclude, EXTRA_RECOMMEND_COUNT)
    selected += extra_stocks

    if not selected:
        send_feishu("⚠️ 今日无符合条件股票")
        return

    # 3. 生成报告
    msg = "🚀 OpenClaw 能源+全市场精选报告\n" + "="*60 + "\n"
    msg += f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
    msg += f"💰 单只上限：{MAX_SINGLE}元 | 总仓位上限：{MAX_TOTAL}元\n"
    msg += "="*60 + "\n\n"

    # 固定3只
    msg += "🔹【固定关注：蓝焰/中石化/海油发展】\n"
    for s in [x for x in selected if x['code'] in FIXED_STOCKS]:
        msg += f"""【{s['code']} {s['name']}】
💵 现价：{s['price']} 元 | 涨跌幅：{s['change']}%
📈 均线：MA5:{s['ma5']} MA10:{s['ma10']} MA20:{s['ma20']}
📊 量价：{s['vol_status']} | RSI：{s['rsi']} | ATR：{s['atr']}
📐 MACD：{s['macd']}/{s['signal']} | KDJ：{s['k']}/{s['d']}/{s['j']}
📌 趋势：{s['trend']} | 底背离：{'✅' if s['bot_div'] else '❌'}
🔥 评级：{s['grade']} | 综合评分：{s['score']}
🛡️ 止损：{s['stop']} 元
"""
        msg += get_operation(s)
        msg += "-"*50 + "\n\n"

    # 市场推荐
    if extra_stocks:
        msg += "🔹【全市场扫描精选】\n"
        for s in extra_stocks:
            msg += f"""【{s['code']} {s['name']}】
💵 现价：{s['price']} 元 | 涨跌幅：{s['change']}%
📈 均线：MA5:{s['ma5']} MA20:{s['ma20']} | 趋势：{s['trend']}
📊 量价：{s['vol_status']} | RSI：{s['rsi']}
🔥 评级：{s['grade']} | 综合评分：{s['score']}
🛡️ 止损：{s['stop']} 元
"""
            msg += get_operation(s)
            msg += "-"*50 + "\n\n"

    msg += """
⚠️ 风险提示
1. 条件单在同花顺「我的条件单」确认「监控中」
2. 云端托管：账号正常登录即可，关机不影响
3. 本分析仅供学习，不构成投资建议，入市风险自担
"""
    send_feishu(msg)
    logger.info("报告推送完成")

if __name__ == "__main__":
    main()
