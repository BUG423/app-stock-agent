"""分时线破位检测单测 —— 合成分时序列, 无网络。分时均价线自算(Σpv/Σv)。"""

from stockagent.engine.intraday_watch import condition_watch, vwap_break_status


def _series(prices, vol=1000):
    """价格列表 → 分时点序列(等量, 便于心算累计均价)。"""
    return [{"price": p, "volume": vol} for p in prices]


def test_insufficient_data():
    assert vwap_break_status(_series([10] * 5))["deteriorating"] is False


def test_holding_above_line_not_deteriorating():
    # 一路小幅上行: 现价始终 >= 累计均价 → 健康
    prices = [10 + i * 0.02 for i in range(40)]
    r = vwap_break_status(_series(prices))
    assert r["deteriorating"] is False
    assert r["cur_vs_vwap_pct"] >= 0


def test_persistent_break_deteriorating():
    # 前高后持续下行, 尾段明显且持续低于累计均价 → 恶化
    prices = [11.0] * 10 + [10.8 - i * 0.03 for i in range(30)]
    r = vwap_break_status(_series(prices))
    assert r["deteriorating"] is True
    assert r["cur_vs_vwap_pct"] < -1.0
    assert r["below_ratio"] >= 0.6


def test_dip_then_recover_not_deteriorating():
    # 中途跌破均价, 但尾部拉回到均价上方 → "踩破后回升", 不算恶化
    prices = [10.0] * 10 + [9.5] * 8 + [10.0, 10.3, 10.5] + [10.8] * 19
    r = vwap_break_status(_series(prices))
    assert r["deteriorating"] is False
    assert r["cur_vs_vwap_pct"] > 0


def test_state_holding_above():
    prices = [10 + i * 0.02 for i in range(40)]
    r = vwap_break_status(_series(prices))
    assert r["state"] == "站稳上方" and r["on_track"] is True


def test_state_persistent_break():
    prices = [11.0] * 10 + [10.8 - i * 0.03 for i in range(30)]
    r = vwap_break_status(_series(prices))
    assert r["state"] == "持续踩破" and r["on_track"] is False


# ---- condition_watch: 忠实评估"回踩参考线+分时企稳" ----
def test_condition_compliant():
    vs = {"state": "站稳上方", "deteriorating": False}
    r = condition_watch(current_price=10.1, ref_line=10.0, vwap_status=vs)
    assert r["on_track"] is True and "符合" in r["verdict"]


def test_condition_too_high_no_chase():
    vs = {"state": "站稳上方", "deteriorating": False}
    r = condition_watch(11.0, 10.0, vs)  # 高于参考位10% → 勿追
    assert r["on_track"] is False and "尚未回踩" in r["verdict"]


def test_condition_broken():
    vs = {"state": "持续踩破", "deteriorating": True}
    r = condition_watch(9.9, 10.0, vs)
    assert r["on_track"] is False and "已破位" in r["verdict"]


def test_condition_near_but_unstable():
    vs = {"state": "贴线回踩", "deteriorating": False}
    r = condition_watch(9.95, 10.0, vs)
    assert r["on_track"] is False and "未企稳" in r["verdict"]


def test_condition_no_ref_falls_back_to_vwap():
    vs = {"state": "站稳上方", "deteriorating": False}
    r = condition_watch(10.0, 0, vs)  # 博主没给参考线 → 纯分时线盯盘
    assert r["near_ref"] is None and "分时线盯盘" in r["verdict"]
