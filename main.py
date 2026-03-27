import requests
import json
import logging
import os
from datetime import datetime
import time
import akshare as ak

# ======================== 1. 配置初始化 ========================
# 从 GitHub Secrets 读取配置
config_content = os.environ.get("CONFIG_CONTENT")
if not config_content:
    raise Exception("请在 GitHub Secrets 中配置 CONFIG_CONTENT")

CONFIG = json.loads(config_content)

# 初始化日志
LOG_LEVEL = CONFIG['logging']['level'].upper()
LOG_FILE = CONFIG['logging']['file']
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.DEBUG),
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 股票配置
STOCK_CONFIG = CONFIG.get('stock', {})
STOCK_SYMBOLS = STOCK_CONFIG.get('symbols', [])
STOCK_FIELDS = STOCK_CONFIG.get('api', {}).get('fields', {})
RETRY_CONFIG = STOCK_CONFIG.get('api', {}).get('retry', {})
MAX_RETRIES = RETRY_CONFIG.get('maxRetries', 3)
RETRY_DELAY = RETRY_CONFIG.get('delayMs', 1000) / 1000

# 飞书配置
FEISHU_CONFIG = CONFIG.get('channels', {}).get('feishu', {})
FEISHU_WEBHOOK = FEISHU_CONFIG.get('webhook', {}).get('url', '')
FEISHU_TIMEOUT = FEISHU_CONFIG.get('webhook', {}).get('timeout', 30)
FEISHU_RETRY = FEISHU_CONFIG.get('webhook', {}).get('retry', {})
FEISHU_MAX_RETRIES = FEISHU_RETRY.get('maxRetries', 3)
EMPTY_DATA_MSG = STOCK_CONFIG.get('push', {}).get('emptyDataMessage', '今日无股票交易数据')

# ======================== 2. 核心函数 ========================
def get_stock_data(stock_code):
    """
    获取真实 A 股行情数据（替换原模拟数据）
    :param stock_code: 股票代码（如 600968）
    :return: 格式化的股票数据字典
    """
    stock_data = None
    for retry in range(MAX_RETRIES):
        try:
            logger.debug(f"获取{stock_code}数据，第{retry+1}次尝试")
            
            # 方式1：获取实时行情（优先）
            try:
                # akshare 实时行情接口
                stock_df = ak.stock_zh_a_spot_em()
                # 筛选指定股票代码
                stock_info = stock_df[stock_df['代码'] == stock_code]
                
                if not stock_info.empty:
                    stock_info = stock_info.iloc[0]
                    stock_data = {
                        'stock_code': stock_code,
                        'stock_name': stock_info['名称'],  # 新增：股票名称
                        'trade_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'current_price': round(float(stock_info['最新价']), 2),
                        'price_change': round(float(stock_info['涨跌幅']), 2),
                        'open_price': round(float(stock_info['开盘价']), 2),    # 新增：开盘价
                        'high_price': round(float(stock_info['最高价']), 2),    # 新增：最高价
                        'low_price': round(float(stock_info['最低价']), 2)      # 新增：最低价
                    }
                    break
            except Exception as e:
                logger.warning(f"实时行情接口失败，尝试备用接口：{e}")
                # 方式2：备用接口（历史数据）
                stock_zh_a_hist_df = ak.stock_zh_a_hist(
                    symbol=stock_code,
                    period="daily",
                    start_date=(datetime.now().date()).strftime("%Y%m%d"),
                    end_date=(datetime.now().date()).strftime("%Y%m%d"),
                    adjust="qfq"
                )
                if not stock_zh_a_hist_df.empty:
                    hist_info = stock_zh_a_hist_df.iloc[0]
                    stock_data = {
                        'stock_code': stock_code,
                        'stock_name': stock_code,  # 备用接口无名称
                        'trade_date': hist_info['日期'],
                        'current_price': round(float(hist_info['收盘']), 2),
                        'price_change': round(float(hist_info['涨跌幅']), 2),
                        'open_price': round(float(hist_info['开盘']), 2),
                        'high_price': round(float(hist_info['最高']), 2),
                        'low_price': round(float(hist_info['最低']), 2)
                    }
                    break

        except Exception as e:
            logger.error(f"获取{stock_code}数据失败（第{retry+1}次）：{str(e)}")
            if retry < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
            else:
                logger.error(f"{stock_code}数据获取最终失败")
                return None

    if stock_data:
        # 字段映射兼容（保持原有逻辑）
        formatted_data = {}
        for old_field, new_field in STOCK_FIELDS.items():
            formatted_data[old_field] = stock_data.get(new_field, stock_data.get(old_field, '未知'))
        formatted_data.update(stock_data)  # 合并所有字段
        logger.debug(f"{stock_code}数据格式化完成：{formatted_data}")
        return formatted_data
    return None

def send_feishu_message(content):
    if not FEISHU_WEBHOOK:
        logger.error("飞书Webhook地址未配置，无法发送消息")
        return False

    headers = {'Content-Type': 'application/json'}
    payload = {
        "msg_type": "text" if not FEISHU_CONFIG.get('message', {}).get('enableMarkdown') else "post",
        "content": {
            "text": content
        } if not FEISHU_CONFIG.get('message', {}).get('enableMarkdown') else {
            "post": {
                "zh-CN": {
                    "title": "个股监测",
                    "content": [[{"tag": "text", "text": content}]]
                }
            }
        }
    }

    for retry in range(FEISHU_MAX_RETRIES):
        try:
            response = requests.post(
                FEISHU_WEBHOOK,
                headers=headers,
                json=payload,
                timeout=FEISHU_TIMEOUT
            )
            response.raise_for_status()
            result = response.json()
            if result.get('code') == 0:
                logger.info("飞书消息发送成功")
                return True
            else:
                logger.error(f"飞书消息发送失败：{result.get('msg')}")
        except Exception as e:
            logger.error(f"发送飞书消息失败（第{retry+1}次）：{str(e)}")
            if retry < FEISHU_MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)

    logger.error("飞书消息发送最终失败")
    return False

def collect_stock_data():
    stock_list = []
    empty_count = 0
    for code in STOCK_SYMBOLS:
        data = get_stock_data(code)
        if data:
            stock_list.append(data)
            logger.info(f"{code}数据获取成功")
        else:
            empty_count += 1
            logger.warning(f"{code}无有效数据")

    if empty_count == len(STOCK_SYMBOLS):
        logger.warning("所有股票均无有效数据")
        return []
    return stock_list

def format_stock_message(stock_data_list):
    """
    修复字段匹配问题，显示真实股票信息
    """
    if not stock_data_list:
        return EMPTY_DATA_MSG

    message = "📊 个股监测数据\n" + "-"*40 + "\n"
    for stock in stock_data_list:
        message += (
            f"股票代码：{stock.get('stock_code', '未知')}\n"
            f"股票名称：{stock.get('stock_name', '未知')}\n"  # 新增：显示股票名称
            f"时间：{stock.get('trade_date', '未知')}\n"
            f"当前价格：{stock.get('current_price', '未知')} 元\n"
            f"涨跌幅：{stock.get('price_change', '未知')}%\n"
            f"开盘价：{stock.get('open_price', '未知')} 元\n"  # 新增：开盘价
            f"最高价：{stock.get('high_price', '未知')} 元\n"  # 新增：最高价
            f"最低价：{stock.get('low_price', '未知')} 元\n"  # 新增：最低价
            + "-"*40 + "\n"
        )
    message += f"更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    return message

# ======================== 3. 主执行流程 ========================
def main():
    logger.info("===== 开始执行四时段股票推送任务 =====")
    stock_data = collect_stock_data()
    message = format_stock_message(stock_data)
    logger.debug(f"待发送消息：{message}")

    if STOCK_CONFIG.get('push', {}).get('enable', True):
        send_success = send_feishu_message(message)
        if send_success:
            logger.info("===== 股票推送任务执行完成 =====")
        else:
            logger.error("===== 股票推送任务执行失败 =====")
    else:
        logger.info("推送功能已禁用，仅打印消息：\n" + message)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.critical(f"程序执行异常：{str(e)}", exc_info=True)
        send_feishu_message(f"⚠️ 股票推送程序执行异常：{str(e)}")
