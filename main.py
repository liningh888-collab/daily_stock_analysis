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

SELECTION_TOP_N = 5
HIST_DAYS = 90
CAPITAL = 10000
SINGLE_MAX = 3000
MAX_PRICE = 25

FUNDAMENTAL_FILTER = {
    "pe_max": 30,
    "pb_max": 5,
    "market_cap_min": 100
}

MY_STOCKS = {
    "000968.SZ": "蓝焰控股",
    "600028.SS": "中国石化",
    "600968.SS": "海油发展"
}

# ======================== ✅ 86 只安全股票池 ========================
MARKET_SCAN_POOL = {
    # 银行 20
    "601398.SS": "工商银行",
    "601939.SS": "建设银行",
    "601288.SS": "农业银行",
    "601988.SS": "中国银行",
    "601328.SS": "交通银行",
    "601166.SS": "兴业银行",
    "600000.SS": "浦发银行",
    "601998.SS": "中信银行",
    "600016.SS": "民生银行",
    "601818.SS": "光大银行",
    "600919.SS": "江苏银行",
    "601229.SS": "上海银行",
    "601169.SS": "北京银行",
    "601009.SS": "南京银行",
    "600926.SS": "杭州银行",
    "601838.SS": "成都银行",
    "601577.SS": "长沙银行",
    "601963.SS": "重庆银行",
    "601997.SS": "贵阳银行",
    "601665.SS": "齐鲁银行",

    # 能源 20
    "601088.SS": "中国神华",
    "601225.SS": "陕西煤业",
    "600028.SS": "中国石化",
    "600900.SS": "长江电力",
    "600011.SS": "华能国际",
    "601898.SS": "中煤能源",
    "600188.SS": "兖矿能源",
    "601001.SS": "晋控煤业",
    "600543.SS": "山煤国际",
    "600508.SS": "上海能源",
    "601918.SS": "新集能源",
    "600968.SS": "海油发展",
    "600583.SS": "海油工程",
    "601808.SS": "中海油服",
    "600027.SS": "华电国际",
    "601991.SS": "大唐发电",
    "600023.SS": "浙能电力",
    "600642.SS": "申能股份",
    "000883.SZ": "湖北能源",
    "601868.SS": "中国能建",

    # 医药 14
    "000538.SZ": "云南白药",
    "600332.SS": "白云山",
    "000999.SZ": "华润三九",
    "600566.SS": "济川药业",
    "600535.SS": "天士力",
    "000623.SZ": "吉林敖东",
    "000028.SZ": "国药一致",
    "600062.SS": "华润双鹤",
    "600329.SS": "中新药业",
    "600129.SS": "太极集团",
    "600557.SS": "康缘药业",
    "002737.SZ": "葵花药业",
    "300026.SZ": "红日药业",
    "000078.SZ": "海王生物",

    # 科技 17
    "000100.SZ": "TCL科技",
    "002236.SZ": "大华股份",
    "002027.SZ": "分众传媒",
    "002555.SZ": "三七互娱",
    "002056.SZ": "横店东磁",
    "000997.SZ": "新大陆",
    "002465.SZ": "海格通信",
    "002544.SZ": "杰赛科技",
    "600562.SS": "国睿科技",
    "600990.SS": "四创电子",
    "600271.SS": "航天信息",
    "002153.SZ": "石基信息",
    "002152.SZ": "广电运通",
    "600570.SS": "恒生电子",
    "603019.SS": "中科曙光",
    "000066.SZ": "中国长城",
    "000977.SZ": "浪潮信息",

    # 航天军工 15
    "600372.SS": "中航电子",
    "002013.SZ": "中航机电",
    "600765.SS": "中航重机",
    "000768.SZ": "中航西飞",
    "600038.SS": "中直股份",
    "600967.SS": "内蒙一机",
    "600435.SS": "北方导航",
    "600184.SS": "光电股份",
    "600262.SS": "北方股份",
    "600480.SS": "凌云股份",
    "600499.SS": "晋西车轴",
    "300719.SZ": "安达维尔",
    "688586.SS": "江航装备",
    "688636.SS": "智明达",
    "002382.SZ": "蓝帆医疗"
}

# ======================== ✅ 新增：A股交易日校验（避免节假日白跑） ========================
def is_trading_day():
    """
    判断今天是否为A股交易日
    逻辑：
    1. 周一到周五
    2. 不在法定节假日列表里（每年更新一次即可）
    """
    today = datetime.now()
    # 1. 先判断周几：周一(0)到周五(4)
    if today.weekday() > 4:
        logger.info("❌ 今天是周末，休市，直接退出")
        return False
    
    # 2. 法定节假日列表（2026年示例，每年更新一次）
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

# ======================== 【优化后】核心工具函数 ========================
def calc_technical_indicators(df):
    """计算技术面指标（修复逻辑+新增量能+放宽金叉）"""
    df = df.copy().sort_index()
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    volume = df["Volume"].astype(float)

    # 均线系统（新增MA10/MA60，完善趋势判断）
    ma5 = close.rolling(5, min_periods=1).mean()
    ma10 = close.rolling(10, min_periods=1).mean()
    ma20 = close.rolling(20, min_periods=1).mean()
    ma60 = close.rolling(60, min_periods=1).mean()
    ma5_vol = volume.rolling(5, min_periods=1).mean()

    # RSI(14)（保留，优化区间判断）
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta).clip(lower=0).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = 50 if np.isnan(rsi.iloc[-1]) else rsi  # 处理NaN

    # MACD（放宽金叉判断，近3天内有效）
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()

    # KDJ(9)（新增金叉判断，替代原来的单一J值）
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
        if len(macd_line) < i+2:
            break
        if macd_line.iloc[last-i] > signal_line.iloc[last-i] and macd_line.iloc[last-i-1] <= signal_line.iloc[last-i-1]:
            macd_gold = True
            break
    # KDJ金叉判断
    kdj_gold = bool(k.iloc[last] > d.iloc[last] and k.iloc[last-1] <= d.iloc[last-1]) if len(k)>=2 else False
    # 放量判断
    volume_enlarge = bool(volume.iloc[last] >= ma5_vol.iloc[last] * 1.2) if len(volume)>=5 else False

    return {
        "price": round(close.iloc[last], 2),
        "ma5": round(ma5.iloc[last], 2),
        "ma10": round(ma10.iloc[last], 2),
        "ma20": round(ma20.iloc[last], 2),
        "ma60": round(ma60.iloc[last], 2),
        "rsi": round(rsi.iloc[last], 1),
        "macd": round(macd_line.iloc[last], 2),
        "signal": round(signal_line.iloc[last], 2),
        "k": round(k.iloc[last], 1),
        "d": round(d.iloc[last], 1),
        "j": round(j.iloc[last], 1),
        "macd_gold": macd_gold,
        "kdj_gold": kdj_gold,
        "trend_up": bool(close.iloc[last] > ma20.iloc[last]),
        "short_trend_up": bool(ma5.iloc[last] > ma10.iloc[last]),
        "volume": round(volume.iloc[last], 0),
        "ma5_vol": round(ma5_vol.iloc[last], 0),
        "volume_enlarge": volume_enlarge
    }

def get_fundamental_data(symbol):
    try:
        tk = yf.Ticker(symbol)
        info = tk.info
        pe = info.get("trailingPE", 999)
        pb = info.get("priceToBook", 999)
        market_cap = info.get("marketCap", 0) / 1e8 if info.get("marketCap") else 0
        return {
            "pe": round(pe, 2) if pe and pe != np.inf else 999,
            "pb": round(pb, 2) if pb and pb != np.inf else 999,
            "market_cap": round(market_cap, 2)
        }
    except Exception as e:
        logger.warning(f"❌ {symbol} 基本面获取失败: {str(e)[:30]}")
        return {"pe": 999, "pb": 999, "market_cap": 0}

def get_stock_data(symbol, name):
    """获取单只股票的技术面+基本面数据（优化评分体系）"""
    try:
        # 1. 获取技术面数据
        logger.info(f"📡 分析 {symbol} {name}...")
        tk = yf.Ticker(symbol)
        df = tk.history(period=f"{HIST_DAYS}d", timeout=10)
        if len(df) < 30:
            logger.warning(f"⚠️ {symbol} 技术数据不足")
            return None
        
        # 2. 计算技术指标
        tech_indicators = calc_technical_indicators(df)

        # 核心：股价超过25元直接跳过
        current_price = tech_indicators["price"]
        if current_price > MAX_PRICE:
            logger.info(f"❌ {symbol} {name} 股价 {current_price} 元 > {MAX_PRICE} 元，已过滤")
            return None
        
        # 3. 获取基本面数据
        fundamental = get_fundamental_data(symbol)
        
        # 4. 【优化后】技术面评分（0-6分，≥3分达标）
        tech_conds = [
            tech_indicators["rsi"] > 30 and tech_indicators["rsi"] < 55,  # RSI健康区间
            tech_indicators["macd_gold"],  # 近3天MACD金叉
            tech_indicators["kdj_gold"],   # KDJ金叉
            tech_indicators["short_trend_up"],  # 短期趋势向上
            tech_indicators["volume_enlarge"],  # 放量有资金
            tech_indicators["price"] > tech_indicators["ma60"],  # 无长期下跌风险
        ]
        tech_score = sum(tech_conds)
        
        # 5. 基本面筛选+评分
        fund_filter_pass = (
            fundamental["pe"] < FUNDAMENTAL_FILTER["pe_max"] and
            fundamental["pb"] < FUNDAMENTAL_FILTER["pb_max"] and
            fundamental["market_cap"] > FUNDAMENTAL_FILTER["market_cap_min"]
        )
        # 基本面深度评分（0-3分）
        fund_score = 0
        if fundamental["pe"] < 20: fund_score += 2
        elif fundamental["pe"] < 30: fund_score += 1
        if fundamental["pb"] < 3: fund_score += 1

        # 6. 【优化后】资金量能评分（0-2分）
        money_score = 2 if tech_indicators["volume_enlarge"] else 0

        # 7. 【优化后】综合评分（技术40% + 基本面30% + 资金30%）
        total_score = round(tech_score * 0.4 + fund_score * 0.3 + money_score * 0.3, 2)
        
        # 8. 【优化后】买入信号（双保险，不浪费优质标的）
        buy_signal = (
            (tech_score >= 3 and fund_filter_pass)
            or (tech_score >= 2 and fund_score >= 3 and money_score >= 2)
        )
        
        # 9. 条件单计算（保留你的原有仓位管理）
        buy_price = round(current_price * 0.97, 2)
        volume = int(SINGLE_MAX / buy_price // 100 * 100)
        volume = max(volume, 100)
        
        return {
            "symbol": symbol,
            "code": symbol.replace(".SS", "").replace(".SZ", ""),
            "name": name,
            "tech": tech_indicators,
            "fund": fundamental,
            "tech_score": tech_score,
            "fund_filter_pass": fund_filter_pass,
            "fund_score": fund_score,  # 新增：用于报告显示
            "total_score": total_score,
            "buy_signal": buy_signal,
            "signal_text": "🔥 买入信号" if buy_signal else "⚠️ 观望",
            "order": {
                "buy_price": buy_price,
                "volume": volume,
                "profit10": round(buy_price * 1.1, 2),
                "profit15": round(buy_price * 1.15, 2),
                "stop_loss": round(current_price * 0.94, 2)
            }
        }
    except Exception as e:
        logger.error(f"❌ {symbol} 数据获取失败: {str(e)[:30]}")
        return None

def scan_market():
    all_stocks = {}
    logger.info("🔍 开始分析自选股...")
    for symbol, name in MY_STOCKS.items():
        data = get_stock_data(symbol, name)
        if data:
            all_stocks[symbol] = data
        time.sleep(random.uniform(0.5, 1.0))
    
    logger.info("🔍 开始扫描 86 只安全股票池...")
    scan_pool = list(MARKET_SCAN_POOL.items())
    for i in range(0, len(scan_pool), 10):
        batch = scan_pool[i:i+10]
        for symbol, name in batch:
            if symbol in all_stocks:
                continue
            data = get_stock_data(symbol, name)
            if data:
                all_stocks[symbol] = data
            time.sleep(random.uniform(0.3, 0.8))
        time.sleep(2)
    
    sorted_stocks = sorted(
        all_stocks.values(),
        key=lambda x: (x["buy_signal"], x["total_score"]),
        reverse=True
    )[:SELECTION_TOP_N]
    return sorted_stocks

def send_feishu_report(stocks):
    if not FEISHU_WEBHOOK:
        logger.error("❌ 飞书Webhook未配置")
        return
    
    report = f"""🚀 A股量化选股报告（优化版）
📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}
📊 筛选规则：股价≤25元 + 技术面≥3分 + 基本面(PE<30/PB<5/市值>100亿)
✨ 优化亮点：放宽金叉+新增量能+修复逻辑互斥+交易日校验
==================================================
"""
    for idx, stock in enumerate(stocks, 1):
        # ✅ 修复：显示清晰的基本面分数，不再是True/False
        fund_display = stock.get("fund_score", 1 if stock["fund_filter_pass"] else 0)
        money_display = 2 if stock['tech']['volume_enlarge'] else 0
        
        report += f"""
【{idx}】{stock['code']} {stock['name']} {stock['signal_text']}
💯 综合评分：{stock['total_score']}（技术{stock['tech_score']}分+基本面{fund_display}分+资金{money_display}分）
💵 现价：{stock['tech']['price']} 元

📈 技术面指标：
RSI：{stock['tech']['rsi']}   MACD金叉：{'是' if stock['tech']['macd_gold'] else '否'}
KDJ：{stock['tech']['k']}/{stock['tech']['d']}/{stock['tech']['j']}
MA5：{stock['tech']['ma5']}  MA20：{stock['tech']['ma20']}  趋势向上：{'是' if stock['tech']['trend_up'] else '否'}
放量：{'是' if stock['tech']['volume_enlarge'] else '否'}

📊 基本面指标：
市盈率(PE)：{stock['fund']['pe']}   市净率(PB)：{stock['fund']['pb']}
市值：{stock['fund']['market_cap']} 亿元

📋 条件单建议：
买入 ≤ {stock['order']['buy_price']} 元，{stock['order']['volume']} 股
止盈10%：{stock['order']['profit10']} 元 | 止盈15%：{stock['order']['profit15']} 元
止损：{stock['order']['stop_loss']} 元
--------------------------------------------------
"""
    report += """
⚠️ 风险提示：本报告仅为量化学习参考，不构成任何投资建议
📌 选股逻辑：技术面抓健康启动，基本面剔除高风险，量能过滤假反弹
"""
    try:
        requests.post(FEISHU_WEBHOOK, json={"msg_type": "text", "content": {"text": report}}, timeout=10)
        logger.info("✅ 飞书报告推送成功")
    except Exception as e:
        logger.error(f"❌ 飞书推送失败: {e}")

def send_feishu_message(content):
    if not FEISHU_WEBHOOK:
        return
    try:
        requests.post(FEISHU_WEBHOOK, json={"msg_type": "text", "content": {"text": content}}, timeout=5)
    except:
        pass

# ======================== 主程序 ========================
def main():
    # ✅ 新增：先校验交易日，不是交易日直接退出
    if not is_trading_day():
        return
    
    logger.info("🚀 启动 86 只安全股票池量化扫描（优化版）")
    selected_stocks = scan_market()
    if not selected_stocks:
        send_feishu_message(f"⚠️ 【{datetime.now().strftime('%Y-%m-%d %H:%M')}】暂无符合条件股票")
        logger.warning("❌ 无符合条件股票")
        return
    send_feishu_report(selected_stocks)
    logger.info("🎉 扫描完成，已推送最优 5 只")

if __name__ == "__main__":
    main()
