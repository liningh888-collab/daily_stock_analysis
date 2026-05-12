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
from datetime import datetime
import pandas as pd
import numpy as np
import yfinance as yf
import pytz
import ntplib

# ======================== 全局配置 ========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 飞书 Webhook
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/7e8c7d35-382e-43de-8479-0434921d338c"

# 钉钉配置
DINGTALK_WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=8cd6832317216fdfaca1d2acba57c11e3024f20921365804ba96444f7945b949"
DINGTALK_SECRET = "SECf67646ed7edca294f7575a5bca513ba7de5c00dffe1ce5750da3175fd8fcdddc"

# ======================== NTP网络时间校准 ========================
NTP_SERVERS = [
    "ntp.ntsc.ac.cn",
    "ntp.aliyun.com",
    "ntp.tencent.com"
]
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
            continue
    logger.error("❌ 所有NTP服务器同步失败，将使用本地时间")
    TIME_OFFSET = 0.0
    return False

def get_standard_now():
    standard_timestamp = t.time() + TIME_OFFSET
    return datetime.fromtimestamp(standard_timestamp, tz=BJ_TZ)

# ======================== 【严格9:20】交易时间判断 ========================
def is_target_trading_time():
    now = get_standard_now()
    # 严格北京时间 9:20 左右（±1分钟内都算）
    return now.hour == 9 and 19 <= now.minute <= 21

# ======================== 【大幅放宽】T+1专属核心参数 ========================
SELECTION_TOP_N = 5
HIST_DAYS = 30
CAPITAL = 10000
MAX_PRICE = 45
TRADING_COST_RATE = 0.0015
MIN_PROFIT_COVER = 0.01
SINGLE_MAX_RISK = 250

T1_MODE = {
    "win_loss_ratio_min": 1.0,
    "day_change_min": -0.03,
    "day_change_max": 0.08,
    "volume_ratio_min": 0.8,
    "turnover_min": 2,
    "turnover_max": 25,
    "open_gap_max": 0.04,
    "trend_up_required": False
}

NORMAL_MODE = {
    "win_loss_ratio_min": 1.1,
    "day_change_min": -0.03,
    "day_change_max": 0.07,
    "volume_ratio_min": 0.6,
    "assist_conds_min": 0,
    "trend_up_required": False
}

WEAK_MODE = {
    "win_loss_ratio_min": 0.9,
    "day_change_min": -0.05,
    "day_change_max": 0.06,
    "volume_ratio_min": 0.3,
    "assist_conds_min": 0,
    "trend_up_required": False
}

# ======================== 行业估值规则 ========================
INDUSTRY_PE_RULES = {
    "银行": {"pe_max": 12, "pb_max": 1.2},
    "保险": {"pe_max": 15, "pb_max": 2.0},
    "证券": {"pe_max": 25, "pb_max": 2.5},
    "煤炭": {"pe_max": 35, "pb_max": 3.0},
    "石油天然气": {"pe_max": 70, "pb_max": 4.5},
    "钢铁": {"pe_max": 25, "pb_max": 2.0},
    "有色": {"pe_max": 40, "pb_max": 3.5},
    "化工": {"pe_max": 30, "pb_max": 3.0},
    "医药生物": {"pe_max": 50, "pb_max": 5.0},
    "食品饮料": {"pe_max": 40, "pb_max": 6.0},
    "零售": {"pe_max": 30, "pb_max": 3.0},
    "计算机": {"pe_max": 70, "pb_max": 6.0},
    "电子": {"pe_max": 55, "pb_max": 5.0},
    "国防军工": {"pe_max": 80, "pb_max": 5.0},
    "通信": {"pe_max": 45, "pb_max": 4.0},
    "电力": {"pe_max": 25, "pb_max": 2.5},
    "交通运输": {"pe_max": 20, "pb_max": 2.0},
    "建筑装饰": {"pe_max": 15, "pb_max": 1.5},
    "其他": {"pe_max": 50, "pb_max": 5.0}
}

FUNDAMENTAL_RED_LINE = {
    "market_cap_min": 50,
    "turnover_min": 2,
    "turnover_max": 25
}

# ======================== T+1专属股票池 ========================
T1_POOL = {
    "600028.SS": "中国石化", "600023.SS": "浙能电力", "600726.SS": "华电能源",
    "601016.SS": "节能风电", "600968.SS": "海油发展", "000968.SZ": "蓝焰控股",
    "600795.SS": "国电电力", "600011.SS": "华能国际",
    "600026.SS": "中远海能", "600279.SS": "重庆港", "601006.SS": "大秦铁路",
    "001872.SZ": "招商港口", "600017.SS": "日照港", "600428.SS": "中远海特",
    "600332.SS": "白云山", "000999.SZ": "华润三九", "600566.SS": "济川药业",
    "000538.SZ": "云南白药", "600572.SS": "康恩贝", "000989.SZ": "九芝堂",
    "000997.SZ": "新大陆", "002027.SZ": "分众传媒", "002152.SZ": "广电运通",
    "000100.SZ": "TCL科技", "002056.SZ": "横店东磁", "600570.SS": "恒生电子",
    "601225.SS": "陕西煤业", "601088.SS": "中国神华", "000830.SZ": "鲁西化工",
    "600426.SS": "华鲁恒升", "600362.SS": "江西铜业",
    "601933.SS": "永辉超市", "002024.SZ": "苏宁易购", "600859.SS": "王府井"
}

MY_STOCKS = {
    "600726.SS": "华电能源", "601016.SS": "节能风电", "600023.SS": "浙能电力",
    "600028.SS": "中国石化", "600968.SS": "海油发展", "000968.SZ": "蓝焰控股",
    "002132.SZ": "恒星科技"
}

# ======================== 工具函数 ========================
def is_trading_day():
    today = get_standard_now()
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
    try:
        hs300 = yf.Ticker("000300.SS")
        df = hs300.history(period="60d", timeout=3)
        if len(df) < 10:
            return 0.5, "大盘数据不足，谨慎观察", T1_MODE
        close = df["Close"].astype(float)
        ma20 = close.rolling(20, min_periods=1).mean()
        current = close.iloc[-1]
        return 0.7, "市场正常，T+1策略就绪", T1_MODE
    except Exception as e:
        logger.warning(f"⚠️ 大盘状态获取异常: {e}")
        return 0.5, "大盘状态正常", T1_MODE

def calc_atr(df, period=14):
    high, low, close = df["High"], df["Low"], df["Close"]
    tr = pd.concat([high-low, abs(high-close.shift(1)), abs(low-close.shift(1))], axis=1).max(axis=1)
    return round(tr.rolling(period).mean().iloc[-1], 2)

def calc_technical_indicators(df, mode):
    close, high, low, volume, open_ = df["Close"], df["High"], df["Low"], df["Volume"], df["Open"]
    ma5, ma10, ma20 = close.rolling(5).mean(), close.rolling(10).mean(), close.rolling(20).mean()
    ma5_vol = volume.rolling(5).mean()

    vol_trend = volume.iloc[-1] > volume.iloc[-2]

    delta = close.diff()
    gain, loss = delta.clip(lower=0).rolling(14).mean(), (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = round(100 - (100 / (1 + rs)), 1).iloc[-1]

    ema12, ema26 = close.ewm(span=12, adjust=False).mean(), close.ewm(span=26, adjust=False).mean()
    macd, signal = ema12 - ema26, (ema12 - ema26).ewm(span=9, adjust=False).mean()
    macd_gold = (macd.iloc[-2] < signal.iloc[-2]) and (macd.iloc[-1] > signal.iloc[-1])

    volume_enlarge = volume.iloc[-1] >= ma5_vol.iloc[-1] * mode["volume_ratio_min"]
    volume_ratio = round(volume.iloc[-1] / ma5_vol.iloc[-1], 2) if ma5_vol.iloc[-1] > 0 else 1

    cp, op = close.iloc[-1], open_.iloc[-1]
    dc = (cp - op) / op
    open_gap = (op - close.iloc[-2]) / close.iloc[-2]

    it = dc >= mode["day_change_min"]
    no = dc <= mode["day_change_max"]
    og = open_gap <= mode["open_gap_max"]
    tu = (close.iloc[-1] > ma5.iloc[-1]) if mode["trend_up_required"] else True

    turnover = round(volume.iloc[-1] / df["Volume"].mean() * 100, 2)

    return {
        "price": round(cp,2), "open_price": round(op,2), "day_change": round(dc*100,2),
        "open_gap": round(open_gap*100,2), "turnover": turnover,
        "ma5": round(ma5.iloc[-1],2), "ma10": round(ma10.iloc[-1],2), "ma20": round(ma20.iloc[-1],2),
        "rsi": rsi, "macd_gold": macd_gold, "trend_up": tu,
        "volume_enlarge": volume_enlarge, "volume_ratio": volume_ratio, "vol_trend": vol_trend,
        "atr": calc_atr(df), "is_intraday_strong": it, "is_not_overbought": no, "is_not_high_open": og
    }

def get_fundamental_data(s, n):
    try:
        info = yf.Ticker(s).info
        pe = info.get("trailingPE",999)
        pb = info.get("priceToBook",999)
        mc = round(info.get("marketCap",0)/1e8,2)
        tur = round(info.get("averageVolume10days",0)/info.get("sharesOutstanding",1)*100,2) if info.get("sharesOutstanding") else 1
        industry = info.get("industry","Other")

        is_st = "ST" in info.get("longName", "")
        im = {"Thermal Coal":"煤炭","Oil & Gas Integrated":"石油天然气","Electric Utilities":"电力","Railroads":"交通运输"}
        ik = im.get(industry,"其他")
        ir = INDUSTRY_PE_RULES.get(ik, INDUSTRY_PE_RULES["其他"])

        ok = (pe<ir["pe_max"] and pb<ir["pb_max"] and mc>FUNDAMENTAL_RED_LINE["market_cap_min"]
              and FUNDAMENTAL_RED_LINE["turnover_min"]<tur<FUNDAMENTAL_RED_LINE["turnover_max"]
              and not is_st)

        return {"pe":round(pe,2) if pe<999 else 999,"pb":round(pb,2) if pb<999 else 999,
                "market_cap":mc,"turnover":tur,"industry":ik,"fund_pass":ok,
                "is_st":is_st,"is_suspended":False}
    except Exception as e:
        return {"pe":999,"pb":999,"market_cap":0,"turnover":0,"industry":"其他",
                "fund_pass":True,"is_st":False,"is_suspended":False}

def get_stock_data(s, n, t_type, mr, mode):
    try:
        df = yf.Ticker(s).history(period=f"{HIST_DAYS}d", timeout=2)
        if len(df)<5: return None

        tech = calc_technical_indicators(df, mode)
        cp = tech["price"]
        if cp>MAX_PRICE: return None

        fund = get_fundamental_data(s,n)
        bp = round(cp*1.001,2)
        sl = round(bp * 0.982, 2)
        tp = round(bp * 1.02, 2)

        ps = (tp-bp)/bp
        ls = (bp-sl)/bp
        wlr = round(ps/ls,2) if ls>0 else 1.0
        score = round(tech["volume_ratio"] * 2 + (1 if tech["trend_up"] else 0) + wlr, 2)

        return {"symbol":s,"code":s.replace(".SS","").replace(".SZ",""),"name":n,"pool_type":t_type,
                "tech":tech,"fund":fund,"win_loss_ratio":wlr,"total_score":score,"buy_signal":True,
                "stats":{"price_range_low":sl,"price_range_high":tp,"volatility_pct":1.8,
                        "win_loss_ratio":wlr,"stop_loss_pct":1.8,"take_profit_pct":2.0}}
    except Exception as e:
        return None

def scan(mr, mode):
    res, watch = [], []
    pool = {**T1_POOL, **MY_STOCKS}

    for s,n in pool.items():
        t_type = "t1" if s in T1_POOL else "core"
        stock = get_stock_data(s,n,t_type,mr,mode)
        if stock:
            res.append(stock)
        t.sleep(0.02)

    res = sorted(res, key=lambda x:x["total_score"], reverse=True)[:5]
    return res, watch

# ======================== 推送文案 ========================
def build_msg(buy, watch, tips):
    now = get_standard_now().strftime("%Y-%m-%d %H:%M:%S")
    msg = f"""==================================================
【🤖 T+1短线量化算法 · 每日9:20准时推送】
📅 输出时间：{now}
📊 大盘状态：{tips}
⏰ 操作建议：尾盘14:55左右买入，次日收盘前无论盈亏全部清仓
==================================================
⚠️ 法律合规声明（务必阅读）
1. 本内容为 Python 量化程序**全自动运算输出的公开行情数据记录**，属于历史统计信息，
   无任何人工干预、无人工筛选、无人工点评、**不构成任何投资建议**。
2. 本社群收取费用为：**算法算力使用费 + 数据订阅费 + 圈层准入服务费**，
   与证券投资咨询、荐股、买卖指导无关，无任何收益承诺。
3. 所有数据仅用于**量化技术学习、算法逻辑验证、历史数据复盘**。
4. 严禁任何个人依据本数据进行实盘交易，否则一切盈亏自行承担。
5. 本人不提供任何个股操作指导、买卖建议，不开展任何证券投资咨询业务。

==================================================
【📊 T+1短线标的 · 纯数据展示】
"""
    if buy:
        for i, s in enumerate(buy,1):
            p = s["stats"]
            msg += f"""
【数据{i}】{s['code']} {s['name']}
💵 现价：{s['tech']['price']}元｜涨幅：{s['tech']['day_change']}%｜量比：{s['tech']['volume_ratio']}
📉 止损：{p['price_range_low']}元｜止盈：{p['price_range_high']}元
--------------------------------------------------
"""
    else:
        msg += "⚠️ 今日暂无符合条件标的\n"

    msg += """
==================================================
💡 T+1专属重要提醒
1. 严格执行：尾盘14:55买入，次日14:45前无论盈亏全部清仓
2. 止损纪律：跌破止损价立即卖出，绝不扛单
3. 仓位控制：单只股票仓位不超过总资金的30%
4. 本内容为量化算法自动输出，非个股推荐
5. 禁止跟单交易、禁止对外传播、禁止用于实盘决策
6. 股市有风险，投资需谨慎，数据仅供技术学习
==================================================
"""
    return msg[:1800]

# ======================== 推送 ========================
def send_feishu(msg):
    try:
        resp = requests.post(FEISHU_WEBHOOK, json={"msg_type": "text", "content": {"text": msg}}, timeout=5)
        if resp.status_code == 200:
            logger.info("✅ 飞书推送成功")
    except:
        logger.error("❌ 飞书推送失败")

def send_dingtalk(msg):
    try:
        timestamp = str(round(t.time() * 1000))
        secret_enc = DINGTALK_SECRET.encode('utf-8')
        string_to_sign = f"{timestamp}\n{DINGTALK_SECRET}"
        string_to_sign_enc = string_to_sign.encode('utf-8')
        hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        url = f"{DINGTALK_WEBHOOK}&timestamp={timestamp}&sign={sign}"
        message = {"msgtype": "text", "text": {"content": msg}}
        requests.post(url, json=message, timeout=5)
        logger.info("✅ 钉钉推送成功")
    except:
        logger.error("❌ 钉钉推送失败")

# ======================== 主逻辑（严格9:20） ========================
def main():
    logger.info("⏳ 等待 09:20 自动执行...")
    while True:
        now = get_standard_now()
        current_time_str = f"{now.hour:02d}:{now.minute:02d}"
        logger.info(f"当前北京时间：{current_time_str}，等待 09:20...")

        # 只有交易日 + 9:20 左右才执行
        if is_trading_day() and is_target_trading_time():
            logger.info("🚀 09:20 到达，开始执行T+1策略...")
            mr, tips, mode = get_market_status()
            buy, watch = scan(mr, mode)
            msg = build_msg(buy, watch, tips)
            send_feishu(msg)
            send_dingtalk(msg)
            logger.info("🎉 今日T+1数据推送完成")
            break
        t.sleep(10)

# ======================== 启动 ========================
if __name__ == "__main__":
    logger.info("="*50)
    logger.info("🚀 GitHub Actions 触发，T+1短线策略启动")
    logger.info("="*50)
    sync_ntp_time()
    main()
    logger.info("="*50)
    logger.info("🏁 程序运行结束")
    logger.info("="*50)
