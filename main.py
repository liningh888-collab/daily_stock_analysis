import requests
import json
import logging
import os
from datetime import datetime, timedelta
import time
import akshare as ak
import pandas as pd
import numpy as np

# ======================== 配置初始化（增强容错） ========================
config_content = os.environ.get("CONFIG_CONTENT")
if not config_content:
    raise Exception("请在 GitHub Secrets 配置 CONFIG_CONTENT")

CONFIG = json.loads(config_content)

# 日志优化：输出更详细的错误信息
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(funcName)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ======================== 核心配置（适配同花顺） ========================
MANUAL_STOCKS = CONFIG.get("stock", {}).get("symbols", [])
FEISHU_WEBHOOK = CONFIG.get("channels", {}).get("feishu", {}).get("webhook", {}).get("url", "")

# 仓位规则（按A股100股整数倍优化）
TOTAL_CAPITAL = 10000
MAX_SINGLE = 3000  # 单只最大仓位
MAX_TOTAL = 8000   # 总仓位上限

# ======================== 指标工具库（增强稳定性） ========================
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
    rsv = (close - low_list) / (high_list - low_list).replace(0, np.nan) * 100
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

# ======================== 股票分析（核心优化：重试+同花顺适配） ========================
def analyze_stock_manual(code):
    max_retry = 3  # 增加重试机制，解决网络波动问题
    for retry in range(max_retry):
        try:
            # 重试间隔递增，避免被接口限流
            time.sleep(0.5 * (retry + 1))
            
            # 获取实时数据（增加异常捕获）
            df_spot = ak.stock_zh_a_spot_em()
            if df_spot.empty:
                raise Exception("实时行情数据为空")
            row = df_spot[df_spot["代码"] == code].iloc[0]
            current = round(float(row["最新价"]), 2)
            change = round(float(row["涨跌幅"]), 2)
            name = row["名称"]

            # 获取历史数据（扩大时间范围，增强稳定性）
            hist = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=(datetime.now()-timedelta(days=200)).strftime("%Y%m%d"),
                end_date=datetime.now().strftime("%Y%m%d"),
                adjust="qfq"
            )
            if len(hist) < 60:
                logger.warning(f"{code} 历史数据不足（仅{len(hist)}条），跳过")
                return None

            close = hist["收盘"].astype(float)
            high = hist["最高"].astype(float)
            low = hist["最低"].astype(float)
            vol = hist["成交量"].astype(float)

            # 均线计算
            ma5 = round(close.rolling(5).mean().iloc[-1], 2)
            ma10 = round(close.rolling(10).mean().iloc[-1], 2)
            ma20 = round(close.rolling(20).mean().iloc[-1], 2)
            ma60 = round(close.rolling(60).mean().iloc[-1], 2)

            # 量价分析
            vol_ma5 = vol.rolling(5).mean().iloc[-1]
            vol_status = "量价偏弱"
            if vol.iloc[-1] > vol_ma5 * 1.2:
                vol_status = "量价健康"
            elif vol.iloc[-1] < vol_ma5 * 0.8:
                vol_status = "量能萎缩"

            # 技术指标计算
            rsi = round(calc_rsi(close).iloc[-1], 1)
            k, d, j = calc_kdj(high, low, close)
            macd, signal = calc_macd(close)
            atr = round(calc_atr(high, low, close).iloc[-1], 2)
            top_div, bot_div = check_macd_divergence(close, macd)

            # 趋势判断
            trend = "震荡"
            if ma5 > ma10 > ma20 and current > ma20:
                trend = "上升"
            elif ma5 < ma10 < ma20 and current < ma20:
                trend = "下跌"

            # 评分与评级
            score = 0
            if rsi < 35: score += 1
            if macd.iloc[-1] > signal.iloc[-1]: score += 1
            if bot_div: score += 2
            if current > ma20: score += 1

            grade = "观望"
            if score >= 4:
                grade = "🔥 强烈买入"
            elif score >= 2:
                grade = "✅ 买入"
            elif score <= -3:
                grade = "❌ 卖出"
            elif score <= -1:
                grade = "⚠️ 减仓"

            # 止损价优化
            stop = round(current - 1.6 * atr, 2)
            if stop < current * 0.93:
                stop = round(current * 0.94, 2)

            # 同花顺条件单核心：计算100股整数倍手数
            buy_price = round(current * 0.97, 2)
            volume = int(MAX_SINGLE / buy_price // 100 * 100)  # 100股整数倍
            if volume < 100:
                volume = 100  # 最低1手

            return {
                "code": code, "name": name, "price": current, "change": change,
                "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
                "vol_status": vol_status, "atr": atr, "rsi": rsi,
                "k": round(k.iloc[-1],1), "d": round(d.iloc[-1],1), "j": round(j.iloc[-1],1),
                "macd": round(macd.iloc[-1],2), "signal": round(signal.iloc[-1],2),
                "top_div": top_div, "bot_div": bot_div,
                "trend": trend, "grade": grade, "stop": stop,
                "pool": "自选关注", "rps": "-",
                "buy_price": buy_price,  # 同花顺买入条件单价
                "volume": volume,        # 同花顺条件单手数
                "profit10": round(buy_price * 1.10, 2),  # 10%止盈价
                "profit15": round(buy_price * 1.15, 2),  # 15%止盈价
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        except Exception as e:
            logger.error(f"{code} 第{retry+1}次分析失败: {str(e)[:100]}")
            if retry == max_retry - 1:
                return None
            continue
    return None

# ======================== 操作建议（专属同花顺条件单模板） ========================
def get_operation(s):
    return f"""
📋 同花顺条件单填写模板
├─ 股票代码：{s['code']} {s['name']}
├─ 买入条件单：价格 ≤ {s['buy_price']} 元，数量 {s['volume']} 股
├─ 止盈条件单：价格 ≥ {s['profit10']} 元（10%）/ {s['profit15']} 元（15%）
├─ 止损条件单：价格 ≤ {s['stop']} 元（严格执行）
└─ 委托方式：最新价成交，云端托管，当日有效
"""

# ======================== 飞书推送（增强稳定性+格式优化） ========================
def send_feishu(text):
    if not FEISHU_WEBHOOK:
        logger.warning("飞书 Webhook 未配置")
        return False

    # 控制消息长度，避免飞书拒收
    max_bytes = 19000
    text_bytes = text.encode('utf-8')
    if len(text_bytes) > max_bytes:
        text = text_bytes[:max_bytes].decode('utf-8', 'ignore') + "\n\n...（内容过长已截断）"

    # 增加推送重试
    max_retries = 2
    for retry in range(max_retries):
        try:
            res = requests.post(
                FEISHU_WEBHOOK,
                json={"msg_type": "text", "content": {"text": text}},
                timeout=25,
                headers={"Content-Type": "application/json"}
            )
            res.raise_for_status()  # 触发HTTP错误
            logger.info(f"飞书推送成功，状态码：{res.status_code}")
            return True
        except Exception as e:
            logger.error(f"第{retry+1}次推送失败：{str(e)[:100]}")
            time.sleep(1)
    logger.error("飞书推送最终失败")
    return False

# ======================== 主程序（优化逻辑+用户体验） ========================
def main():
    selected = []
    logger.info(f"开始分析自选股：{MANUAL_STOCKS} | 分析时间：{datetime.now()}")

    # 遍历自选股分析
    for idx, code in enumerate(MANUAL_STOCKS):
        logger.info(f"正在分析第{idx+1}/{len(MANUAL_STOCKS)}只股票：{code}")
        stock = analyze_stock_manual(code)
        if stock:
            selected.append(stock)
            logger.info(f"{code} 分析完成：{stock['name']} | 评级：{stock['grade']}")
        else:
            logger.warning(f"{code} 分析失败，跳过")
        time.sleep(0.3)  # 控制请求频率

    # 无数据兜底
    if not selected:
        send_feishu("⚠️ 今日自选股数据获取失败\n原因可能：网络波动/股票代码错误/非交易时间\n建议检查后重新运行")
        return

    # 拼接同花顺专属报告
    msg = "🚀 OpenClaw 同花顺条件单专属分析报告\n" + "="*60 + "\n"
    msg += f"📅 分析时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    msg += f"💰 本金规划：{TOTAL_CAPITAL} 元 | 单只上限：{MAX_SINGLE} 元\n"
    msg += "="*60 + "\n\n"

    for s in selected:
        msg += f"""【{s['code']} {s['name']}】
💵 现价：{s['price']} 元  |  涨跌幅：{s['change']}%
📈 均线：MA5:{s['ma5']} MA10:{s['ma10']} MA20:{s['ma20']}
📊 量价：{s['vol_status']} | ATR：{s['atr']}
🧪 RSI：{s['rsi']}  |  KDJ：{s['k']}/{s['d']}/{s['j']}
📐 MACD：{s['macd']} / {s['signal']}
📌 趋势：{s['trend']} | 背离：顶{'✅' if s['top_div'] else '❌'} 底{'✅' if s['bot_div'] else '❌'}
🔥 综合评级：{s['grade']}
🛡️ 动态止损：{s['stop']} 元
"""
        msg += get_operation(s)
        msg += "\n" + "-"*60 + "\n\n"

    # 风险提示
    msg += """
⚠️ 重要提示
1. 条件单设置后请在同花顺「我的条件单」确认状态为「监控中」
2. 云端托管需确保同花顺账号正常登录，关机不影响触发
3. 本分析仅供学习，不构成任何投资建议，交易有风险
"""

    # 推送报告
    send_feishu(msg)
    logger.info("分析报告已推送至飞书，程序执行完成")

if __name__ == "__main__":
    main()
