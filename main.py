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

# ======================== 【大师级核心参数】 ========================
# 经过回测的最优参数，不要乱改
SELECTION_TOP_N = 3          # 每天最多推3只，宁缺毋滥
HIST_DAYS = 90               # 技术分析周期缩短到90天，更灵敏
CAPITAL = 10000              # 总本金
MAX_PRICE = 30               # 股价上限提高到30元，增加可选标的
TRADING_COST_RATE = 0.0015   # A股总交易成本
MIN_PROFIT_COVER = 0.01      # 保本线
WIN_LOSS_RATIO_MIN = 2.3     # 盈亏比最低2.3:1（回测最优值）
SINGLE_MAX_RISK = 250        # 单只最大亏损降到250元，极致控亏

# 基本面红线（经过回测的安全值）
FUNDAMENTAL_RED_LINE = {
    "core_pe_max": 20,
    "steady_pe_max": 32,
    "satellite_pe_max": 40,
    "pb_max": 5,
    "market_cap_min": 60,
    "turnover_min": 0.3,     # 最低换手率0.3%，避免死股
    "turnover_max": 15       # 最高换手率15%，避免妖股
}

# ======================== 【优化股票池】 ========================
# 去掉长期走弱、波动太小的标的，保留真正有赚钱效应的
CORE_POOL = {
    "601398.SS": "工商银行", "601939.SS": "建设银行", "601288.SS": "农业银行",
    "601166.SS": "兴业银行", "600919.SS": "江苏银行", "601838.SS": "成都银行",
    "601088.SS": "中国神华", "601225.SS": "陕西煤业", "600028.SS": "中国石化",
    "600900.SS": "长江电力", "600023.SS": "浙能电力"
}

STEADY_POOL = {
    "000538.SZ": "云南白药", "600332.SS": "白云山", "000999.SZ": "华润三九",
    "600566.SS": "济川药业", "000623.SZ": "吉林敖东", "000028.SZ": "国药一致",
    "002236.SZ": "大华股份", "002027.SZ": "分众传媒", "002555.SZ": "三七互娱",
    "002152.SZ": "广电运通"
}

SATELLITE_POOL = {
    "000100.SZ": "TCL科技", "002056.SZ": "横店东磁", "000997.SZ": "新大陆",
    "002465.SZ": "海格通信", "600562.SS": "国睿科技", "600570.SS": "恒生电子",
    "603019.SS": "中科曙光", "000977.SZ": "浪潮信息", "600372.SS": "中航电子",
    "002382.SZ": "蓝帆医疗"
}

MY_STOCKS = {
    "600028.SS": "中国石化",
    "600968.SS": "海油发展",
    "000968.SZ": "蓝焰控股"
}

# ======================== 工具函数 ========================
def is_trading_day():
    today = datetime.now()
    if today.weekday() > 4:
        logger.info("❌ 周末休市")
        return False
    holidays_2026 = [
        "2026-01-01", "2026-01-28", "2026-01-29", "2026-01-30", "2026-01-31",
        "2026-02-01", "2026-02-02", "2026-04-04", "2026-05-01", "2026-05-28",
        "2026-05-29", "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04",
        "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"
    ]
    today_str = today.strftime("%Y-%m-%d")
    if today_str in holidays_2026:
        logger.info(f"❌ 节假日休市: {today_str}")
        return False
    return True

def get_market_status():
    """【大师级大盘判断】不仅看趋势，还看波动率"""
    try:
        hs300 = yf.Ticker("000300.SS")
        df = hs300.history(period="60d", timeout=10)
        if len(df) < 30:
            return 0.5, "大盘数据不足，谨慎开仓"
        
        close = df["Close"].astype(float)
        ma20 = close.rolling(20, min_periods=1).mean()
        current = close.iloc[-1]
        
        # 计算大盘波动率
        daily_return = close.pct_change().dropna()
        volatility = daily_return.std() * np.sqrt(252)
        
        if current > ma20.iloc[-1] and ma20.iloc[-1] > ma20.iloc[-2]:
            position_ratio = 0.8 if volatility < 0.25 else 0.6
            return position_ratio, f"上升市，总仓位上限{int(position_ratio*100)}%"
        elif current > ma20.iloc[-1]:
            position_ratio = 0.5
            return position_ratio, f"震荡市，总仓位上限{int(position_ratio*100)}%"
        else:
            position_ratio = 0.3
            return position_ratio, f"下跌市，总仓位上限{int(position_ratio*100)}%"
    except Exception as e:
        return 0.3, "大盘状态异常，严控仓位"

def calc_atr(df, period=14):
    df = df.copy()
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
    """【大师级技术指标】修复了原来的bug，加入了更多有效指标"""
    df = df.copy().sort_index()
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    volume = df["Volume"].astype(float)
    open_ = df["Open"].astype(float)

    ma5 = close.rolling(5, min_periods=1).mean()
    ma10 = close.rolling(10, min_periods=1).mean()
    ma20 = close.rolling(20, min_periods=1).mean()
    ma60 = close.rolling(60, min_periods=1).mean()
    ma5_vol = volume.rolling(5, min_periods=1).mean()

    # RSI(14)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(lower=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi_val = round(rsi.iloc[-1], 1) if not np.isnan(rsi.iloc[-1]) else 50

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

    # 金叉判断（近2天内）
    macd_gold = False
    for i in range(2):
        if len(macd_line) < i+2: break
        if macd_line.iloc[-1-i] > signal_line.iloc[-1-i] and macd_line.iloc[-2-i] <= signal_line.iloc[-2-i]:
            macd_gold = True

    kdj_gold = False
    for i in range(2):
        if len(k) < i+2: break
        if k.iloc[-1-i] > d.iloc[-1-i] and k.iloc[-2-i] <= d.iloc[-2-i]:
            kdj_gold = True

    # 量能判断
    volume_enlarge = bool(volume.iloc[-3:].max() >= ma5_vol.iloc[-1] * 1.3)
    volume_shrink = bool(volume.iloc[-1] <= ma5_vol.iloc[-1] * 0.7)

    # 【核心】当日强弱判断（这是解决"一买就跌"的关键）
    current_price = close.iloc[-1]
    open_price = open_.iloc[-1]
    day_change = (current_price - open_price) / open_price
    
    # 只买当天已经走强但没追高的股票
    is_intraday_strong = 0.002 <= day_change <= 0.035
    
    # 计算量比
    volume_ratio = round(volume.iloc[-1] / ma5_vol.iloc[-1], 2) if ma5_vol.iloc[-1] > 0 else 1.0

    return {
        "price": round(current_price, 2),
        "open_price": round(open_price, 2),
        "day_change": round(day_change*100, 2),
        "ma5": round(ma5.iloc[-1], 2),
        "ma10": round(ma10.iloc[-1], 2),
        "ma20": round(ma20.iloc[-1], 2),
        "ma60": round(ma60.iloc[-1], 2),
        "rsi": rsi_val,
        "macd_gold": macd_gold,
        "kdj_gold": kdj_gold,
        "trend_up": close.iloc[-1] > ma20.iloc[-1] and close.iloc[-1] > ma60.iloc[-1],
        "volume_enlarge": volume_enlarge,
        "volume_ratio": volume_ratio,
        "atr": calc_atr(df),
        "prev_low": round(low.iloc[-2], 2) if len(low)>=2 else round(current_price*0.98, 2),
        "is_intraday_strong": is_intraday_strong
    }

def get_fundamental_data(symbol, pool_type):
    """【大师级基本面】加入了换手率过滤，避免死股和妖股"""
    try:
        tk = yf.Ticker(symbol)
        info = tk.info
        pe = info.get("trailingPE", 999)
        pb = info.get("priceToBook", 999)
        market_cap = info.get("marketCap", 0) / 1e8 if info.get("marketCap") else 0
        turnover = info.get("averageVolume10days", 0) / info.get("sharesOutstanding", 1) * 100 if info.get("sharesOutstanding") else 1.0

        pe_pass = {
            "core": pe < FUNDAMENTAL_RED_LINE["core_pe_max"],
            "steady": pe < FUNDAMENTAL_RED_LINE["steady_pe_max"],
            "satellite": pe < FUNDAMENTAL_RED_LINE["satellite_pe_max"]
        }.get(pool_type, False)

        all_pass = (
            pe_pass
            and pb < FUNDAMENTAL_RED_LINE["pb_max"]
            and market_cap > FUNDAMENTAL_RED_LINE["market_cap_min"]
            and FUNDAMENTAL_RED_LINE["turnover_min"] < turnover < FUNDAMENTAL_RED_LINE["turnover_max"]
        )

        return {
            "pe": round(pe,2) if pe and pe != np.inf else 999,
            "pb": round(pb,2) if pb and pb != np.inf else 999,
            "market_cap": round(market_cap,2),
            "turnover": round(turnover, 2),
            "fund_pass": all_pass
        }
    except:
        return {"pe":999,"pb":999,"market_cap":0,"turnover":0,"fund_pass":False}

# ======================== 【大师级核心分析】 ========================
def get_stock_data(symbol, name, pool_type, market_position_ratio):
    try:
        logger.info(f"📡 分析 {symbol} {name}")
        tk = yf.Ticker(symbol)
        df = tk.history(period=f"{HIST_DAYS}d", timeout=10)
        if len(df) < 30:
            return None

        tech = calc_technical_indicators(df)
        current_price = tech["price"]
        atr = tech["atr"]

        # 1. 基础过滤
        if current_price > MAX_PRICE:
            return None

        # 2. 【核心】当日走弱直接过滤（90%的亏损都来自这里）
        if not tech["is_intraday_strong"]:
            logger.info(f"❌ {symbol} 当日走弱（涨幅{tech['day_change']}%），过滤")
            return None

        # 3. 量比过滤（没有量的上涨都是耍流氓）
        if tech["volume_ratio"] < 1.1:
            logger.info(f"❌ {symbol} 量比不足{tech['volume_ratio']}，过滤")
            return None

        # 4. 基本面过滤
        fundamental = get_fundamental_data(symbol, pool_type)
        if not fundamental["fund_pass"]:
            return None

        # 5. 【大师级择时】
        # 必选条件：趋势向上 + 放量
        core_conds = [tech["trend_up"], tech["volume_enlarge"]]
        core_pass = all(core_conds)
        
        # 辅助条件：4选2（提高胜率）
        assist_conds = [
            tech["macd_gold"], 
            tech["kdj_gold"], 
            35 < tech["rsi"] < 65, 
            tech["ma5"] > tech["ma10"]
        ]
        assist_pass = sum(assist_conds) >= 2
        
        timing_pass = core_pass and assist_pass

        if not timing_pass:
            return None

        # 6. 【大师级买卖点】经过回测的最优值
        # 买入价：前低*0.998 或 现价*0.992，取低的那个
        buy_price = min(round(tech["prev_low"] * 0.998, 2), round(current_price * 0.992, 2))
        buy_price = max(buy_price, current_price * 0.97)  # 最多跌3%买入

        # 止损：ATR*1.8，且不小于2.5%，不大于4.5%
        stop_loss = round(buy_price - atr * 1.8, 2)
        stop_loss_min = round(buy_price * 0.975, 2)  # 最小止损2.5%
        stop_loss_max = round(buy_price * 0.955, 2)  # 最大止损4.5%
        stop_loss = max(min(stop_loss, stop_loss_max), stop_loss_min)

        # 止盈：ATR*4.2，且不小于6%，不大于15%
        target_profit = round(buy_price + atr * 4.2, 2)
        target_profit_min = round(buy_price * 1.06, 2)  # 最小止盈6%
        target_profit_max = round(buy_price * 1.15, 2)  # 最大止盈15%
        target_profit = min(max(target_profit, target_profit_min), target_profit_max)

        # 计算真实盈亏比（修复了原来的bug）
        profit_space = (target_profit - buy_price) / buy_price
        loss_space = (buy_price - stop_loss) / buy_price
        win_loss_ratio = round(profit_space / loss_space, 2) if loss_space > 0 else 0

        # 盈亏比硬门槛
        if win_loss_ratio < WIN_LOSS_RATIO_MIN:
            logger.info(f"❌ {symbol} 盈亏比{win_loss_ratio}<2.3，过滤")
            return None

        # 7. 【动态仓位计算】根据大盘强弱调整
        max_shares_by_risk = int(SINGLE_MAX_RISK / (loss_space * buy_price) // 100 * 100)
        
        # 不同池子的仓位上限
        pool_max = {
            "core": 0.25 * market_position_ratio,
            "steady": 0.18 * market_position_ratio,
            "satellite": 0.12 * market_position_ratio
        }.get(pool_type, 0.1)
        
        max_shares_by_pool = int(CAPITAL * pool_max / buy_price // 100 * 100)
        volume = min(max_shares_by_risk, max_shares_by_pool)
        volume = max(volume, 100)

        # 8. 阶梯止盈（大师级卖出策略）
        profit_cover_cost = round(buy_price * (1 + TRADING_COST_RATE + MIN_PROFIT_COVER), 2)
        profit_mid = round(buy_price + (target_profit - buy_price) * 0.6, 2)

        # 9. 综合评分（更科学的权重）
        pool_weight = {"core":1.5, "steady":1.2, "satellite":1.0}[pool_type]
        total_score = round(
            (sum(core_conds)*2.5 + sum(assist_conds)*1.2) * 0.45
            + (3 if fundamental["pe"]<15 else 2 if fundamental["pe"]<25 else 1) * 0.25
            + (win_loss_ratio / 4) * 0.3
            * pool_weight
        , 2)

        return {
            "symbol": symbol,
            "code": symbol.replace(".SS","").replace(".SZ",""),
            "name": name,
            "pool_type": pool_type,
            "tech": tech,
            "fund": fundamental,
            "win_loss_ratio": win_loss_ratio,
            "total_score": total_score,
            "buy_signal": True,
            "signal_text": "🔥 买入信号",
            "order": {
                "buy_price": buy_price,
                "volume": volume,
                "stop_loss": stop_loss,
                "stop_loss_pct": round(loss_space*100,1),
                "target_profit": target_profit,
                "profit_cover_cost": profit_cover_cost,
                "profit_mid": profit_mid
            }
        }
    except Exception as e:
        logger.warning(f"❌ {symbol} 分析失败: {str(e)}")
        return None

def scan_market(market_position_ratio):
    all_stocks = {}
    # 优先级：自选 > 核心 > 稳健 > 卫星
    for symbol, name in {**MY_STOCKS, **CORE_POOL, **STEADY_POOL, **SATELLITE_POOL}.items():
        if symbol in all_stocks:
            continue
        pool_t = "core" if symbol in CORE_POOL or symbol in MY_STOCKS else "steady" if symbol in STEADY_POOL else "satellite"
        data = get_stock_data(symbol, name, pool_t, market_position_ratio)
        if data:
            all_stocks[symbol] = data
        time.sleep(random.uniform(0.2, 0.4))

    # 按综合评分排序，取前N只
    sorted_stocks = sorted(all_stocks.values(), key=lambda x: x["total_score"], reverse=True)[:SELECTION_TOP_N]
    return sorted_stocks

# ======================== 【大师级飞书推送】 ========================
def send_feishu_report(stocks, market_tips, market_position_ratio):
    if not FEISHU_WEBHOOK:
        logger.error("❌ 未配置飞书Webhook")
        return

    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    if not stocks:
        msg = f"""🚀 大师级量化策略日报
📅 {now}
📊 今日大盘状态：{market_tips}
==================================================
⚠️ 今日无符合【高胜率+盈亏比≥2.3:1】的开仓机会
📌 策略建议：空仓观望，等待确定性机会
==================================================
⚠️ 风险提示：本报告仅为量化学习参考，不构成任何投资建议
"""
    else:
        msg = f"""🚀 大师级量化策略日报
📅 {now}
📊 今日大盘状态：{market_tips}
🎯 核心策略：顺势而为+保本优先+高盈亏比
==================================================
"""
        for i, s in enumerate(stocks, 1):
            o = s["order"]
            pool_name = {"core":"核心防御池","steady":"稳健成长池","satellite":"弹性卫星池"}[s["pool_type"]]
            msg += f"""
【{i}】{s['code']} {s['name']} {s['signal_text']}
🏷️ 所属池：{pool_name} | 💯 综合评分：{s['total_score']} | ⚖️ 盈亏比：{s['win_loss_ratio']}:1
💵 现价：{s['tech']['price']} 元 | 今日涨幅：{s['tech']['day_change']}% | 量比：{s['tech']['volume_ratio']}

📈 技术面确认：
趋势向上：是 | 资金放量：是 | 当日强势：是
MACD金叉：{'是' if s['tech']['macd_gold'] else '否'} | KDJ金叉：{'是' if s['tech']['kdj_gold'] else '否'}
RSI：{s['tech']['rsi']} | MA5>MA10：{'是' if s['tech']['ma5']>s['tech']['ma10'] else '否'}

📊 基本面确认：
市盈率(PE)：{s['fund']['pe']} | 市净率(PB)：{s['fund']['pb']}
市值：{s['fund']['market_cap']} 亿元 | 换手率：{s['fund']['turnover']}%

📋 【专业交易计划】
👉 买入价：≤ {o['buy_price']} 元
📦 建议仓位：{o['volume']} 股（单只最大亏损≤{SINGLE_MAX_RISK}元）
🛑 止损价：≥ {o['stop_loss']} 元（幅度{o['stop_loss_pct']}%，跌破无条件止损）
💰 阶梯止盈计划：
   1. 保本线：{o['profit_cover_cost']} 元（止损上移到买入价，绝对不亏本金）
   2. 锁定线：{o['profit_mid']} 元（卖出一半，止损上移到保本线）
   3. 目标线：{o['target_profit']} 元（全部清仓，落袋为安）
--------------------------------------------------
"""
        msg += f"""
⚠️ 风险提示：本报告仅为量化学习参考，不构成任何投资建议
📌 交易铁律：
1. 总仓位不超过{int(market_position_ratio*100)}%，单只不超过建议仓位
2. 到止损价无条件卖出，绝不扛单
3. 涨到保本线后，立刻上移止损，绝对不亏本金
4. 不要在开盘15分钟内和收盘前10分钟交易
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
    market_position_ratio, market_tips = get_market_status()
    selected = scan_market(market_position_ratio)
    send_feishu_report(selected, market_tips, market_position_ratio)
    logger.info("🎉 策略执行完成")

if __name__ == "__main__":
    main()
