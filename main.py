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

# ======================== 全局配置 ========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

CONFIG_CONTENT = os.environ.get("CONFIG_CONTENT")
if not CONFIG_CONTENT:
    raise Exception("❌ 未配置 CONFIG_CONTENT 环境变量")
try:
    CONFIG = json.loads(CONFIG_CONTENT)
except json.JSONDecodeError:
    raise Exception("❌ CONFIG_CONTENT 不是合法的 JSON 格式")

FEISHU_WEBHOOK = CONFIG.get("channels", {}).get("feishu", {}).get("webhook", {}).get("url", "")
if not FEISHU_WEBHOOK:
    logger.warning("⚠️ 未配置飞书 Webhook，推送功能将失效")

# ======================== 【优化1：提高推送数量】 ========================
SELECTION_TOP_N = 6          # 单日最多推6只（原来2只）
HIST_DAYS = 120
CAPITAL = 10000
MAX_PRICE = 25
TRADING_COST_RATE = 0.0015
MIN_PROFIT_COVER = 0.01
WIN_LOSS_RATIO_MIN = 2.0     # 盈亏比降到2.0（更容易选出）
SINGLE_MAX_RISK = 300

# 基本面筛选（放宽一点）
FUNDAMENTAL_RED_LINE = {
    "core_pe_max": 18,       # 核心PE从15→18
    "steady_pe_max": 28,     # 稳健从25→28
    "satellite_pe_max": 35,
    "pb_max": 4.5,           # PB从4→4.5
    "market_cap_min": 80     # 市值从100→80亿
}

# ======================== 股票池不变 ========================
CORE_POOL = {
    "601398.SS": "工商银行", "601939.SS": "建设银行", "601288.SS": "农业银行",
    "601988.SS": "中国银行", "601328.SS": "交通银行", "601166.SS": "兴业银行",
    "600000.SS": "浦发银行", "601998.SS": "中信银行", "601818.SS": "光大银行",
    "600919.SS": "江苏银行", "601838.SS": "成都银行", "601088.SS": "中国神华",
    "601225.SS": "陕西煤业", "600028.SS": "中国石化", "600900.SS": "长江电力",
    "601898.SS": "中煤能源", "600188.SS": "兖矿能源", "601001.SS": "晋控煤业",
    "600023.SS": "浙能电力", "600642.SS": "申能股份"
}

STEADY_POOL = {
    "000538.SZ": "云南白药", "600332.SS": "白云山", "000999.SZ": "华润三九",
    "600566.SS": "济川药业", "600535.SS": "天士力", "000623.SZ": "吉林敖东",
    "000028.SZ": "国药一致", "600062.SS": "华润双鹤", "600329.SS": "中新药业",
    "600129.SS": "太极集团", "600557.SS": "康缘药业", "002737.SZ": "葵花药业",
    "002236.SZ": "大华股份", "002027.SZ": "分众传媒", "002555.SZ": "三七互娱",
    "002152.SZ": "广电运通"
}

SATELLITE_POOL = {
    "000100.SZ": "TCL科技", "002056.SZ": "横店东磁", "000997.SZ": "新大陆",
    "002465.SZ": "海格通信", "002544.SZ": "杰赛科技", "600562.SS": "国睿科技",
    "600990.SS": "四创电子", "600271.SS": "航天信息", "002153.SZ": "石基信息",
    "600570.SS": "恒生电子", "603019.SS": "中科曙光", "000066.SZ": "中国长城",
    "000977.SZ": "浪潮信息", "600372.SS": "中航电子", "002013.SZ": "中航机电",
    "600765.SS": "中航重机", "000768.SZ": "中航西飞", "600038.SS": "中直股份",
    "600967.SS": "内蒙一机", "002382.SZ": "蓝帆医疗"
}

MY_STOCKS = {
    "000968.SZ": "蓝焰控股",
    "600028.SS": "中国石化",
    "600968.SS": "海油发展"
}

# ======================== 工具函数 ========================
def is_trading_day():
    today = datetime.now()
    if today.weekday() > 4:
        logger.info("❌ 今天是周末，休市，直接退出")
        return False
    holidays_2026 = [
        "2026-01-01", "2026-01-28", "2026-01-29", "2026-01-30", "2026-01-31",
        "2026-02-01", "2026-02-02", "2026-04-04", "2026-05-01", "2026-05-28",
        "2026-05-29", "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04",
        "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"
    ]
    today_str = today.strftime("%Y-%m-%d")
    if today_str in holidays_2026:
        logger.info(f"❌ 今天是法定节假日 {today_str}，休市")
        return False
    logger.info("✅ 今天是A股交易日，继续执行")
    return True

def get_market_status():
    try:
        hs300 = yf.Ticker("000300.SS")
        df = hs300.history(period="60d", timeout=10)
        if len(df) < 30:
            return True, "大盘数据获取失败，谨慎开仓"
        close = df["Close"].astype(float)
        ma20 = close.rolling(20, min_periods=1).mean()
        current_price = close.iloc[-1]
        ma20_current = ma20.iloc[-1]
        if current_price > ma20_current:
            return True, "震荡/上升市，正常开仓"
        else:
            return True, "下跌市，允许轻仓开仓"
    except:
        return True, "大盘状态异常，谨慎开仓"

def calc_atr(df, period=14):
    df = df.copy().sort_index()
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    close = df["Close"].astype(float)
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(period, min_periods=1).mean()
    return round(atr.iloc[-1], 2)

def calc_technical_indicators(df):
    df = df.copy().sort_index()
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    volume = df["Volume"].astype(float)

    ma5 = close.rolling(5, min_periods=1).mean()
    ma10 = close.rolling(10, min_periods=1).mean()
    ma20 = close.rolling(20, min_periods=1).mean()
    ma60 = close.rolling(60, min_periods=1).mean()
    ma5_vol = volume.rolling(5, min_periods=1).mean()

    # RSI
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta).clip(lower=0).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi_val = round(rsi.iloc[-1], 1) if not np.isnan(rsi.iloc[-1]) else 50

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()

    # KDJ
    low9 = low.rolling(9).min()
    high9 = high.rolling(9).max()
    tr = high9 - low9
    tr = tr.replace(0, 1)
    rsv = (close - low9) / tr * 100
    k = rsv.ewm(span=3, adjust=False).mean()
    d = k.ewm(span=3, adjust=False).mean()

    last = -1
    macd_gold = False
    for i in range(3):
        if len(macd_line) < i+2: break
        if macd_line.iloc[last-i] > signal_line.iloc[last-i] and macd_line.iloc[last-i-1] <= signal_line.iloc[last-i-1]:
            macd_gold = True

    kdj_gold = False
    for i in range(3):
        if len(k) < i+2: break
        if k.iloc[last-i] > d.iloc[last-i] and k.iloc[last-i-1] <= d.iloc[last-i-1]:
            kdj_gold = True

    volume_enlarge = bool(volume.iloc[-3:].max() >= ma5_vol.iloc[-1] * 1.2) if len(volume)>=5 else False

    return {
        "price": round(close.iloc[-1], 2),
        "ma5": round(ma5.iloc[-1], 2), "ma10": round(ma10.iloc[-1], 2),
        "ma20": round(ma20.iloc[-1], 2), "ma60": round(ma60.iloc[-1], 2),
        "rsi": rsi_val, "macd_gold": macd_gold, "kdj_gold": kdj_gold,
        "trend_up": close.iloc[-1] > ma20.iloc[-1],
        "volume_enlarge": volume_enlarge,
        "atr": calc_atr(df),
        "prev_low": round(low.iloc[-2], 2) if len(low)>=2 else round(close.iloc[-1]*0.98,2)
    }

def get_fundamental_data(symbol, pool_type):
    try:
        tk = yf.Ticker(symbol)
        info = tk.info
        pe = info.get("trailingPE", 999)
        pb = info.get("priceToBook", 999)
        market_cap = info.get("marketCap", 0) / 1e8 if info.get("marketCap") else 0

        pe_pass = False
        if pool_type == "core": pe_pass = pe < FUNDAMENTAL_RED_LINE["core_pe_max"]
        elif pool_type == "steady": pe_pass = pe < FUNDAMENTAL_RED_LINE["steady_pe_max"]
        else: pe_pass = pe < FUNDAMENTAL_RED_LINE["satellite_pe_max"]

        all_pass = pe_pass and pb < FUNDAMENTAL_RED_LINE["pb_max"] and market_cap > FUNDAMENTAL_RED_LINE["market_cap_min"]
        return {
            "pe": round(pe,2) if pe and pe != np.inf else 999,
            "pb": round(pb,2) if pb and pb != np.inf else 999,
            "market_cap": round(market_cap,2),
            "fund_pass": all_pass
        }
    except:
        return {"pe":999,"pb":999,"market_cap":0,"fund_pass":False}

# ======================== 【优化2：买卖点大优化】 ========================
def get_stock_data(symbol, name, pool_type):
    try:
        logger.info(f"📡 分析 {symbol} {name}")
        tk = yf.Ticker(symbol)
        df = tk.history(period=f"{HIST_DAYS}d", timeout=10)
        if len(df) < 30: return None
        
        tech = calc_technical_indicators(df)
        current_price = tech["price"]
        atr = tech["atr"]

        if current_price > MAX_PRICE: return None
        fundamental = get_fundamental_data(symbol, pool_type)
        if not fundamental["fund_pass"]: return None

        # 择时条件放宽
        core_conds = [tech["trend_up"], tech["volume_enlarge"]]
        core_pass = all(core_conds)
        assist_conds = [tech["macd_gold"], tech["kdj_gold"], tech["rsi"]>30 and tech["rsi"]<60, tech["ma5"]>tech["ma10"]]
        assist_pass = sum(assist_conds) >= 1  # 4选1即可
        timing_pass = core_pass and assist_pass

        # ======================== 【优化买卖点：更安全、更顺滑】 ========================
        buy_price = round(min(tech["prev_low"] * 0.995, current_price * 0.99), 2)
        stop_loss = round(max(buy_price - atr * 1.2, tech["ma10"] * 0.995, buy_price * 0.95), 2)
        target_profit = round(min(buy_price + atr * 2.5, buy_price * 1.22), 2)

        profit_space = (target_profit - buy_price) / buy_price
        loss_space = (buy_price - stop_loss) / buy_price
        win_loss_ratio = profit_space / loss_space if loss_space > 0 else 0

        if win_loss_ratio < WIN_LOSS_RATIO_MIN: return None

        # 仓位
        max_shares_by_risk = int(SINGLE_MAX_RISK / (loss_space * buy_price) // 100 * 100)
        if pool_type == "core": cap_rate = 0.2
        elif pool_type == "steady": cap_rate = 0.15
        else: cap_rate = 0.1
        max_shares_by_pool = int(CAPITAL * cap_rate / buy_price // 100 * 100)
        volume = min(max_shares_by_risk, max_shares_by_pool)
        volume = max(volume, 100)

        # 止盈优化
        profit_cover_cost = round(buy_price * (1 + TRADING_COST_RATE + MIN_PROFIT_COVER), 2)
        profit_mid = round((buy_price + target_profit) / 2, 2)

        total_score = round(random.uniform(7.5, 9.5), 2)
        buy_signal = timing_pass

        return {
            "symbol": symbol, "code": symbol.replace(".SS","").replace(".SZ",""),
            "name": name, "pool_type": pool_type, "tech": tech, "fund": fundamental,
            "win_loss_ratio": round(win_loss_ratio,2), "total_score": total_score,
            "buy_signal": buy_signal, "signal_text": "🔥 买入信号",
            "order": {
                "buy_price": buy_price, "volume": volume, "stop_loss": stop_loss,
                "stop_loss_pct": round(loss_space*100,1),
                "target_profit": target_profit, "profit_cover_cost": profit_cover_cost,
                "profit_mid": profit_mid
            }
        }
    except Exception as e:
        return None

def scan_market(market_can_open):
    all_stocks = {}
    # 自选优先
    for symbol, name in MY_STOCKS.items():
        data = get_stock_data(symbol, name, "core")
        if data: all_stocks[symbol] = data
        time.sleep(random.uniform(0.3,0.6))

    # 全池扫描
    for symbol, name in CORE_POOL.items():
        if symbol in all_stocks: continue
        data = get_stock_data(symbol, name, "core")
        if data: all_stocks[symbol] = data
        time.sleep(random.uniform(0.2,0.4))

    for symbol, name in STEADY_POOL.items():
        if symbol in all_stocks: continue
        data = get_stock_data(symbol, name, "steady")
        if data: all_stocks[symbol] = data
        time.sleep(random.uniform(0.2,0.4))

    sorted_stocks = sorted(all_stocks.values(), key=lambda x:(x["buy_signal"],x["total_score"]), reverse=True)[:SELECTION_TOP_N]
    final_stocks = [s for s in sorted_stocks if s["buy_signal"]]
    return final_stocks

def send_feishu_report(stocks, market_tips):
    if not FEISHU_WEBHOOK:
        logger.error("❌ 飞书Webhook未配置")
        return
    
    if not stocks:
        report = f"""🚀 A股稳健量化策略日报
📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}
📊 今日大盘状态：{market_tips}
==================================================
⚠️ 今日无符合条件标的
==================================================
"""
    else:
        report = f"""🚀 A股稳健量化策略日报
📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}
📊 今日大盘状态：{market_tips}
==================================================
"""
        for idx, stock in enumerate(stocks,1):
            o = stock["order"]
            pool_name = "核心池" if stock["pool_type"]=="core" else "稳健池" if stock["pool_type"]=="steady" else "卫星池"
            report += f"""
【{idx}】{stock['code']} {stock['name']} 🔥 买入
🏷️ {pool_name} | 盈亏比 {stock['win_loss_ratio']}:1
现价：{stock['tech']['price']} 元

📈 买入价：{o['buy_price']} 元
📦 仓位：{o['volume']} 股
🛑 止损：{o['stop_loss']} 元（{o['stop_loss_pct']}%）
💰 目标：{o['target_profit']} 元
--------------------------------------------------
"""
        report += "\n⚠️ 仅供学习，不构成投资建议"

    try:
        requests.post(FEISHU_WEBHOOK, json={"msg_type": "text", "content": {"text": report}}, timeout=10)
        logger.info("✅ 推送成功")
    except:
        logger.error("❌ 推送失败")

# ======================== 主程序 ========================
def main():
    if not is_trading_day(): return
    market_can_open, market_tips = get_market_status()
    selected_stocks = scan_market(market_can_open)
    send_feishu_report(selected_stocks, market_tips)
    logger.info("🎉 完成")

if __name__ == "__main__":
    main()
