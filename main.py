# -*- coding: utf-8 -*-
import yfinance as yf
import pandas as pd
import requests
import json
import warnings
warnings.filterwarnings('ignore')

# ====================== 你的飞书Webhook（已直接填入） ======================
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/7e8c7d35-382e-43de-8479-0434921d338c"

# ====================== 全量股票池（你所有标的：蓝筹+科技+航天火箭回收） ======================
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
        "601088.SS","601225.SS","601898.SS","600188.SS","601666.SS","601918.SS",
        "600740.SS","600348.SS",
        # 电力公用
        "600900.SS","600025.SS","600011.SS","600795.SS","601991.SS","600642.SS",
        "600101.SS","600886.SS",
        # 基建交建
        "601668.SS","601390.SS","601186.SS","601868.SS","600018.SS","601117.SS",
        "601399.SS","601880.SS","601006.SS",
        # 医药
        "000538.SZ","600332.SS","000999.SZ","600566.SS","000623.SZ","000028.SZ",
        "600867.SS","002004.SZ","000650.SZ",
        # 消费农业
        "600597.SS","600872.SS","000729.SZ","600132.SS","300498.SZ","002027.SZ",
        # 科技/电子/算力
        "000100.SZ","002056.SZ","000977.SZ","603019.SS","600879.SS","002413.SZ",
        "002297.SZ","600435.SS","600150.SS","300474.SZ","002151.SZ","300394.SZ",
        "603758.SS","002655.SZ","600523.SS",
        # 证券
        "600030.SS","600837.SS","601211.SS","601688.SS","601066.SS","601881.SS",
        # 航天火箭发射/回收/蓝箭链（核心新增）
        "600343.SS","600118.SS","000547.SZ","300065.SZ","688102.SH","002074.SZ",
        "600151.SS","600316.SS","600677.SS","002025.SZ","600855.SS","600262.SS"
    ]
    return list(set(pool))

# ====================== 飞书推送函数（修复完成，无报错） ======================
def send_to_feishu(buy_list):
    if not buy_list:
        content = "⚠️ 今日无符合条件标的，空仓保本观望"
    else:
        content = "📈 【真实数据源】底部放量选股信号\n\n符合条件标的：\n"
        for i, code in enumerate(buy_list[:15], 1):
            content += f"{i}. {code}\n"
        content += "\n🛡️ 风控：止损6% | 止盈10% | 单票≤3成仓"

    msg = {"msg_type": "text", "content": {"text": content}}
    try:
        requests.post(FEISHU_WEBHOOK, json=msg, timeout=10)
        print("✅ 已推送到飞书")
    except Exception as e:
        print(f"❌ 飞书推送失败：{e}")

# ====================== 核心：yfinance真实数据选股（宽松均线） ======================
def check_stock(code):
    try:
        # 真实数据源：下载近90天K线
        df = yf.download(code, period="90d", progress=False)
        if len(df) < 60:
            return False

        # 1. 真实均线计算（宽松条件：贴近/即将金叉，不卡死）
        df['ma5'] = df['Close'].rolling(5).mean()
        df['ma10'] = df['Close'].rolling(10).mean()
        ma5 = df['ma5'].iloc[-1]
        ma10 = df['ma10'].iloc[-1]
        ma_diff = abs(ma5 - ma10) / ma10
        ma_good = (ma_diff <= 0.015) or (ma5 >= ma10)  # 贴近1.5% 或 微金叉

        # 2. 真实放量：今日成交量 > 20日均量1.5倍
        vol_now = df['Volume'].iloc[-1]
        vol_ma20 = df['Volume'].rolling(20).mean().iloc[-1]
        vol_good = vol_now > vol_ma20 * 1.5

        # 3. 真实底部超跌：60日跌幅 ≥ 18%
        price_now = df['Close'].iloc[-1]
        price_60 = df['Close'].iloc[-60]
        drop_60 = (price_60 - price_now) / price_60
        bottom_good = drop_60 >= 0.18

        # 4. 过滤垃圾股：股价 > 3元
        price_good = price_now > 3.0

        # 全部满足才入选
        return ma_good and vol_good and bottom_good and price_good

    except:
        return False

# ====================== 主程序 ======================
def main():
    pool = get_stock_pool()
    print("="*60)
    print(f"📊 真实数据源选股 | 股票池总数：{len(pool)} 只")
    print("✅ 数据源：yfinance 真实行情")
    print("✅ 策略：宽松均线 + 底部超跌 + 放量突破")
    print("="*60)

    # 真实扫描
    buy_list = [code for code in pool if check_stock(code)]
    buy_list = buy_list[:15]  # 最多推15只

    # 输出结果
    if buy_list:
        print(f"\n🔥 今日入选 {len(buy_list)} 只：")
        for i, code in enumerate(buy_list, 1):
            print(f"{i}. {code}")
    else:
        print("\n⚠️ 今日无符合条件标的")

    # 推送飞书
    send_to_feishu(buy_list)

if __name__ == "__main__":
    main()
