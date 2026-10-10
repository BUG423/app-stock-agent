"""OCR 持仓标的多证据消歧测试。"""

from datetime import datetime

from stockagent.analysis.ocr_stock_resolver import (
    extract_ocr_market_evidence,
    resolve_ocr_holding_stock,
)

OCR_HOLDING = """总市值
266,858.00
持仓股
市值
盈亏
持仓/可用
成本/现价
世茂
源
38,793.12
10700
21.314
17.010%
24.840
266,8
24.940
"""

OCR_HOLDING_SHORT_NAME = """269,961.00
1,291.19
持仓股
市值
盈亏
持仓/可用
成本/现价
世茂
96.12
10700
21.314
8.370%
25.230
**2874
+41,896.12
"""


class FakeMarket:
    def __init__(self, st_close: float = 1.5, main_close: float = 24.94):
        self.st_close = st_close
        self.main_close = main_close

    def search_stock_candidates(self, _keyword, limit=10):
        assert limit == 10
        return [
            {"name": "世茂能源", "code": "605028"},
            {"name": "*ST世茂", "code": "600823"},
        ]

    def get_close_on_or_before(self, code, _target_day):
        return self.main_close if code == "605028" else self.st_close


def test_extracts_name_fragments_and_market_value_per_share():
    evidence = extract_ocr_market_evidence(OCR_HOLDING)

    assert evidence["fragments"] == ["世茂", "源"]
    assert 24.94 in evidence["prices"]
    assert 24.94 in evidence["implied_prices"]
    assert 1.0 not in evidence["implied_prices"]


def test_resolves_real_ocr_holding_with_name_and_price_evidence():
    result = resolve_ocr_holding_stock(
        OCR_HOLDING,
        datetime(2026, 7, 17, 14, 30),
        FakeMarket(),
    )

    assert result is not None
    assert result["code"] == "605028"
    assert result["name"] == "世茂能源"
    assert result["confidence"] == "high"
    assert result["score"] >= 7
    assert any("市值÷持仓数" in item for item in result["evidence"])


def test_refuses_ambiguous_candidates_even_when_both_prices_match():
    result = resolve_ocr_holding_stock(
        OCR_HOLDING,
        datetime(2026, 7, 17, 14, 30),
        FakeMarket(st_close=24.94),
    )

    assert result is None


def test_short_name_fragment_is_disambiguated_by_real_holding_price():
    evidence = extract_ocr_market_evidence(OCR_HOLDING_SHORT_NAME)
    assert evidence["fragments"] == ["世茂"]
    assert 25.23 in evidence["implied_prices"]
    assert 1.0 not in evidence["implied_prices"]

    result = resolve_ocr_holding_stock(
        OCR_HOLDING_SHORT_NAME,
        datetime(2026, 7, 16, 9, 34),
        FakeMarket(st_close=1.0, main_close=25.23),
    )

    assert result is not None
    assert result["code"] == "605028"
    assert result["runner_up_score"] < result["score"] - 2


def test_refuses_text_without_position_name_fragment():
    assert (
        resolve_ocr_holding_stock(
            "总资产 100000\n现价 24.94",
            datetime(2026, 7, 17),
            FakeMarket(),
        )
        is None
    )
