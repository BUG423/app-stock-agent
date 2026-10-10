"""股票名称候选与历史收盘价接口测试。"""

from datetime import date, datetime

from stockagent.market.stock_client import StockClient


class FakeResponse:
    content = (
        'var suggestvalue="世茂能源,11,605028,sh605028,世茂能源;'
        '*ST世茂,11,600823,sh600823,*ST世茂;腾讯控股,31,00700,hk00700,腾讯控股";'
    ).encode("gb18030")

    def raise_for_status(self):
        return None


class FakeSession:
    def get(self, *_args, **_kwargs):
        return FakeResponse()


class FakeJsonResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_search_stock_candidates_keeps_all_a_share_matches():
    client = StockClient()
    client._session = FakeSession()
    client._rate_limit = lambda: None

    assert client.search_stock_candidates("世茂") == [
        {"name": "世茂能源", "code": "605028"},
        {"name": "*ST世茂", "code": "600823"},
    ]


def test_resolve_stock_code_prefers_exact_name_not_first_partial_match():
    client = StockClient()
    client.search_stock_candidates = lambda *_args, **_kwargs: [
        {"name": "*ST世茂", "code": "600823"},
        {"name": "世茂能源", "code": "605028"},
    ]

    assert client.resolve_stock_code("世茂能源") == "605028"


def test_resolve_stock_code_recovers_unique_asr_homophone_by_pinyin(monkeypatch):
    client = StockClient()

    def fake_search(keyword, _limit=10, **_kwargs):
        if keyword == "jinwangdianzi":
            return [{"name": "景旺电子", "code": "603228"}]
        return []

    pinyin = {"谨望电子": "jinwangdianzi", "景旺电子": "jingwangdianzi"}
    client.search_stock_candidates = fake_search
    monkeypatch.setattr(client, "_name_to_pinyin", lambda name: pinyin.get(name, ""))

    assert client.resolve_stock_code("谨望电子") == "603228"


def test_resolve_stock_code_rejects_ambiguous_pinyin_candidates(monkeypatch):
    client = StockClient()

    def fake_search(keyword, _limit=10, **_kwargs):
        if keyword == "abcdefgh":
            return [
                {"name": "候选甲", "code": "600001"},
                {"name": "候选乙", "code": "600002"},
            ]
        return []

    pinyin = {"误听名称": "abcdefgh", "候选甲": "abcdefgi", "候选乙": "abcdefgj"}
    client.search_stock_candidates = fake_search
    monkeypatch.setattr(client, "_name_to_pinyin", lambda name: pinyin.get(name, ""))

    assert client.resolve_stock_code("误听名称") is None


def test_get_close_on_or_before_uses_previous_trading_day():
    client = StockClient()
    client.get_kline = lambda *_args, **_kwargs: [
        {"date": "2026-07-15", "close": 24.1},
        {"date": "2026-07-16", "close": 24.5},
        {"date": "2026-07-20", "close": 25.0},
    ]

    assert client.get_close_on_or_before("605028", date(2026, 7, 17)) == 24.5


def test_get_price_at_or_before_prefers_last_visible_intraday_minute():
    client = StockClient()
    client.get_intraday_hist = lambda *_args, **_kwargs: [
        {"time": "09:33", "price": 24.9},
        {"time": "09:34", "price": 24.94},
        {"time": "09:35", "price": 24.53},
    ]
    client.get_close_on_or_before = lambda *_args, **_kwargs: 24.38

    price = client.get_price_at_or_before("605028", datetime(2026, 7, 16, 9, 34, 50))

    assert price == 24.94


def test_intraday_failure_falls_back_to_previous_day_not_future_close():
    client = StockClient()
    client.get_intraday_hist = lambda *_args, **_kwargs: []
    captured = {}

    def fake_close(_code, target_day):
        captured["target_day"] = target_day
        return 23.82

    client.get_close_on_or_before = fake_close

    client.get_price_at_or_before("605028", datetime(2026, 7, 16, 9, 34))

    assert captured["target_day"] == date(2026, 7, 15)


def test_historical_intraday_rejects_rows_from_wrong_date():
    client = StockClient()
    client._rate_limit = lambda: None

    class HistorySession:
        def get(self, url, **_kwargs):
            assert url.endswith("/trends2/get")
            return FakeJsonResponse(
                {
                    "data": {
                        "trends": [
                            "2026-07-16 09:34,0,25.83,26.15,25.83,3135,8170774,25.175",
                            "2026-07-17 09:34,24.80,24.94,24.96,24.80,2216,5528815,24.560",
                        ]
                    }
                }
            )

    client._session = HistorySession()

    rows = client.get_intraday_hist("605028", "20260716")

    assert rows == [
        {
            "date": "2026-07-16",
            "time": "09:34",
            "price": 25.83,
            "high": 26.15,
            "low": 25.83,
            "volume": 3135.0,
            "vwap": 25.175,
            "period_minutes": 1,
        }
    ]
