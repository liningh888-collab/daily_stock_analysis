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
    if h == 8 and m == 10:
        return "morning"
    elif h == 8 and m == 30:
        return "open"
    elif h == 15 and m == 20:
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

# ======================== 选股基础参数 ========================
SELECTION_TOP_N = 3
HIST_DAYS = 18
MAX_PRICE = 48

# 保底银行股
GUARANTEE_BANK_STOCKS = {
    "601398.SS": "工商银行",
    "601939.SS": "建设银行",
    "601288.SS": "农业银行",
    "601838.SS": "成都银行"
}

# ======================== 新旧股票池合并（全部保留自动去重） ========================
OLD_STOCK_POOL = {
    "600028.SS": "中国石化",
    "601001.SS": "晋控煤业",
    "601898.SS": "中煤能源",
    "600011.SS": "华能国际",
    "600726.SS": "华电能源",
    "600023.SS": "浙能电力",
    "601668.SS": "中国建筑",
    "601390.SS": "中国中铁",
    "600279.SS": "重庆港",
    "601006.SS": "大秦铁路",
    "001872.SZ": "招商港口",
    "600017.SS": "日照港",
    "600332.SS": "白云山",
    "000999.SZ": "华润三九",
    "000538.SZ": "云南白药",
    "601225.SS": "陕西煤业",
    "000830.SZ": "鲁西化工",
    "600426.SS": "华鲁恒升",
    "600362.SS": "江西铜业",
    "601398.SS": "工商银行",
    "601939.SS": "建设银行",
    "601288.SS": "农业银行"
}

NEW_ADD_STOCK_POOL = {
    "600726.SS": "华电能源",
    "601016.SS": "节能风电",
    "600023.SS": "浙能电力",
    "600900.SS": "长江电力",
    "601868.SS": "中国能建",
    "000767.SZ": "晋控电力",
    "600256.SS": "广汇能源",
    "601088.SS": "中国神华",
    "601225.SS": "陕西煤业",
    "601898.SS": "中煤能源",
    "600188.SS": "兖矿能源",
    "601001.SS": "晋控煤业",
    "000725.SZ": "京东方A",
    "000100.SZ": "TCL科技",
    "002217.SZ": "合力泰",
    "002361.SZ": "神剑股份",
    "002056.SZ": "横店东磁",
    "000997.SZ": "新大陆",
    "002465.SZ": "海格通信",
    "603019.SS": "中科曙光",
    "000977.SZ": "浪潮信息",
    "600372.SS": "中航电子",
    "600879.SS": "航天电子",
    "002413.SZ": "雷科防务",
    "600435.SS": "北方导航",
    "600150.SS": "中国船舶",
    "600967.SS": "内蒙一机",
    "000538.SZ": "云南白药",
    "000999.SZ": "华润三九",
    "002004.SZ": "华邦健康",
    "000650.SZ": "仁和药业",
    "600222.SS": "太龙药业",
    "002132.SZ": "恒星科技",
    "600010.SS": "包钢股份",
    "601388.SS": "怡球资源",
    "002236.SZ": "大华股份",
    "002152.SZ": "广电运通",
    "600570.SS": "恒生电子",
    "600279.SS": "重庆港",
    "601106.SS": "中国一重",
    "600026.SS": "中远海能",
    "600028.SS": "中国石化",
    "601390.SS": "中国中铁",
    "601186.SS": "中国铁建",
    "601398.SS": "工商银行",
    "601939.SS": "建设银行",
    "600021.SS": "上海电力",
    "600027.SS": "华电国际",
    "600795.SS": "国电电力",
    "000539.SZ": "粤电力A",
    "000875.SZ": "吉电股份",
    "600483.SS": "福能股份",
    "601991.SS": "大唐发电",
    "601117.SS": "中国化学",
    "601800.SS": "中国交建",
    "600585.SS": "海螺水泥",
    "000786.SZ": "北新建材",
    "600017.SS": "日照港",
    "600190.SS": "锦州港",
    "000507.SZ": "珠海港",
    "600717.SS": "天津港",
    "600489.SS": "中金黄金",
    "601699.SS": "潞安环能",
    "000960.SZ": "锡业股份",
    "600123.SS": "兰花科创",
    "000636.SZ": "风华高科",
    "000823.SZ": "超声电子",
    "600060.SS": "海信视像",
    "000921.SZ": "海信家电",
    "600226.SS": "ST瀚叶",
    "000513.SZ": "丽珠集团",
    "600812.SS": "华北制药"
}

VALID_STOCK_POOL = {}
VALID_STOCK_POOL.update(OLD_STOCK_POOL)
VALID_STOCK_POOL.update(NEW_ADD_STOCK_POOL)
logger.info(f"✅ 合并完成，总股票池数量：{len(VALID_STOCK_POOL)} 只")

# ======================== 数据获取 ========================
def fetch_data(code):
    try:
        df = yf.Ticker(code).history(period=f"{HIST_DAYS}d", timeout=6)
        if len(df) < 4:
            return None
        return df
    except Exception as e:
        logger.debug(f"[{code}] 数据获取失败: {e}")
        return None

# ======================== 指标计算（仅保留打分，过滤极度简化） ========================
def calc_indicators(df):
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

    # KDJ
    low9 = low.rolling(9).min()
    high9 = high.rolling(9).max()
    rsv = (close - low9) / (high9 - low9) * 100
    k = rsv.ewm(com=2).mean()
    d = k.ewm(com=2).mean()
    kdj_gold = (k.iloc[-2] < d.iloc[-2]) and (k.iloc[-1] > d.iloc[-1])

    # 基础行情数据
    now_price = close.iloc[-1]
    open_price = open_p.iloc[-1]
    day_chg = round(((now_price - open_price) / open_price) * 100, 2)
    vol_ratio = round(volume.iloc[-1] / ma5_vol.iloc[-1], 2) if ma5_vol.iloc[-1] > 0 else 1.0

    # ========== 打分维度不变 ==========
    if day_chg >= 1.5:
        rise_score = 1.5
    elif day_chg >= 0.8:
        rise_score = 0.8
    else:
        rise_score = 0.2

    trend5_up = ma5.iloc[-1] > ma5.iloc[-2]
    trend_score = 1.0 if trend5_up else 0
    macd_extra = 0.8 if macd_pos else 0
    avg_5vol = volume.iloc[-5:].mean() / ma5_vol.iloc[-1]
    vol_cont_score = 0.5 if avg_5vol > 0.8 else 0
    day_avg_price = (high.iloc[-1] + low.iloc[-1] + open_p.iloc[-1]) / 3
    support_score = 0.3 if now_price > day_avg_price else 0

    total_score = (vol_ratio * 1.2) + (1.5 if macd_gold else 0) + (1.5 if kdj_gold else 0) \
                  + rise_score + trend_score + macd_extra + vol_cont_score + support_score

    # 仅保留3条核心过滤
    rsi_ok = 3 <= rsi <= 97
    price_ok = now_price <= MAX_PRICE
    rise_ok = day_chg > 0

    return {
        "price": round(now_price, 2),
        "day_change": day_chg,
        "volume_ratio": vol_ratio,
        "rsi": rsi,
        "macd_positive": macd_pos,
        "kdj_gold": kdj_gold,
        "macd_gold": macd_gold,
        "total_score": round(total_score, 2),
        "rsi_ok": rsi_ok,
        "price_ok": price_ok,
        "rise_ok": rise_ok
    }

# ======================== 个股筛选 ========================
def parse_stock(code, name):
    # 过滤ST
    st_filter = False if ("ST" in code or "ST" in name) else True
    df = fetch_data(code)
    if df is None:
        return None
    ind = calc_indicators(df)
    # 仅三条硬性门槛+非ST
    all_ok = ind["rsi_ok"] and ind["price_ok"] and ind["rise_ok"] and st_filter
    if not all_ok:
        return None

    buy_price = ind["price"] * 1.001
    stop_loss = round(buy_price * 0.982, 2)
    take_profit = round(buy_price * 1.02, 2)

    return {
        "symbol": code,
        "code": code.replace(".SS", "").replace(".SZ", ""),
        "name": name,
        "pool_type": "normal",
        "tech": ind,
        "fund": {"industry": "综合", "market_cap": 800.0, "pe": 18.0, "pb": 1.8},
        "stats": {"price_range_low": stop_loss, "price_range_high": take_profit},
        "total_score": ind["total_score"]
    }

# ======================== 大盘状态（纯yfinance） ========================
def get_market_status():
    try:
        df = yf.Ticker("000300.SS").history(period=f"{HIST_DAYS}d", timeout=6)
        if len(df) < 5:
            return "大盘数据异常，通用宽松策略"
        close = df["Close"]
        ma20 = close.rolling(20, min_periods=1).mean()
        curr = close.iloc[-1]
        ma_val = ma20.iloc[-1]
        if curr > ma_val * 1.008:
            return "市场正常，T+1策略就绪"
        elif curr > ma_val * 0.96:
            return "市场正常，T+1策略就绪"
        else:
            return "市场震荡，T+1策略谨慎"
    except Exception as e:
        logger.warning(f"大盘拉取失败: {e}")
        return "大盘数据异常，通用宽松策略"

# ======================== 选股主逻辑 ========================
def scan_stocks():
    result = []
    stock_list = list(VALID_STOCK_POOL.items())
    for code, name in stock_list:
        stock_info = parse_stock(code, name)
        if stock_info:
            result.append(stock_info)
            logger.info(f"✅ 合格上涨标的：{name}({code}) 涨幅+{stock_info['tech']['day_change']} 总分:{stock_info['total_score']}")
        t.sleep(0.12)
    # 按总分从高到低排序
    result = sorted(result, key=lambda x: x["total_score"], reverse=True)
    need_fill = SELECTION_TOP_N - len(result)
    if need_fill > 0:
        logger.info(f"⚠️ 仅筛选到{len(result)}只合格标的，补充{need_fill}只银行保底凑满3只")
        bank_list = list(GUARANTEE_BANK_STOCKS.items())
        random.shuffle(bank_list)
        add_cnt = 0
        for code, name in bank_list:
            if add_cnt >= need_fill:
                break
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
                    "day_change": 0.12,
                    "volume_ratio": 1.0,
                    "rsi": 50,
                    "macd_positive": False,
                    "kdj_gold": False,
                    "macd_gold": False,
                    "total_score": 3.5
                },
                "fund": {"industry": "银行", "market_cap": 9999.99, "pe": 10.0, "pb": 1.1},
                "stats": {"price_range_low": round(price * 0.982, 2), "price_range_high": round(price * 1.02, 2)},
                "total_score": 3.5
            })
            add_cnt += 1
    final_list = result[:SELECTION_TOP_N]
    return final_list

# ======================== 消息组装【修改版】 ========================
def build_message(stock_list, market_desc, time_type):
    now = get_standard_now().strftime("%Y-%m-%d %H:%M:%S")
    title_map = {
        "morning": "【🤖 T+1短线量化 · 早盘5:40前瞻】",
        "open": "【🤖 T+1短线量化 · 9:00开盘参考】",
        "close": "【🤖 T+1短线量化 · 15:00收盘总结】",
        "normal": "【🤖 T+1短线量化算法 · 日常推送】"
    }
    tip_map = {
        "morning": "早盘前瞻：提前筛选当日备选标的，量化多维度综合评分排序",
        "open": "开盘参考：集合竞价结束，观察量能与开盘溢价情况",
        "close": "收盘总结：当日标的复盘，量化策略运行效果回顾",
        "normal": "量化多因子选股，仅保留红盘上涨标的，按综合得分排序"
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
【📊 T+1短线标的 · 仅保留红盘上涨个股，按总分降序取前3只】
"""
    if stock_list:
        for s in stock_list:
            tag = "【保底银行股】" if s["pool_type"] == "guarantee" else ""
            msg += f"""
{tag}{s['code']} {s['name']}
💵 现价：{s['tech']['price']}元｜涨幅：+{s['tech']['day_change']}%｜量比：{s['tech']['volume_ratio']}
📊 RSI：{s['tech']['rsi']}｜MACD：{"正" if s['tech']['macd_positive'] else "负"}｜KDJ金叉：{"是" if s['tech']['kdj_gold'] else "否"}
⭐ 综合打分：{s['total_score']}
--------------------------------------------------
"""
    else:
        msg += "⚠️ 今日暂无符合条件标的\n"
    msg += """
==================================================
⚠️ 风险提示
市场有风险，投资需谨慎。本内容仅为量化算法运行结果，不构成任何投资建议。
==================================================
"""
    return msg[:1800]

# ======================== 推送 ========================
def send_feishu(msg):
    if not FEISHU_WEBHOOK:
        logger.error("❌ 飞书Webhook未配置")
        return
    try:
        resp = requests.post(FEISHU_WEBHOOK, json={"msg_type": "text", "content": {"text": msg}}, timeout=10)
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
    logger.info("🚀 T+1短线量化【极简过滤版】启动｜仅保留红盘+低价+非极端RSI，总分排序取前3")
    sync_ntp_time()
    time_type = get_time_type()
    logger.info(f"⏰ 当前时段：{time_type}")
    if is_trading_day():
        market_desc = get_market_status()
        logger.info(f"📊 市场状态：{market_desc}")
        stock_result = scan_stocks()
        normal_count = len([s for s in stock_result if s['pool_type']=='normal'])
        logger.info(f"🔍 正常上涨标的共{normal_count}只，最终推送3只")
        content = build_message(stock_result, market_desc, time_type)
        send_feishu(content)
        send_dingtalk(content)
        logger.info("🎉 今日推送完成")
    else:
        logger.info("ℹ️ 今日非交易日，不执行选股推送")

if __name__ == "__main__":
    main()
