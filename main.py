import requests
import json
import logging
import os
from datetime import datetime, timedelta
import time
import pandas as pd
import numpy as np

# ======================== 多数据源适配（严格按你的 requirements.txt 优先级） ========================
# Priority 0: efinance → Priority 1: akshare → Priority 4: yfinance
ef = None
ak = None
yf = None

# 加载日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 按优先级加载数据源
try:
    import efinance as ef
    logger.info("✅ [优先级0] efinance 加载成功（东方财富数据源）")
except ImportError as e:
    logger.warning(f"⚠️ efinance 加载失败: {e}，尝试加载 akshare")
    try:
        import akshare as ak
        logger.info("✅ [优先级1] akshare 加载成功（东方财富爬虫数据源）")
    except ImportError as e2:
        logger.warning(f"⚠️ akshare 加载失败: {e2}，尝试加载 yfinance 兜底")
        try:
            import yfinance as yf
            logger.info("✅ [优先级4] yfinance 加载成功（Yahoo Finance 兜底）")
        except ImportError as e3:
            logger.error(f"❌ 所有数据源均加载失败: {e3}")
            raise Exception("请检查 requirements.txt 依赖安装")

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

# 核心配置
STOCK_MAP = {
    "000968": "蓝焰控股",
    "600028": "中国石化",
    "600968": "海油发展"
}
CAPITAL = 10000
SINGLE_MAX = 3000
HIST_DAYS = 90  # 历史数据天数

# ======================== 通用指标计算（兼容所有数据源） ========================
def calc_indicators(df):
    """计算核心技术指标（RSI/MACD/KDJ/均线）"""
    df = df.copy().sort_index()
    
    # 统一列名（适配不同数据源的字段名）
    if "收盘价" in df.columns:
        close = df["收盘价"].astype(float)
        high = df["最高价"].astype(float)
        low = df["最低价"].astype(float)
    elif "Close" in df.columns:
        close = df["Close"].astype(float)
        high = df["High"].astype(float)
        low = df["Low"].astype(float)
    else:
        close = df["收盘"].astype(float)
        high = df["最高"].astype(float)
        low = df["最低"].astype(float)

    # 均线（5日/20日）
    ma5 = close.rolling(window=5, min_periods=1).mean()
    ma20 = close.rolling(window=20, min_periods=1).mean()

    # RSI (14日)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window=14, min_periods=1).mean()
    loss = (-delta).clip(lower=0).rolling(window=14, min_periods=1).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()

    # KDJ (9日)
    low9 = low.rolling(window=9, min_periods=1).min()
    high9 = high.rolling(window=9, min_periods=1).max()
    tr = high9 - low9
    tr = tr.replace(0, 1)  # 避免除零
    rsv = (close - low9) / tr * 100
    k = rsv.ewm(span=3, adjust=False).mean()
    d = k.ewm(span=3, adjust=False).mean()
    j = 3 * k - 2 * d

    # 取最新值
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
        # MACD金叉判断（线上穿信号线）
        "macd_gold": bool(macd_line.iloc[last] > signal_line.iloc[last] and macd_line.iloc[last-1] <= signal_line.iloc[last-1]),
        # 趋势判断（站稳20日均线）
        "trend_up": bool(close.iloc[last] > ma20.iloc[last])
    }

# ======================== 多数据源获取历史K线 ========================
def get_stock_history(code):
    """按优先级获取股票历史数据"""
    start_date = (datetime.now() - timedelta(days=HIST_DAYS)).strftime("%Y-%m-%d")
    end_date = datetime.now().strftime("%Y-%m-%d")

    # 1. 优先 efinance
    if ef:
        try:
            df = ef.stock.get_quote_history(code, beg=start_date, end=end_date)
            if len(df) >= 30:
                return calc_indicators(df)
            logger.warning(f"{code} efinance 数据不足（{len(df)}条），尝试下一个数据源")
        except Exception as e:
            logger.warning(f"{code} efinance 获取失败: {str(e)[:50]}，尝试下一个数据源")

    # 2. 其次 akshare
    if ak:
        try:
            df = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                adjust="qfq"
            )
            if len(df) >= 30:
                return calc_indicators(df)
            logger.warning(f"{code} akshare 数据不足（{len(df)}条），尝试下一个数据源")
        except Exception as e:
            logger.warning(f"{code} akshare 获取失败: {str(e)[:50]}，尝试下一个数据源")

    # 3. 最后 yfinance 兜底
    if yf:
        try:
            # A股代码转换（00开头=深交所.SZ，60开头=上交所.SS）
            suffix = ".SZ" if code.startswith("00") else ".SS"
            symbol = code + suffix
            tk = yf.Ticker(symbol)
            df = tk.history(period=f"{HIST_DAYS}d", timeout=20)
            if len(df) >= 30:
                return calc_indicators(df)
            logger.warning(f"{code} yfinance 数据不足（{len(df)}条）")
        except Exception as e:
            logger.warning(f"{code} yfinance 获取失败: {str(e)[:50]}")

    return None

# ======================== 严格量化买入策略 ========================
def get_buy_signal(indicators):
    """
    严格买入策略（满足≥3条触发买入）：
    1. RSI < 40（超跌）
    2. MACD金叉
    3. KDJ-J < 40（低位）
    4. 站稳20日均线（趋势向上）
    """
    # 策略条件
    conds = {
        "RSI超跌": indicators["rsi"] < 40,
        "MACD金叉": indicators["macd_gold"],
        "KDJ低位": indicators["j"] < 40,
        "趋势向上": indicators["trend_up"]
    }
    
    # 计算得分和理由
    score = sum(conds.values())
    reason = " + ".join([k for k, v in conds.items() if v]) or "无满足条件"
    buy_signal = score >= 3

    return {
        "buy": buy_signal,
        "score": score,
        "reason": reason,
        "signal_text": "🔥 买入信号" if buy_signal else "⚠️ 观望"
    }

# ======================== 条件单计算 ========================
def calc_trade_order(price):
    """计算买入价/股数/止盈止损"""
    buy_price = round(price * 0.97, 2)  # 买入价=现价×0.97
    volume = int(SINGLE_MAX / buy_price // 100 * 100)  # 整百股
    volume = max(volume, 100)  # 最低100股
    
    return {
        "buy_price": buy_price,
        "volume": volume,
        "profit10": round(buy_price * 1.10, 2),  # 止盈10%
        "profit15": round(buy_price * 1.15, 2),  # 止盈15%
        "stop_loss": round(price * 0.94, 2)      # 止损价
    }

# ======================== 飞书推送 ========================
def send_feishu_message(content):
    """发送消息到飞书"""
    if not FEISHU_WEBHOOK:
        logger.error("❌ 飞书 Webhook 未配置，推送失败")
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
    logger.info("🚀 开始执行A股量化分析（防漏跑终极版）")
    analysis_result = []

    # 遍历分析每只股票
    for code, name in STOCK_MAP.items():
        logger.info(f"📈 分析股票：{code} {name}")
        # 获取指标
        indicators = get_stock_history(code)
        if not indicators:
            logger.warning(f"❌ {code} {name} 所有数据源均获取失败，跳过")
            continue
        
        # 获取买入信号
        signal = get_buy_signal(indicators)
        # 计算条件单
        order = calc_trade_order(indicators["price"])
        
        # 整合结果
        analysis_result.append({
            "code": code,
            "name": name,
            **indicators,
            **signal,
            **order
        })
        
        # 间隔防限流
        time.sleep(1.0)

    # 无有效数据处理
    if not analysis_result:
        send_feishu_message(f"⚠️ 【{datetime.now().strftime('%Y-%m-%d %H:%M')}】今日所有股票数据获取失败，无分析报告")
        logger.error("❌ 无有效分析数据，程序退出")
        return

    # 生成报告
    report_header = f"""🚀 A股量化分析报告（{datetime.now().strftime('%Y-%m-%d %H:%M')}）
=================================================="""
    
    report_body = ""
    for stock in analysis_result:
        report_body += f"""
【{stock['code']} {stock['name']}】 {stock['signal_text']}
💵 现价：{stock['price']} 元 | 📊 策略得分：{stock['score']}/4
✅ 满足条件：{stock['reason']}

📈 核心技术指标：
RSI：{stock['rsi']}   MACD金叉：{'是' if stock['macd_gold'] else '否'}
KDJ：{stock['k']}/{stock['d']}/{stock['j']}
MA5：{stock['ma5']}  MA20：{stock['ma20']}  趋势向上：{'是' if stock['trend_up'] else '否'}

📋 同花顺条件单：
买入 ≤ {stock['buy_price']} 元，{stock['volume']} 股
止盈10%：{stock['profit10']} 元 | 止盈15%：{stock['profit15']} 元
止损：{stock['stop_loss']} 元

--------------------------------------------------"""

    report_footer = "\n⚠️ 本报告仅为量化学习参考，不构成任何投资建议"
    full_report = report_header + report_body + report_footer

    # 推送报告
    send_feishu_message(full_report)
    logger.info("🎉 量化分析执行完成，报告已推送")

if __name__ == "__main__":
    main()
