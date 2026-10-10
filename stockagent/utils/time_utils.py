"""交易时间相关工具函数。"""

from datetime import date, datetime, time, timedelta

import pytz

CST = pytz.timezone("Asia/Shanghai")

# A股交易时间
MORNING_START = time(9, 30)
MORNING_END = time(11, 30)
AFTERNOON_START = time(13, 0)
AFTERNOON_END = time(15, 0)


def is_trading_time(dt: datetime | None = None) -> bool:
    """判断当前是否为 A 股交易时间。"""
    if dt is None:
        dt = datetime.now(CST)
    t = dt.time()
    return (MORNING_START <= t <= MORNING_END) or (AFTERNOON_START <= t <= AFTERNOON_END)


def is_trading_day(d: date | None = None) -> bool:
    """判断是否为交易日（简化版：周一到周五）。完整版需接入交易日历。"""
    if d is None:
        d = date.today()
    return d.weekday() < 5  # 0=Monday, 6=Sunday


def next_trading_day(d: date | None = None) -> date:
    """获取下一个交易日。"""
    if d is None:
        d = date.today()
    next_day = d + timedelta(days=1)
    while not is_trading_day(next_day):
        next_day += timedelta(days=1)
    return next_day


def today_cst() -> date:
    """获取当前中国时区的日期。"""
    return datetime.now(CST).date()


def now_cst() -> datetime:
    """获取当前中国时区的时间。"""
    return datetime.now(CST)
