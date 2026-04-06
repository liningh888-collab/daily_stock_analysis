import requests
import json
import logging
import os
from datetime import datetime, timedelta
import time
import pandas as pd
import numpy as np
import yfinance as yf

# ======================== 日志配置 ========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ======================== 配置加载 ========================
config_content = os.environ.get("CONFIG_CONTENT")
if not config_content:
    raise Exception("❌ 未配置 CONFIG_CONTENT 环境变量")
try:
    CONFIG = json.loads(config_content)
except json.JSONDecodeError:
    raise Exception("❌ CONFIG_CONTENT 不是合法的 JSON 格式")

# 飞书 Webhook 配置
FEISHU_WEBHOOK = CONFIG.get("channels", {}).get("feishu", {}).get("webhook", {}).get("url", "")
if not FEISHU_WEBHOOK:
    logger.warning("⚠️ 未配置飞书 Webhook，推送功能将失效")

# ======================== 核心配置 ========================
# A股 yfinance 代码映射（.SZ=深交所，.SS=上交所）
STOCK_MAP = {
    "000968.SZ": "蓝焰控股",
    "600028.SS": "中国石化",
    "600968.SS": "海油发展"
}
CAPITAL = 10000
SINGLE_MAX = 3000
HIST_DAYS = 90  # 历史数据天数

# ======================== 指标计算（通用版） ========================
def calc_indicators(df):
    df = df.copy().sort_index()
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)

    # 均线
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()

    # RSI(14)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta).clip(lower=0).rolling(14).mean()
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

    last = -1
    return {
        "price": round(close.iloc[last], 2),
        "ma5": round(ma5.iloc[last], 2),
        "ma20": round(ma20.iloc[last], 2),
        "rsi": round(rsi.iloc[last], 1),
        "macd": round(macd_line.iloc[last], 2),
        "signal": round(signal_line.iloc[last], 2),
        "k": round(k.iloc[last], 1),
        "d": round(d.iloc[last], 1),
        "j": round(j.iloc[last], 1),
        "macd_gold": bool(macd_line.iloc[last] > signal_line.iloc[last] and macd_line.iloc[last-1] <= signal_line.iloc[last-1]),
        "trend_up": bool(close.iloc[last] > ma20.iloc[last])
    }

# ======================== 数据获取（yfinance 唯一数据源） ========================
def get_stock_history(symbol):
    try:
        logger.info(f"📡 正在获取 {symbol} 历史数据...")
        tk = yf.Ticker(symbol)
        df = tk.history(period=f"{HIST_DAYS}d", timeout=15)
        if len(df) >= 30:
            logger.info(f"✅ {symbol} 数据获取成功，共 {len(df)} 条")
            return calc_indicators(df)
        logger.warning(f"⚠️ {symbol} 数据不足（{len(df)}条）")
        return None
    except Exception as e:
        logger.error(f"❌ {symbol} 数据获取失败: {str(e)}")
        return None

# ======================== 量化买入策略 ========================
def get_buy_signal(indicators):
    conds = {
        "RSI超跌": indicators["rsi"] < 40,
        "MACD金叉": indicators["macd_gold"],
        "KDJ低位": indicators["j"] < 40,
        "趋势向上": indicators["trend_up"]
    }
    score = sum(conds.values())
    reason = " + ".join([k for k, v in conds.items() if v]) or "无满足条件"
    return {
        "buy": score >= 3,
        "score": score,
        "reason": reason,
        "signal_text": "🔥 买入信号" if score >= 3 else "⚠️ 观望"
    }

# ======================== 条件单计算 ========================
def calc_trade_order(price):
    buy_price = round(price * 0.97, 2)
    volume = int(SINGLE_MAX / buy_price // 100 * 100)
    volume = max(volume, 100)
    return {
        "buy_price": buy_price,
        "volume": volume,
        "profit10": round(buy_price * 1.10, 2),
        "profit15": round(buy_price * 1.15, 2),
        "stop_loss": round(price * 0.94, 2)
    }

# ======================== 飞书推送 ========================
def send_feishu_message(content):
    if not FEISHU_WEBHOOK:
        logger.error("❌ 飞书 Webhook 未配置")
        return False
    try:
        response = requests.post(
            FEISHU_WEBHOOK,
            json={"msg_type": "text", "content": {"text": content}},
            timeout=10
        )
        response.raise_for_status()
        logger.info("✅ 飞书消息推送成功")
        return True
    except Exception as e:
        logger.error(f"❌ 飞书推送失败: {e}")
        return False

# ======================== 主程序 ========================
def main():
    logger.info("🚀 开始执行 yfinance 稳定版量化分析")
    analysis_result = []

    for symbol, name in STOCK_MAP.items():
        indicators = get_stock_history(symbol)
        if not indicators:
            logger.warning(f"❌ {name} 数据获取失败，跳过")
            continue
        
        signal = get_buy_signal(indicators)
        order = calc_trade_order(indicators["price"])
        
        analysis_result.append({
            "code": symbol.replace(".SS", "").replace(".SZ", ""),
            "name": name,
            **indicators,
            **signal,
            **order
        })
        time.sleep(0.5)  # 防限流

    if not analysis_result:
        send_feishu_message(f"⚠️ 【{datetime.now().strftime('%Y-%m-%d %H:%M')}】所有股票数据获取失败")
        return

    # 生成报告
    report = f"""🚀 A股量化分析报告（yfinance 稳定版）
📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}
==================================================
"""
    for stock in analysis_result:
        report += f"""
【{stock['code']} {stock['name']}】 {stock['signal_text']}
💵 现价：{stock['price']} 元 | 📊 策略得分：{stock['score']}/4
✅ 满足条件：{stock['reason']}

📈 核心指标：
RSI：{stock['rsi']}   MACD金叉：{'是' if stock['macd_gold'] else '否'}
KDJ：{stock['k']}/{stock['d']}/{stock['j']}
MA5：{stock['ma5']}  MA20：{stock['ma20']}  趋势向上：{'是' if stock['trend_up'] else '否'}

📋 条件单：
买入 ≤ {stock['buy_price']} 元，{stock['volume']} 股
止盈10%：{stock['profit10']} 元 | 止盈15%：{stock['profit15']} 元
止损：{stock['stop_loss']} 元
--------------------------------------------------
"""
    report += "\n⚠️ 本报告仅为量化学习参考，不构成投资建议"
    send_feishu_message(report)
    logger.info("🎉 分析完成，报告已推送")

if __name__ == "__main__":
    main()
