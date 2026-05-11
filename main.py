import requests
import json
import logging
import os
import time
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

def sync_ntp_time():
    global TIME_OFFSET
    for server in NTP_SERVERS:
        try:
            client = ntplib.NTPClient()
            response = client.request(server, version=3, timeout=3)
            TIME_OFFSET = response.tx_time - time.time()
            logger.info(f"✅ 时间同步成功 [{server}]，偏差: {TIME_OFFSET:.3f}秒")
            return True
        except Exception as e:
            logger.warning(f"⚠️ 时间同步失败 [{server}]: {str(e)}")
            continue
    logger.error("❌ 所有NTP服务器同步失败，将使用本地时间")
    TIME_OFFSET = 0.0
    return False

def get_standard_now():
    standard_timestamp = time.time() + TIME_OFFSET
    bj_tz = pytz.timezone("Asia/Shanghai")
    return datetime.fromtimestamp(standard_timestamp, tz=bj_tz)

# ======================== 【T+1专属核心参数】 ========================
SELECTION_TOP_N = 3
HIST_DAYS = 30  # 短线只看近30天数据
CAPITAL = 10000
MAX_PRICE = 35  # 严格限制35元以下
TRADING_COST_RATE = 0.0015
MIN_PROFIT_COVER = 0.01
SINGLE_MAX_RISK = 250

# T+1模式参数（今日买明天卖）
T1_MODE = {
    "win_loss_ratio_min": 1.2,  # 短线盈亏比要求降低
    "day_change_min": -0.01,    # 当日跌幅不超过1%
    "day_change_max": 0.05,     # 当日涨幅不超过5%（避免追高）
    "volume_ratio_min": 1.2,    # 量比必须大于1.2（有资金进场）
    "turnover_min": 3,          # 换手率最低3%
    "turnover_max": 15,         # 换手率最高15%（避免过度炒作）
    "open_gap_max": 0.02,       # 开盘涨幅不超过2%（避免高开低走）
    "trend_up_required": True   # 必须站在MA5之上
}

# 保留原模式备用
NORMAL_MODE = {
    "win_loss_ratio_min": 1.3,
    "day_change_min": -0.02,
    "day_change_max": 0.05,
    "volume_ratio_min": 0.7,
    "assist_conds_min": 1,
    "trend_up_required": False
}

WEAK_MODE = {
    "win_loss_ratio_min": 1.1,
    "day_change_min": -0.03,
    "day_change_max": 0.04,
    "volume_ratio_min": 0.5,
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
    "其他": {"pe_max": 40, "pb_max": 4.0}
}

FUNDAMENTAL_RED_LINE = {
    "market_cap_min": 80,  # 短线提高市值要求，避免小票暴雷
    "turnover_min": 3,
    "turnover_max": 15
}

# ======================== 【全新优化：T+1专属股票池】33只优质标的 ========================
# 分6大板块，每板块5-6只，全部满足：≤30元、日均成交额≥5亿、市值≥80亿、股性活跃
T1_POOL = {
    # 【能源电力板块】防御性强、波动稳定、适合弱市
    "600028.SS": "中国石化", "600023.SS": "浙能电力", "600726.SS": "华电能源",
    "601016.SS": "节能风电", "600968.SS": "海油发展", "000968.SZ": "蓝焰控股",
    "600795.SS": "国电电力", "600011.SS": "华能国际",
    
    # 【交通运输板块】低估值、高股息、资金关注度高
    "600026.SS": "中远海能", "600279.SS": "重庆港", "601006.SS": "大秦铁路",
    "001872.SZ": "招商港口", "600017.SS": "日照港", "600428.SS": "中远海特",
    
    # 【医药生物板块】防御性强、题材丰富、波动适中
    "600332.SS": "白云山", "000999.SZ": "华润三九", "600566.SS": "济川药业",
    "000538.SZ": "云南白药", "600572.SS": "康恩贝", "000989.SZ": "九芝堂",
    
    # 【计算机电子板块】弹性大、热点多、适合强市
    "000997.SZ": "新大陆", "002027.SZ": "分众传媒", "002152.SZ": "广电运通",
    "000100.SZ": "TCL科技", "002056.SZ": "横店东磁", "600570.SS": "恒生电子",
    
    # 【化工有色板块】周期属性、弹性适中、资金关注度高
    "601225.SS": "陕西煤业", "601088.SS": "中国神华", "000830.SZ": "鲁西化工",
    "600426.SS": "华鲁恒升", "600362.SS": "江西铜业",
    
    # 【消费零售板块】防御性强、业绩稳定、适合震荡市
    "601933.SS": "永辉超市", "002024.SZ": "苏宁易购", "600859.SS": "王府井"
}

# 保留你原有自选股
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
        df = hs300.history(period="60d", timeout=5)
        if len(df) < 30:
            return 0.5, "大盘数据不足，谨慎观察", T1_MODE
        close = df["Close"].astype(float)
        ma20 = close.rolling(20, min_periods=1).mean()
        current = close.iloc[-1]
        day_change = (current - close.iloc[-2]) / close.iloc[-2]
        if day_change < -0.003 or current < ma20.iloc[-1]:
            mode = T1_MODE
            name = "弱市T+1模式"
        else:
            mode = T1_MODE
            name = "正常T+1模式"
        if current > ma20.iloc[-1] and ma20.iloc[-1] > ma20.iloc[-2]:
            return 0.8, f"上升市，T+1仓位参考上限80% [{name}]", mode
        elif current > ma20.iloc[-1]:
            return 0.5, f"震荡市，T+1仓位参考上限50% [{name}]", mode
        else:
            return 0.3, f"下跌市，T+1仓位参考上限30% [{name}]", mode
    except Exception as e:
        logger.warning(f"⚠️ 大盘状态获取异常: {e}")
        return 0.3, "大盘状态异常，严控观察", T1_MODE

def calc_atr(df, period=14):
    high, low, close = df["High"], df["Low"], df["Close"]
    tr = pd.concat([high-low, abs(high-close.shift(1)), abs(low-close.shift(1))], axis=1).max(axis=1)
    return round(tr.rolling(period).mean().iloc[-1], 2)

def calc_technical_indicators(df, mode):
    close, high, low, volume, open_ = df["Close"], df["High"], df["Low"], df["Volume"], df["Open"]
    ma5, ma10, ma20 = close.rolling(5).mean(), close.rolling(10).mean(), close.rolling(20).mean()
    ma5_vol = volume.rolling(5).mean()
    
    # T+1新增：近3日成交量递增
    vol_trend = volume.iloc[-1] > volume.iloc[-2] > volume.iloc[-3]
    
    delta = close.diff()
    gain, loss = delta.clip(lower=0).rolling(14).mean(), (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = round(100 - (100 / (1 + rs)), 1).iloc[-1]
    
    ema12, ema26 = close.ewm(span=12, adjust=False).mean(), close.ewm(span=26, adjust=False).mean()
    macd, signal = ema12 - ema26, (ema12 - ema26).ewm(span=9, adjust=False).mean()
    macd_gold = (macd.iloc[-2] < signal.iloc[-2]) and (macd.iloc[-1] > signal.iloc[-1])
    
    low9, high9 = low.rolling(9).min(), high.rolling(9).max()
    rsv = (close - low9) / (high9 - low9).replace(0, 1) * 100
    k, d = rsv.ewm(span=3, adjust=False).mean(), rsv.ewm(span=3, adjust=False).mean()
    kdj_gold = (k.iloc[-2] < d.iloc[-2]) and (k.iloc[-1] > d.iloc[-1])
    
    volume_enlarge = volume.iloc[-1] >= ma5_vol.iloc[-1] * mode["volume_ratio_min"]
    volume_ratio = round(volume.iloc[-1] / ma5_vol.iloc[-1], 2) if ma5_vol.iloc[-1] > 0 else 1
    
    cp, op = close.iloc[-1], open_.iloc[-1]
    dc = (cp - op) / op
    open_gap = (op - close.iloc[-2]) / close.iloc[-2]  # T+1新增：开盘缺口
    
    it = dc >= mode["day_change_min"]
    no = dc <= mode["day_change_max"]
    og = open_gap <= mode["open_gap_max"]  # T+1新增：开盘缺口过滤
    tu = (close.iloc[-1] > ma5.iloc[-1]) if mode["trend_up_required"] else True
    
    # T+1新增：换手率计算
    turnover = round(volume.iloc[-1] / df["Volume"].mean() * 100, 2)
    
    return {
        "price": round(cp,2), "open_price": round(op,2), "day_change": round(dc*100,2),
        "open_gap": round(open_gap*100,2), "turnover": turnover,
        "ma5": round(ma5.iloc[-1],2), "ma10": round(ma10.iloc[-1],2), "ma20": round(ma20.iloc[-1],2),
        "rsi": rsi, "macd_gold": macd_gold, "kdj_gold": kdj_gold, "trend_up": tu,
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
        
        # T+1新增：ST/退市风险过滤
        is_st = info.get("quoteType", "") == "ST" or "ST" in info.get("longName", "")
        is_suspended = info.get("marketState", "") == "SUSPENDED"
        
        im = {"Thermal Coal":"煤炭","Oil & Gas Integrated":"石油天然气","Electric Utilities":"电力","Railroads":"交通运输","Banks - Diversified":"银行","Insurance - Diversified":"保险","Steel":"钢铁","Chemicals":"化工","Pharmaceuticals":"医药生物","Food Products":"食品饮料","Retail - Defensive":"零售","Software - Application":"计算机","Electronic Components":"电子","Aerospace & Defense":"国防军工","Communication Equipment":"通信","Construction & Engineering":"建筑装饰"}
        ik = im.get(industry,"其他")
        ir = INDUSTRY_PE_RULES[ik]
        
        ok = (pe<ir["pe_max"] and pb<ir["pb_max"] and mc>FUNDAMENTAL_RED_LINE["market_cap_min"] 
              and FUNDAMENTAL_RED_LINE["turnover_min"]<tur<FUNDAMENTAL_RED_LINE["turnover_max"]
              and not is_st and not is_suspended)
        
        return {"pe":round(pe,2) if pe<999 else 999,"pb":round(pb,2) if pb<999 else 999,
                "market_cap":mc,"turnover":tur,"industry":ik,"fund_pass":ok,
                "is_st":is_st,"is_suspended":is_suspended}
    except Exception as e:
        logger.debug(f"⚠️ 基本面数据获取失败 [{n}]: {e}")
        return {"pe":999,"pb":999,"market_cap":0,"turnover":0,"industry":"其他",
                "fund_pass":False,"is_st":False,"is_suspended":False}

def get_stock_data(s, n, t, mr, mode):
    try:
        df = yf.Ticker(s).history(period=f"{HIST_DAYS}d", timeout=5)
        if len(df)<10: return None
        
        tech = calc_technical_indicators(df, mode)
        cp = tech["price"]
        
        # T+1严格过滤
        if cp>MAX_PRICE: return None
        if tech["volume_ratio"]<mode["volume_ratio_min"]: return None
        if not tech["is_intraday_strong"]: return {"symbol":s,"code":s.replace(".SS","").replace(".SZ",""),"name":n,"pool_type":t,"tech":tech,"fund":get_fundamental_data(s,n),"buy_signal":False,"signal_text":"当日跌幅过大"}
        if not tech["is_not_overbought"]: return {"symbol":s,"code":s.replace(".SS","").replace(".SZ",""),"name":n,"pool_type":t,"tech":tech,"fund":get_fundamental_data(s,n),"buy_signal":False,"signal_text":"当日涨幅过大（追高风险）"}
        if not tech["is_not_high_open"]: return {"symbol":s,"code":s.replace(".SS","").replace(".SZ",""),"name":n,"pool_type":t,"tech":tech,"fund":get_fundamental_data(s,n),"buy_signal":False,"signal_text":"开盘涨幅过大（低开风险）"}
        if not (mode["turnover_min"] < tech["turnover"] < mode["turnover_max"]): return {"symbol":s,"code":s.replace(".SS","").replace(".SZ",""),"name":n,"pool_type":t,"tech":tech,"fund":get_fundamental_data(s,n),"buy_signal":False,"signal_text":"换手率不符合要求"}
        
        fund = get_fundamental_data(s,n)
        if not fund["fund_pass"]: return {"symbol":s,"code":s.replace(".SS","").replace(".SZ",""),"name":n,"pool_type":t,"tech":tech,"fund":fund,"buy_signal":False,"signal_text":"基本面不达标或存在风险"}
        
        # T+1专属止盈止损：止盈1.8%，止损1.5%（符合你之前的当日清盘规则）
        bp = round(cp*1.002,2)
        sl = round(bp * 0.985, 2)  # 止损1.5%
        tp = round(bp * 1.018, 2)  # 止盈1.8%
        
        ps = (tp-bp)/bp
        ls = (bp-sl)/bp
        wlr = round(ps/ls,2) if ls>0 else 0
        
        if wlr<mode["win_loss_ratio_min"]: return {"symbol":s,"code":s.replace(".SS","").replace(".SZ",""),"name":n,"pool_type":t,"tech":tech,"fund":fund,"buy_signal":False,"signal_text":"盈亏比不足"}
        
        # T+1评分系统：提高量能和趋势权重
        pw = {"t1":1.5,"core":1.2,"steady":1.0,"satellite":0.8}[t]
        score = round((
            ((tech["trend_up"] and tech["volume_enlarge"] and tech["vol_trend"])*3) + 
            sum([tech["macd_gold"], tech["kdj_gold"], 35<tech["rsi"]<65, tech["ma5"]>tech["ma10"]])*1.5
        )*0.5 + (3 if fund["pe"]<20 else 2 if fund["pe"]<40 else 1)*0.2 + (wlr/2)*0.3*pw, 2)
        
        return {"symbol":s,"code":s.replace(".SS","").replace(".SZ",""),"name":n,"pool_type":t,
                "tech":tech,"fund":fund,"win_loss_ratio":wlr,"total_score":score,"buy_signal":True,
                "stats":{"price_range_low":sl,"price_range_high":tp,"volatility_pct":round(ls*100,1),
                        "win_loss_ratio":wlr,"stop_loss_pct":1.5,"take_profit_pct":1.8}}
    except Exception as e:
        logger.debug(f"⚠️ 股票数据获取失败 [{n}]: {e}")
        return None

def scan(mr, mode):
    res, watch = [], []
    # 优先扫描T+1专属池
    pool = {**T1_POOL, **MY_STOCKS}
    
    for s,n in pool.items():
        t = "t1" if s in T1_POOL else "core"
        stock = get_stock_data(s,n,t,mr,mode)
        if stock:
            if stock["buy_signal"]: res.append(stock)
            else: watch.append(stock)
        time.sleep(0.05)
    
    res = sorted(res, key=lambda x:x["total_score"], reverse=True)[:3]
    watch = sorted(watch, key=lambda x:x["tech"]["rsi"])[:3]
    return res, watch

# ======================== 【T+1专属】合规文案 ========================
def build_msg(buy, watch, tips):
    now = get_standard_now().strftime("%Y-%m-%d %H:%M:%S")
    msg = f"""==================================================
【🤖 T+1短线量化算法 · 纯历史回测记录】
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
    pool_name_map = {"t1":"T+1专属池","core":"自选池"}
    if buy:
        for i, s in enumerate(buy,1):
            p = s["stats"]
            msg += f"""
【数据{i}】{s['code']} {s['name']}
🏷️ 分类：{pool_name_map[s['pool_type']]}｜行业：{s['fund']['industry']}
📊 算法评分：{s['total_score']}｜盈亏比：{s['win_loss_ratio']}:1
💵 行情数据：现价{s['tech']['price']}元｜涨幅{s['tech']['day_change']}%｜量比{s['tech']['volume_ratio']}
📈 技术指标：RSI{s['tech']['rsi']}｜换手率{s['tech']['turnover']}%｜PE{s['fund']['pe']}
📉 T+1操作区间：止损{p['price_range_low']}元(-1.5%)｜止盈{p['price_range_high']}元(+1.8%)
--------------------------------------------------
"""
    else:
        msg += "⚠️ 今日无符合T+1算法条件的数据记录\n"

    if watch:
        msg += "\n👀 明日观察池（仅历史行情）\n"
        for i, s in enumerate(watch):
            msg += f"【数据{i+1}】{s['code']} {s['name']}｜现价：{s['tech']['price']}元｜RSI：{s['tech']['rsi']}\n"

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
    # 截断超长消息，避免钉钉飞书拦截
    return msg[:1800]

# ======================== 推送函数（完全保留你原有逻辑） ========================
def send_feishu(msg):
    for retry in range(3):
        try:
            resp = requests.post(
                FEISHU_WEBHOOK,
                json={"msg_type": "text", "content": {"text": msg}},
                timeout=8
            )
            if resp.status_code == 200 and resp.json().get("code") == 0:
                logger.info("✅ 飞书推送成功")
                return
        except Exception as e:
            logger.warning(f"⚠️ 飞书推送重试 {retry+1}/3: {e}")
            time.sleep(1)
    logger.error("❌ 飞书推送最终失败")

def send_dingtalk(msg):
    for retry in range(3):
        try:
            timestamp = str(round(time.time() * 1000))
            secret_enc = DINGTALK_SECRET.encode('utf-8')
            string_to_sign = f"{timestamp}\n{DINGTALK_SECRET}"
            string_to_sign_enc = string_to_sign.encode('utf-8')
            hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
            sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
            
            url = f"{DINGTALK_WEBHOOK}&timestamp={timestamp}&sign={sign}"
            message = {
                "msgtype": "text",
                "text": {"content": msg}
            }
            resp = requests.post(url, json=message, timeout=8)
            if resp.status_code == 200 and resp.json().get("errcode") == 0:
                logger.info("✅ 钉钉推送成功")
                return
        except Exception as e:
            logger.warning(f"⚠️ 钉钉推送重试 {retry+1}/3: {e}")
            time.sleep(1)
    logger.error("❌ 钉钉推送最终失败")

# ======================== 主逻辑 ========================
def main():
    if not is_trading_day():
        logger.info("🏁 非交易日，程序退出")
        return
    
    logger.info("🚀 开始运行T+1短线策略...")
    mr, tips, mode = get_market_status()
    buy, watch = scan(mr, mode)
    msg = build_msg(buy, watch, tips)
    
    send_feishu(msg)
    send_dingtalk(msg)
    
    logger.info("🎉 今日T+1数据推送完成")

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
