# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 主调度程序
===================================

职责：
1. 协调各模块完成股票分析流程
2. 实现低并发的线程池调度
3. 全局异常处理，确保单股失败不影响整体
4. 提供命令行入口
5. 新增：适配四时段纯文字推送（无符号/专业术语）

使用方式：
    python main.py              # 正常运行
    python main.py --debug      # 调试模式
    python main.py --dry-run    # 仅获取数据不分析
    python main.py --simple-push # 四时段纯文字推送模式（核心需求）
"""
import os
from src.config import setup_env
setup_env()

# 代理配置 - 通过 USE_PROXY 环境变量控制，默认关闭
# GitHub Actions 环境自动跳过代理配置
if os.getenv("GITHUB_ACTIONS") != "true" and os.getenv("USE_PROXY", "false").lower() == "true":
    proxy_host = os.getenv("PROXY_HOST", "127.0.0.1")
    proxy_port = os.getenv("PROXY_PORT", "10809")
    proxy_url = f"http://{proxy_host}:{proxy_port}"
    os.environ["http_proxy"] = proxy_url
    os.environ["https_proxy"] = proxy_url

import argparse
import logging
import sys
import time
import uuid
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple

from data_provider.base import canonical_stock_code
from src.core.pipeline import StockAnalysisPipeline
from src.core.market_review import run_market_review
from src.webui_frontend import prepare_webui_frontend_assets
from src.config import get_config, Config
from src.logging_config import setup_logging


logger = logging.getLogger(__name__)

# ==================== 新增：四时段纯文字推送核心函数（适配你的需求） ====================
def get_simple_stock_name(ts_code, pro):
    """获取股票名称（极简版，适配Tushare）"""
    try:
        df = pro.stock_basic(ts_code=ts_code, fields="name")
        return df.iloc[0]["name"] if not df.empty else ts_code
    except:
        return ts_code

def simple_stock_analysis(ts_code, pro):
    """适中深度的量化分析（专业术语/无符号）"""
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - pd.Timedelta(days=60)).strftime("%Y%m%d")
    df = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
    
    if df.empty or len(df) < 20:
        return None
    
    df = df.sort_values("trade_date").reset_index(drop=True)
    last = df.iloc[-1]
    pre = df.iloc[-2]

    # 均线趋势
    df["ma5"] = df.close.rolling(5).mean()
    df["ma10"] = df.close.rolling(10).mean()
    df["ma20"] = df.close.rolling(20).mean()
    
    if last.close > df.ma5.iloc[-1] > df.ma10.iloc[-1]:
        trend = "多头趋势"
    elif last.close < df.ma5.iloc[-1] < df.ma10.iloc[-1]:
        trend = "空头趋势"
    else:
        trend = "震荡整理"

    # MACD
    df["ema12"] = df.close.ewm(span=12, adjust=False).mean()
    df["ema26"] = df.close.ewm(span=26, adjust=False).mean()
    df["dif"] = df.ema12 - df.ema26
    df["dea"] = df.dif.ewm(span=9, adjust=False).mean()
    macd = "偏多" if df.dif.iloc[-1] > df.dea.iloc[-1] else "偏空"

    # 量能
    vol_avg = df.vol.iloc[-5:].mean()
    vol_rate = last.vol / vol_avg if vol_avg > 0 else 1
    vol = "放量" if vol_rate > 1.5 else "缩量" if vol_rate < 0.7 else "平量"

    # RSI
    delta = df.close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, 0.0001)
    rsi = 100 - (100 / (1 + rs))
    rsi_v = round(rsi.iloc[-1], 1)
    if rsi_v > 70:
        rsi_s = "超买"
    elif rsi_v < 30:
        rsi_s = "超卖"
    else:
        rsi_s = "正常"

    # 支撑压力 + 涨跌幅
    support = round(df.low.iloc[-10:].min(), 2)
    pressure = round(df.high.iloc[-10:].max(), 2)
    pct = round((last.close / pre.close - 1) * 100, 2)

    return {
        "name": get_simple_stock_name(ts_code, pro),
        "code": ts_code,
        "price": round(last.close, 2),
        "pct": pct,
        "trend": trend,
        "macd": macd,
        "vol": vol,
        "rsi": f"{rsi_v}({rsi_s})",
        "support": support,
        "pressure": pressure
    }

def generate_simple_report(stock_list, tushare_token):
    """生成四时段纯文字报告（无符号/专业术语）"""
    import tushare as ts
    pro = ts.pro_api(tushare_token)
    now = datetime.now().strftime("%H:%M")
    
    # 四时段标题适配
    if "09:30" in now:
        title = "早盘监测"
    elif "11:30" in now:
        title = "午盘综述"
    elif "15:30" in now:
        title = "收盘总结"
    elif "17:30" in now:
        title = "盘后分析"
    else:
        title = "个股监测"

    lines = [title, "------------------"]
    for code in stock_list:
        code = code.strip()
        # 补全股票代码后缀
        if not code.endswith((".SH", ".SZ")):
            ts_code = f"{code}.SH" if code.startswith("6") else f"{code}.SZ"
        else:
            ts_code = code

        res = simple_stock_analysis(ts_code, pro)
        if not res:
            continue

        lines.append(f"{res['name']} {res['code']}")
        lines.append(f"价格：{res['price']}  涨跌幅：{res['pct']}%")
        lines.append(f"趋势：{res['trend']}")
        lines.append(f"MACD：{res['macd']}  量能：{res['vol']}")
        lines.append(f"RSI：{res['rsi']}")
        lines.append(f"支撑：{res['support']}  压力：{res['pressure']}")
        lines.append("")

    return "\n".join(lines)

def push_to_feishu_simple(content, webhook_url):
    """极简飞书推送（纯文字/无格式）"""
    if not webhook_url:
        return
    try:
        requests.post(webhook_url, json={
            "msg_type": "text",
            "content": {"text": content}
        }, timeout=8)
        logger.info("纯文字报告已推送至飞书")
    except Exception as e:
        logger.error(f"飞书推送失败: {e}")

# ==================== 保留原代码所有解析参数逻辑 ====================
def parse_arguments() -> argparse.Namespace:
    """解析命令行参数（新增 --simple-push 参数）"""
    parser = argparse.ArgumentParser(
        description='A股自选股智能分析系统',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
  python main.py                    # 正常运行
  python main.py --debug            # 调试模式
  python main.py --dry-run          # 仅获取数据，不进行 AI 分析
  python main.py --stocks 600519,000001  # 指定分析特定股票
  python main.py --no-notify        # 不发送推送通知
  python main.py --single-notify    # 启用单股推送模式（每分析完一只立即推送）
  python main.py --schedule         # 启用定时任务模式
  python main.py --market-review    # 仅运行大盘复盘
  python main.py --simple-push      # 四时段纯文字推送模式（核心需求）
        '''
    )

    # 保留原所有参数
    parser.add_argument('--debug', action='store_true', help='启用调试模式，输出详细日志')
    parser.add_argument('--dry-run', action='store_true', help='仅获取数据，不进行 AI 分析')
    parser.add_argument('--stocks', type=str, help='指定要分析的股票代码，逗号分隔（覆盖配置文件）')
    parser.add_argument('--no-notify', action='store_true', help='不发送推送通知')
    parser.add_argument('--single-notify', action='store_true', help='启用单股推送模式')
    parser.add_argument('--workers', type=int, default=None, help='并发线程数（默认使用配置值）')
    parser.add_argument('--schedule', action='store_true', help='启用定时任务模式，每日定时执行')
    parser.add_argument('--no-run-immediately', action='store_true', help='定时任务启动时不立即执行一次')
    parser.add_argument('--market-review', action='store_true', help='仅运行大盘复盘分析')
    parser.add_argument('--no-market-review', action='store_true', help='跳过大盘复盘分析')
    parser.add_argument('--force-run', action='store_true', help='跳过交易日检查，强制执行全量分析')
    parser.add_argument('--webui', action='store_true', help='启动 Web 管理界面')
    parser.add_argument('--webui-only', action='store_true', help='仅启动 Web 服务，不执行自动分析')
    parser.add_argument('--serve', action='store_true', help='启动 FastAPI 后端服务')
    parser.add_argument('--serve-only', action='store_true', help='仅启动 FastAPI 后端服务，不自动执行分析')
    parser.add_argument('--port', type=int, default=8000, help='FastAPI 服务端口（默认 8000）')
    parser.add_argument('--host', type=str, default='0.0.0.0', help='FastAPI 服务监听地址')
    parser.add_argument('--no-context-snapshot', action='store_true', help='不保存分析上下文快照')
    parser.add_argument('--backtest', action='store_true', help='运行回测')
    parser.add_argument('--backtest-code', type=str, default=None, help='仅回测指定股票代码')
    parser.add_argument('--backtest-days', type=int, default=None, help='回测评估窗口')
    parser.add_argument('--backtest-force', action='store_true', help='强制回测')
    
    # 新增：四时段纯文字推送参数
    parser.add_argument('--simple-push', action='store_true', help='四时段纯文字推送模式（无符号/专业术语）')

    return parser.parse_args()

# ==================== 保留原代码所有核心函数 ====================
def _compute_trading_day_filter(
    config: Config,
    args: argparse.Namespace,
    stock_codes: List[str],
) -> Tuple[List[str], Optional[str], bool]:
    force_run = getattr(args, 'force_run', False)
    if force_run or not getattr(config, 'trading_day_check_enabled', True):
        return (stock_codes, None, False)

    from src.core.trading_calendar import (
        get_market_for_stock,
        get_open_markets_today,
        compute_effective_region,
    )

    open_markets = get_open_markets_today()
    filtered_codes = []
    for code in stock_codes:
        mkt = get_market_for_stock(code)
        if mkt in open_markets or mkt is None:
            filtered_codes.append(code)

    if config.market_review_enabled and not getattr(args, 'no_market_review', False):
        effective_region = compute_effective_region(
            getattr(config, 'market_review_region', 'cn') or 'cn', open_markets
        )
    else:
        effective_region = None

    should_skip_all = (not filtered_codes) and (effective_region or '') == ''
    return (filtered_codes, effective_region, should_skip_all)

def run_full_analysis(
    config: Config,
    args: argparse.Namespace,
    stock_codes: Optional[List[str]] = None
):
    try:
        if stock_codes is None:
            config.refresh_stock_list()

        effective_codes = stock_codes if stock_codes is not None else config.stock_list
        filtered_codes, effective_region, should_skip = _compute_trading_day_filter(
            config, args, effective_codes
        )
        if should_skip:
            logger.info("今日所有相关市场均为非交易日，跳过执行。可使用 --force-run 强制执行。")
            return
        if set(filtered_codes) != set(effective_codes):
            skipped = set(effective_codes) - set(filtered_codes)
            logger.info("今日休市股票已跳过: %s", skipped)
        stock_codes = filtered_codes

        if getattr(args, 'single_notify', False):
            config.single_stock_notify = True

        merge_notification = (
            getattr(config, 'merge_email_notification', False)
            and config.market_review_enabled
            and not getattr(args, 'no_market_review', False)
            and not config.single_stock_notify
        )

        save_context_snapshot = None
        if getattr(args, 'no_context_snapshot', False):
            save_context_snapshot = False
        query_id = uuid.uuid4().hex
        pipeline = StockAnalysisPipeline(
            config=config,
            max_workers=args.workers,
            query_id=query_id,
            query_source="cli",
            save_context_snapshot=save_context_snapshot
        )

        results = pipeline.run(
            stock_codes=stock_codes,
            dry_run=args.dry_run,
            send_notification=not args.no_notify,
            merge_notification=merge_notification
        )

        analysis_delay = getattr(config, 'analysis_delay', 0)
        if (
            analysis_delay > 0
            and config.market_review_enabled
            and not args.no_market_review
            and effective_region != ''
        ):
            logger.info(f"等待 {analysis_delay} 秒后执行大盘复盘...")
            time.sleep(analysis_delay)

        market_report = ""
        if (
            config.market_review_enabled
            and not args.no_market_review
            and effective_region != ''
        ):
            review_result = run_market_review(
                notifier=pipeline.notifier,
                analyzer=pipeline.analyzer,
                search_service=pipeline.search_service,
                send_notification=not args.no_notify,
                merge_notification=merge_notification,
                override_region=effective_region,
            )
            if review_result:
                market_report = review_result

        if merge_notification and (results or market_report) and not args.no_notify:
            parts = []
            if market_report:
                parts.append(f"# 📈 大盘复盘\n\n{market_report}")
            if results:
                dashboard_content = pipeline.notifier.generate_aggregate_report(
                    results,
                    getattr(config, 'report_type', 'simple'),
                )
                parts.append(f"# 🚀 个股决策仪表盘\n\n{dashboard_content}")
            if parts:
                combined_content = "\n\n---\n\n".join(parts)
                if pipeline.notifier.is_available():
                    if pipeline.notifier.send(combined_content, email_send_to_all=True):
                        logger.info("已合并推送（个股+大盘复盘）")
                    else:
                        logger.warning("合并推送失败")

        if results:
            logger.info("\n===== 分析结果摘要 =====")
            for r in sorted(results, key=lambda x: x.sentiment_score, reverse=True):
                emoji = r.get_emoji()
                logger.info(
                    f"{emoji} {r.name}({r.code}): {r.operation_advice} | "
                    f"评分 {r.sentiment_score} | {r.trend_prediction}"
                )

        logger.info("\n任务执行完成")

        # 飞书云文档生成
        try:
            from src.feishu_doc import FeishuDocManager
            feishu_doc = FeishuDocManager()
            if feishu_doc.is_configured() and (results or market_report):
                logger.info("正在创建飞书云文档...")
                tz_cn = timezone(timedelta(hours=8))
                now = datetime.now(tz_cn)
                doc_title = f"{now.strftime('%Y-%m-%d %H:%M')} 大盘复盘"
                full_content = ""
                if market_report:
                    full_content += f"# 📈 大盘复盘\n\n{market_report}\n\n---\n\n"
                if results:
                    dashboard_content = pipeline.notifier.generate_aggregate_report(
                        results, getattr(config, 'report_type', 'simple'),
                    )
                    full_content += f"# 🚀 个股决策仪表盘\n\n{dashboard_content}"
                doc_url = feishu_doc.create_daily_doc(doc_title, full_content)
                if doc_url:
                    logger.info(f"飞书云文档创建成功: {doc_url}")
                    if not args.no_notify:
                        pipeline.notifier.send(f"[{now.strftime('%Y-%m-%d %H:%M')}] 复盘文档创建成功: {doc_url}")
        except Exception as e:
            logger.error(f"飞书文档生成失败: {e}")

        # 自动回测
        try:
            if getattr(config, 'backtest_enabled', False):
                from src.services.backtest_service import BacktestService
                logger.info("开始自动回测...")
                service = BacktestService()
                stats = service.run_backtest(
                    force=False,
                    eval_window_days=getattr(config, 'backtest_eval_window_days', 10),
                    min_age_days=getattr(config, 'backtest_min_age_days', 14),
                    limit=200,
                )
                logger.info(
                    f"自动回测完成: processed={stats.get('processed')} saved={stats.get('saved')} "
                    f"completed={stats.get('completed')} insufficient={stats.get('insufficient')} errors={stats.get('errors')}"
                )
        except Exception as e:
            logger.warning(f"自动回测失败（已忽略）: {e}")

    except Exception as e:
        logger.exception(f"分析流程执行失败: {e}")

def start_api_server(host: str, port: int, config: Config) -> None:
    import threading
    import uvicorn
    def run_server():
        level_name = (config.log_level or "INFO").lower()
        uvicorn.run(
            "api.app:app",
            host=host,
            port=port,
            log_level=level_name,
            log_config=None,
        )
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    logger.info(f"FastAPI 服务已启动: http://{host}:{port}")

def _is_truthy_env(var_name: str, default: str = "true") -> bool:
    value = os.getenv(var_name, default).strip().lower()
    return value not in {"0", "false", "no", "off"}

def start_bot_stream_clients(config: Config) -> None:
    if config.dingtalk_stream_enabled:
        try:
            from bot.platforms import start_dingtalk_stream_background, DINGTALK_STREAM_AVAILABLE
            if DINGTALK_STREAM_AVAILABLE:
                if start_dingtalk_stream_background():
                    logger.info("[Main] Dingtalk Stream client started in background.")
                else:
                    logger.warning("[Main] Dingtalk Stream client failed to start.")
            else:
                logger.warning("[Main] Dingtalk Stream enabled but SDK is missing.")
        except Exception as exc:
            logger.error(f"[Main] Failed to start Dingtalk Stream client: {exc}")

    if getattr(config, 'feishu_stream_enabled', False):
        try:
            from bot.platforms import start_feishu_stream_background, FEISHU_SDK_AVAILABLE
            if FEISHU_SDK_AVAILABLE:
                if start_feishu_stream_background():
                    logger.info("[Main] Feishu Stream client started in background.")
                else:
                    logger.warning("[Main] Feishu Stream client failed to start.")
            else:
                logger.warning("[Main] Feishu Stream enabled but SDK is missing.")
        except Exception as exc:
            logger.error(f"[Main] Failed to start Feishu Stream client: {exc}")

def _resolve_scheduled_stock_codes(stock_codes: Optional[List[str]]) -> Optional[List[str]]:
    if stock_codes is not None:
        logger.warning("定时模式下检测到 --stocks 参数；计划执行将忽略启动时股票快照，并重新读取最新的 STOCK_LIST。")
    return None

# ==================== 改造主函数（新增 --simple-push 分支） ====================
def main() -> int:
    args = parse_arguments()
    config = get_config()
    setup_logging(log_prefix="stock_analysis", debug=args.debug, log_dir=config.log_dir)

    logger.info("=" * 60)
    logger.info("A股自选股智能分析系统 启动")
    logger.info(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    warnings = config.validate()
    for warning in warnings:
        logger.warning(warning)

    stock_codes = None
    if args.stocks:
        stock_codes = [canonical_stock_code(c) for c in args.stocks.split(',') if (c or "").strip()]
        logger.info(f"使用命令行指定的股票列表: {stock_codes}")

    # 兼容 WebUI 参数
    if args.webui:
        args.serve = True
    if args.webui_only:
        args.serve_only = True
    if config.webui_enabled and not (args.serve or args.serve_only):
        args.serve = True

    # 启动 Web 服务
    start_serve = (args.serve or args.serve_only) and os.getenv("GITHUB_ACTIONS") != "true"
    if start_serve:
        if args.host == '0.0.0.0' and os.getenv('WEBUI_HOST'):
            args.host = os.getenv('WEBUI_HOST')
        if args.port == 8000 and os.getenv('WEBUI_PORT'):
            args.port = int(os.getenv('WEBUI_PORT'))

    bot_clients_started = False
    if start_serve:
        if not prepare_webui_frontend_assets():
            logger.warning("前端静态资源未就绪，继续启动 FastAPI 服务")
        try:
            start_api_server(host=args.host, port=args.port, config=config)
            bot_clients_started = True
        except Exception as e:
            logger.error(f"启动 FastAPI 服务失败: {e}")

    if bot_clients_started:
        start_bot_stream_clients(config)

    # 仅 Web 服务模式
    if args.serve_only:
        logger.info("模式: 仅 Web 服务")
        logger.info(f"Web 服务运行中: http://{args.host}:{args.port}")
        logger.info("按 Ctrl+C 退出...")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("\n用户中断，程序退出")
        return 0

    try:
        # 新增：四时段纯文字推送模式（核心需求）
        if args.simple_push:
            logger.info("模式: 四时段纯文字推送（无符号/专业术语）")
            # 获取环境变量中的配置
            tushare_token = os.getenv("TUSHARE_TOKEN", config.tushare_token)
            feishu_webhook = os.getenv("FEISHU_WEBHOOK_URL", "")
            # 优先级：命令行 --stocks > 配置文件 > 默认
            target_stocks = stock_codes if stock_codes else config.stock_list
            if not target_stocks:
                target_stocks = ["600519"]
            
            # 生成纯文字报告 + 推送
            simple_report = generate_simple_report(target_stocks, tushare_token)
            print(simple_report)
            push_to_feishu_simple(simple_report, feishu_webhook)
            return 0

        # 回测模式
        if getattr(args, 'backtest', False):
            logger.info("模式: 回测")
            from src.services.backtest_service import BacktestService
            service = BacktestService()
            stats = service.run_backtest(
                code=getattr(args, 'backtest_code', None),
                force=getattr(args, 'backtest_force', False),
                eval_window_days=getattr(args, 'backtest_days', None),
            )
            logger.info(
                f"回测完成: processed={stats.get('processed')} saved={stats.get('saved')} "
                f"completed={stats.get('completed')} insufficient={stats.get('insufficient')} errors={stats.get('errors')}"
            )
            return 0

        # 仅大盘复盘模式
        if args.market_review:
            from src.analyzer import GeminiAnalyzer
            from src.core.market_review import run_market_review
            from src.notification import NotificationService
            from src.search_service import SearchService

            effective_region = None
            if not getattr(args, 'force_run', False) and getattr(config, 'trading_day_check_enabled', True):
                from src.core.trading_calendar import get_open_markets_today, compute_effective_region as _compute_region
                open_markets = get_open_markets_today()
                effective_region = _compute_region(
                    getattr(config, 'market_review_region', 'cn') or 'cn', open_markets
                )
                if effective_region == '':
                    logger.info("今日大盘复盘相关市场均为非交易日，跳过执行。")
                    return 0

            logger.info("模式: 仅大盘复盘")
            notifier = NotificationService()
            search_service = None
            analyzer = None

            if config.has_search_capability_enabled():
                search_service = SearchService(
                    bocha_keys=config.bocha_api_keys,
                    tavily_keys=config.tavily_api_keys,
                    brave_keys=config.brave_api_keys,
                    serpapi_keys=config.serpapi_keys,
                    minimax_keys=config.minimax_api_keys,
                    searxng_base_urls=config.searxng_base_urls,
                    searxng_public_instances_enabled=config.searxng_public_instances_enabled,
                    news_max_age_days=config.news_max_age_days,
                    news_strategy_profile=getattr(config, "news_strategy_profile", "short"),
                )

            if config.gemini_api_key or config.openai_api_key:
                analyzer = GeminiAnalyzer(api_key=config.gemini_api_key)
                if not analyzer.is_available():
                    logger.warning("AI 分析器初始化后不可用，请检查 API Key 配置")
                    analyzer = None
            else:
                logger.warning("未检测到 API Key，将仅使用模板生成报告")

            run_market_review(
                notifier=notifier,
                analyzer=analyzer,
                search_service=search_service,
                send_notification=not args.no_notify,
                override_region=effective_region,
            )
            return 0

        # 定时任务模式
        if args.schedule or config.schedule_enabled:
            logger.info("模式: 定时任务")
            logger.info(f"每日执行时间: {config.schedule_time}")
            should_run_immediately = config.schedule_run_immediately
            if getattr(args, 'no_run_immediately', False):
                should_run_immediately = False
            logger.info(f"启动时立即执行: {should_run_immediately}")

            from src.scheduler import run_with_schedule
            scheduled_stock_codes = _resolve_scheduled_stock_codes(stock_codes)
            def scheduled_task():
                run_full_analysis(config, args, scheduled_stock_codes)
            run_with_schedule(
                task=scheduled_task,
                schedule_time=config.schedule_time,
                run_immediately=should_run_immediately
            )
            return 0

        # 正常单次运行
        if config.run_immediately:
            run_full_analysis(config, args, stock_codes)
        else:
            logger.info("配置为不立即运行分析 (RUN_IMMEDIATELY=false)")

        logger.info("\n程序执行完成")
        keep_running = start_serve and not (args.schedule or config.schedule_enabled)
        if keep_running:
            logger.info("API 服务运行中 (按 Ctrl+C 退出)...")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass

        return 0

    except KeyboardInterrupt:
        logger.info("\n用户中断，程序退出")
        return 130

    except Exception as e:
        logger.exception(f"程序执行失败: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
