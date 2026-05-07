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

# ======================== 全局配置 ========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 飞书 Webhook
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/7e8c7d35-382e-43de-8479-0434921d338c"

# 钉钉配置（你可用的）
DINGTALK_WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=8cd6832317216fdfaca1d2acba57c11e3024f20921365804ba96444f7945b949"
DINGTALK_SECRET = "SECf67646ed7edca294f7575a5bca513ba7de5c00dffe1ce5750da3175fd8fcdddc"

# ======================== 核心参数 ========================
SELECTION_TOP_N = 3
HIST_DAYS = 90
CAPITAL = 10000
MAX_PRICE = 50
TRADING_COST_RATE = 0.0015
MIN_PROFIT_COVER = 0.01
SINGLE_MAX_RISK = 250

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

# ======================== 行业估值 ========================
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
    "market_cap_min": 30,
    "turnover_min": 0.1,
    "turnover_max": 30
}

# ======================== 股票池 ========================
CORE_POOL = {
    "601398.SS": "工商银行", "601939.SS": "建设银行", "601288.SS": "农业银行",
    "601328.SS": "交通银行", "601166.SS": "兴业银行", "600919.SS": "江苏银行",
    "601838.SS": "成都银行", "601088.SS": "中国神华", "601225.SS": "陕西煤业",
    "600028.SS": "中国石化", "600900.SS": "长江电力", "600023.SS": "浙能电力",
    "601006.SS": "大秦铁路", "601668.SS": "中国建筑", "601390.SS": "中国中铁",
    "601186.SS": "中国铁建", "601868.SS": "中国能建", "601898.SS": "中煤能源",
    "600188.SS": "兖矿能源", "601001.SS": "晋控煤业", "600642.SS": "申能股份",
    "600015.SS": "华夏银行"
}

STEADY_POOL = {
    "000538.SZ": "云南白药", "600332.SS": "白云山", "000999.SZ": "华润三九",
    "600566.SS": "济川药业", "000623.SZ": "吉林敖东", "000028.SZ": "国药一致",
    "002236.SZ": "大华股份", "002027.SZ": "分众传媒", "002555.SZ": "三七互娱",
    "002152.SZ": "广电运通", "600867.SS": "通化东宝", "002004.SZ": "华邦健康",
    "000650.SZ": "仁和药业", "300498.SZ": "温氏股份", "300705.SZ": "九典制药",
    "600572.SS": "康恩贝", "000989.SZ": "九芝堂", "600252.SS": "中恒集团",
    "300026.SZ": "红日药业", "600222.SS": "太龙药业", "002183.SZ": "怡亚通",
    "600420.SS": "现代制药"
}

SATELLITE_POOL = {
    "000100.SZ": "TCL科技", "002056.SZ": "横店东磁", "000997.SZ": "新大陆",
    "002465.SZ": "海格通信", "600562.SS": "国睿科技", "600570.SS": "恒生电子",
    "603019.SS": "中科曙光", "000977.SZ": "浪潮信息", "600372.SS": "中航电子",
    "002382.SZ": "蓝帆医疗", "600879.SS": "航天电子", "002413.SZ": "雷科防务",
    "002297.SZ": "博云新材", "600435.SS": "北方导航", "600150.SS": "中国重工",
    "300008.SZ": "天海防务", "600967.SS": "内蒙一机", "600279.SS": "重庆港",
    "601106.SS": "中国一重", "601388.SS": "怡球资源",
    "000968.SZ": "蓝焰控股", "600759.SS": "洲际油气", "601857.SS": "中国石油",
    "600026.SS": "中远海能", "600918.SS": "中泰股份", "002496.SZ": "辉丰股份"
}

MY_STOCKS = {
    "600726.SS": "华电能源", "601016.SS": "节能风电", "600023.SS": "浙能电力",
    "600028.SS": "中国石化", "600968.SS": "海油发展", "000968.SZ": "蓝焰控股",
    "600759.SS": "洲际油气", "002132.SZ": "恒星科技"
}

# ======================== 工具 ========================
def is_trading_day():
    today = datetime.now()
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
        df = hs300.history(period="60d", timeout=10)
        if len(df) < 30:
            return 0.5, "大盘数据不足，谨慎观察", NORMAL_MODE
        close = df["Close"].astype(float)
        ma20 = close.rolling(20, min_periods=1).mean()
        current = close.iloc[-1]
        day_change = (current - close.iloc[-2]) / close.iloc[-2]
        if day_change < -0.003 or current < ma20.iloc[-1]:
            mode = WEAK_MODE
            name = "弱市模式"
        else:
            mode = NORMAL_MODE
            name = "正常模式"
        if current > ma20.iloc[-1] and ma20.iloc[-1] > ma20.iloc[-2]:
            return 0.8, f"上升市，模型仓位参考上限80% [{name}]", mode
        elif current > ma20.iloc[-1]:
            return 0.5, f"震荡市，模型仓位参考上限50% [{name}]", mode
        else:
            return 0.3, f"下跌市，模型仓位参考上限30% [{name}]", mode
    except:
        return 0.3, "大盘状态异常，严控观察", WEAK_MODE

def calc_atr(df, period=14):
    high, low, close = df["High"], df["Low"], df["Close"]
    tr = pd.concat([high-low, abs(high-close.shift(1)), abs(low-close.shift(1))], axis=1).max(axis=1)
    return round(tr.rolling(period).mean().iloc[-1], 2)

def calc_technical_indicators(df, mode):
    close, high, low, volume, open_ = df["Close"], df["High"], df["Low"], df["Volume"], df["Open"]
    ma5, ma10, ma20, ma60 = close.rolling(5).mean(), close.rolling(10).mean(), close.rolling(20).mean(), close.rolling(60).mean()
    ma5_vol = volume.rolling(5).mean()
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
    volume_enlarge = volume.iloc[-3:].max() >= ma5_vol.iloc[-1] * 1.2
    volume_ratio = round(volume.iloc[-1] / ma5_vol.iloc[-1], 2) if ma5_vol.iloc[-1] > 0 else 1
    cp, op = close.iloc[-1], open_.iloc[-1]
    dc = (cp - op) / op
    it = dc >= mode["day_change_min"]
    no = dc <= mode["day_change_max"]
    tu = (close.iloc[-1] > ma20.iloc[-1] and close.iloc[-1] > ma60.iloc[-1]) if mode["trend_up_required"] else True
    return {
        "price": round(cp,2), "open_price": round(op,2), "day_change": round(dc*100,2),
        "ma5": round(ma5.iloc[-1],2), "ma10": round(ma10.iloc[-1],2), "ma20": round(ma20.iloc[-1],2), "ma60": round(ma60.iloc[-1],2),
        "rsi": rsi, "macd_gold": macd_gold, "kdj_gold": kdj_gold, "trend_up": tu,
        "volume_enlarge": volume_enlarge, "volume_ratio": volume_ratio, "atr": calc_atr(df),
        "is_intraday_strong": it, "is_not_overbought": no
    }

def get_fundamental_data(s, n):
    try:
        info = yf.Ticker(s).info
        pe = info.get("trailingPE",999)
        pb = info.get("priceToBook",999)
        mc = round(info.get("marketCap",0)/1e8,2)
        tur = round(info.get("averageVolume10days",0)/info.get("sharesOutstanding",1)*100,2) if info.get("sharesOutstanding") else 1
        industry = info.get("industry","Other")
        im = {"Thermal Coal":"煤炭","Oil & Gas Integrated":"石油天然气","Electric Utilities":"电力","Railroads":"交通运输","Banks - Diversified":"银行","Insurance - Diversified":"保险","Steel":"钢铁","Chemicals":"化工","Pharmaceuticals":"医药生物","Food Products":"食品饮料","Retail - Defensive":"零售","Software - Application":"计算机","Electronic Components":"电子","Aerospace & Defense":"国防军工","Communication Equipment":"通信","Construction & Engineering":"建筑装饰"}
        ik = im.get(industry,"其他")
        ir = INDUSTRY_PE_RULES[ik]
        ok = (pe<ir["pe_max"] and pb<ir["pb_max"] and mc>FUNDAMENTAL_RED_LINE["market_cap_min"] and FUNDAMENTAL_RED_LINE["turnover_min"]<tur<FUNDAMENTAL_RED_LINE["turnover_max"])
        return {"pe":round(pe,2) if pe<999 else 999,"pb":round(pb,2) if pb<999 else 999,"market_cap":mc,"turnover":tur,"industry":ik,"fund_pass":ok}
    except:
        return {"pe":999,"pb":999,"market_cap":0,"turnover":0,"industry":"其他","fund_pass":False}

def get_stock_data(s, n, t, mr, mode):
    try:
        df = yf.Ticker(s).history(period=f"{HIST_DAYS}d", timeout=10)
        if len(df)<20: return None
        tech = calc_technical_indicators(df, mode)
        cp = tech["price"]
        if cp>MAX_PRICE: return None
        if tech["volume_ratio"]<mode["volume_ratio_min"]: return None
        if not tech["is_intraday_strong"]: return None
        if not tech["is_not_overbought"]: return {"symbol":s,"code":s.replace(".SS","").replace(".SZ",""),"name":n,"pool_type":t,"tech":tech,"fund":get_fundamental_data(s,n),"buy_signal":False,"signal_text":"涨幅过大"}
        fund = get_fundamental_data(s,n)
        bp = round(cp*1.002,2)
        sl = round(bp - tech["atr"]*1.8,2)
        sl = max(sl, round(bp*0.955,2))
        tp = round(bp + tech["atr"]*4.2,2)
        tp = min(tp, round(bp*1.15,2))
        ps = (tp-bp)/bp
        ls = (bp-sl)/bp
        wlr = round(ps/ls,2) if ls>0 else 0
        if wlr<mode["win_loss_ratio_min"]: return {"symbol":s,"code":s.replace(".SS","").replace(".SZ",""),"name":n,"pool_type":t,"tech":tech,"fund":fund,"buy_signal":False,"signal_text":"盈亏比不足"}
        pw = {"core":1.5,"steady":1.2,"satellite":1.0}[t]
        score = round((((tech["trend_up"] and tech["volume_enlarge"])*2.5)+sum([tech["macd_gold"],tech["kdj_gold"],30<tech["rsi"]<70,tech["ma5"]>tech["ma10"]])*1.2)*0.45 + (3 if fund["pe"]<15 else 2 if fund["pe"]<30 else 1)*0.25 + (wlr/4)*0.3*pw,2)
        return {"symbol":s,"code":s.replace(".SS","").replace(".SZ",""),"name":n,"pool_type":t,"tech":tech,"fund":fund,"win_loss_ratio":wlr,"total_score":score,"buy_signal":True,"stats":{"price_range_low":sl,"price_range_high":tp,"volatility_pct":round(ls*100,1),"win_loss_ratio":wlr}}
    except:
        return None

def scan(mr, mode):
    res, watch = [], []
    pool = {**MY_STOCKS,**CORE_POOL,**STEADY_POOL,**SATELLITE_POOL}
    for s,n in pool.items():
        t = "core" if s in CORE_POOL or s in MY_STOCKS else "steady" if s in STEADY_POOL else "satellite"
        stock = get_stock_data(s,n,t,mr,mode)
        if stock:
            if stock["buy_signal"]: res.append(stock)
            else: watch.append(stock)
        time.sleep(0.2)
    res = sorted(res, key=lambda x:x["total_score"], reverse=True)[:3]
    watch = sorted(watch, key=lambda x:x["tech"]["rsi"])[:3]
    return res, watch

# ======================== 合规文案 ========================
def build_msg(buy, watch, tips):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    msg = f"""⚠️【终极免责&圈层服务说明】
1. 本圈层收取的是大数据算力使用费、机器人算法运行成本、圈层准入门槛费，绝非证券投资咨询费、荐股费、交易指导费。
2. 以下所有内容均为Python量化程序全自动无人工干预爬取公开行情、算法运算输出，
   仅作圈层内部技术学习、量化模型逻辑复盘交流使用，不构成任何投资建议、个股推荐、买卖点位指导。
3. 本人无任何证券投资咨询从业资质，不开展投顾业务，不承诺收益、不保证胜率，历史数据不代表未来走势。
4. 所有展示的股票代码、名称、价格、指标、区间仅为程序原始数据记录，禁止对外转发、禁止跟单实盘操作，
   任何人自行据此交易盈亏自负，与本人及圈层无关。
5. 股市有风险，投资需谨慎，圈层内严禁询问个股买卖、止损止盈等操作类问题。

📊 大数据AI算力量化模型日报（圈层专属）
📅 {now}
📊 大盘状态：{tips}
==================================================
📈 模型自动筛选标的（程序原始数据记录，非人工推荐）
"""
    pool_name_map = {"core":"核心防御池","steady":"稳健成长池","satellite":"弹性卫星池"}
    if buy:
        for i, s in enumerate(buy,1):
            p = s["stats"]
            msg += f"""
【{i}】{s['code']} {s['name']}
🏷️ 池：{pool_name_map[s['pool_type']]}｜行业：{s['fund']['industry']}｜评分：{s['total_score']}｜盈亏比：{s['win_loss_ratio']}:1
💵 现价：{s['tech']['price']}元｜涨幅：{s['tech']['day_change']}%｜量比：{s['tech']['volume_ratio']}

📈 指标：
趋势向上：是｜放量：是｜日内强势：是
MACD金叉：{'是' if s['tech']['macd_gold'] else '否'}｜KDJ金叉：{'是' if s['tech']['kdj_gold'] else '否'}
RSI：{s['tech']['rsi']}｜MA5>MA10：{'是' if s['tech']['ma5']>s['tech']['ma10'] else '否'}

📊 基本面：PE：{s['fund']['pe']}｜PB：{s['fund']['pb']}｜市值：{s['fund']['market_cap']}亿
📉 模型区间：{p['price_range_low']} ~ {p['price_range_high']} 元
--------------------------------------------------
"""
    else:
        msg += "⚠️ 今日无符合模型条件标的\n"

    if watch:
        msg += "\n👀 观察池\n"
        for i, s in enumerate(watch):
            msg += f"【{i+1}】{s['code']} {s['name']}｜现价：{s['tech']['price']}元｜RSI：{s['tech']['rsi']}\n"

    msg += """
==================================================
💡 圈层规则重申：
1. 付费仅为大数据算力+圈层准入服务，不属于证券投顾服务
2. 所有内容为程序自动输出，无人工荐股、无操作指导
3. 禁止跟单、禁止实盘依据、禁止对外转发
"""
    return msg

# ======================== 推送 ========================
def send_feishu(msg):
    try:
        requests.post(FEISHU_WEBHOOK, json={"msg_type":"text","content":{"text":msg}}, timeout=10)
        logger.info("✅ 飞书推送成功")
    except Exception as e:
        logger.error(f"❌ 飞书失败：{e}")

def send_dingtalk(msg):
    try:
        timestamp = str(round(time.time() * 1000))
        secret_enc = DINGTALK_SECRET.encode('utf-8')
        string_to_sign = '{}\n{}'.format(timestamp, DINGTALK_SECRET)
        string_to_sign_enc = string_to_sign.encode('utf-8')
        hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        url = f"{DINGTALK_WEBHOOK}&timestamp={timestamp}&sign={sign}"
        
        message = {
            "msgtype": "text",
            "text": {"content": msg}
        }
        resp = requests.post(url, json=message)
        logger.info("✅ 钉钉推送成功")
    except Exception as e:
        logger.error(f"❌ 钉钉失败：{e}")

# ======================== 主程序 ========================
def main():
    if not is_trading_day():
        return
    mr, tips, mode = get_market_status()
    buy, watch = scan(mr, mode)
    msg = build_msg(buy, watch, tips)
    send_feishu(msg)
    send_dingtalk(msg)
    logger.info("🎉 全部推送完成")

if __name__ == "__main__":
    main()
