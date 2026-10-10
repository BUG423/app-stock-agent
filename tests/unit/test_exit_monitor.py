"""妖板次日兑现决策单测 —— A股T+1: 买入日不可卖，最早次日兑现。"""

from datetime import date

from stockagent.engine.exit_monitor import _trading_days_held, yaoban_t1_reason


def test_buy_day_cannot_sell():
    # 买入日(held==1) → A股T+1不可卖，即便未涨停也不离场
    assert yaoban_t1_reason(held=1, is_limit_up=False, ret_pct=2.3) is None


def test_next_day_exits():
    # 次日(held==2)未连板 → 尽早了结
    r = yaoban_t1_reason(held=2, is_limit_up=False, ret_pct=-0.7)
    assert r is not None and "次日兑现" in r


def test_limit_up_holds_for_continuation():
    # 涨停(可能连板) → 留到再下一日，不卖
    assert yaoban_t1_reason(held=2, is_limit_up=True, ret_pct=9.9) is None
    assert yaoban_t1_reason(held=3, is_limit_up=True, ret_pct=9.9) is None


def test_later_days_still_exit():
    # 持到第3、5日仍未连板 → 继续尽早了结(别拖到T+3塌方)
    assert yaoban_t1_reason(held=3, is_limit_up=False, ret_pct=-3.0) is not None
    assert yaoban_t1_reason(held=5, is_limit_up=False, ret_pct=-8.0) is not None


def test_held_zero_noop():
    # held=0(理论上不出现) → None
    assert yaoban_t1_reason(held=0, is_limit_up=False, ret_pct=0.0) is None


def test_trading_days_held_skips_weekend_for_t1():
    assert _trading_days_held(date(2026, 7, 17), date(2026, 7, 17)) == 1
    assert _trading_days_held(date(2026, 7, 17), date(2026, 7, 19)) == 1
    assert _trading_days_held(date(2026, 7, 17), date(2026, 7, 20)) == 2
