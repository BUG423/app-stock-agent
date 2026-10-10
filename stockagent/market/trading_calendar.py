"""A股交易日历 —— 判断交易日、交易时间等。"""

import logging
from datetime import date, datetime, time

import pytz

logger = logging.getLogger(__name__)

CST = pytz.timezone("Asia/Shanghai")

MORNING_START = time(9, 30)
MORNING_END = time(11, 30)
AFTERNOON_START = time(13, 0)
AFTERNOON_END = time(15, 0)

# 已知的 A股休市日期（节假日），后续可接入 AKShare 交易日历 API 自动更新
_KNOWN_HOLIDAYS: set[date] = set()


def is_trading_day(d: date | None = None) -> bool:
    """判断是否为 A 股交易日。

    简化规则：周一至周五 且 非已知节假日。
    完整实现需接入 AKShare 交易日历。
    """
    if d is None:
        d = date.today()
    # 周末不交易
    if d.weekday() >= 5:
        return False
    # 节假日不交易
    if d in _KNOWN_HOLIDAYS:
        return False
    return True


def is_trading_time(dt: datetime | None = None) -> bool:
    """判断当前是否在 A 股交易时间内。"""
    if dt is None:
        dt = datetime.now(CST)
    t = dt.time()
    return (MORNING_START <= t <= MORNING_END) or (AFTERNOON_START <= t <= AFTERNOON_END)


def is_market_open() -> bool:
    """判断当前市场是否开盘（交易日 + 交易时间）。"""
    now = datetime.now(CST)
    return is_trading_day(now.date()) and is_trading_time(now)


def next_trading_day(d: date | None = None) -> date:
    """获取下一个交易日。"""
    if d is None:
        d = date.today()
    d = d + __import__("datetime").timedelta(days=1)
    while not is_trading_day(d):
        d = d + __import__("datetime").timedelta(days=1)
    return d


def set_holidays(holidays: list[date]) -> None:
    """设置节假日列表。"""
    _KNOWN_HOLIDAYS.clear()
    _KNOWN_HOLIDAYS.update(holidays)
