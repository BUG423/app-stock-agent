"""东方财富直连行情客户端 —— 不依赖 AKShare，直接请求东方财富 API。"""

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

# 东方财富 API 常量
_EM_UT = "fa5fd1943c7b386f172d6893dbbd1180"
_EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com",
}


def _market_prefix(stock_code: str) -> str:
    """股票代码 → 东方财富市场前缀。沪市=1, 深市/创业板=0."""
    if stock_code.startswith(("6", "9")):
        return "1"
    return "0"


class EastMoneyClient:
    """东方财富直连行情客户端。"""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[Any, float]] = {}
        self._session = requests.Session()
        self._session.headers.update(_EM_HEADERS)

    def _cache_get(self, key: str, ttl: float = 30) -> Any | None:
        if key in self._cache:
            val, ts = self._cache[key]
            if time.time() - ts < ttl:
                return val
        return None

    def _cache_set(self, key: str, val: Any, ttl: float = 30) -> None:
        self._cache[key] = (val, time.time())

    def get_realtime_quote(self, stock_code: str) -> dict[str, Any]:
        """获取个股实时行情。"""
        cache_key = f"rt_{stock_code}"
        cached = self._cache_get(cache_key, ttl=10)
        if cached is not None:
            return cached

        try:
            secid = f"{_market_prefix(stock_code)}.{stock_code}"
            resp = self._session.get(
                "https://push2.eastmoney.com/api/qt/stock/get",
                params={
                    "secid": secid,
                    "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f170",
                    "ut": _EM_UT,
                },
                timeout=10,
            )
            d = resp.json().get("data")
            if not d:
                return {}

            # 东方财富价格单位是"分"，需除以100
            data = {
                "stock_code": stock_code,
                "stock_name": d.get("f58", ""),
                "current_price": d.get("f43", 0) / 100,
                "prev_close": d.get("f60", 0) / 100,
                "open": d.get("f46", 0) / 100,
                "high": d.get("f44", 0) / 100,
                "low": d.get("f45", 0) / 100,
                "volume": d.get("f47", 0),
                "amount": d.get("f48", 0),
                "change_pct": d.get("f170", 0) / 100,
            }
            self._cache_set(cache_key, data, ttl=10)
            return data

        except Exception as e:
            logger.error(f"获取实时行情失败 ({stock_code}): {e}")
            return {}

    def get_kline(self, stock_code: str, days: int = 60) -> list[dict[str, Any]]:
        """获取日K线数据（前复权）。"""
        cache_key = f"kline_{stock_code}_{days}"
        cached = self._cache_get(cache_key, ttl=300)
        if cached is not None:
            return cached

        try:
            secid = f"{_market_prefix(stock_code)}.{stock_code}"
            resp = self._session.get(
                "https://push2his.eastmoney.com/api/qt/stock/kline/get",
                params={
                    "secid": secid,
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57",
                    "klt": "101",  # 日K
                    "fqt": "1",  # 前复权
                    "end": "20500101",
                    "lmt": str(days),
                    "ut": _EM_UT,
                },
                timeout=10,
            )
            klines_raw = resp.json().get("data", {}).get("klines", [])

            result = []
            for line in klines_raw:
                parts = line.split(",")
                if len(parts) < 7:
                    continue
                result.append(
                    {
                        "date": parts[0],
                        "open": float(parts[1]),
                        "close": float(parts[2]),
                        "high": float(parts[3]),
                        "low": float(parts[4]),
                        "volume": int(parts[5]),
                        "amount": float(parts[6]),
                    }
                )

            self._cache_set(cache_key, result, ttl=300)
            return result

        except Exception as e:
            logger.error(f"获取K线失败 ({stock_code}): {e}")
            return []

    def get_technical_indicators(self, stock_code: str) -> dict[str, Any]:
        """获取技术指标（均线、支撑位、压力位、企稳判断）。"""
        cache_key = f"tech_{stock_code}"
        cached = self._cache_get(cache_key, ttl=300)
        if cached is not None:
            return cached

        klines = self.get_kline(stock_code, days=60)
        if not klines or len(klines) < 5:
            return {}

        closes = [k["close"] for k in klines]
        highs = [k["high"] for k in klines]
        lows = [k["low"] for k in klines]

        def _ma(values: list[float], n: int) -> float:
            if len(values) < n:
                return 0.0
            return sum(values[-n:]) / n

        # 近20日最低/最高
        recent_lows = lows[-20:]
        recent_highs = highs[-20:]
        support = min(recent_lows)
        resistance = max(recent_highs)

        # 企稳判断：最近3天低点不创新低 + 今日收盘≥昨日收盘
        stable = False
        if len(closes) >= 4 and len(lows) >= 4:
            stable = min(lows[-3:]) >= lows[-4] and closes[-1] >= closes[-2]

        # 放量判断：今日量 > 5日均量×1.5
        volumes = [k["volume"] for k in klines]
        avg_vol_5 = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else 0
        surge = volumes[-1] > avg_vol_5 * 1.5 if avg_vol_5 > 0 else False

        data = {
            "stock_code": stock_code,
            "current_price": closes[-1],
            "prev_close": closes[-2] if len(closes) >= 2 else closes[-1],
            "ma5": round(_ma(closes, 5), 3),
            "ma10": round(_ma(closes, 10), 3),
            "ma20": round(_ma(closes, 20), 3),
            "ma60": round(_ma(closes, 60), 3) if len(closes) >= 60 else 0.0,
            "support": round(support, 3),
            "resistance": round(resistance, 3),
            "is_stable": stable,
            "volume_surge": surge,
            "klines": klines,
        }

        self._cache_set(cache_key, data, ttl=300)
        return data

    def get_avg_volume(self, stock_code: str, days: int = 20) -> float:
        """获取 N 日平均成交量。"""
        klines = self.get_kline(stock_code, days=days)
        if not klines:
            return 0.0
        volumes = [k["volume"] for k in klines if k["volume"] > 0]
        return sum(volumes) / len(volumes) if volumes else 0.0

    def resolve_stock_code(self, stock_name: str) -> str | None:
        """按名称模糊查股票代码（走东方财富搜索接口）。"""
        if not stock_name or stock_name == "unknown":
            return None

        cache_key = f"resolve_{stock_name}"
        cached = self._cache_get(cache_key, ttl=86400)
        if cached is not None:
            return cached

        try:
            resp = self._session.get(
                "https://searchapi.eastmoney.com/api/suggest/get",
                params={
                    "input": stock_name,
                    "type": "14",
                    "count": "5",
                },
                timeout=5,
            )
            items = resp.json().get("QuotationCodeTable", {}).get("Data", [])
            if items:
                code = items[0].get("Code", "")
                self._cache_set(cache_key, code, ttl=86400)
                return code or None
            return None
        except Exception as e:
            logger.error(f"股票名称查代码失败 ({stock_name}): {e}")
            return None


# 全局单例
_em_client: EastMoneyClient | None = None


def get_eastmoney_client() -> EastMoneyClient:
    global _em_client
    if _em_client is None:
        _em_client = EastMoneyClient()
    return _em_client
