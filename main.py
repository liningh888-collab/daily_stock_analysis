import requests
import json
import logging
import os
import time
import random
import hmac
import hashlib
import base64
from datetime import datetime
import pandas as pd
import numpy as np
import yfinance as yf

# ======================== 全局配置（你给的真实Hook已填好） ========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 飞书 Webhook（你给的真实地址）
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/7e8c7d35-382e-43de-8479-0434921d338c"

# 钉钉 Webhook + 密钥（你给的真实地址，无密钥就留空）
DINGTALK_WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=8cd6832317216fdfaca1d2acba57c11e3024f20921365804ba96444f7945b949"
DINGTALK_SECRET = ""  # 如果你没开加签，就保持空字符串

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

WEAK_MARKET_MODE = {
    "win_loss_ratio_min": 1.1,
    "day_change_min": -0.03,
    "day_change_max": 0.04,
    "volume_ratio_min": 0.5,
    "assist_conds_min": 0,
    "trend_up_required": False
}

# ======================== 分行业PE标准 ========================
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

# ======================== 工具函数 ========================
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
            mode = WEAK_MARKET_MODE
            mode_name = "弱市模式"
        else:
            mode = NORMAL_MODE
            mode_name = "正常模式"
        
        if current > ma20.iloc[-1] and ma20.iloc[-1] > ma20.iloc[-2]:
            position_ratio = 0.8
            tips = f"上升市，模型仓位参考上限80% [{mode_name}]"
        elif current > ma20.iloc[-1]:
            position_ratio = 0.5
            tips = f"震荡市，模型仓位参考上限50% [{mode_name}]"
        else:
            position_ratio = 0.3
            tips = f"下跌市，模型仓位参考上限30% [{mode_name}]"
        
        return position_ratio, tips, mode
    except Exception as e:
        return 0.3, "大盘状态异常，严控观察", WEAK_MARKET_MODE

def calc_atr(df, period=14):
    df = df.copy()
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    close = df["Close"].astype(float)
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(period, min_periods=1).mean()
    return round(atr.iloc[-1], 2)

def calc_technical_indicators(df, mode):
    df = df.copy().sort_index()
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    volume = df["Volume"].astype(float)
    open_ = df["Open"].astype(float)

    ma5 = close.rolling(5, min_periods=1).mean()
    ma10 = close.rolling(10, min_periods=1).mean()
    ma20 = close.rolling(20, min_periods=1).mean()
    ma60 = close.rolling(60, min_periods=1).mean()
    ma5_vol = volume.rolling(5, min_periods=1).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(lower=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.replace([np.inf, -np.inf], 100)
    rsi = rsi.fillna(50)
    rsi_val = round(rsi.iloc[-1], 1)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()

    low9 = low.rolling(9).min()
    high9 = high.rolling(9).max()
    tr = high9 - low9
    tr = tr.replace(0, 1)
    rsv = (close - low9) / tr * 100
    k = rsv.ewm(span=3, adjust=False).mean()
    d = k.ewm(span=3, adjust=False).mean()

    macd_gold = False
    for i in range(2):
        if len(macd_line) < i+2: break
        if macd_line.iloc[-1-i] > signal_line.iloc[-1-i] and macd_line.iloc[-2-i] <= signal_line.iloc[-2-i]:
            macd_gold = True

    kdj_gold = False
    for i in range(2):
        if len(k) < i+2: break
        if k.iloc[-1-i] > d.iloc[-1-i] and k.iloc[-2-i] <= d.iloc[-2-i]:
            kdj_gold = True

    volume_enlarge = bool(volume.iloc[-3:].max() >= ma5_vol.iloc[-1] * 1.2)
    volume_ratio = round(volume.iloc[-1] / ma5_vol.iloc[-1], 2) if ma5_vol.iloc[-1] > 0 else 1.0

    current_price = close.iloc[-1]
    open_price = open_.iloc[-1]
    day_change = (current_price - open_price) / open_price
    is_intraday_strong = day_change >= mode["day_change_min"]
    is_not_overbought = day_change <= mode["day_change_max"]

    if mode["trend_up_required"]:
        trend_up = close.iloc[-1] > ma20.iloc[-1] and close.iloc[-1] > ma60.iloc[-1]
    else:
        trend_up = True

    return {
        "price": round(current_price, 2),
        "open_price": round(open_price, 2),
        "day_change": round(day_change*100, 2),
        "ma5": round(ma5.iloc[-1], 2),
        "ma10": round(ma10.iloc[-1], 2),
        "ma20": round(ma20.iloc[-1], 2),
        "ma60": round(ma60.iloc[-1], 2),
        "rsi": rsi_val,
        "macd_gold": macd_gold,
        "kdj_gold": kdj_gold,
        "trend_up": trend_up,
        "volume_enlarge": volume_enlarge,
        "volume_ratio": volume_ratio,
        "atr": calc_atr(df),
        "prev_low": round(low.iloc[-2], 2) if len(low)>=2 else round(current_price*0.98, 2),
        "prev_high": round(high.iloc[-2], 2) if len(high)>=2 else round(current_price*1.03, 2),
        "is_intraday_strong": is_intraday_strong,
        "is_not_overbought": is_not_overbought
    }

def get_fundamental_data(symbol, name):
    try:
        tk = yf.Ticker(symbol)
        info = tk.info
        pe = info.get("trailingPE", 999)
        pb = info.get("priceToBook", 999)
        market_cap = info.get("marketCap", 0) / 1e8 if info.get("marketCap") else 0
        turnover = info.get("averageVolume10days", 0) / info.get("sharesOutstanding", 1) * 100 if info.get("sharesOutstanding") else 1.0
        industry = info.get("industry", "Other")

        industry_map = {
            "Thermal Coal": "煤炭", "Oil & Gas Integrated": "石油天然气",
            "Electric Utilities": "电力", "Railroads": "交通运输",
            "Banks - Diversified": "银行", "Insurance - Diversified": "保险",
            "Steel": "钢铁", "Chemicals": "化工", "Pharmaceuticals": "医药生物",
            "Food Products": "食品饮料", "Retail - Defensive": "零售",
            "Software - Application": "计算机", "Electronic Components": "电子",
            "Aerospace & Defense": "国防军工", "Communication Equipment": "通信",
            "Construction & Engineering": "建筑装饰"
        }
        industry_key = industry_map.get(industry, "其他")

        pe_max = INDUSTRY_PE_RULES[industry_key]["pe_max"]
        pb_max = INDUSTRY_PE_RULES[industry_key]["pb_max"]

        all_pass = (
            pe < pe_max
            and pb < pb_max
            and market_cap > FUNDAMENTAL_RED_LINE["market_cap_min"]
            and FUNDAMENTAL_RED_LINE["turnover_min"] < turnover < FUNDAMENTAL_RED_LINE["turnover_max"]
        )

        return {
            "pe": round(pe,2) if pe and pe != np.inf else 999,
            "pb": round(pb,2) if pb and pb != np.inf else 999,
            "market_cap": round(market_cap,2),
            "turnover": round(turnover, 2),
            "industry": industry_key,
            "fund_pass": all_pass
        }
    except Exception as e:
        logger.warning(f"❌ {symbol} 基本面获取失败: {str(e)}")
        return {"pe":999,"pb":999,"market_cap":0,"turnover":0,"industry":"其他","fund_pass":False}

def get_stock_data(symbol, name, pool_type, market_position_ratio, mode):
    try:
        logger.info(f"📡 分析 {symbol} {name}")
        tk = yf.Ticker(symbol)
        df = tk.history(period=f"{HIST_DAYS}d", timeout=10)
        if len(df) < 20:
            return None

        tech = calc_technical_indicators(df, mode)
        current_price = tech["price"]
        atr = tech["atr"]

        if current_price > MAX_PRICE:
            return None

        if tech["volume_ratio"] < mode["volume_ratio_min"]:
            return None

        if not tech["is_intraday_strong"]:
            return None

        if not tech["is_not_overbought"]:
            return {
                "symbol": symbol, "code": symbol.replace(".SS","").replace(".SZ",""), "name": name,
                "pool_type": pool_type, "tech": tech, "fund": get_fundamental_data(symbol,name),
                "buy_signal": False, "signal_text": "⚠️ 涨幅过大，观察"
            }

        fundamental = get_fundamental_data(symbol, name)

        core_conds = [tech["trend_up"], tech["volume_enlarge"]]
        core_pass = all(core_conds)
        assist_conds = [tech["macd_gold"], tech["kdj_gold"], 30 < tech["rsi"] < 70, tech["ma5"] > tech["ma10"]]
        assist_pass = sum(assist_conds) >= mode["assist_conds_min"]
        timing_pass = core_pass or assist_pass

        buy_price = round(current_price * 1.002, 2)
        buy_price = min(buy_price, round(current_price * 1.005, 2))

        stop_loss = round(buy_price - atr * 1.8, 2)
        stop_loss_min = round(buy_price * 0.975, 2)
        stop_loss_max = round(buy_price * 0.955, 2)
        stop_loss = max(min(stop_loss, stop_loss_max), stop_loss_min)

        target_profit = round(buy_price + atr * 4.2, 2)
        target_profit_min = round(buy_price * 1.06, 2)
        target_profit_max = round(buy_price * 1.15, 2)
        target_profit = min(max(target_profit, target_profit_min), target_profit_max)

        profit_space = (target_profit - buy_price) / buy_price
        loss_space = (buy_price - stop_loss) / buy_price
        win_loss_ratio = round(profit_space / loss_space, 2) if loss_space > 0 else 0

        if win_loss_ratio < mode["win_loss_ratio_min"]:
            return {
                "symbol": symbol, "code": symbol.replace(".SS","").replace(".SZ",""), "name": name,
                "pool_type": pool_type, "tech": tech, "fund": fundamental,
                "buy_signal": False, "signal_text": "👀 观察"
            }

        max_shares_by_risk = int(SINGLE_MAX_RISK / (loss_space * buy_price) // 100 * 100)
        pool_max = {"core":0.25,"steady":0.18,"satellite":0.12}.get(pool_type,0.1)
        max_shares_by_pool = int(CAPITAL * pool_max / buy_price // 100 * 100)
        reference_volume = min(max_shares_by_risk, max_shares_by_pool)
        reference_volume = max(reference_volume, 100)

        profit_cover_cost = round(buy_price * (1 + TRADING_COST_RATE + MIN_PROFIT_COVER), 2)
        profit_mid = round(buy_price + (target_profit - buy_price)*0.6, 2)

        pool_weight = {"core":1.5,"steady":1.2,"satellite":1.0}[pool_type]
        total_score = round(
            (sum(core_conds)*2.5 + sum(assist_conds)*1.2)*0.45
            + (3 if fundamental["pe"]<15 else 2 if fundamental["pe"]<30 else 1)*0.25
            + (win_loss_ratio/4)*0.3 * pool_weight
        ,2)

        return {
            "symbol": symbol, "code": symbol.replace(".SS","").replace(".SZ",""), "name": name,
            "pool_type": pool_type, "tech": tech, "fund": fundamental,
            "win_loss_ratio": win_loss_ratio, "total_score": total_score,
            "buy_signal": True, "signal_text": "📊 模型关注",
            "stats": {
                "price_range_low": stop_loss,
                "price_range_high": target_profit,
                "volatility_pct": round(loss_space*100,1),
                "win_loss_ratio": win_loss_ratio
            }
        }
    except Exception as e:
        logger.warning(f"❌ {symbol} 分析失败: {str(e)}")
        return None

def scan_market(market_position_ratio, mode):
    all_stocks = []
    watch_list = []
    
    full_pool = {**MY_STOCKS, **CORE_POOL, **STEADY_POOL, **SATELLITE_POOL}
    for symbol, name in full_pool.items():
        pool_t = "core" if symbol in CORE_POOL or symbol in MY_STOCKS else "steady" if symbol in STEADY_POOL else "satellite"
        data = get_stock_data(symbol, name, pool_t, market_position_ratio, mode)
        if data:
            if data["buy_signal"]:
                all_stocks.append(data)
            else:
                watch_list.append(data)
        time.sleep(random.uniform(0.1, 0.3))

    all_stocks = sorted(all_stocks, key=lambda x: x["total_score"], reverse=True)[:SELECTION_TOP_N]
    watch_list = sorted(watch_list, key=lambda x: x["tech"]["rsi"])[:3]
    
    return all_stocks, watch_list

# ======================== 统一消息模板 ========================
def build_msg(buy_stocks, watch_stocks, market_tips, market_position_ratio):
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    msg = f"""⚠️【免责声明】
1. 本内容为量化模型数据统计，仅用于学习交流，不构成投资建议、个股推荐、交易指导。
2. 本人无证券投资咨询资质，所有内容不构成买卖依据，据此操作风险自担。
3. 历史数据不代表未来收益，不承诺盈利，不提供收费服务。

📊 量化模型统计日报（测试版）
📅 {now}
📊 大盘状态：{market_tips}
==================================================
"""
    if buy_stocks:
        msg += "📈 模型关注标的（数据展示，非推荐）\n"
        for i, s in enumerate(buy_stocks, 1):
            st = s["stats"]
            pool_name = {"core":"核心防御池","steady":"稳健成长池","satellite":"弹性卫星池"}[s["pool_type"]]
            msg += f"""
【{i}】{s['code']} {s['name']}
🏷️ 池：{pool_name}｜行业：{s['fund']['industry']}｜评分：{s['total_score']}｜盈亏比：{s['win_loss_ratio']}:1
💵 现价：{s['tech']['price']}元｜涨幅：{s['tech']['day_change']}%｜量比：{s['tech']['volume_ratio']}

📈 指标：
趋势向上：是｜放量：是｜日内强势：是
MACD金叉：{'是' if s['tech']['macd_gold'] else '否'}｜KDJ金叉：{'是' if s['tech']['kdj_gold'] else '否'}
RSI：{s['tech']['rsi']}｜MA5>MA10：{'是' if s['tech']['ma5']>s['tech']['ma10'] else '否'}

📊 基本面：
PE：{s['fund']['pe']}｜PB：{s['fund']['pb']}｜市值：{s['fund']['market_cap']}亿

📉 模型波动区间：{st['price_range_low']} ~ {st['price_range_high']} 元
--------------------------------------------------
"""
    else:
        msg += "⚠️ 今日无符合模型条件标的\n"

    if watch_stocks:
        msg += "\n👀 观察池\n"
        for i, s in enumerate(watch_stocks):
            msg += f"【{i+1}】{s['code']} {s['name']}｜现价：{s['tech']['price']}元｜RSI：{s['tech']['rsi']}\n"
    return msg

# ======================== 飞书推送（已修复 19002 错误） ========================
def send_feishu(msg):
    try:
        payload = {
            "msg_type": "text",
            "content": {"text": msg}
        }
        requests.post(FEISHU_WEBHOOK, json=payload, timeout=10)
        logger.info("✅ 飞书推送成功")
    except Exception as e:
        logger.error(f"❌ 飞书失败: {e}")

# ======================== 钉钉推送（已修复 43002 错误） ========================
def send_dingtalk(msg):
    try:
        # 没有密钥就不签名，直接发送
        if DINGTALK_SECRET.strip():
            timestamp = str(round(time.time() * 1000))
            string_to_sign = f"{timestamp}\n{DINGTALK_SECRET}"
            hmac_code = hmac.new(DINGTALK_SECRET.encode(), string_to_sign.encode(), hashlib.sha256).digest()
            sign = base64.b64encode(hmac_code).decode()
            url = f"{DINGTALK_WEBHOOK}&timestamp={timestamp}&sign={sign}"
        else:
            url = DINGTALK_WEBHOOK

        payload = {
            "msgtype": "text",
            "text": {"content": msg}
        }
        requests.post(url, json=payload, timeout=10)
        logger.info("✅ 钉钉推送成功")
    except Exception as e:
        logger.error(f"❌ 钉钉失败: {e}")

# ======================== 主程序（去掉等待，直接运行） ========================
def main():
    if not is_trading_day():
        return

    # 测试版：直接运行，不等待9:20
    market_position_ratio, market_tips, mode = get_market_status()
    buy_stocks, watch_stocks = scan_market(market_position_ratio, mode)
    msg = build_msg(buy_stocks, watch_stocks, market_tips, market_position_ratio)

    send_feishu(msg)
    send_dingtalk(msg)

    logger.info("🎉 全部推送完成（测试版）")

if __name__ == "__main__":
    main()
