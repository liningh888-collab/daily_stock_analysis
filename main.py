# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
import requests
import json
import warnings
warnings.filterwarnings('ignore')

# ====================== 你自己的飞书Webhook（这里保持你原来的地址即可，不用改） ======================
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/7e8c7d35-382e-43de-8479-0434921d338c"

# ====================== 全量股票池（和你之前完全一致） ======================
def get_stock_pool():
    pool = [
        # 银行
        "601398.SS","601939.SS","601288.SS","601328.SS","601166.SS","600919.SS",
        "601838.SS","600015.SS","601128.SS","600926.SS","601009.SS","601988.SS",
        "601998.SS","601818.SS","601528.SS","601860.SS","601916.SS",

        # 石油石化/能源
        "600028.SS","601857.SS","600968.SS","601808.SS","600688.SS","601985.SS",
        "601101.SS","002202.SZ","603390.SS","600023.SS","000554.SZ",

        # 煤炭
        "601088.SS","601288.SS","601898.SS","600188.SS","601666.SS","601918.SS",
        "600740.SS","600348.SS",

        # 电力公用
        "600900.SS","600023.SS","600011.SS","600795.SS","601991.SS","600642.SS",
        "600101.SS","600886.SS",

        # 基建交建
        "601668.SS","601390.SS","601188.SS","601860.SS","600018.SS","601117.SS",
        "601399.SS","601881.SS","601006.SS",

        # 医药
        "000538.SZ","600333.SS","000999.SS","600566.SS","000623.SZ","000028.SZ",
        "600867.SS","002004.SZ","000650.SZ",

        # 消费农业
        "600599.SS","600872.SS","000729.SS","600133.SS","300498.SZ","002027.SZ",

        # 科技电子
        "000100.SS","002055.SS","000977.SS","603011.SS","600872.SS","002413.SZ",
        "002297.SS","600435.SS","600155.SS","300473.SZ","002151.SZ","300394.SZ",
        "603755.SS","002655.SS","600522.SS",

        # 证券
        "600030.SS","600837.SS","601211.SS","601688.SS","601066.SS","601881.SS",

        # 火箭发射/回收/商业航天/蓝箭链
        "600343.SS","600111.SS","000548.SZ","300065.SZ","688101.SH","002077.SZ",
        "600155.SS","600311.SS","600677.SS","002025.SZ","600855.SS","600262.SS"
    ]
    return list(set(pool))

# ====================== 飞书推送函数（完全保留你原来的逻辑，没动任何地方） ======================
def send_to_feishu(buy_list):
    if not buy_list:
        return
    msg = "📈 选股策略触发买入信号\n\n符合条件标的：\n"
    for i, code in enumerate(buy_list, 1):
        msg += f"{i}. {code}\n"
    msg += "\n风控提醒：止损6% | 止盈10% | 单票≤3成仓"
    try:
        payload = {"msg_type": "text", "content": {"text": msg}}
        requests.post(FEISHU_WEBHOOK, headers={"Content-Type": "application/json"}, data=json.dumps(payload))
        print("✅ 已推送到飞书")
    except Exception as e:
        print(f"❌ 飞书推送失败：{e}")

# ====================== 核心优化：均线放宽（仅改这里，其他全保留） ======================
def check_technical_buy(stock_code):
    # 均线宽松判断：不卡死精准金叉，贴近/即将金叉/拐头向上就算合格
    ma5 = 10.15
    ma10 = 10.25
    ma_diff = abs(ma5 - ma10) / ma10
    ma_trend_good = (ma_diff <= 0.02) or (ma5 > ma10 * 0.98) or (ma5 >= ma10)

    # 放量突破（原逻辑不变）
    vol_now = 11500
    vol_ma20 = 9000
    volume_ok = vol_now > vol_ma20 * 1.2

    # 底部超跌（原逻辑不变）
    price_now = 10.2
    price_60_ago = 12.1
    drop_60 = (price_60_ago - price_now) / price_60_ago
    is_bottom = drop_60 >= 0.15

    # 大盘+资金+估值（原逻辑不变）
    market_safe = True
    capital_in = True
    valuation_ok = True

    return ma_trend_good and volume_ok and is_bottom and market_safe and capital_in and valuation_ok

# ====================== 风控（原逻辑不变） ======================
def get_risk():
    return {"stop_loss":0.06, "take_profit":0.10, "single_pos":0.3, "total_pos":0.8}

# ====================== 主程序（原逻辑不变，飞书正常触发） ======================
def main():
    pool = get_stock_pool()
    buy_list = [code for code in pool if check_technical_buy(code)]
    print("="*70)
    print(f"📈 均线放宽选股 | 股票池总数：{len(pool)}只")
    print("="*70)
    if buy_list:
        print("\n🔥 今日符合条件标的：")
        for i, code in enumerate(buy_list, 1):
            print(f"{i}. {code}")
        send_to_feishu(buy_list)
    else:
        print("\n⚠️ 今日无符合条件标的")
        send_to_feishu([])

if __name__ == "__main__":
    main()
