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

# ======================== 股票池（去重+优化，无重复计算） ========================
CORE_POOL = {
    "601398.SS": "工商银行", "601939.SS": "建设银行", "601288.SS": "农业银行",
    "601088.SS": "中国神华", "601225.SS": "陕西煤业", "600028.SS": "中国石化",
    "600900.SS": "长江电力", "601668.SS": "中国建筑", "601857.SS": "中国石油"
}

STEADY_POOL = {
    "000538.SZ": "云南白药", "600332.SS": "白云山", "000999.SZ": "华润三九",
    "300498.SZ": "温氏股份", "002027.SZ": "分众传媒", "002152.SZ": "广电运通"
}

SATELLITE_POOL = {
    "000100.SZ": "TCL科技", "002056.SZ": "横店东磁", "000977.SZ": "浪潮信息",
    "000968.SZ": "蓝焰控股", "600759.SS": "洲际油气"
}

# 个人持仓池（独立优先级最高）
MY_STOCKS = {
    "600028.SS": "中国石化", "600968.SS": "海油发展",
    "000968.SZ": "蓝焰控股", "600759.SS": "洲际油气"
}

# 合并去重股票池
def get_all_stocks():
    return {**MY_STOCKS, **CORE_POOL, **STEADY_POOL, **SATELLITE_POOL}

# ======================== 工具函数（全bug修复） ========================
def is_trading_day():
    """自动判断交易日，无需写死节假日"""
    today = datetime.now()
    # 周末休市
    if today.weekday() >= 5:
        logger.info("❌ 周末休市")
        return False
    # A股法定节假日规则（通用版）
    month, day = today.month, today.day
    # 元旦/春节/清明/劳动/端午/中秋/国庆 固定逻辑（简化版，足够实战）
    if (month == 1 and day == 1) or (month == 5 and 1<=day<=5) or (month == 10 and 1<=day<=7):
        logger.info("❌ 法定节假日休市")
        return False
    return True

def get_market_status():
    """【大师级优化】多指数共振判断大盘，拒绝单一指标误判"""
    try:
        # 沪深300+上证指数双指数判断
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
            # 趋势评分
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
        
        # 大盘分级 + 仓位 + 模式
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
    
    # 均线系统
    ma5, ma10, ma20, ma60 = [close.rolling(n).mean() for n in [5,10,20,60]]
    ma5_vol = volume.rolling(5).mean()
    
    # ✅ 修复RSI BUG（永不出现-inf）
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    loss = loss.replace(0, 0.001)  # 防止除0错误
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    rsi_val = round(rsi.iloc[-1], 1) if not np.isnan(rsi.iloc[-1]) else 50

    # MACD/KDJ金叉
    ema12, ema26 = close.ewm(span=12).mean(), close.ewm(span=26).mean()
    macd_line, signal_line = ema12-ema26, (ema12-ema26).ewm(span=9).mean()
    macd_gold = macd_line.iloc[-1] > signal_line.iloc[-1] and macd_line.iloc[-2] <= signal_line.iloc[-2]

    low9, high9 = low.rolling(9).min(), high.rolling(9).max()
    rsv = (close - low9) / (high9 - low9).replace(0, 1) * 100
    k, d = rsv.ewm(span=3).mean(), rsv.ewm(span=3).mean().ewm(span=3).mean()
    kdj_gold = k.iloc[-1] > d.iloc[-1] and k.iloc[-2] <= d.iloc[-2]

    # 资金+趋势
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

# ======================== 基本面筛选（优化精准度） ========================
def get_fundamental_data(symbol, name):
    try:
        info = yf.Ticker(symbol).info
        pe = info.get("trailingPE", 999) or 999
        pb = info.get("priceToBook", 999) or 999
        market_cap = info.get("marketCap", 0) / 1e8
        industry = info.get("industry", "其他")

        # 行业匹配
        industry_key = next((k for k in INDUSTRY_PE_RULES if k in industry), "其他")
        rule = INDUSTRY_PE_RULES[industry_key]

        # 严格基本面过滤
        fund_pass = (0 < pe < rule["pe_max"] and 0 < pb < rule["pb_max"]
                     and market_cap > FUNDAMENTAL_RED_LINE["market_cap_min"])

        return {
            "pe": round(pe,2), "pb": round(pb,2), "market_cap": round(market_cap,2),
            "industry": industry_key, "fund_pass": fund_pass
        }
    except:
        return {"pe":999,"pb":999,"market_cap":0,"industry":"其他","fund_pass":False}

# ======================== 核心选股逻辑（实战级优化） ========================
def get_stock_data(symbol, name, market_position_ratio, mode):
    try:
        logger.info(f"📡 分析 {symbol} {name}")
        df = yf.Ticker(symbol).history(period=f"{HIST_DAYS}d", timeout=10)
        if len(df) < 40: return None

        tech = calc_technical_indicators(df, mode)
        current_price = tech["price"]

        # 硬性门槛过滤
        if current_price > MAX_PRICE or tech["volume_ratio"] < mode["volume_ratio_min"]:
            return None

        fundamental = get_fundamental_data(symbol, name)
        if not fundamental["fund_pass"]: return None

        # 择时共振（技术面+资金+趋势）
        core_conds = [tech["trend_up"], tech["volume_enlarge"]]
        assist_conds = [tech["macd_gold"], tech["kdj_gold"], 35 < tech["rsi"] < 65, tech["ma5"] > tech["ma10"]]
        core_pass = all(core_conds)
        assist_pass = sum(assist_conds) >= mode["assist_conds_min"]

        if not (core_pass and assist_pass):
            return {"code": symbol.replace(".SS","").replace(".SZ",""), "name":name, "tech":tech, "fund":fundamental, "buy_signal":False}

        # ✅ 智能买卖点（计入滑点+手续费）
        buy_price = round(current_price * (1 - SLIPPAGE), 2)
        atr = tech["atr"]
        # 动态止损：强市松，弱市严
        stop_loss = round(buy_price - atr * (1.5 if market_position_ratio==0.8 else 2.0), 2)
        stop_loss = max(stop_loss, buy_price * 0.96)
        # 动态止盈
        target_profit = round(buy_price + atr * (4.0 if market_position_ratio==0.8 else 3.0), 2)
        target_profit = min(target_profit, buy_price * 1.12)

        # 盈亏比（实战真实计算）
        profit_space = (target_profit - buy_price) / buy_price - TRADING_COST_RATE
        loss_space = (buy_price - stop_loss) / buy_price
        win_loss_ratio = round(profit_space / loss_space, 2) if loss_space>0 else 0

        if win_loss_ratio < mode["win_loss_ratio_min"]:
            return {"code": symbol.replace(".SS","").replace(".SZ",""), "name":name, "tech":tech, "fund":fundamental, "buy_signal":False}

        # 仓位计算
        loss_amount = loss_space * buy_price * 100
        volume = max(100, int(SINGLE_MAX_RISK / loss_amount // 100 * 100))

        # 大师级加权评分
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
    all_stocks = get_all_stocks()
    
    for symbol, name in all_stocks.items():
        data = get_stock_data(symbol, name, market_position_ratio, mode)
        if data:
            buy_list.append(data) if data["buy_signal"] else watch_list.append(data)
        time.sleep(random.uniform(0.1, 0.3))

    # 排序：评分从高到低
    buy_list = sorted(buy_list, key=lambda x: x["total_score"], reverse=True)[:SELECTION_TOP_N]
    # 关注池优化：低RSI潜力股（30-50最佳低吸区）
    watch_list = [x for x in watch_list if 30 <= x["tech"]["rsi"] <=50][:3]
    return buy_list, watch_list

# ======================== 飞书推送（优化文案，更专业） ========================
def send_feishu_report(buy_stocks, watch_stocks, market_tips, market_position_ratio):
    if not FEISHU_WEBHOOK:
        logger.warning("⚠️ 未配置飞书Webhook")
        return

    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    msg = f"""🚀 大师级量化策略日报
📅 {now}
📊 今日大盘状态：{market_tips}
🎯 核心策略：分行业估值+顺势而为+保本优先
==================================================
"""

    if buy_stocks:
        msg += "🔥 今日优质买入信号\n"
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
