"""旧 crontab 入口；盘中邮件已迁移至应用内 10:30 定时任务。"""

import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("intraday_report")


def main() -> None:
    """避免服务器遗留 crontab 在 9:35/11:00/13:30/14:45 继续重复发信。"""
    logger.info("旧盘中快报入口已停用；scheduler 将在交易日 10:30 统一发送盯盘汇总")


if __name__ == "__main__":
    main()
