"""K线 API 计算辅助函数测试。"""

from stockagent.web.routers.kline import _live_ma5


def test_live_ma5_adds_current_price_when_daily_excludes_today():
    daily = [
        {"date": "2026-07-10", "close": 10},
        {"date": "2026-07-13", "close": 11},
        {"date": "2026-07-14", "close": 12},
        {"date": "2026-07-15", "close": 13},
    ]
    assert _live_ma5(daily, 14, today="2026-07-16") == 12


def test_live_ma5_does_not_duplicate_today_close():
    daily = [
        {"date": "2026-07-10", "close": 10},
        {"date": "2026-07-13", "close": 11},
        {"date": "2026-07-14", "close": 12},
        {"date": "2026-07-15", "close": 13},
        {"date": "2026-07-16", "close": 14},
    ]
    assert _live_ma5(daily, 99, today="2026-07-16") == 12
