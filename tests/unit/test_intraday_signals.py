"""分时策略事件检测单测（合成数据，无网络）。只测博主买入条件术语对应的 4 类事件。"""

from stockagent.analysis.intraday_signals import classify_intraday_state, detect_intraday_signals


def _pt(i, price, avg):
    return {"time": f"09:{30 + i:02d}", "price": price, "avg": avg, "vol": 100}


def test_too_few_points_returns_empty():
    data = [_pt(i, 10, 10) for i in range(10)]
    assert detect_intraday_signals(data) == []


def test_persistent_break_detected():
    # 20 分钟持续低于均价线 → 破位（没承接住）区间
    data = [_pt(i, 9.0, 10.0) for i in range(20)]
    sig = detect_intraday_signals(data, persist_min=12)
    breaks = [s for s in sig if s["kind"] == "break"]
    assert len(breaks) == 1
    assert "持续踩破" in breaks[0]["label"] and "没承接" in breaks[0]["label"]
    assert breaks[0]["start_time"] and breaks[0]["end_time"]


def test_support_after_dip_detected():
    # 5 上 → 4 下(回踩) → 10 上(站回并保持) → 承接住
    data = (
        [_pt(i, 10.5, 10.0) for i in range(5)]
        + [_pt(5 + i, 9.5, 10.0) for i in range(4)]
        + [_pt(9 + i, 10.5, 10.0) for i in range(10)]
    )
    sig = detect_intraday_signals(data, persist_min=12, dip_min=3)
    sups = [s for s in sig if s["kind"] == "support"]
    assert len(sups) >= 1 and "承接住" in sups[0]["label"]
    assert "分时企稳" in sups[0]["label"]


def test_realtime_tail_does_not_confirm_support_too_early():
    # 尾端刚上穿 1 分钟，没有后续 3 分钟确认，不应提前标记分时企稳。
    data = (
        [_pt(i, 10.5, 10.0) for i in range(10)]
        + [_pt(10 + i, 9.5, 10.0) for i in range(4)]
        + [_pt(14, 10.1, 10.0)]
    )
    assert not [s for s in detect_intraday_signals(data) if s["kind"] == "support"]


def test_current_state_uses_blogger_terms():
    data = [_pt(i, 9.8, 10.0) for i in range(15)]
    state = classify_intraday_state(data, detect_intraday_signals(data))
    assert state["code"] == "broken"
    assert state["label"] == "持续踩破·没承接"
    assert "15分钟" in state["detail"]


def test_ma5_pullback_detected():
    # 全程贴着 5 日线(=10)附近 → 回踩五日线
    data = [_pt(i, 10.0, 10.0) for i in range(16)]
    sig = detect_intraday_signals(data, ma5=10.0)
    m5 = [s for s in sig if s["kind"] == "ma5"]
    assert len(m5) >= 1 and "回踩5日线" in m5[0]["label"]


def test_ma5_skipped_when_no_ref():
    data = [_pt(i, 10.0, 10.0) for i in range(16)]
    assert not [s for s in detect_intraday_signals(data, ma5=0) if s["kind"] == "ma5"]


def test_high_fade_detected():
    # 冲高至局部顶(11.0, i=5)后回落>1% → 冲高回落
    up = [_pt(i, round(10 + i * 0.2, 2), 10.0) for i in range(6)]  # 10.0→11.0
    down = [_pt(6 + i, round(10.9 - i * 0.2, 2), 10.4) for i in range(12)]  # 10.9→回落
    sig = detect_intraday_signals(up + down, prev_close=10.0)
    hf = [s for s in sig if s["kind"] == "high_fade"]
    assert len(hf) >= 1 and "冲高回落" in hf[0]["label"]
