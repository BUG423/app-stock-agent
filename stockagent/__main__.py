"""StockAgent 入口 —— 支持 web / scheduler / pipeline 等模式。"""

import argparse
import asyncio
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="StockAgent - A股投资信号聚合系统")
    subparsers = parser.add_subparsers(dest="command", help="运行模式")

    # web 模式
    web_parser = subparsers.add_parser("web", help="启动 Web 面板")
    web_parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    web_parser.add_argument("--port", type=int, default=8000, help="监听端口")
    web_parser.add_argument("--reload", action="store_true", help="开发模式热重载")

    # scheduler 模式
    subparsers.add_parser("scheduler", help="启动定时任务调度器")

    # pipeline 模式 (手动执行一次)
    subparsers.add_parser("pipeline", help="手动执行一次抓取+分析流水线")

    # monitor 模式 (手动执行一次盘中监控)
    subparsers.add_parser("monitor", help="手动执行一次条件匹配")

    # intraday-watch 模式 (盘中分时线盯盘, 手动跑一次)
    subparsers.add_parser(
        "intraday-watch", help="盘中分时线盯盘: 候选股持续踩破分时线即预警(手动执行一次)"
    )

    # eod-review 模式 (盘后复盘, 手动跑一次)
    subparsers.add_parser("eod-review", help="盘后复盘: 汇总候选股是否符合博主预期(手动执行一次)")

    # init-db 模式
    subparsers.add_parser("init-db", help="初始化数据库表")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    # 加载配置
    from stockagent.core.config import config as app_config

    app_config.load()

    if args.command == "web":
        import uvicorn

        from stockagent.web.app import create_app

        app = create_app()
        uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)

    elif args.command == "scheduler":
        from stockagent.scheduler.scheduler import run_scheduler

        asyncio.run(run_scheduler())

    elif args.command == "pipeline":
        from stockagent.analysis.pipeline import AnalysisPipeline

        asyncio.run(AnalysisPipeline().run())

    elif args.command == "monitor":
        from stockagent.engine.matcher import ConditionMatchingEngine

        asyncio.run(ConditionMatchingEngine().run())

    elif args.command == "intraday-watch":
        from stockagent.scheduler.jobs.intraday_watch_job import intraday_watch_job

        asyncio.run(intraday_watch_job())

    elif args.command == "eod-review":
        from stockagent.scheduler.jobs.post_market_review_job import post_market_review_job

        asyncio.run(post_market_review_job())

    elif args.command == "init-db":
        asyncio.run(_init_db())

    else:
        parser.print_help()
        sys.exit(1)


async def _init_db() -> None:
    """初始化数据库表。"""
    from stockagent.core.db import init_db

    await init_db()
    print("数据库表已创建。")


if __name__ == "__main__":
    main()
