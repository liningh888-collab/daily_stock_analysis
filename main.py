import requests
import json
import logging
import os
from datetime import datetime
import time
import akshare as ak

# ======================== 1. 配置初始化 ========================
config_content = os.environ.get("CONFIG_CONTENT")
if not config_content:
    raise Exception("请在 GitHub Secrets 配置 CONFIG_CONTENT")

CONFIG = json.loads(config_content)

# 日志（极简，不写文件，避免路径错误）
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 股票配置
STOCK_SYMBOLS = CONFIG.get("stock", {}).get("symbols", [])

# 飞书配置
FEISHU_WEBHOOK = CONFIG.get("channels", {}).get("feishu", {}).get("webhook", {}).get("url", "")
EMPTY_DATA_MSG = "今日无股票交易数据"

# ======================== 2. 获取真实股价 ========================
def get_real_stock(code):
    try:
        df = ak.stock_zh_a_spot_em()
        row = df[df["代码"] == code].iloc[0]
        return {
            "code": code,
            "name": row["名称"],
            "price": round(float(row["最新价"]), 2),
            "change": round(float(row["涨跌幅"]), 2),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        logger.error(f"{code} 获取失败: {e}")
        return None

# ======================== 3. 发送飞书 ========================
def send_feishu(text):
    if not FEISHU_WEBHOOK:
        logger.error("未配置飞书Webhook")
        return False
    try:
        resp = requests.post(
            FEISHU_WEBHOOK,
            json={"msg_type": "text", "content": {"text": text}},
            timeout=15
        )
        return resp.json().get("code") == 0
    except Exception as e:
        logger.error(f"发送失败: {e}")
        return False

# ======================== 4. 主逻辑 ========================
def main():
    logger.info("开始获取股票数据")
    stocks = []
    for code in STOCK_SYMBOLS:
        data = get_real_stock(code)
        if data:
            stocks.append(data)

    if not stocks:
        send_feishu(EMPTY_DATA_MSG)
        return

    # 拼接消息
    msg = "📊 个股实时行情\n" + "-"*30 + "\n"
    for s in stocks:
        msg += (
            f"{s['code']} {s['name']}\n"
            f"现价：{s['price']} 元\n"
            f"涨跌幅：{s['change']}%\n"
            f"时间：{s['time']}\n"
            + "-"*30 + "\n"
        )
    msg += f"更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    send_feishu(msg)
    logger.info("推送完成")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.critical(f"异常: {e}")
        send_feishu(f"⚠️ 股票推送异常：{e}")
