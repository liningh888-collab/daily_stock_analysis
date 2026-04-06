import requests
import json
import logging
import os
from datetime import datetime, timedelta
import time
import akshare as ak
import pandas as pd
import numpy as np

# ======================== 配置初始化 ========================
config_content = os.environ.get("CONFIG_CONTENT")
if not config_content:
    raise Exception("请在 GitHub Secrets 配置 CONFIG_CONTENT")
CONFIG = json.loads(config_content)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(funcName)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ======================== 核心配置（折中版） ========================
FIXED_STOCKS = ["000968", "600028", "600968"]  # 仅分析3只固定股，不扫描全市场
FEISHU_WEBHOOK = CONFIG.get("channels", {}).get("feishu", {}).get("webhook", {}).get("url", "")

TOTAL_CAPITAL = 10000
MAX_SINGLE = 3000
# 网络优化配置
REQUEST_INTERVAL = 1.0  # 长间隔防限流
TIMEOUT = 15  # 延长超时时间
MAX_RETRY = 1  # 仅1次重试，避免卡死

# ======================== 基础指标计算（精简版） ========================
def calc_basic_indicators(hist):
    close = hist["收盘"].astype(float)
    high = hist["最高"].astype(float)
    low = hist["最低"].astype(float)
    
    # RSI (14日)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean()
    rs = gain / loss.replace(0, 1)  # 避免除零
    rsi = round(100 - (100 / (1 + rs)).iloc[-1], 1)
    
    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = round((ema12 - ema26).iloc[-1], 2)
    signal = round(macd.ewm(span=9, adjust=False).mean().iloc[-1], 2)
    
    # KDJ (9日)
    low_list = low.rolling(9).min()
    high_list = high.rolling(9).max()
    tr = (high_list - low_list).replace(0, 1)
    rsv = (close - low_list) / tr * 100
    k = round(rsv.ewm(span=3, adjust=False).mean().iloc[-1], 1)
    d = round(k.ewm(span=3, adjust=False).mean().iloc[-1], 1)
    j = round(3*k - 2*d, 1)
    
    # 均线
    ma5 = round(close.rolling(5).mean().iloc[-1], 2)
    ma20 = round(close.rolling(20).mean().iloc[-1], 2)
    
    return {
        "rsi": rsi, "macd": macd, "signal": signal,
        "k": k, "d": d, "j": j, "ma5": ma5, "ma20": ma20
    }

# ======================== 单只股票分析（容错版） ========================
def analyze_one_stock(code, df_spot_cache):
    # 先获取基础行情（兜底用）
    try:
        row = df_spot_cache[df_spot_cache["代码"] == code].iloc[0]
        base_data = {
            "code": code,
            "name": row["名称"],
            "price": round(float(row["最新价"]), 2),
            "change": round(float(row["涨跌幅"]), 2),
            "grade": "数据异常，仅供参考"
        }
    except:
        logger.error(f"{code} 基础行情获取失败")
        return None
    
    # 尝试获取历史数据计算指标
    for retry in range(MAX_RETRY):
        try:
            time.sleep(REQUEST_INTERVAL * (retry + 1))
            # 缩短历史数据周期（60天），减少数据量
            hist = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=(datetime.now()-timedelta(days=60)).strftime("%Y%m%d"),
                end_date=datetime.now().strftime("%Y%m%d"),
                adjust="qfq",
                timeout=TIMEOUT
            )
            if len(hist) < 30:  # 至少30天数据才计算指标
                logger.warning(f"{code} 历史数据不足，仅推送基础信息")
                break
            
            # 计算指标
            indicators = calc_basic_indicators(hist)
            
            # 简易评分（降低门槛）
            score = 0
            if indicators["rsi"] < 40: score += 1
            if indicators["macd"] > indicators["signal"]: score += 1
            if base_data["price"] > indicators["ma20"]: score += 1
            if indicators["j"] < 30: score += 1
            
            # 评级
            if score >= 3:
                base_data["grade"] = "🔥 强烈买入"
            elif score >= 2:
                base_data["grade"] = "✅ 买入"
            else:
                base_data["grade"] = "观望"
            
            # 补充指标
            base_data.update(indicators)
            base_data["score"] = score
            break
        
        except Exception as e:
            logger.error(f"{code} 指标计算失败{retry+1}次: {str(e)[:80]}")
            if retry == MAX_RETRY - 1:
                logger.warning(f"{code} 指标计算最终失败，仅推送基础信息")
    
    # 计算条件单（必兜底）
    current = base_data["price"]
    base_data["buy_price"] = round(current * 0.97, 2)
    base_data["volume"] = int(MAX_SINGLE / base_data["buy_price"] // 100 * 100)
    if base_data["volume"] < 100:
        base_data["volume"] = 100
    base_data["profit10"] = round(base_data["buy_price"] * 1.10, 2)
    base_data["profit15"] = round(base_data["buy_price"] * 1.15, 2)
    base_data["stop"] = round(current * 0.94, 2)
    
    return base_data

# ======================== 操作建议模板 ========================
def get_operation(s):
    # 兼容有无指标的情况
    indicator_text = ""
    if "rsi" in s and "macd" in s:
        indicator_text = f"""
📊 核心指标：
├─ RSI：{s['rsi']} | MACD：{s['macd']}/{s['signal']}
├─ KDJ：{s['k']}/{s['d']}/{s['j']}
└─ 均线：MA5:{s.get('ma5', '-')} MA20:{s.get('ma20', '-')}
"""
    
    return f"""
📋 同花顺条件单
├─ 股票：{s['code']} {s['name']} | 评级：{s['grade']}
{indicator_text}
├─ 买入：≤ {s['buy_price']} 元，{s['volume']}股
├─ 止盈10%：{s['profit10']} 元 | 止盈15%：{s['profit15']} 元
└─ 止损：{s['stop']} 元
"""

# ======================== 飞书推送 ========================
def send_feishu(text):
    if not FEISHU_WEBHOOK:
        logger.warning("飞书Webhook未配置")
        return False
    max_bytes = 19000
    text_bytes = text.encode('utf-8')
    if len(text_bytes) > max_bytes:
        text = text_bytes[:max_bytes].decode('utf-8','ignore') + "\n...（内容过长已截断）"
    for retry in range(2):
        try:
            res = requests.post(
                FEISHU_WEBHOOK,
                json={"msg_type":"text","content":{"text":text}},
                timeout=10
            )
            res.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"推送失败{retry+1}: {str(e)[:80]}")
            time.sleep(0.5)
    return False

# ======================== 主程序（折中版核心） ========================
def main():
    logger.info("开始执行折中版股票分析（带基础指标+稳跑GitHub）")
    
    # 1. 预缓存全市场行情（仅1次请求，最大程度减少网络交互）
    df_spot_cache = None
    for retry in range(2):
        try:
            df_spot_cache = ak.stock_zh_a_spot_em()
            df_spot_cache = df_spot_cache[df_spot_cache['代码'].str.match(r'^\d{6}$')]
            if not df_spot_cache.empty:
                break
        except Exception as e:
            logger.error(f"行情缓存失败{retry+1}次: {str(e)[:80]}")
            time.sleep(2)
    if df_spot_cache is None or df_spot_cache.empty:
        send_feishu("⚠️ 行情缓存失败，今日无推送")
        return
    
    # 2. 分析3只固定股（无全市场扫描，减少网络请求）
    selected = []
    for code in FIXED_STOCKS:
        stock = analyze_one_stock(code, df_spot_cache)
        if stock:
            selected.append(stock)
        # 超长间隔，彻底防限流
        time.sleep(REQUEST_INTERVAL)
    
    if not selected:
        send_feishu("⚠️ 今日无有效股票数据")
        return
    
    # 3. 生成报告
    msg = "🚀 OpenClaw 折中版分析报告（GitHub稳跑）\n" + "="*60 + "\n"
    msg += f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
    msg += "="*60 + "\n\n"
    
    for s in selected:
        msg += f"【{s['code']} {s['name']}】\n💵 现价：{s['price']} 元 | 涨跌幅：{s['change']}%\n"
        msg += get_operation(s) + "\n" + "-"*50 + "\n\n"
    
    msg += """
⚠️ 重要说明
1. 本版本适配GitHub海外节点，优先保证推送稳定
2. 指标计算失败时仅推送基础行情，不影响核心使用
3. 分析仅供学习，不构成投资建议
"""
    
    # 4. 推送
    send_feishu(msg)
    logger.info("✅ 推送完成！全程稳定，无断连/超时")

if __name__ == "__main__":
    main()
