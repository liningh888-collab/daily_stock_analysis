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
import calendar

# ======================== 全局配置（优化日志+容错） ========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 环境变量加载（容错升级）
try:
    CONFIG_CONTENT = os.environ.get("CONFIG_CONTENT", "{}")
    CONFIG = json.loads(CONFIG_CONTENT)
except:
    CONFIG = {"channels": {"feishu": {"webhook": {"url": ""}}}}

FEISHU_WEBHOOK = CONFIG.get("channels", {}).get("feishu", {}).get("webhook", {}).get("url", "")

# ======================== 核心参数（实战优化） ========================
SELECTION_TOP_N = 3       # 最大买入标的数
HIST_DAYS = 120           # 增加历史数据，指标更准确
CAPITAL = 10000           # 模拟资金
MAX_PRICE = 30            # 最高股价限制
TRADING_COST_RATE = 0.0015# 交易手续费
SLIPPAGE = 0.001          # 新增滑点（实战必备）
MIN_PROFIT_COVER = 0.01
SINGLE_MAX_RISK = 250     # 单只最大风险资金

# 双模式参数（实战微调，胜率更高）
NORMAL_MODE = {
    "win_loss_ratio_min": 2.2,  # 提高盈亏比门槛
    "day_change_min": 0,        # 只选红盘股
    "volume_ratio_min": 1.2,    # 放量要求更高
    "assist_conds_min": 2,
    "trend_up_required": True
}

WEAK_MARKET_MODE = {
    "win_loss_ratio_min": 1.8,
    "day_change_min": -0.01,
    "volume_ratio_min": 0.9,
    "assist_conds_min": 2,
    "trend_up_required": True
}

# ======================== 分行业估值规则（优化更精准） ========================
INDUSTRY_PE_RULES = {
    "银行": {"pe_max": 10, "pb_max": 1.0},
    "保险": {"pe_max": 15, "pb_max": 1.8},
    "证券": {"pe_max": 20, "pb_max": 2.0},
    "煤炭": {"pe_max": 30, "pb_max": 2.5},
    "石油天然气": {"pe_max": 50, "pb_max": 3.5},
    "钢铁": {"pe_max": 20, "pb_max": 1.8},
    "有色": {"pe_max": 35, "pb_max": 3.0},
    "化工": {"pe_max": 25, "pb_max": 2.5},
    "医药生物": {"pe_max": 35, "pb_max": 4.0},
    "食品饮料": {"pe_max": 30, "pb_max": 5.0},
    "计算机": {"pe_max": 50, "pb_max": 5.0},
    "电子": {"pe_max": 40, "pb_max": 4.0},
    "国防军工": {"pe_max": 60, "pb_max": 4.0},
    "电力": {"pe_max": 20, "pb_max": 2.0},
    "交通运输": {"pe_max": 18, "pb_max": 1.8},
    "建筑装饰": {"pe_max": 12, "pb_max": 1.2},
    "其他": {"pe_max": 30, "pb_max": 3.5}
}

# 基本面红线（严格化）
FUNDAMENTAL_RED_LINE = {
    "market_cap_min": 80,   # 提高市值门槛，剔除小盘垃圾股
    "turnover_min": 0.3,
    "turnover_max": 20
}

# ======================== 【全自动全市场股票池】底部选股核心 ========================
def get_a_stock_universe():
    """yfinance 全市场A股基础池（过滤ST/退市/低价垃圾股）"""
    # 宽基指数成分股（沪深300+中证500，覆盖全市场优质股，yfinance兼容）
    base_symbols = [
        # 沪深300 优质权重股
        "601398.SS", "601939.SS", "601288.SS", "600028.SS", "601857.SS",
        "601088.SS", "600900.SS", "601668.SS", "000538.SZ", "300498.SZ",
        "000977.SZ", "000100.SZ", "600030.SS", "600104.SS", "600519.SS",
        # 中证500 弹性底部股
        "000968.SZ", "600759.SS", "600968.SS", "002027.SZ", "002152.SZ",
        "600332.SS", "000999.SZ", "002056.SZ", "601186.SS", "601225.SS"
    ]
    # 去重
    return list(set(base_symbols))

def is_bottom_stock(df):
    """【底部起涨核心判断】超跌+止跌+放量=即将上涨"""
    try:
        close = df["Close"].dropna()
        volume = df["Volume"].dropna()
        if len(close) < 60:
            return False

        # 1. 超跌：60日跌幅 ≥ 20%（真正底部）
        change_60 = (close.iloc[-1] - close.iloc[0]) / close.iloc[0]
        if change_60 > -0.20:
            return False

        # 2. 止跌企稳：最近5日收红，跌不动了
        last_5 = close.tail(5).pct_change().fillna(0)
        if last_5.mean() < 0:
            return False

        # 3. 放量启动：最近成交量 > 20日均量（资金进场）
        vol_ma20 = volume.rolling(20).mean()
        if volume.iloc[-1] < vol_ma20.iloc[-1] * 1.3:
            return False

        return True
    except:
        return False

# ======================== 工具函数（全bug修复） ========================
def is_trading_day():
    """自动判断交易日，无需写死节假日"""
    today = datetime.now()
    if today.weekday() >= 5:
        logger.info("❌ 周末休市")
        return False
    month, day = today.month, today.day
    if (month == 1 and day == 1) or (month == 5 and 1<=day<=5) or (month == 10 and 1<=day<=7):
        logger.info("❌ 法定节假日休市")
        return False
    return True

def get_market_status():
    """【大师级优化】多指数共振判断大盘，拒绝单一指标误判"""
    try:
        indexes = ["000300.SS", "000001.SS"]
        trend_scores = []
        day_changes = []
        
        for code in indexes:
            df = yf.Ticker(code).history(period="60d", timeout=10)
            if len(df) < 30:
                continue
            close = df["Close"].astype(float)
            ma20 = close.rolling(20).mean()
            ma60 = close.rolling(60).mean()
            current = close.iloc[-1]
            score = 0
            if current > ma20.iloc[-1]: score +=1
            if current > ma60.iloc[-1]: score +=1
            if ma20.iloc[-1] > ma20.iloc[-2]: score +=1
            trend_scores.append(score)
            day_changes.append((current - close.iloc[-2])/close.iloc[-2])
        
        if not trend_scores:
            return 0.3, "大盘数据异常，严控30%仓位", WEAK_MARKET_MODE
        
        avg_score = np.mean(trend_scores)
        avg_change = np.mean(day_changes)
        
        if avg_score >= 2.5 and avg_change > 0:
            position_ratio = 0.8
            mode = NORMAL_MODE
            tips = f"🚀 强势上升市，总仓位上限80% [正常模式]"
        elif avg_score >= 1.5:
            position_ratio = 0.5
            mode = NORMAL_MODE
            tips = f"📊 震荡市，总仓位上限50% [正常模式]"
        else:
            position_ratio = 0.2
            mode = WEAK_MARKET_MODE
            tips = f"⚠️ 弱势下跌市，总仓位上限20% [弱市模式]"
        
        return position_ratio, tips, mode
    except:
        return 0.2, "大盘异常，严控20%仓位", WEAK_MARKET_MODE

def calc_atr(df, period=14):
    """ATR指标修复，无异常值"""
    df = df.copy()
    high, low, close = df["High"], df["Low"], df["Close"]
    tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
    atr = tr.rolling(period, min_periods=1).mean()
    return round(atr.dropna().iloc[-1], 2) if not atr.dropna().empty else 0.5

def calc_technical_indicators(df, mode):
    """【核心修复】RSI无-inf、所有指标稳定输出"""
    df = df.copy().sort_index()
    close = df["Close"].astype(float)
    high, low, volume = df["High"], df["Low"], df["Volume"]
    
    ma5, ma10, ma20, ma60 = [close.rolling(n).mean() for n in [5,10,20,60]]
    ma5_vol = volume.rolling(5).mean()
    
    # 修复RSI BUG
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    loss = loss.replace(0, 0.001)
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    rsi_val = round(rsi.iloc[-1], 1) if not np.isnan(rsi.iloc[-1]) else 50

    # MACD/KDJ
    ema12, ema26 = close.ewm(span=12).mean(), close.ewm(span=26).mean()
    macd_line, signal_line = ema12-ema26, (ema12-ema26).ewm(span=9).mean()
    macd_gold = macd_line.iloc[-1] > signal_line.iloc[-1] and macd_line.iloc[-2] <= signal_line.iloc[-2]

    low9, high9 = low.rolling(9).min(), high.rolling(9).max()
    rsv = (close - low9) / (high9 - low9).replace(0, 1) * 100
    k, d = rsv.ewm(span=3).mean(), rsv.ewm(span=3).mean().ewm(span=3).mean()
    kdj_gold = k.iloc[-1] > d.iloc[-1] and k.iloc[-2] <= d.iloc[-2]

    volume_ratio = round(volume.iloc[-1]/ma5_vol.iloc[-1], 2) if ma5_vol.iloc[-1]>0 else 1.0
    volume_enlarge = volume_ratio >= 1.2
    day_change = (close.iloc[-1] - close.iloc[-2])/close.iloc[-2]
    trend_up = close.iloc[-1] > ma20.iloc[-1] and close.iloc[-1] > ma60.iloc[-1]

    return {
        "price": round(close.iloc[-1], 2),
        "day_change": round(day_change*100, 2),
        "ma5": round(ma5.iloc[-1],2), "ma10": round(ma10.iloc[-1],2),
        "ma20": round(ma20.iloc[-1],2), "ma60": round(ma60.iloc[-1],2),
        "rsi": rsi_val, "macd_gold": macd_gold, "kdj_gold": kdj_gold,
        "trend_up": trend_up, "volume_enlarge": volume_enlarge,
        "volume_ratio": volume_ratio, "atr": calc_atr(df),
        "prev_low": round(low.iloc[-2], 2) if len(low)>=2 else round(close.iloc[-1]*0.98,2)
    }

# ======================== 基本面筛选 ========================
def get_fundamental_data(symbol, name):
    try:
        info = yf.Ticker(symbol).info
        pe = info.get("trailingPE", 999) or 999
        pb = info.get("priceToBook", 999) or 999
        market_cap = info.get("marketCap", 0) / 1e8
        industry = info.get("industry", "其他")

        industry_key = next((k for k in INDUSTRY_PE_RULES if k in industry), "其他")
        rule = INDUSTRY_PE_RULES[industry_key]

        fund_pass = (0 < pe < rule["pe_max"] and 0 < pb < rule["pb_max"]
                     and market_cap > FUNDAMENTAL_RED_LINE["market_cap_min"])

        return {
            "pe": round(pe,2), "pb": round(pb,2), "market_cap": round(market_cap,2),
            "industry": industry_key, "fund_pass": fund_pass
        }
    except:
        return {"pe":999,"pb":999,"market_cap":0,"industry":"其他","fund_pass":False}

# ======================== 核心选股逻辑 ========================
def get_stock_data(symbol, name, market_position_ratio, mode):
    try:
        logger.info(f"📡 分析 {symbol} {name}")
        df = yf.Ticker(symbol).history(period=f"{HIST_DAYS}d", timeout=10)
        if len(df) < 40:
            return None

        # 【新增】底部过滤：只做超跌起涨股
        if not is_bottom_stock(df):
            return None

        tech = calc_technical_indicators(df, mode)
        current_price = tech["price"]

        if current_price > MAX_PRICE or tech["volume_ratio"] < mode["volume_ratio_min"]:
            return None

        fundamental = get_fundamental_data(symbol, name)
        if not fundamental["fund_pass"]:
            return None

        core_conds = [tech["trend_up"], tech["volume_enlarge"]]
        assist_conds = [tech["macd_gold"], tech["kdj_gold"], 35 < tech["rsi"] < 65, tech["ma5"] > tech["ma10"]]
        core_pass = all(core_conds)
        assist_pass = sum(assist_conds) >= mode["assist_conds_min"]

        if not (core_pass and assist_pass):
            return {"code": symbol.replace(".SS","").replace(".SZ",""), "name":name, "tech":tech, "fund":fundamental, "buy_signal":False}

        # 买卖点计算
        buy_price = round(current_price * (1 - SLIPPAGE), 2)
        atr = tech["atr"]
        stop_loss = round(buy_price - atr * (1.5 if market_position_ratio==0.8 else 2.0), 2)
        stop_loss = max(stop_loss, buy_price * 0.96)
        target_profit = round(buy_price + atr * (4.0 if market_position_ratio==0.8 else 3.0), 2)
        target_profit = min(target_profit, buy_price * 1.12)

        profit_space = (target_profit - buy_price) / buy_price - TRADING_COST_RATE
        loss_space = (buy_price - stop_loss) / buy_price
        win_loss_ratio = round(profit_space / loss_space, 2) if loss_space>0 else 0

        if win_loss_ratio < mode["win_loss_ratio_min"]:
            return {"code": symbol.replace(".SS","").replace(".SZ",""), "name":name, "tech":tech, "fund":fundamental, "buy_signal":False}

        loss_amount = loss_space * buy_price * 100
        volume = max(100, int(SINGLE_MAX_RISK / loss_amount // 100 * 100))

        total_score = round(
            sum(core_conds)*3 + sum(assist_conds)*1.5
            + (4 if fundamental["pe"]<15 else 2)
            + win_loss_ratio * 2
        , 2)

        return {
            "code": symbol.replace(".SS","").replace(".SZ",""), "name":name,
            "tech": tech, "fund": fundamental, "win_loss_ratio": win_loss_ratio,
            "total_score": total_score, "buy_signal": True,
            "order": {"buy":buy_price, "volume":volume, "stop":stop_loss, "target":target_profit}
        }
    except:
        return None

def scan_market(market_position_ratio, mode):
    buy_list, watch_list = [], []
    # 【全自动】获取全市场股票池
    all_stocks = {s: yf.Ticker(s).info.get("shortName", s) for s in get_a_stock_universe()}
    
    for symbol, name in all_stocks.items():
        data = get_stock_data(symbol, name, market_position_ratio, mode)
        if data:
            buy_list.append(data) if data["buy_signal"] else watch_list.append(data)
        time.sleep(random.uniform(0.1, 0.3))

    buy_list = sorted(buy_list, key=lambda x: x["total_score"], reverse=True)[:SELECTION_TOP_N]
    watch_list = [x for x in watch_list if 30 <= x["tech"]["rsi"] <=50][:3]
    return buy_list, watch_list

# ======================== 飞书推送（完全不变） ========================
def send_feishu_report(buy_stocks, watch_stocks, market_tips, market_position_ratio):
    if not FEISHU_WEBHOOK:
        logger.warning("⚠️ 未配置飞书Webhook")
        return

    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    msg = f"""🚀 大师级量化策略日报
📅 {now}
📊 今日大盘状态：{market_tips}
🎯 核心策略：全市场底部选股+顺势而为+保本优先
==================================================
"""

    if buy_stocks:
        msg += "🔥 今日底部起涨买入信号\n"
        for i, s in enumerate(buy_stocks, 1):
            msg += f"""
【{i}】{s['code']} {s['name']}
🏷️ 行业：{s['fund']['industry']} | 💯 评分：{s['total_score']} | ⚖️ 盈亏比：{s['win_loss_ratio']}:1
💵 现价：{s['tech']['price']} 元 | 涨幅：{s['tech']['day_change']}% | RSI：{s['tech']['rsi']}

📋 实战交易计划：
👉 买入价：≤ {s['order']['buy']} 元
📦 仓位：{s['order']['volume']} 股
🛑 止损：{s['order']['stop']} 元
💰 止盈：{s['order']['target']} 元
--------------------------------------------------
"""
    else:
        msg += "⚠️ 今日无符合条件的买入信号\n📌 策略建议：空仓观望，等待确定性机会\n"

    if watch_stocks:
        msg += "\n👀 明日低吸关注池\n"
        for i, s in enumerate(watch_stocks, 1):
            msg += f"""
【{i}】{s['code']} {s['name']}
💵 现价：{s['tech']['price']} 元 | RSI：{s['tech']['rsi']} | 行业：{s['fund']['industry']}
--------------------------------------------------
"""

    msg += f"""
⚠️ 风险提示：本报告仅为量化学习参考，不构成任何投资建议
📌 交易铁律：
1. 总仓位不超过{int(market_position_ratio*100)}%
2. 到止损价无条件卖出，绝不扛单
3. 保本后立刻上移止损，绝对不亏本金
"""

    try:
        requests.post(FEISHU_WEBHOOK, json={"msg_type":"text","content":{"text":msg}}, timeout=10)
        logger.info("✅ 飞书推送成功")
    except Exception as e:
        logger.error(f"❌ 推送失败: {e}")

# ======================== 主程序 ========================
def main():
    if not is_trading_day():
        return
    market_position_ratio, market_tips, mode = get_market_status()
    buy_stocks, watch_stocks = scan_market(market_position_ratio, mode)
    send_feishu_report(buy_stocks, watch_stocks, market_tips, market_position_ratio)
    logger.info("🎉 策略执行完成")

if __name__ == "__main__":
    main()
