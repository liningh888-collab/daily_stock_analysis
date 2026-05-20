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

# 飞书 Webhook (替换成你自己的有效地址！！！)
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/你的飞书Webhook"

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

# 判断当前属于哪个推送时段
def get_time_type():
    now = get_standard_now()
    h = now.hour
    m = now.minute
    if h == 5 and m == 40:
        return "morning"
    elif h == 9 and m == 0:
        return "open"
    elif h == 15 and m == 0:
        return "close"
    else:
        return "normal"

# ======================== 交易日期判断 ========================
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
    workdays_2026 = [
        "2026-01-25", "2026-02-08", "2026-04-26", "2026-05-25", "2026-09-28", "2026-10-11"
    ]
    today_str = today.strftime("%Y-%m-%d")
    if today_str in holidays_2026:
        logger.info(f"❌ 节假日休市: {today_str}")
        return False
    if today_str in workdays_2026:
        logger.info(f"✅ 调休补班: {today_str}")
        return True
    return True

# ======================== 【严格化】T+1专属核心参数 ========================
SELECTION_TOP_N = 3
HIST_DAYS = 30
CAPITAL = 10000
MAX_PRICE = 35
TRADING_COST_RATE = 0.0015
MIN_PROFIT_COVER = 0.01
SINGLE_MAX_RISK = 250

# 选股参数
T1_MODE = {
    "win_loss_ratio_min": 1.2,
    "day_change_min": -0.02,
    "day_change_max": 0.06,
    "volume_ratio_min": 1.0,
    "turnover_min": 3,
    "turnover_max": 20,
    "open_gap_max": 0.03,
    "trend_up_required": True,
    "rsi_min": 30,
    "rsi_max": 70,
    "macd_positive": True
}

NORMAL_MODE = {
    "win_loss_ratio_min": 1.3,
    "day_change_min": -0.02,
    "day_change_max": 0.05,
    "volume_ratio_min": 0.8,
    "assist_conds_min": 1,
    "trend_up_required": True,
    "rsi_min": 35,
    "rsi_max": 65,
    "macd_positive": True
}

WEAK_MODE = {
    "win_loss_ratio_min": 1.1,
    "day_change_min": -0.03,
    "day_change_max": 0.05,
    "volume_ratio_min": 0.5,
    "assist_conds_min": 0,
    "trend_up_required": True,
    "rsi_min": 25,
    "rsi_max": 75,
    "macd_positive": False
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
    "半导体": {"pe_max": 80, "pb_max": 6.0},
    "新能源": {"pe_max": 60, "pb_max": 5.0},
    "其他": {"pe_max": 50, "pb_max": 5.0}
}

FUNDAMENTAL_RED_LINE = {
    "market_cap_min": 80,
    "turnover_min": 3,
    "turnover_max": 20,
    "avg_volume_min": 5000
}

# ======================== 【保底银行股】低风险兜底 ========================
GUARANTEE_BANK_STOCKS = {
    "601398.SS": "工商银行",
    "601939.SS": "建设银行",
    "601288.SS": "农业银行",
    "601838.SS": "成都银行"
}

# ======================== 股票池 ========================
T1_POOL = {
    "600028.SS": "中国石化", "600023.SS": "浙能电力", "600726.SS": "华电能源",
    "601016.SS": "节能风电", "600968.SS": "海油发展", "000968.SZ": "蓝焰控股",
    "600795.SS": "国电电力", "600011.SS": "华能国际", "600026.SS": "中远海能",
    "600279.SS": "重庆港", "601006.SS": "大秦铁路", "001872.SZ": "招商港口",
    "600017.SS": "日照港", "600428.SS": "中远海特", "600332.SS": "白云山",
    "000999.SZ": "华润三九", "600566.SS": "济川药业", "000538.SZ": "云南白药",
    "600572.SS": "康恩贝", "000989.SZ": "九芝堂", "000997.SZ": "新大陆",
    "002027.SZ": "分众传媒", "002152.SZ": "广电运通", "000100.SZ": "TCL科技",
    "002056.SZ": "横店东磁", "601225.SS": "陕西煤业", "000830.SZ": "鲁西化工",
    "600426.SS": "华鲁恒升", "600362.SS": "江西铜业", "601933.SS": "永辉超市",
    "002281.SZ": "光迅科技", "300308.SZ": "中际旭创", "300394.SZ": "天孚通信",
    "000988.SZ": "华工科技", "600487.SS": "亨通光电", "002491.SZ": "通鼎互联",
    "600584.SS": "长电科技", "002156.SZ": "通富微电", "603501.SS": "韦尔股份",
    "002049.SZ": "紫光国微", "600171.SS": "上海贝岭", "002185.SZ": "华天科技",
    "002594.SZ": "比亚迪", "300750.SZ": "宁德时代", "600549.SS": "厦门钨业",
    "002460.SZ": "赣锋锂业", "002466.SZ": "天齐锂业", "600478.SS": "科力远",
    "002747.SZ": "埃斯顿", "300024.SZ": "机器人", "601717.SS": "郑煤机",
    "002559.SZ": "亚威股份", "002248.SZ": "华东数控",
    "600019.SS": "宝钢股份", "000932.SZ": "华菱钢铁", "601668.SS": "中国建筑",
    "601390.SS": "中国中铁", "601186.SS": "中国铁建"
}

MY_STOCKS = {
    "600726.SS": "华电能源", "601016.SS": "节能风电", "600023.SS": "浙能电力",
    "600028.SS": "中国石化", "600968.SS": "海油发展", "000968.SZ": "蓝焰控股",
    "002132.SZ": "恒星科技",
    "002281.SZ": "光迅科技", "600584.SS": "长电科技", "002594.SZ": "比亚迪"
}

# ======================== 工具函数 ========================
def get_market_status():
    try:
        hs300 = yf.Ticker("000300.SS")
        df = hs300.history(period="60d", timeout=3)
        if len(df) < 10:
            return 0.5, "大盘数据不足，谨慎观察", WEAK_MODE
        close = df["Close"].astype(float)
        ma20 = close.rolling(20, min_periods=1).mean()
        current = close.iloc[-1]
        
        if current > ma20.iloc[-1] * 1.02:
            return 0.8, "市场强势，T+1策略积极", T1_MODE
        elif current > ma20.iloc[-1]:
            return 0.7, "市场正常，T+1策略就绪", T1_MODE
        elif current > ma20.iloc[-1] * 0.98:
            return 0.6, "市场震荡，T+1策略谨慎", NORMAL_MODE
        else:
            return 0.4, "市场弱势，T+1策略防御", WEAK_MODE
    except Exception as e:
        logger.warning(f"⚠️ 大盘状态获取异常: {e}")
        return 0.5, "大盘状态正常", NORMAL_MODE

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

    turnover = round(volume.iloc[-1] / df["Volume"].mean() * 100, 2)
    
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

def get_fundamental_data(s, n):
    try:
        info = yf.Ticker(s).info
        pe = info.get("trailingPE",999)
        pb = info.get("priceToBook",999)
        mc = round(info.get("marketCap",0)/1e8,2)
        tur = round(info.get("averageVolume10days",0)/info.get("sharesOutstanding",1)*100,2) if info.get("sharesOutstanding") else 1
        avg_volume = round(info.get("averageVolume10days",0) * info.get("currentPrice",0)/1e4,2)
        industry = info.get("industry","Other")

        is_st = "ST" in info.get("longName", "")
        im = {
            "Thermal Coal":"煤炭","Oil & Gas Integrated":"石油天然气","Electric Utilities":"电力",
            "Railroads":"交通运输","Semiconductors":"半导体","Semiconductor":"半导体",
            "Solar":"新能源","Wind":"新能源","Batteries":"新能源","Electrical Components":"新能源",
            "Electronic Components":"电子","Computer Hardware":"计算机","Software":"计算机"
        }
        ik = im.get(industry,"其他")
        ir = INDUSTRY_PE_RULES.get(ik, INDUSTRY_PE_RULES["其他"])

        ok = (pe<ir["pe_max"] and pb<ir["pb_max"] and mc>FUNDAMENTAL_RED_LINE["market_cap_min"]
              and FUNDAMENTAL_RED_LINE["turnover_min"]<tur<FUNDAMENTAL_RED_LINE["turnover_max"]
              and avg_volume>FUNDAMENTAL_RED_LINE["avg_volume_min"]
              and not is_st)

        return {"pe":round(pe,2) if pe<999 else 999,"pb":round(pb,2) if pb<999 else 999,
                "market_cap":mc,"turnover":tur,"avg_volume":avg_volume,"industry":ik,"fund_pass":ok,
                "is_st":is_st,"is_suspended":False}
    except Exception as e:
        return {"pe":999,"pb":999,"market_cap":0,"turnover":0,"avg_volume":0,"industry":"其他",
                "fund_pass":True,"is_st":False,"is_suspended":False}

def get_stock_data(s, n, t_type, mr, mode):
    try:
        df = yf.Ticker(s).history(period=f"{HIST_DAYS}d", timeout=2)
        if len(df)<10: return None

        tech = calc_technical_indicators(df, mode)
        cp = tech["price"]
        if cp>MAX_PRICE: return None

        fund = get_fundamental_data(s,n)
        if not fund["fund_pass"]: return None
        
        if not (tech["is_intraday_strong"] and tech["is_not_overbought"] and 
                tech["is_not_high_open"] and tech["trend_up"] and 
                tech["rsi_ok"] and tech["macd_ok"]):
            return None

        bp = round(cp*1.001,2)
        sl = round(bp * 0.982, 2)
        tp = round(bp * 1.02, 2)

        ps = (tp-bp)/bp
        ls = (bp-sl)/bp
        wlr = round(ps/ls,2) if ls>0 else 1.0
        
        score = round(
            tech["volume_ratio"] * 3 + 
            (1 if tech["macd_gold"] else 0) + 
            (1 if tech["kdj_gold"] else 0) + 
            (1 if tech["trend_up"] else 0) + 
            wlr * 2, 
            2
        )

        return {"symbol":s,"code":s.replace(".SS","").replace(".SZ",""),"name":n,"pool_type":t_type,
                "tech":tech,"fund":fund,"win_loss_ratio":wlr,"total_score":score,"buy_signal":True,
                "stats":{"price_range_low":sl,"price_range_high":tp,"volatility_pct":1.8,
                        "win_loss_ratio":wlr,"stop_loss_pct":1.8,"take_profit_pct":2.0}}
    except Exception as e:
        logger.debug(f"获取股票数据失败 {s} {n}: {str(e)[:50]}")
        return None

# ======================== 扫描 + 银行股保底 ========================
def scan(mr, mode):
    res, watch = [], []
    pool = {**T1_POOL, **MY_STOCKS}
    
    pool_items = list(pool.items())
    random.shuffle(pool_items)

    for s,n in pool_items:
        t_type = "t1" if s in T1_POOL else "core"
        stock = get_stock_data(s,n,t_type,mr,mode)
        if stock:
            res.append(stock)
        t.sleep(0.03)

    res = sorted(res, key=lambda x:x["total_score"], reverse=True)[:SELECTION_TOP_N]

    # 无推荐 → 自动选银行股保底
    if len(res) == 0:
        logger.info("⚠️ 今日无符合条件标的，自动启用【银行股保底】")
        bank_items = list(GUARANTEE_BANK_STOCKS.items())
        random.shuffle(bank_items)
        
        for s, n in bank_items:
            stock = get_stock_data(s, n, "guarantee", mr, mode)
            if stock:
                res.append(stock)
                logger.info(f"✅ 保底银行股已选中：{n}({s})")
                break

    return res, watch

# ======================== 消息构建 ========================
def build_msg(buy, watch, tips, time_type):
    now = get_standard_now().strftime("%Y-%m-%d %H:%M:%S")
    if time_type == "morning":
        title = "【🤖 T+1量化 · 早盘5:40前瞻】"
        tip_text = "早盘前瞻：提前筛选当日备选标的，等待尾盘定点介入"
    elif time_type == "open":
        title = "【🤖 T+1量化 · 9:00开盘参考】"
        tip_text = "开盘参考：集合竞价结束，观察量能与开盘溢价"
    elif time_type == "close":
        title = "【🤖 T+1量化 · 15:00收盘总结】"
        tip_text = "收盘总结：当日标的复盘，明日持仓隔日处理规划"
    else:
        title = "【🤖 T+1短线量化算法 · 日常推送】"
        tip_text = "尾盘14:55左右买入，次日收盘前无论盈亏全部清仓"

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

# ======================== 【优化修复】飞书推送（稳定版） ========================
def send_feishu(msg):
    if not FEISHU_WEBHOOK or FEISHU_WEBHOOK.endswith("你的飞书Webhook"):
        logger.error("❌ 飞书Webhook未配置，跳过推送")
        return
    if not msg or len(msg.strip()) == 0:
        logger.error("❌ 推送消息为空，跳过推送")
        return

    headers = {"Content-Type": "application/json;charset=utf-8"}
    payload = {
        "msg_type": "text",
        "content": {"text": msg}
    }

    # 重试2次，解决网络波动问题
    for retry in range(2):
        try:
            response = requests.post(
                FEISHU_WEBHOOK,
                headers=headers,
                data=json.dumps(payload),
                timeout=10
            )
            result = response.json()
            if result.get("code") == 0:
                logger.info("✅ 飞书推送成功！")
                return
            else:
                logger.warning(f"⚠️ 飞书推送失败({retry+1}/2)：{result}")
        except Exception as e:
            logger.warning(f"⚠️ 飞书推送异常({retry+1}/2)：{str(e)[:50]}")
        t.sleep(1)
    
    logger.error("❌ 飞书推送重试2次均失败")

# ======================== 钉钉推送 ========================
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
        resp = requests.post(url, json=message, timeout=5)
        if resp.status_code == 200:
            result = resp.json()
            if result.get("errcode") == 0:
                logger.info("✅ 钉钉推送成功")
            else:
                logger.error(f"❌ 钉钉推送失败: {result.get('errmsg')}")
        else:
            logger.error(f"❌ 钉钉推送失败: HTTP {resp.status_code}")
    except Exception as e:
        logger.error(f"❌ 钉钉推送异常: {str(e)[:50]}")

# ======================== 主逻辑 ========================
def main():
    logger.info("🚀 开始运行T+1短线策略...")
    time_type = get_time_type()
    logger.info(f"⏰ 当前推送时段类型：{time_type}")

    if is_trading_day():
        mr, tips, mode = get_market_status()
        logger.info(f"📊 市场状态: {tips}")
        buy, watch = scan(mr, mode)
        logger.info(f"🔍 扫描完成，选出 {len(buy)} 只股票")
        msg = build_msg(buy, watch, tips, time_type)
        send_feishu(msg)
        send_dingtalk(msg)
        logger.info("🎉 今日推送完成")
    else:
        logger.info("ℹ️ 今日非交易日，不推送")

# ======================== 启动 ========================
if __name__ == "__main__":
    logger.info("="*50)
    logger.info("🚀 GitHub Actions 定时触发策略启动")
    logger.info("="*50)
    sync_ntp_time()
    main()
    logger.info("="*50)
    logger.info("🏁 程序运行结束")
    logger.info("="*50)
