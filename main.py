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
# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 核心配置
CONFIG_CONTENT = os.environ.get("CONFIG_CONTENT")
if not CONFIG_CONTENT:
    raise Exception("❌ 未配置 CONFIG_CONTENT 环境变量")
try:
    CONFIG = json.loads(CONFIG_CONTENT)
except json.JSONDecodeError:
    raise Exception("❌ CONFIG_CONTENT 不是合法的 JSON 格式")

# 飞书Webhook
FEISHU_WEBHOOK = CONFIG.get("channels", {}).get("feishu", {}).get("webhook", {}).get("url", "")
if not FEISHU_WEBHOOK:
    logger.warning("⚠️ 未配置飞书 Webhook，推送功能将失效")

# 选股参数
SELECTION_TOP_N = 5  # 最终推送N只股票
HIST_DAYS = 90       # 技术面分析天数
CAPITAL = 10000      # 本金
SINGLE_MAX = 3000    # 单只股票最大持仓
MAX_PRICE = 25       # ✅ 新增：股价上限 25 元

# 基本面筛选阈值（可根据需求调整）
FUNDAMENTAL_FILTER = {
    "pe_max": 30,    # 市盈率上限
    "pb_max": 5,     # 市净率上限
    "market_cap_min": 100  # 市值下限（亿）
}

# 你原有自选股（优先保留）
MY_STOCKS = {
    "000968.SZ": "蓝焰控股",
    "600028.SS": "中国石化",
    "600968.SS": "海油发展"
}

# 全市场扫描池（沪深300+中证500核心成分股，精简版）
MARKET_SCAN_POOL = {
    # 沪深300核心
    "600000.SS": "浦发银行", "600016.SS": "民生银行", "600036.SS": "招商银行",
    "601318.SS": "中国平安", "601689.SS": "拓普集团", "601899.SS": "紫金矿业",
    "000858.SZ": "五粮液",   "000895.SZ": "双汇发展", "002594.SZ": "比亚迪",
    "300750.SZ": "宁德时代", "600519.SS": "贵州茅台", "601012.SS": "隆基绿能",
    # 中证500核心
    "000725.SZ": "京东方A",  "002475.SZ": "立讯精密", "002304.SZ": "洋河股份",
    "601898.SS": "中煤能源", "601989.SS": "中国重工", "600309.SS": "万华化学",
    "002230.SZ": "科大讯飞", "002415.SZ": "海康威视", "600887.SS": "伊利股份",
    "600438.SS": "通威股份", "000333.SZ": "美的集团", "601601.SS": "中国太保"
}

# ======================== 核心工具函数 ========================
def calc_technical_indicators(df):
    """计算技术面指标（RSI/MACD/KDJ/均线）"""
    df = df.copy().sort_index()
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)

    # 均线
    ma5 = close.rolling(5, min_periods=1).mean()
    ma20 = close.rolling(20, min_periods=1).mean()

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
    k = rsv.ewm(span=3).mean()
    d = k.ewm(span=3).mean()
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

def get_fundamental_data(symbol):
    """获取基本面数据（PE/PB/市值）"""
    try:
        tk = yf.Ticker(symbol)
        info = tk.info
        
        # 提取核心基本面指标
        pe = info.get("trailingPE", 999)  # 市盈率
        pb = info.get("priceToBook", 999) # 市净率
        # 市值（转成亿）
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
    """获取单只股票的技术面+基本面数据"""
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

        # ✅ 核心：股价超过25元直接跳过
        current_price = tech_indicators["price"]
        if current_price > MAX_PRICE:
            logger.info(f"❌ {symbol} {name} 股价 {current_price} 元 > {MAX_PRICE} 元，已过滤")
            return None
        
        # 3. 获取基本面数据
        fundamental = get_fundamental_data(symbol)
        
        # 4. 技术面评分（0-4分）
        tech_conds = [
            tech_indicators["rsi"] < 40,
            tech_indicators["macd_gold"],
            tech_indicators["j"] < 40,
            tech_indicators["trend_up"]
        ]
        tech_score = sum(tech_conds)
        
        # 5. 基本面筛选
        fund_filter_pass = (
            fundamental["pe"] < FUNDAMENTAL_FILTER["pe_max"] and
            fundamental["pb"] < FUNDAMENTAL_FILTER["pb_max"] and
            fundamental["market_cap"] > FUNDAMENTAL_FILTER["market_cap_min"]
        )
        
        # 6. 综合评分（技术分*0.7 + 基本面达标加1分）
        total_score = tech_score * 0.7 + (1 if fund_filter_pass else 0)
        
        # 7. 买入信号（技术分≥3 + 基本面达标）
        buy_signal = tech_score >= 3 and fund_filter_pass
        
        # 8. 条件单计算
        buy_price = round(tech_indicators["price"] * 0.97, 2)
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
            "total_score": round(total_score, 2),
            "buy_signal": buy_signal,
            "signal_text": "🔥 买入信号" if buy_signal else "⚠️ 观望",
            "order": {
                "buy_price": buy_price,
                "volume": volume,
                "profit10": round(buy_price * 1.1, 2),
                "profit15": round(buy_price * 1.15, 2),
                "stop_loss": round(tech_indicators["price"] * 0.94, 2)
            }
        }
    except Exception as e:
        logger.error(f"❌ {symbol} 数据获取失败: {str(e)[:30]}")
        return None

def scan_market():
    """全市场扫描选股"""
    all_stocks = {}
    
    # 1. 先处理自选股（优先保留）
    logger.info("🔍 开始分析自选股...")
    for symbol, name in MY_STOCKS.items():
        data = get_stock_data(symbol, name)
        if data:
            all_stocks[symbol] = data
        time.sleep(random.uniform(0.5, 1.0))  # 随机间隔防限流
    
    # 2. 全市场扫描（分批处理，避免超时）
    logger.info("🔍 开始全市场扫描...")
    scan_pool = list(MARKET_SCAN_POOL.items())
    # 分批：每10只一批，间隔2秒
    for i in range(0, len(scan_pool), 10):
        batch = scan_pool[i:i+10]
        for symbol, name in batch:
            # 跳过已在自选股的股票
            if symbol in all_stocks:
                continue
            data = get_stock_data(symbol, name)
            if data:
                all_stocks[symbol] = data
            time.sleep(random.uniform(0.3, 0.8))
        time.sleep(2)  # 批次间隔
    
    # 3. 筛选前N只综合评分最高的股票
    sorted_stocks = sorted(
        all_stocks.values(),
        key=lambda x: (x["buy_signal"], x["total_score"]),
        reverse=True
    )[:SELECTION_TOP_N]
    
    return sorted_stocks

def send_feishu_report(stocks):
    """生成并推送飞书报告"""
    if not FEISHU_WEBHOOK:
        logger.error("❌ 飞书Webhook未配置")
        return
    
    # 报告头部
    report = f"""🚀 A股量化选股报告（基本面+技术面）
📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}
📊 筛选规则：股价≤25元 + 技术面≥3分 + 基本面(PE<30/PB<5/市值>100亿)
==================================================
"""
    
    # 报告主体
    for idx, stock in enumerate(stocks, 1):
        report += f"""
【{idx}】{stock['code']} {stock['name']} {stock['signal_text']}
💯 综合评分：{stock['total_score']}（技术{stock['tech_score']}分+基本面{"1" if stock['fund_filter_pass'] else "0"}分）
💵 现价：{stock['tech']['price']} 元

📈 技术面指标：
RSI：{stock['tech']['rsi']}   MACD金叉：{'是' if stock['tech']['macd_gold'] else '否'}
KDJ：{stock['tech']['k']}/{stock['tech']['d']}/{stock['tech']['j']}
MA5：{stock['tech']['ma5']}  MA20：{stock['tech']['ma20']}  趋势向上：{'是' if stock['tech']['trend_up'] else '否'}

📊 基本面指标：
市盈率(PE)：{stock['fund']['pe']}   市净率(PB)：{stock['fund']['pb']}
市值：{stock['fund']['market_cap']} 亿元

📋 条件单建议：
买入 ≤ {stock['order']['buy_price']} 元，{stock['order']['volume']} 股
止盈10%：{stock['order']['profit10']} 元 | 止盈15%：{stock['order']['profit15']} 元
止损：{stock['order']['stop_loss']} 元
--------------------------------------------------
"""
    
    # 报告尾部
    report += """
⚠️ 风险提示：本报告仅为量化学习参考，不构成任何投资建议
📌 选股逻辑：技术面抓超跌反弹，基本面剔除高风险股票
"""
    
    # 推送飞书
    try:
        response = requests.post(
            FEISHU_WEBHOOK,
            json={"msg_type": "text", "content": {"text": report}},
            timeout=10
        )
        response.raise_for_status()
        logger.info("✅ 飞书报告推送成功")
    except Exception as e:
        logger.error(f"❌ 飞书推送失败: {e}")

def send_feishu_message(content):
    """单独发送飞书消息"""
    if not FEISHU_WEBHOOK:
        return
    try:
        requests.post(
            FEISHU_WEBHOOK,
            json={"msg_type": "text", "content": {"text": content}},
            timeout=5
        )
    except:
        pass

# ======================== 主程序 ========================
def main():
    logger.info("🚀 启动全市场量化选股系统（yfinance稳定版）")
    
    # 1. 全市场扫描选股
    selected_stocks = scan_market()
    
    # 2. 无符合条件股票处理
    if not selected_stocks:
        send_feishu_message(f"⚠️ 【{datetime.now().strftime('%Y-%m-%d %H:%M')}】暂无符合条件的股票（股价≤25元）")
        logger.warning("❌ 无符合条件的股票")
        return
    
    # 3. 推送报告
    send_feishu_report(selected_stocks)
    logger.info("🎉 全市场选股完成，报告已推送")

if __name__ == "__main__":
    main()
