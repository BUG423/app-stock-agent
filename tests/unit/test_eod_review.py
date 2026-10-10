"""盘后复盘评估纯函数单测。"""

from stockagent.analysis.eod_review import assess_candidate, summarize


def test_bullish_up_and_holds_meets():
    met, _ = assess_candidate("bullish", 2.0, deteriorating=False)
    assert met is True


def test_bullish_small_dip_within_tolerance_meets():
    met, _ = assess_candidate("bullish", -0.5, deteriorating=False)
    assert met is True


def test_bullish_down_fails():
    met, _ = assess_candidate("bullish", -3.0, deteriorating=False)
    assert met is False


def test_bullish_clearly_up_meets_even_if_intraday_wobbled():
    # 明显上涨(>=1%)=方向预估兑现, 即便盘中一度破线仍算符合(破线仅附注)
    met, note = assess_candidate("bullish", 5.0, deteriorating=True)
    assert met is True
    assert "踩破分时线" in note  # 附注提示盘中走弱


def test_bullish_flat_but_broke_vwap_fails():
    # 基本走平(-1%~+1%)且持续踩破分时线 → 没守住 → 不符合
    met, note = assess_candidate("bullish", 0.3, deteriorating=True)
    assert met is False
    assert "踩破分时线" in note


def test_bullish_flat_and_holds_meets():
    met, _ = assess_candidate("bullish", 0.3, deteriorating=False)
    assert met is True


def test_bearish_down_is_correct():
    met, _ = assess_candidate("bearish", -2.0, deteriorating=False)
    assert met is True


def test_bearish_up_is_wrong():
    met, _ = assess_candidate("bearish", 1.0, deteriorating=False)
    assert met is False


def test_neutral_records_only():
    met, _ = assess_candidate("neutral", 3.0, deteriorating=False)
    assert met is None


def test_summarize_counts():
    results = [
        {"met": True},
        {"met": True},
        {"met": False},
        {"met": None},
    ]
    s = summarize(results)
    assert s["total"] == 4 and s["graded"] == 3
    assert s["met"] == 2 and s["unmet"] == 1 and s["watch_only"] == 1
    assert s["met_rate"] == round(2 / 3 * 100, 1)
