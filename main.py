import requests
import json
import logging
import os
from datetime import datetime
import time

# ======================== 1. 配置初始化 ========================
# 读取配置文件（请确保配置文件路径正确）
CONFIG_PATH = "C:\\Users\\ning\\.openclaw\\config.json"  # 替换为你的配置文件实际路径
with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
    CONFIG = json.load(f)

# 初始化日志
LOG_LEVEL = CONFIG['logging']['level'].upper()
LOG_FILE = CONFIG['logging']['file']
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.DEBUG),
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()  # 同时输出到控制台
    ]
)
logger = logging.getLogger(__name__)

# 股票配置
STOCK_CONFIG = CONFIG.get('stock', {})
STOCK_SYMBOLS = STOCK_CONFIG.get('symbols', [])
STOCK_FIELDS = STOCK_CONFIG.get('api', {}).get('fields', {})
RETRY_CONFIG = STOCK_CONFIG.get('api', {}).get('retry', {})
MAX_RETRIES = RETRY_CONFIG.get('maxRetries', 3)
RETRY_DELAY = RETRY_CONFIG.get('delayMs', 1000) / 1000  # 转换为秒

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
    获取单只股票数据（兼容date/trade_date字段）
    :param stock_code: 股票代码
    :return: 格式化的股票数据字典，失败返回None
    """
    # 这里替换为你的实际股票API调用逻辑
    # 示例：调用股票数据接口（请替换为真实API）
    stock_data = None
    for retry in range(MAX_RETRIES):
        try:
            logger.debug(f"获取{stock_code}数据，第{retry+1}次尝试")
            
            # ------------------- 替换为你的真实API调用 -------------------
            # 示例接口（仅演示，需替换）：
            # response = requests.get(
            #     f"你的股票API地址?code={stock_code}",
            #     timeout=STOCK_CONFIG.get('api', {}).get('timeout', 30)
            # )
            # response.raise_for_status()
            # stock_data = response.json()
            # ------------------------------------------------------------

            # 模拟数据（测试用，实际使用时删除）
            stock_data = {
                'trade_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'stock_code': stock_code,
                'current_price': round(10 + (retry * 0.1), 2),
                'price_change': round(0.2 + (retry * 0.05), 2)
            }
            break  # 成功获取数据，退出重试
        
        except Exception as e:
            logger.error(f"获取{stock_code}数据失败（第{retry+1}次）：{str(e)}")
            if retry < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
            else:
                logger.error(f"{stock_code}数据获取最终失败")
                return None

    # 字段映射（解决'date'字段错误核心逻辑）
    if stock_data:
        formatted_data = {}
        for old_field, new_field in STOCK_FIELDS.items():
            # 优先用新字段，兼容旧字段
            formatted_data[old_field] = stock_data.get(new_field, stock_data.get(old_field, '未知'))
        
        # 补充原始数据，避免信息丢失
        formatted_data['raw'] = stock_data
        logger.debug(f"{stock_code}数据格式化完成：{formatted_data}")
        return formatted_data
    return None

def send_feishu_message(content):
    """
    发送消息到飞书
    :param content: 消息内容（文本/Markdown）
    :return: 是否发送成功
    """
    if not FEISHU_WEBHOOK:
        logger.error("飞书Webhook地址未配置，无法发送消息")
        return False

    headers = {'Content-Type': 'application/json'}
    payload = {
        "msg_type": "text" if not FEISHU_CONFIG.get('message', {}).get('enableMarkdown') else "post",
        "content": {
            "text": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
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
    """
    批量获取所有监控股票数据
    :return: 股票数据列表，空列表表示全部失败
    """
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

    # 判断是否全部无数据
    if empty_count == len(STOCK_SYMBOLS):
        logger.warning("所有股票均无有效数据")
        return []
    return stock_list

def format_stock_message(stock_data_list):
    """
    格式化股票数据为飞书消息内容
    :param stock_data_list: 股票数据列表
    :return: 格式化后的消息文本
    """
    if not stock_data_list:
        return EMPTY_DATA_MSG

    message = "📊 个股监测数据\n" + "-"*30 + "\n"
    for stock in stock_data_list:
        message += (
            f"股票代码：{stock.get('code', '未知')}\n"
            f"时间：{stock.get('date', '未知')}\n"
            f"当前价格：{stock.get('price', '未知')} 元\n"
            f"涨跌幅：{stock.get('change', '未知')}%\n"
            + "-"*30 + "\n"
        )
    message += f"更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    return message

# ======================== 3. 主执行流程 ========================
def main():
    """主函数：获取股票数据并推送到飞书"""
    logger.info("===== 开始执行四时段股票推送任务 =====")
    
    # 1. 获取股票数据
    stock_data = collect_stock_data()
    
    # 2. 格式化消息
    message = format_stock_message(stock_data)
    logger.debug(f"待发送消息：{message}")
    
    # 3. 发送到飞书
    if STOCK_CONFIG.get('push', {}).get('enable', True):
        send_success = send_feishu_message(message)
        if send_success:
            logger.info("===== 股票推送任务执行完成 =====")
        else:
            logger.error("===== 股票推送任务执行失败 =====")
            # 如果配置了错误通知，可在此处补充告警逻辑
    else:
        logger.info("推送功能已禁用，仅打印消息：\n" + message)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.critical(f"程序执行异常：{str(e)}", exc_info=True)
        # 异常时发送告警到飞书
        send_feishu_message(f"⚠️ 股票推送程序执行异常：{str(e)}")
