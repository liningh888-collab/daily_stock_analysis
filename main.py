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
import akshare as ak  # 主数据源

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
    if h == 5 and m == 40: return "morning"
    elif h == 9 and m == 0: return "open"
    elif h == 15 and m == 0: return "close"
    else: return "normal"

# ======================== 交易日判断 ========================
def is_trading_day():
    today = get_standard_now().date()
    if today.weekday() > 4:
        logger.info("❌ 周末休市")
        return False
    holidays_2026 = {"2026-01-01","2026-01-28","2026-01-29","2026-01-30","2026-01-31","2026-02-01","2026-02-02","2026-04-04","2026-05-01","2026-05-28","2026-05-29","2026-10-01","2026-10-02","2026-10-03","2026-10-04","2026-10-05","2026-10-06","2026-10-07","2026-10-08"}
    workdays_2026 = {"2026-01-25","2026-02-08","2026-04-26","2026-05-25","2026-09-28","2026-10-11"}
    today_str = today.strftime("%Y-%m-%d")
    if today_str in holidays_2026:
        logger.info(f"❌ 节假日休市: {today_str}")
        return False
    if today_str in workdays_2026:
        logger.info(f"✅ 调休补班: {today_str}")
        return True
    return True

# ======================== 选股参数（宽松，保证出票） ========================
SELECTION_TOP_N = 5
HIST_DAYS = 20
MAX_PRICE = 45
TRADING_COST_RATE = 0.0015
MIN_PROFIT_COVER = 0.01
SINGLE_MAX_RISK = 250

# 强势模式（主用）
T1_MODE = {
    "win_loss_ratio_min": 0.5,
    "day_change_min": -0.1,
    "day_change_max": 0.2,
    "volume_ratio_min": 0.2,
    "turnover_min": 1,
    "turnover_max": 30,
    "open_gap_max": 0.08,
    "trend_up_required": False,
    "rsi_min": 5,
    "rsi_max": 95,
    "macd_positive": False
}
NORMAL_MODE = T1_MODE.copy()
WEAK_MODE = T1_MODE.copy()

# 行业规则（仅展示）
INDUSTRY_PE_RULES = {"银行":{"pe_max":12,"pb_max":1.2},"保险":{"pe_max":15,"pb_max":2.0},"证券":{"pe_max":25,"pb_max":2.5},"煤炭":{"pe_max":35,"pb_max":3.0},"石油天然气":{"pe_max":70,"pb_max":4.5},"钢铁":{"pe_max":25,"pb_max":2.0},"有色":{"pe_max":40,"pb_max":3.5},"化工":{"pe_max":30,"pb_max":3.0},"医药生物":{"pe_max":50,"pb_max":5.0},"食品饮料":{"pe_max":40,"pb_max":6.0},"零售":{"pe_max":30,"pb_max":3.0},"计算机":{"pe_max":70,"pb_max":6.0},"电子":{"pe_max":55,"pb_max":5.0},"国防军工":{"pe_max":80,"pb_max":5.0},"通信":{"pe_max":45,"pb_max":4.0},"电力":{"pe_max":25,"pb_max":2.5},"交通运输":{"pe_max":20,"pb_max":2.0},"建筑装饰":{"pe_max":15,"pb_max":1.5},"半导体":{"pe_max":80,"pb_max":6.0},"新能源":{"pe_max":60,"pb_max":5.0},"其他":{"pe_max":50,"pb_max":5.0}}
FUNDAMENTAL_RED_LINE = {"market_cap_min":50,"turnover_min":1,"turnover_max":30,"avg_volume_min":1000}

# 保底银行股
GUARANTEE_BANK_STOCKS = {"601398.SS":"工商银行","601939.SS":"建设银行","601288.SS":"农业银行","601838.SS":"成都银行"}

# 你的股票池（AKShare能正常拿到数据）
T1_POOL = {
    "601016.SS":"节能风电","000767.SZ":"晋控电力","600905.SS":"三峡能源",
    "601001.SS":"晋控煤业","601898.SS":"中煤能源","600026.SS":"中远海能",
    "600018.SS":"上港集团","601668.SS":"中国建筑","601390.SS":"中国中铁",
    "000725.SZ":"京东方A","000100.SZ":"TCL科技","600028.SS":"中国石化",
    "601068.SS":"中铝国际","000938.SZ":"紫光股份","002384.SZ":"东山精密",
    "002129.SZ":"中环股份","600151.SS":"航天机电","600343.SS":"航天动力",
    "600879.SS":"航天电子","002389.SZ":"航天彩虹","600183.SS":"生益科技",
    "600360.SS":"华微电子","600023.SS":"浙能电力","600726.SS":"华电能源",
    "600795.SS":"国电电力","600011.SS":"华能国际","600279.SS":"重庆港",
    "601006.SS":"大秦铁路","001872.SZ":"招商港口","600017.SS":"日照港",
    "600428.SS":"中远海特","600332.SS":"白云山","000999.SZ":"华润三九",
    "600566.SS":"济川药业","000538.SZ":"云南白药","600572.SS":"康恩贝",
    "000989.SZ":"九芝堂","000997.SZ":"新大陆","002027.SZ":"分众传媒",
    "002152.SZ":"广电运通","002056.SZ":"横店东磁","601225.SS":"陕西煤业",
    "000830.SZ":"鲁西化工","600426.SS":"华鲁恒升","600362.SS":"江西铜业",
    "601933.SS":"永辉超市"
}
MY_STOCKS = {"600726.SS":"华电能源","601016.SS":"节能风电","600023.SS":"浙能电力","600028.SS":"中国石化","600968.SS":"海油发展","000968.SZ":"蓝焰控股","002132.SZ":"恒星科技","002281.SZ":"光迅科技","600584.SS":"长电科技","002594.SZ":"比亚迪"}

# ======================== 双数据源统一获取（AK主，yfinance备用） ========================
def standardize_code(s):
    return s.replace(".SS", "").replace(".SZ", "")

def fetch_stock_data(code, name, days=HIST_DAYS):
    """AKShare为主，失败自动切yfinance，统一字段"""
    ak_code = standardize_code(code)
    end = get_standard_now().strftime("%Y%m%d")
    start = (get_standard_now() - timedelta(days=days+5)).strftime("%Y%m%d")

    # 主：AKShare
    try:
        df = ak.stock_zh_a_hist(symbol=ak_code, period="daily", start_date=start, end_date=end, adjust="qfq")
        if df.empty or len(df) < 10:
            raise Exception("AK数据不足")
        df.rename(columns={"日期":"date","开盘":"open","收盘":"close","最高":"high","最低":"low","成交量":"volume"}, inplace=True)
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        return df
    except Exception as e:
        logger.warning(f"⚠️ AKShare失败 {code}: {e}，切换yfinance")

    # 备用：yfinance
    try:
        df = yf.Ticker(code).history(period=f"{days+5}d", timeout=5)
        if df.empty or len(df) < 5:
            return None
        df.rename(columns={"Open":"open","Close":"close","High":"high","Low":"low","Volume":"volume"}, inplace=True)
        return df
    except Exception as e:
        logger.error(f"❌ 双数据源均失败 {code}: {e}")
        return None

# ======================== 技术指标计算 ========================
def calc_atr(df, period=14):
    high, low, close = df["High"], df["Low"], df["Close"]
    tr = pd.concat([high-low, abs(high-close.shift(1)), abs(low-close.shift(1))], axis=1).max(axis=1)
    return round(tr.rolling(period).mean().iloc[-1], 2)

def calc_technical_indicators(df, mode):
    close, high, low, volume, open_ = df["close"], df["high"], df["low"], df["volume"], df["open"]
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
    macd_positive = macd.iloc[-1] > 0

    volume_enlarge = volume.iloc[-1] >= ma5_vol.iloc[-1] * mode["volume_ratio_min"]
    volume_ratio = round(volume.iloc[-1] / ma5_vol.iloc[-1], 2) if ma5_vol.iloc[-1] > 0 else 1

    cp, op = close.iloc[-1], open_.iloc[-1]
    dc = (cp - op) / op
    open_gap = (op - close.iloc[-2]) / close.iloc[-2]

    it = dc >= mode["day_change_min"]
    no = dc <= mode["day_change_max"]
    og = open_gap <= mode["open_gap_max"]
    tu = (close.iloc[-1] > ma5.iloc[-1]) if mode["trend_up_required"] else True
    
    rsi_ok = (rsi >= mode.get("rsi_min", 0)) and (rsi <= mode.get("rsi_max", 100))
    macd_ok = (not mode.get("macd_positive", False)) or macd_positive

    turnover = round(volume.iloc[-1] / df["volume"].mean() * 100, 2)
    
    low_min = low.rolling(9).min()
    high_max = high.rolling(9).max()
    rsv = (close - low_min) / (high_max - low_min) * 100
    k = rsv.ewm(com=2).mean()
    d = k.ewm(com=2).mean()
    j = 3*k - 2*d
    kdj_k = round(k.iloc[-1], 1)
    kdj_d = round(d.iloc[-1], 1)
    kdj_j = round(j.iloc[-1], 1)
    kdj_gold = (k.iloc[-2] < d.iloc[-2]) and (k.iloc[-1] > d.iloc[-1])

    return {
        "price": round(cp,2), "open_price": round(op,2), "day_change": round(dc*100,2),
        "open_gap": round(open_gap*100,2), "turnover": turnover,
        "ma5": round(ma5.iloc[-1],2), "ma10": round(ma10.iloc[-1],2), "ma20": round(ma20.iloc[-1],2),
        "rsi": rsi, "macd_gold": macd_gold, "macd_positive": macd_positive,
        "trend_up": tu, "volume_enlarge": volume_enlarge, "volume_ratio": volume_ratio,
        "vol_trend": vol_trend, "atr": calc_atr(df), "kdj_k": kdj_k, "kdj_d": kdj_d,
        "kdj_j": kdj_j, "kdj_gold": kdj_gold,
        "is_intraday_strong": it, "is_not_overbought": no, "is_not_high_open": og,
        "rsi_ok": rsi_ok, "macd_ok": macd_ok
    }

# ======================== 大盘状态（AKShare优先） ========================
def get_market_status():
    try:
        # AKShare 沪深300
        df = ak.stock_zh_index_daily(symbol="000300")
        if len(df) < 10:
            raise Exception("AK指数数据不足")
        close = df["close"].astype(float).tail(20)
        ma20 = close.rolling(20, min_periods=1).mean()
        current = close.iloc[-1]
    except Exception as e:
        logger.warning(f"⚠️ AK指数失败，切yfinance: {e}")
        try:
            df = yf.Ticker("000300.SS").history(period="20d", timeout=5)
            close = df["Close"].astype(float)
            ma20 = close.rolling(20, min_periods=1).mean()
            current = close.iloc[-1]
        except:
            return 0.7, "大盘数据异常，按正常策略运行", T1_MODE

    if current > ma20.iloc[-1] * 1.01:
        return 0.8, "市场强势，T+1策略积极", T1_MODE
    elif current > ma20.iloc[-1] * 0.97:
        return 0.7, "市场正常，T+1策略就绪", T1_MODE
    else:
        return 0.6, "市场震荡，T+1策略谨慎", T1_MODE

# ======================== 个股数据获取 ========================
def get_stock_data(s, n, t_type, mr, mode):
    logger.debug(f"检测标的: {s} {n}")
    df = fetch_stock_data(s, n)
    if df is None:
        return None
    tech = calc_technical_indicators(df, mode)
    cp = tech["price"]
    if cp > MAX_PRICE:
        return None

    # 基本面简化（不强制过滤）
    fund = {"industry":"其他","market_cap":500.0,"pe":20.0,"pb":2.0,"turnover":tech["turnover"],"avg_volume":2000.0}
    if not (tech["is_intraday_strong"] and tech["rsi_ok"]):
        return None

    bp = round(cp*1.001,2)
    sl = round(bp * 0.982, 2)
    tp = round(bp * 1.02, 2)
    ps = (tp-bp)/bp
    ls = (bp-sl)/bp
    wlr = round(ps/ls,2) if ls>0 else 1.0

    score = round(tech["volume_ratio"]*3 + (1 if tech["macd_gold"] else 0) + (1 if tech["kdj_gold"] else 0) + (1 if tech["trend_up"] else 0) + wlr*2, 2)
    return {"symbol":s,"code":s.replace(".SS","").replace(".SZ",""),"name":n,"pool_type":t_type,"tech":tech,"fund":fund,"win_loss_ratio":wlr,"total_score":score,"buy_signal":True,"stats":{"price_range_low":sl,"price_range_high":tp}}

# ======================== 扫描选股 ========================
def scan(mr, mode):
    res, watch = [], []
    pool = {**T1_POOL, **MY_STOCKS}
    pool_items = list(pool.items())

    for s,n in pool_items:
        t_type = "t1" if s in T1_POOL else "core"
        stock = get_stock_data(s,n,t_type,mr,mode)
        if stock:
            res.append(stock)
            logger.info(f"✅ 选中：{n}({s})")
        t.sleep(0.15)

    res = sorted(res, key=lambda x:x["total_score"], reverse=True)[:SELECTION_TOP_N]

    # 保底银行股（补全字段）
    if len(res) == 0:
        logger.info("⚠️ 无符合条件标的，启用银行股保底")
        bank_items = list(GUARANTEE_BANK_STOCKS.items())
        random.shuffle(bank_items)
        for s,n in bank_items:
            df = fetch_stock_data(s,n)
            if df is None:
                continue
            cp = round(df["close"].iloc[-1],2)
            res.append({"symbol":s,"code":s.replace(".SS","").replace(".SZ",""),"name":n,"pool_type":"guarantee","tech":{"price":cp,"day_change":0.0,"volume_ratio":1.0,"rsi":50,"macd_positive":False,"kdj_gold":False},"fund":{"industry":"银行","market_cap":9999.99,"pe":10.0,"pb":1.1},"win_loss_ratio":1.0,"total_score":5.0,"stats":{"price_range_low":round(cp*0.982,2),"price_range_high":round(cp*1.02,2)}})
            break
    return res, watch

# ======================== 消息构建/推送 ========================
def build_msg(buy, watch, tips, time_type):
    now = get_standard_now().strftime("%Y-%m-%d %H:%M:%S")
    title_map = {"morning":"【🤖 T+1量化 · 早盘5:40前瞻】","open":"【🤖 T+1量化 · 9:00开盘参考】","close":"【🤖 T+1量化 · 15:00收盘总结】","normal":"【🤖 T+1短线量化算法 · 日常推送】"}
    tip_map = {"morning":"早盘前瞻：提前筛选当日备选标的，等待尾盘定点介入","open":"开盘参考：集合竞价结束，观察量能与开盘溢价","close":"收盘总结：当日标的复盘，明日持仓隔日处理规划","normal":"尾盘14:55左右买入，次日14:45前清仓"}
    title = title_map[time_type]
    tip_text = tip_map[time_type]

    msg = f"""==================================================
{title}
📅 输出时间：{now}
📊 大盘状态：{tips}
💡 策略提示：{tip_text}
==================================================
⚠️ 法律合规声明
1. 本内容为Python量化程序自动运算的公开行情数据，不构成投资建议。
2. 仅用于量化技术学习、算法验证、历史数据复盘，禁止实盘交易。
==================================================
【📊 T+1短线标的 · 纯数据展示】
"""
    if buy:
        for i, s in enumerate(buy,1):
            p = s["stats"]
            guarantee_tag = "【保底银行股】" if s["pool_type"] == "guarantee" else ""
            msg += f"""
【数据{i}】{guarantee_tag}{s['code']} {s['name']}
💵 现价：{s['tech']['price']}元｜涨幅：{s['tech']['day_change']}%｜量比：{s['tech']['volume_ratio']}
📉 止损：{p['price_range_low']}元｜止盈：{p['price_range_high']}元
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

def send_feishu(msg):
    if not FEISHU_WEBHOOK:
        logger.error("❌ 飞书Webhook未配置")
        return
    try:
        r = requests.post(FEISHU_WEBHOOK, json={"msg_type":"text","content":{"text":msg}}, timeout=10)
        if r.json().get("code") == 0:
            logger.info("✅ 飞书推送成功")
    except Exception as e:
        logger.warning(f"⚠️ 飞书推送异常: {e}")

def send_dingtalk(msg):
    try:
        timestamp = str(round(t.time() * 1000))
        secret_enc = DINGTALK_SECRET.encode('utf-8')
        string_to_sign = f"{timestamp}\n{DINGTALK_SECRET}"
        string_to_sign_enc = string_to_sign.encode('utf-8')
        hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        url = f"{DINGTALK_WEBHOOK}&timestamp={timestamp}&sign={sign}"
        requests.post(url, json={"msgtype":"text","text":{"content":msg}}, timeout=5)
        logger.info("✅ 钉钉推送成功")
    except Exception as e:
        logger.error(f"❌ 钉钉推送异常: {e}")

# ======================== 主逻辑 ========================
def main():
    logger.info("🚀 T+1短线量化策略（AK主+ yfinance保底）启动")
    sync_ntp_time()
    time_type = get_time_type()
    logger.info(f"⏰ 时段：{time_type}")

    if is_trading_day():
        mr, tips, mode = get_market_status()
        logger.info(f"📊 市场状态：{tips}")
        buy, watch = scan(mr, mode)
        logger.info(f"🔍 选出 {len(buy)} 只标的")
        msg = build_msg(buy, watch, tips, time_type)
        send_feishu(msg)
        send_dingtalk(msg)
        logger.info("🎉 推送完成")
    else:
        logger.info("ℹ️ 非交易日，不推送")

if __name__ == "__main__":
    main()
