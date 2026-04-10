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

# ======================== 全局配置（分析师优化版） ========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 环境配置
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

# ======================== 策略核心参数（严格风控优先） ========================
SELECTION_TOP_N = 2  # 单日最多推2只，控制交易频率
HIST_DAYS = 120      # 技术分析周期拉长，提高趋势判断准确率
CAPITAL = 10000      # 总本金
MAX_PRICE = 25       # 股价上限
TRADING_COST_RATE = 0.0015  # A股买卖总交易成本（佣金+印花税+过户费）
MIN_PROFIT_COVER = 0.01      # 保本线：覆盖成本后净赚1%
WIN_LOSS_RATIO_MIN = 2.2     # 盈亏比最低2.2:1，硬门槛
SINGLE_MAX_RISK = 300        # 单只股票最大亏损不超过300元，极致控亏

# 基本面筛选红线（一票否决）
FUNDAMENTAL_RED_LINE = {
    "core_pe_max": 15,    # 核心池PE上限
    "steady_pe_max": 25,  # 稳健池PE上限
    "satellite_pe_max": 30,# 卫星池PE上限
    "pb_max": 4,          # 全池PB上限
    "market_cap_min": 100 # 全池市值下限（亿）
}

# ======================== 三层安全分级股票池（分析师精选） ========================
# 核心防御池（60%权重，绝对安全，高分红低波动）
CORE_POOL = {
    "601398.SS": "工商银行", "601939.SS": "建设银行", "601288.SS": "农业银行",
    "601988.SS": "中国银行", "601328.SS": "交通银行", "601166.SS": "兴业银行",
    "600000.SS": "浦发银行", "601998.SS": "中信银行", "601818.SS": "光大银行",
    "600919.SS": "江苏银行", "601838.SS": "成都银行", "601088.SS": "中国神华",
    "601225.SS": "陕西煤业", "600028.SS": "中国石化", "600900.SS": "长江电力",
    "601898.SS": "中煤能源", "600188.SS": "兖矿能源", "601001.SS": "晋控煤业",
    "600023.SS": "浙能电力", "600642.SS": "申能股份"
}

# 稳健成长池（30%权重，稳中有涨，业绩稳定）
STEADY_POOL = {
    "000538.SZ": "云南白药", "600332.SS": "白云山", "000999.SZ": "华润三九",
    "600566.SS": "济川药业", "600535.SS": "天士力", "000623.SZ": "吉林敖东",
    "000028.SZ": "国药一致", "600062.SS": "华润双鹤", "600329.SS": "中新药业",
    "600129.SS": "太极集团", "600557.SS": "康缘药业", "002737.SZ": "葵花药业",
    "002236.SZ": "大华股份", "002027.SZ": "分众传媒", "002555.SZ": "三七互娱",
    "002152.SZ": "广电运通"
}

# 弹性卫星池（10%权重，小仓位博弹性，严格风控）
SATELLITE_POOL = {
    "000100.SZ": "TCL科技", "002056.SZ": "横店东磁", "000997.SZ": "新大陆",
    "002465.SZ": "海格通信", "002544.SZ": "杰赛科技", "600562.SS": "国睿科技",
    "600990.SS": "四创电子", "600271.SS": "航天信息", "002153.SZ": "石基信息",
    "600570.SS": "恒生电子", "603019.SS": "中科曙光", "000066.SZ": "中国长城",
    "000977.SZ": "浪潮信息", "600372.SS": "中航电子", "002013.SZ": "中航机电",
    "600765.SS": "中航重机", "000768.SZ": "中航西飞", "600038.SS": "中直股份",
    "600967.SS": "内蒙一机", "002382.SZ": "蓝帆医疗"
}

# 自选股优先池
MY_STOCKS = {
    "000968.SZ": "蓝焰控股",
    "600028.SS": "中国石化",
    "600968.SS": "海油发展"
}

# ======================== 核心工具函数（分析师优化版） ========================
def is_trading_day():
    """A股交易日校验，避免节假日/周末无效运行"""
    today = datetime.now()
    if today.weekday() > 4:
        logger.info("❌ 今天是周末，休市，直接退出")
        return False
    # 2026年A股法定节假日休市列表，每年更新一次
    holidays_2026 = [
        "2026-01-01", "2026-01-28", "2026-01-29", "2026-01-30", "2026-01-31",
        "2026-02-01", "2026-02-02", "2026-04-04", "2026-05-01", "2026-05-28",
        "2026-05-29", "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04",
        "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"
    ]
    today_str = today.strftime("%Y-%m-%d")
    if today_str in holidays_2026:
        logger.info(f"❌ 今天是法定节假日 {today_str}，休市，直接退出")
        return False
    logger.info("✅ 今天是A股交易日，继续执行")
    return True

def get_market_status():
    """大盘安全开关：下跌趋势也允许开仓（已修改）"""
    try:
        # 沪深300指数
        hs300 = yf.Ticker("000300.SS")
        df = hs300.history(period="60d", timeout=10)
        if len(df) < 30:
            logger.warning("⚠️ 沪深300数据获取失败，默认开启开仓权限")
            return True, "大盘数据获取失败，谨慎开仓"
        
        close = df["Close"].astype(float)
        ma20 = close.rolling(20, min_periods=1).mean()
        current_price = close.iloc[-1]
        ma20_current = ma20.iloc[-1]
        ma20_prev = ma20.iloc[-2]

        # 👇👇👇 关键修改：无论趋势如何，都允许开仓
        if current_price > ma20_current and ma20_current > ma20_prev:
            logger.info("✅ 大盘处于上升趋势，开放开仓权限")
            return True, "上升市，总仓位上限80%"
        elif current_price > ma20_current:
            logger.info("⚠️ 大盘处于震荡趋势，限制开仓权限")
            return True, "震荡市，总仓位上限50%"
        else:
            logger.info("⚠️ 大盘处于下跌趋势，允许谨慎开仓（已修改）")
            return True, "下跌市，谨慎开仓，严控仓位与止损"
    except Exception as e:
        logger.error(f"❌ 大盘状态获取失败: {str(e)}")
        return True, "大盘状态异常，谨慎开仓"

def calc_atr(df, period=14):
    """计算ATR平均真实波幅，用于动态止损止盈"""
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
    """技术指标计算（高胜率优化版）"""
    df = df.copy().sort_index()
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    volume = df["Volume"].astype(float)

    # 均线系统（趋势判断核心）
    ma5 = close.rolling(5, min_periods=1).mean()
    ma10 = close.rolling(10, min_periods=1).mean()
    ma20 = close.rolling(20, min_periods=1).mean()
    ma60 = close.rolling(60, min_periods=1).mean()
    ma5_vol = volume.rolling(5, min_periods=1).mean()

    # RSI(14)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta).clip(lower=0).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = 50 if np.isnan(rsi.iloc[-1]) else rsi

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
    # 近3天MACD金叉判断
    macd_gold = False
    for i in range(0, 3):
        if len(macd_line) < i+2: break
        if macd_line.iloc[last-i] > signal_line.iloc[last-i] and macd_line.iloc[last-i-1] <= signal_line.iloc[last-i-1]:
            macd_gold = True
            break
    # 近3天KDJ金叉判断
    kdj_gold = False
    for i in range(0, 3):
        if len(k) < i+2: break
        if k.iloc[last-i] > d.iloc[last-i] and k.iloc[last-i-1] <= d.iloc[last-i-1]:
            kdj_gold = True
            break
    # 放量判断（近3天有放量）
    volume_enlarge = bool(volume.iloc[-3:].max() >= ma5_vol.iloc[last] * 1.2) if len(volume)>=5 else False

    return {
        "price": round(close.iloc[last], 2),
        "ma5": round(ma5.iloc[last], 2),
        "ma10": round(ma10.iloc[last], 2),
        "ma20": round(ma20.iloc[last], 2),
        "ma60": round(ma60.iloc[last], 2),
        "rsi": round(rsi.iloc[last], 1),
        "macd_gold": macd_gold,
        "kdj_gold": kdj_gold,
        "trend_up": bool(close.iloc[last] > ma20.iloc[last] and close.iloc[last] > ma60.iloc[last]),
        "volume_enlarge": volume_enlarge,
        "atr": calc_atr(df),
        "prev_low": round(low.iloc[last-1], 2)
    }

def get_fundamental_data(symbol, pool_type):
    """基本面数据获取+分级筛选，一票否决"""
    try:
        tk = yf.Ticker(symbol)
        info = tk.info
        # 核心基本面指标
        pe = info.get("trailingPE", 999)
        pb = info.get("priceToBook", 999)
        market_cap = info.get("marketCap", 0) / 1e8 if info.get("marketCap") else 0
        net_profit_growth = info.get("netIncomeToCommon", 0)
        pledge_ratio = info.get("pledgedPercent", 0)

        # 分级PE筛选
        pe_pass = False
        if pool_type == "core":
            pe_pass = pe < FUNDAMENTAL_RED_LINE["core_pe_max"]
        elif pool_type == "steady":
            pe_pass = pe < FUNDAMENTAL_RED_LINE["steady_pe_max"]
        elif pool_type == "satellite":
            pe_pass = pe < FUNDAMENTAL_RED_LINE["satellite_pe_max"]

        # 一票否决项
        all_pass = (
            pe_pass
            and pb < FUNDAMENTAL_RED_LINE["pb_max"]
            and market_cap > FUNDAMENTAL_RED_LINE["market_cap_min"]
            and pledge_ratio < 30
        )

        return {
            "pe": round(pe, 2) if pe and pe != np.inf else 999,
            "pb": round(pb, 2) if pb and pb != np.inf else 999,
            "market_cap": round(market_cap, 2),
            "net_profit_growth": round(net_profit_growth, 2),
            "pool_type": pool_type,
            "fund_pass": all_pass
        }
    except Exception as e:
        logger.warning(f"❌ {symbol} 基本面获取失败: {str(e)[:30]}")
        return {"pe": 999, "pb": 999, "market_cap": 0, "fund_pass": False}

def get_stock_data(symbol, name, pool_type):
    """单只股票全维度分析，严格筛选"""
    try:
        logger.info(f"📡 分析 {symbol} {name} [{pool_type}池]")
        tk = yf.Ticker(symbol)
        df = tk.history(period=f"{HIST_DAYS}d", timeout=10)
        if len(df) < 30:
            logger.warning(f"⚠️ {symbol} 技术数据不足")
            return None
        
        # 技术指标计算
        tech = calc_technical_indicators(df)
        current_price = tech["price"]
        atr = tech["atr"]

        # 股价上限过滤
        if current_price > MAX_PRICE:
            logger.info(f"❌ {symbol} 股价{current_price}元>25元，过滤")
            return None
        
        # 基本面筛选
        fundamental = get_fundamental_data(symbol, pool_type)
        if not fundamental["fund_pass"]:
            logger.info(f"❌ {symbol} 基本面不达标，过滤")
            return None
        
        # 【核心】高胜率择时条件判断
        # 必选2个核心条件
        core_conds = [tech["trend_up"], tech["volume_enlarge"]]
        core_pass = all(core_conds)
        # 辅助条件（4选2）
        assist_conds = [tech["macd_gold"], tech["kdj_gold"], tech["rsi"]>30 and tech["rsi"]<55, tech["ma5"]>tech["ma10"]]
        assist_pass = sum(assist_conds) >= 2
        # 择时总开关
        timing_pass = core_pass and assist_pass

        # 【核心】盈亏比硬门槛判断
        # 动态买入价
        buy_price = min(round(tech["prev_low"] * 0.997, 2), round(current_price * 0.99, 2))
        buy_price = max(buy_price, current_price * 0.97)
        # 动态止损价
        stop_loss = max(round(buy_price - atr * 1.5, 2), round(tech["ma10"] * 0.99, 2), buy_price * 0.94)
        # 目标止盈价
        target_profit = round(buy_price + atr * 2.2, 2)
        target_profit = min(target_profit, buy_price * 1.2)
        # 计算盈亏比
        profit_space = (target_profit - buy_price) / buy_price
        loss_space = (buy_price - stop_loss) / buy_price
        win_loss_ratio = profit_space / loss_space if loss_space > 0 else 0

        # 盈亏比不达标，直接过滤
        if win_loss_ratio < WIN_LOSS_RATIO_MIN:
            logger.info(f"❌ {symbol} 盈亏比{round(win_loss_ratio,2)}<2.2，过滤")
            return None
        
        # 动态仓位计算（基于风险）
        stop_loss_pct = loss_space
        max_shares_by_risk = int(SINGLE_MAX_RISK / (stop_loss_pct * buy_price) // 100 * 100)
        # 分级仓位上限
        if pool_type == "core":
            max_shares_by_pool = int(CAPITAL * 0.2 / buy_price // 100 * 100)
        elif pool_type == "steady":
            max_shares_by_pool = int(CAPITAL * 0.15 / buy_price // 100 * 100)
        else:
            max_shares_by_pool = int(CAPITAL * 0.1 / buy_price // 100 * 100)
        volume = min(max_shares_by_risk, max_shares_by_pool)
        volume = max(volume, 100)

        # 阶梯止盈计算
        profit_cover_cost = round(buy_price * (1 + TRADING_COST_RATE + MIN_PROFIT_COVER), 2)
        profit_mid = round(buy_price + (target_profit - buy_price) * 0.5, 2)

        # 综合评分（池类型权重+择时+基本面+盈亏比）
        pool_weight = 1.5 if pool_type == "core" else 1.2 if pool_type == "steady" else 1.0
        total_score = round(
            (sum(core_conds) * 2 + sum(assist_conds) * 1) * 0.4
            + (3 if fundamental["pe"] < 15 else 2 if fundamental["pe"] <25 else 1) * 0.3
            + (win_loss_ratio / 3) * 0.3
            * pool_weight
        , 2)

        # 买入信号最终确认
        buy_signal = timing_pass and win_loss_ratio >= WIN_LOSS_RATIO_MIN

        return {
            "symbol": symbol,
            "code": symbol.replace(".SS", "").replace(".SZ", ""),
            "name": name,
            "pool_type": pool_type,
            "tech": tech,
            "fund": fundamental,
            "win_loss_ratio": round(win_loss_ratio, 2),
            "total_score": total_score,
            "buy_signal": buy_signal,
            "signal_text": "🔥 买入信号" if buy_signal else "⚠️ 观望",
            "order": {
                "buy_price": buy_price,
                "volume": volume,
                "stop_loss": stop_loss,
                "stop_loss_pct": round(stop_loss_pct * 100, 1),
                "target_profit": target_profit,
                "profit_cover_cost": profit_cover_cost,
                "profit_mid": profit_mid
            }
        }
    except Exception as e:
        logger.error(f"❌ {symbol} 分析失败: {str(e)[:30]}")
        return None

def scan_market(market_can_open):
    """全市场扫描，按优先级排序"""
    all_stocks = {}
    # 1. 优先分析自选股
    logger.info("🔍 开始分析自选股...")
    for symbol, name in MY_STOCKS.items():
        data = get_stock_data(symbol, name, "core")
        if data:
            all_stocks[symbol] = data
        time.sleep(random.uniform(0.5, 1.0))
    
    # 2. 按池优先级扫描（核心池→稳健池→卫星池）
    if market_can_open:
        logger.info("🔍 开始扫描核心防御池...")
        for symbol, name in CORE_POOL.items():
            if symbol in all_stocks: continue
            data = get_stock_data(symbol, name, "core")
            if data: all_stocks[symbol] = data
            time.sleep(random.uniform(0.3, 0.7))
        
        logger.info("🔍 开始扫描稳健成长池...")
        for symbol, name in STEADY_POOL.items():
            if symbol in all_stocks: continue
            data = get_stock_data(symbol, name, "steady")
            if data: all_stocks[symbol] = data
            time.sleep(random.uniform(0.3, 0.7))
        
        logger.info("🔍 开始扫描弹性卫星池...")
        for symbol, name in SATELLITE_POOL.items():
            if symbol in all_stocks: continue
            data = get_stock_data(symbol, name, "satellite")
            if data: all_stocks[symbol] = data
            time.sleep(random.uniform(0.3, 0.7))
    
    # 排序：买入信号优先，综合评分从高到低，最多取TOP2
    sorted_stocks = sorted(
        all_stocks.values(),
        key=lambda x: (x["buy_signal"], x["total_score"]),
        reverse=True
    )[:SELECTION_TOP_N]
    
    # 只保留有买入信号的标的
    final_stocks = [s for s in sorted_stocks if s["buy_signal"]]
    return final_stocks

def send_feishu_report(stocks, market_tips):
    """飞书报告生成（分析师专业版）"""
    if not FEISHU_WEBHOOK:
        logger.error("❌ 飞书Webhook未配置")
        return
    
    if not stocks:
        report = f"""🚀 A股稳健量化策略日报
📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}
📊 今日大盘状态：{market_tips}
==================================================
⚠️ 今日无符合【高胜率+盈亏比≥2.2:1】的开仓机会
📌 策略建议：空仓观望，不做无效交易，等待确定性机会
==================================================
⚠️ 风险提示：本报告仅为量化学习参考，不构成任何投资建议
"""
    else:
        report = f"""🚀 A股稳健量化策略日报
📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}
📊 今日大盘状态：{market_tips}
🎯 核心策略：保本优先+高盈亏比+少交易少犯错
==================================================
"""
        for idx, stock in enumerate(stocks, 1):
            o = stock["order"]
            pool_name = "核心防御池" if stock["pool_type"]=="core" else "稳健成长池" if stock["pool_type"]=="steady" else "弹性卫星池"
            report += f"""
【{idx}】{stock['code']} {stock['name']} 🔥 买入信号
🏷️ 所属池：{pool_name} | 💯 综合评分：{stock['total_score']} | ⚖️ 盈亏比：{stock['win_loss_ratio']}:1
💵 现价：{stock['tech']['price']} 元 | ATR波动率：{stock['tech']['atr']} 元

📈 技术面确认：
趋势向上：是（MA20+MA60上方）| 资金放量：是
MACD金叉：{'是' if stock['tech']['macd_gold'] else '否'} | KDJ金叉：{'是' if stock['tech']['kdj_gold'] else '否'}
RSI：{stock['tech']['rsi']} | MA5>MA10：{'是' if stock['tech']['ma5']>stock['tech']['ma10'] else '否'}

📊 基本面确认：
市盈率(PE)：{stock['fund']['pe']} | 市净率(PB)：{stock['fund']['pb']}
市值：{stock['fund']['market_cap']} 亿元 | 基本面达标：是

📋 【专业交易计划】
👉 买入价：≤ {o['buy_price']} 元
📦 建议仓位：{o['volume']} 股（单只最大亏损≤{SINGLE_MAX_RISK}元）
🛑 止损价：≥ {o['stop_loss']} 元（幅度{o['stop_loss_pct']}%，跌破MA10无条件止损）
💰 阶梯止盈计划：
   1. 保本线：{o['profit_cover_cost']} 元（覆盖成本+1%净赚，止损上移到买入价，绝对不亏本金）
   2. 锁定线：{o['profit_mid']} 元（目标50%，止损上移到买入价+3%，锁定利润）
   3. 目标线：{o['target_profit']} 元（全部清仓，落袋为安）
--------------------------------------------------
"""
        report += """
⚠️ 风险提示：本报告仅为量化学习参考，不构成任何投资建议
📌 交易铁律：
1. 不超过建议仓位，不加仓下跌的股票
2. 到止损价无条件卖出，不扛单
3. 涨到保本线后，立刻上移止损，绝对不亏本金
"""
    
    # 推送飞书
    try:
        requests.post(FEISHU_WEBHOOK, json={"msg_type": "text", "content": {"text": report}}, timeout=10)
        logger.info("✅ 飞书报告推送成功")
    except Exception as e:
        logger.error(f"❌ 飞书推送失败: {e}")

# ======================== 主程序 ========================
def main():
    # 1. 交易日校验
    if not is_trading_day():
        return
    
    # 2. 大盘状态判断
    market_can_open, market_tips = get_market_status()
    
    # 3. 扫描选股
    logger.info("🚀 启动高胜率稳健量化扫描")
    selected_stocks = scan_market(market_can_open)
    
    # 4. 推送报告
    send_feishu_report(selected_stocks, market_tips)
    logger.info("🎉 扫描完成，报告已推送")

if __name__ == "__main__":
    main()
