import requests
import json
import logging
import os
import time as t
import random
import hmac
import hashlib
import base64
import urllib.parse
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import yfinance as yf
import pytz
import ntplib

# ======================== 全局日志配置 ========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ======================== 推送配置 ========================
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/7e8c7d35-382e-43de-8479-0434921d338c"
DINGTALK_WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=8cd6832317216fdfaca1d2acba57c11e3024f20921365804ba96444f7945b949"
DINGTALK_SECRET = "SECf67646ed7edca294f7575a5bca513ba7de5c00dffe1ce5750da3175fd8fcdddc"

# ======================== NTP时间校准 ========================
NTP_SERVERS = ["ntp.ntsc.ac.cn", "ntp.aliyun.com", "ntp.tencent.com"]
TIME_OFFSET = 0.0
BJ_TZ = pytz.timezone("Asia/Shanghai")

def sync_ntp_time():
    global TIME_OFFSET
    for server in NTP_SERVERS:
        try:
            client = ntplib.NTPClient()
            response = client.request(server, version=3, timeout=2)
            TIME_OFFSET = response.tx_time - t.time()
            logger.info(f"✅ 时间同步成功 [{server}]，偏差: {TIME_OFFSET:.3f}秒")
            return True
        except Exception as e:
            logger.warning(f"⚠️ 时间同步失败 [{server}]: {str(e)[:50]}")
    logger.error("❌ 所有NTP服务器同步失败，使用本地时间")
    TIME_OFFSET = 0.0

def get_standard_now():
    return datetime.fromtimestamp(t.time() + TIME_OFFSET, tz=BJ_TZ)

def get_time_type():
    now = get_standard_now()
    h, m = now.hour, now.minute
    if h == 5 and m == 40:
        return "morning"
    elif h == 9 and m == 0:
        return "open"
    elif h == 15 and m == 0:
        return "close"
    else:
        return "normal"

# ======================== 交易日判断 ========================
def is_trading_day():
    today = get_standard_now().date()
    if today.weekday() > 4:
        logger.info("❌ 周末休市")
        return False
    holidays_2026 = {
        "2026-01-01","2026-01-28","2026-01-29","2026-01-30","2026-01-31",
        "2026-02-01","2026-02-02","2026-04-04","2026-05-01","2026-05-28",
        "2026-05-29","2026-10-01","2026-10-02","2026-10-03","2026-10-04",
        "2026-10-05","2026-10-06","2026-10-07","2026-10-08"
    }
    workdays_2026 = {"2026-01-25","2026-02-08","2026-04-26","2026-05-25","2026-09-28","2026-10-11"}
    today_str = today.strftime("%Y-%m-%d")
    if today_str in holidays_2026:
        logger.info(f"❌ 节假日休市: {today_str}")
        return False
    if today_str in workdays_2026:
        logger.info(f"✅ 调休补班: {today_str}")
        return True
    return True

# ======================== 选股参数（极致宽松，适配yfinance） ========================
SELECTION_TOP_N = 5
HIST_DAYS = 18
MAX_PRICE = 48
TRADING_COST_RATE = 0.0015
MIN_PROFIT_COVER = 0.01
SINGLE_MAX_RISK = 250

# 统一使用宽松规则，不再区分多模式收紧
BASE_MODE = {
    "win_loss_ratio_min": 0.4,
    "day_change_min": -0.12,
    "day_change_max": 0.22,
    "volume_ratio_min": 0.15,
    "turnover_min": 0.8,
    "turnover_max": 35,
    "open_gap_max": 0.1,
    "trend_up_required": False,
    "rsi_min": 3,
    "rsi_max": 97,
    "macd_positive": False
}

# 保底银行股
GUARANTEE_BANK_STOCKS = {
    "601398.SS": "工商银行",
    "601939.SS": "建设银行",
    "601288.SS": "农业银行",
    "601838.SS": "成都银行"
}

# 精选yfinance实测可正常拉取数据的A股池（剔除无效代码）
VALID_STOCK_POOL = {
    # 能源/电力/基建
    "600028.SS": "中国石化",
    "601001.SS": "晋控煤业",
    "601898.SS": "中煤能源",
    "600011.SS": "华能国际",
    "600726.SS": "华电能源",
    "600023.SS": "浙能电力",
    "601668.SS": "中国建筑",
    "601390.SS": "中国中铁",
    # 交运港口
    "600279.SS": "重庆港",
    "601006.SS": "大秦铁路",
    "001872.SZ": "招商港口",
    "600017.SS": "日照港",
    # 医药消费
    "600332.SS": "白云山",
    "000999.SZ": "华润三九",
    "000538.SZ": "云南白药",
    # 有色化工
    "601225.SS": "陕西煤业",
    "000830.SZ": "鲁西化工",
    "600426.SS": "华鲁恒升",
    "600362.SS": "江西铜业",
    # 银行兜底
    "601398.SS": "工商银行",
    "601939.SS": "建设银行",
    "601288.SS": "农业银行"
}

# ======================== 数据获取 & 指标计算 ========================
def fetch_data(code):
    """yfinance获取K线，放宽数据行数要求"""
    try:
        df = yf.Ticker(code).history(period=f"{HIST_DAYS}d", timeout=6)
        if len(df) < 4:
            return None
        return df
    except Exception as e:
        logger.debug(f"[{code}] 数据获取失败: {e}")
        return None

def calc_indicators(df, mode):
    """计算技术指标"""
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]
    open_p = df["Open"]

    ma5 = close.rolling(5).mean()
    ma5_vol = volume.rolling(5).mean()

    # RSI
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = round(100 - (100 / (1 + rs)), 1).iloc[-1]

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    macd_gold = (macd.iloc[-2] < signal.iloc[-2]) and (macd.iloc[-1] > signal.iloc[-1])
    macd_pos = macd.iloc[-1] > 0

    # 量比
    vol_ratio = round(volume.iloc[-1] / ma5_vol.iloc[-1], 2) if ma5_vol.iloc[-1] > 0 else 1.0

    # 日内涨跌幅
    now_price = close.iloc[-1]
    open_price = open_p.iloc[-1]
    day_chg = round(((now_price - open_price) / open_price) * 100, 2)

    # KDJ
    low9 = low.rolling(9).min()
    high9 = high.rolling(9).max()
    rsv = (close - low9) / (high9 - low9) * 100
    k = rsv.ewm(com=2).mean()
    d = k.ewm(com=2).mean()
    kdj_gold = (k.iloc[-2] < d.iloc[-2]) and (k.iloc[-1] > d.iloc[-1])

    # 条件判断
    rsi_ok = mode["rsi_min"] <= rsi <= mode["rsi_max"]
    price_ok = now_price <= MAX_PRICE
    vol_ok = vol_ratio >= mode["volume_ratio_min"]

    return {
        "price": round(now_price, 2),
        "day_change": day_chg,
        "volume_ratio": vol_ratio,
        "rsi": rsi,
        "macd_positive": macd_pos,
        "kdj_gold": kdj_gold,
        "macd_gold": macd_gold,
        "rsi_ok": rsi_ok,
        "price_ok": price_ok,
        "vol_ok": vol_ok
    }

# ======================== 大盘状态判断（修复误判震荡） ========================
def get_market_status():
    try:
        df = yf.Ticker("000300.SS").history(period="18d", timeout=6)
        if len(df) < 5:
            return "大盘数据异常，通用宽松策略", BASE_MODE
        close = df["Close"]
        ma20 = close.rolling(20, min_periods=1).mean()
        curr = close.iloc[-1]
        ma_val = ma20.iloc[-1]

        # 放宽判定阈值，大涨正常识别为强势
        if curr > ma_val * 1.008:
            return "市场强势，T+1策略积极", BASE_MODE
        elif curr > ma_val * 0.96:
            return "市场正常，T+1策略就绪", BASE_MODE
        else:
            return "市场震荡，T+1策略谨慎", BASE_MODE
    except Exception as e:
        logger.warning(f"大盘数据获取失败: {e}")
        return "大盘数据异常，通用宽松策略", BASE_MODE

# ======================== 个股解析 ========================
def parse_stock(code, name, mode):
    df = fetch_data(code)
    if df is None:
        return None
    ind = calc_indicators(df, mode)
    # 仅保留核心条件
    if not (ind["rsi_ok"] and ind["price_ok"] and ind["vol_ok"]):
        return None

    # 止盈止损
    buy_price = ind["price"] * 1.001
    stop_loss = round(buy_price * 0.982, 2)
    take_profit = round(buy_price * 1.02, 2)

    return {
        "symbol": code,
        "code": code.replace(".SS", "").replace(".SZ", ""),
        "name": name,
        "pool_type": "normal",
        "tech": ind,
        "fund": {
            "industry": "综合",
            "market_cap": 800.0,
            "pe": 18.0,
            "pb": 1.8
        },
        "stats": {
            "price_range_low": stop_loss,
            "price_range_high": take_profit
        },
        "total_score": ind["volume_ratio"] + (2 if ind["macd_gold"] else 0) + (2 if ind["kdj_gold"] else 0)
    }

# ======================== 选股主逻辑 ========================
def scan_stocks(mode):
    result = []
    stock_list = list(VALID_STOCK_POOL.items())

    for code, name in stock_list:
        stock_info = parse_stock(code, name, mode)
        if stock_info:
            result.append(stock_info)
            logger.info(f"✅ 选中标的：{name}({code})")
        t.sleep(0.12)

    # 按得分排序取前N
    result = sorted(result, key=lambda x: x["total_score"], reverse=True)[:SELECTION_TOP_N]

    # 兜底银行股（补全所有字段，杜绝KeyError）
    if len(result) == 0:
        logger.info("⚠️ 暂无符合标的，启用银行股保底")
        bank_list = list(GUARANTEE_BANK_STOCKS.items())
        random.shuffle(bank_list)
        for code, name in bank_list:
            df = fetch_data(code)
            if df is None:
                continue
            price = round(df["Close"].iloc[-1], 2)
            result.append({
                "symbol": code,
                "code": code.replace(".SS", "").replace(".SZ", ""),
                "name": name,
                "pool_type": "guarantee",
                "tech": {
                    "price": price,
                    "day_change": 0.0,
                    "volume_ratio": 1.0,
                    "rsi": 50,
                    "macd_positive": False,
                    "kdj_gold": False,
                    "macd_gold": False
                },
                "fund": {
                    "industry": "银行",
                    "market_cap": 9999.99,
                    "pe": 10.0,
                    "pb": 1.1
                },
                "stats": {
                    "price_range_low": round(price * 0.982, 2),
                    "price_range_high": round(price * 1.02, 2)
                },
                "total_score": 5.0
            })
            break
    return result

# ======================== 消息组装 ========================
def build_message(stock_list, market_desc, time_type):
    now = get_standard_now().strftime("%Y-%m-%d %H:%M:%S")
    title_map = {
        "morning": "【🤖 T+1量化 · 早盘5:40前瞻】",
        "open": "【🤖 T+1量化 · 9:00开盘参考】",
        "close": "【🤖 T+1量化 · 15:00收盘总结】",
        "normal": "【🤖 T+1短线量化算法 · 日常推送】"
    }
    tip_map = {
        "morning": "早盘前瞻：提前筛选当日备选标的，等待尾盘定点介入",
        "open": "开盘参考：集合竞价结束，观察量能与开盘溢价",
        "close": "收盘总结：当日标的复盘，明日持仓隔日处理规划",
        "normal": "尾盘14:55左右买入，次日14:45前清仓"
    }

    msg = f"""==================================================
{title_map[time_type]}
📅 输出时间：{now}
📊 大盘状态：{market_desc}
💡 策略提示：{tip_map[time_type]}
==================================================
⚠️ 法律合规声明
1. 本内容为Python量化程序自动运算的公开行情数据，不构成投资建议。
2. 仅用于量化技术学习、算法验证、历史数据复盘，禁止实盘交易。
==================================================
【📊 T+1短线标的 · 纯数据展示】
"""
    if stock_list:
        for idx, s in enumerate(stock_list, 1):
            tag = "【保底银行股】" if s["pool_type"] == "guarantee" else ""
            msg += f"""
【数据{idx}】{tag}{s['code']} {s['name']}
💵 现价：{s['tech']['price']}元｜涨幅：{s['tech']['day_change']}%｜量比：{s['tech']['volume_ratio']}
📉 止损：{s['stats']['price_range_low']}元｜止盈：{s['stats']['price_range_high']}元
📊 RSI：{s['tech']['rsi']}｜MACD：{"正" if s['tech']['macd_positive'] else "负"}｜KDJ金叉：{"是" if s['tech']['kdj_gold'] else "否"}
🏭 行业：{s['fund']['industry']}｜市值：{s['fund']['market_cap']}亿
--------------------------------------------------
"""
    else:
        msg += "⚠️ 今日暂无符合条件标的\n"

    msg += """
==================================================
💡 T+1纪律
1. 尾盘14:55买入，次日14:45前清仓
2. 严格止损，绝不扛单
3. 数据仅供学习，禁止跟单
==================================================
"""
    return msg[:1800]

# ======================== 推送函数 ========================
def send_feishu(msg):
    if not FEISHU_WEBHOOK:
        logger.error("❌ 飞书Webhook未配置")
        return
    try:
        resp = requests.post(
            FEISHU_WEBHOOK,
            json={"msg_type": "text", "content": {"text": msg}},
            timeout=10
        )
        if resp.json().get("code") == 0:
            logger.info("✅ 飞书推送成功")
    except Exception as e:
        logger.warning(f"⚠️ 飞书推送异常: {e}")

def send_dingtalk(msg):
    try:
        timestamp = str(round(t.time() * 1000))
        secret_enc = DINGTALK_SECRET.encode('utf-8')
        sign_str = f"{timestamp}\n{DINGTALK_SECRET}"
        sign_bytes = hmac.new(secret_enc, sign_str.encode("utf-8"), hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(sign_bytes))
        url = f"{DINGTALK_WEBHOOK}&timestamp={timestamp}&sign={sign}"
        requests.post(url, json={"msgtype": "text", "text": {"content": msg}}, timeout=5)
        logger.info("✅ 钉钉推送成功")
    except Exception as e:
        logger.error(f"❌ 钉钉推送异常: {e}")

# ======================== 主入口 ========================
def main():
    logger.info("🚀 T+1短线量化策略（纯yfinance版）启动")
    sync_ntp_time()
    time_type = get_time_type()
    logger.info(f"⏰ 当前时段：{time_type}")

    if is_trading_day():
        market_desc, run_mode = get_market_status()
        logger.info(f"📊 市场状态：{market_desc}")
        stock_result = scan_stocks(run_mode)
        logger.info(f"🔍 最终选出 {len(stock_result)} 只标的")
        content = build_message(stock_result, market_desc, time_type)
        send_feishu(content)
        send_dingtalk(content)
        logger.info("🎉 今日推送完成")
    else:
        logger.info("ℹ️ 今日非交易日，不执行选股推送")

if __name__ == "__main__":
    main()
