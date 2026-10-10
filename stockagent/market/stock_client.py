"""股票行情客户端 —— 新浪、腾讯和东方财富公开行情接口。"""

import json
import logging
import re
import time
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

import requests

logger = logging.getLogger(__name__)

_SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn",
}

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:139.0) Gecko/20100101 Firefox/139.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0",
]


def _sina_symbol(stock_code: str) -> str:
    """股票代码 → 新浪格式 (sh600707 / sz000001)。"""
    if stock_code.startswith(("6", "9")):
        return f"sh{stock_code}"
    return f"sz{stock_code}"


# A股代码前缀白名单：沪主板/科创/深主板中小/创业板；北交所走 4/8 打头或 920。
_A_SHARE_PREFIXES = (
    "600",
    "601",
    "603",
    "605",
    "606",
    "607",
    "608",  # 沪市主板
    "688",
    "689",  # 科创板
    "900",  # 沪B
    "000",
    "001",
    "002",
    "003",
    "004",  # 深市主板/中小
    "300",
    "301",
    "302",  # 创业板
    "200",
    "201",  # 深B
)


def is_a_share(stock_code: str) -> bool:
    """判断是否为 A 股（含北交所/B股）代码，用于把美股/港股等非 A 股挡在盯盘管道外。

    规则：必须 6 位纯数字，且前缀在白名单内，或北交所（4/8 打头 / 920）。
    美股代码含字母、港股为 5 位，均会被判 False。
    """
    code = (stock_code or "").strip()
    if not (code.isdigit() and len(code) == 6):
        return False
    if code[:3] in _A_SHARE_PREFIXES:
        return True
    # 北交所：83xxxx/87xxxx/88xxxx/43xxxx/920xxx
    return code[0] in ("4", "8") or code[:3] == "920"


class StockClient:
    """股票行情客户端（新浪 + 腾讯）。"""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[Any, float]] = {}
        self._session = requests.Session()
        # 行情接口不继承宿主机代理。失效代理会让新浪/腾讯接口随机断连，
        # 且这些公开行情接口本身无需代理。
        self._session.trust_env = False
        self._session.headers.update(_SINA_HEADERS)
        self._last_request_time: float = 0
        self._min_interval: float = 3.0

    def _rate_limit(self) -> None:
        """限速：每次请求间隔至少 _min_interval 秒。"""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.time()
        self._rotate_ua()

    def _rotate_ua(self) -> None:
        """轮换 User-Agent。"""
        import random

        self._session.headers["User-Agent"] = random.choice(_USER_AGENTS)

    def _cache_get(self, key: str, ttl: float = 30) -> Any | None:
        if key in self._cache:
            val, ts = self._cache[key]
            if time.time() - ts < ttl:
                return val
        return None

    def _cache_set(self, key: str, val: Any, ttl: float = 30) -> None:
        self._cache[key] = (val, time.time())

    def get_realtime_quote(self, stock_code: str) -> dict[str, Any]:
        """获取个股实时行情（新浪 → 腾讯 fallback）。"""
        cache_key = f"rt_{stock_code}"
        cached = self._cache_get(cache_key, ttl=10)
        if cached is not None:
            return cached

        # 先尝试新浪
        data = self._get_realtime_quote_sina(stock_code)
        if data:
            return data

        # fallback: 腾讯
        data = self._get_realtime_quote_tencent(stock_code)
        if data:
            return data

        return {}

    def _get_realtime_quote_sina(self, stock_code: str) -> dict[str, Any]:
        try:
            self._rate_limit()
            symbol = _sina_symbol(stock_code)
            resp = self._session.get(
                f"https://hq.sinajs.cn/list={symbol}",
                timeout=10,
            )
            if "Forbidden" in resp.text or len(resp.text.strip()) < 10:
                return {}
            text = resp.text
            match = re.search(r'"(.+)"', text)
            if not match:
                return {}

            fields = match.group(1).split(",")
            if len(fields) < 32:
                return {}

            data = {
                "stock_code": stock_code,
                "stock_name": fields[0],
                "open": float(fields[1]),
                "prev_close": float(fields[2]),
                "current_price": float(fields[3]),
                "high": float(fields[4]),
                "low": float(fields[5]),
                "volume": int(float(fields[8])),
                "amount": float(fields[9]),
                "change_pct": round(
                    (float(fields[3]) - float(fields[2])) / float(fields[2]) * 100, 2
                )
                if float(fields[2]) > 0
                else 0,
            }
            self._cache_set(f"rt_{stock_code}", data, ttl=10)
            return data
        except Exception as e:
            logger.debug(f"新浪行情失败 ({stock_code}): {e}")
            return {}

    def _get_realtime_quote_tencent(self, stock_code: str) -> dict[str, Any]:
        """腾讯行情 API fallback。"""
        cache_key = f"rt_{stock_code}"
        try:
            self._rate_limit()
            symbol = _sina_symbol(stock_code)
            resp = self._session.get(
                f"https://qt.gtimg.cn/q={symbol}",
                timeout=10,
            )
            resp.encoding = "gbk"
            text = resp.text
            match = re.search(r'"(.+)"', text)
            if not match:
                return {}

            fields = match.group(1).split("~")
            if len(fields) < 45:
                return {}

            prev_close = float(fields[4]) if fields[4] else 0
            current = float(fields[3]) if fields[3] else 0
            data = {
                "stock_code": stock_code,
                "stock_name": fields[1],
                "open": float(fields[5]) if fields[5] else 0,
                "prev_close": prev_close,
                "current_price": current,
                "high": float(fields[33]) if fields[33] else 0,
                "low": float(fields[34]) if fields[34] else 0,
                "volume": int(float(fields[6])) if fields[6] else 0,
                "amount": float(fields[37]) if fields[37] else 0,
                "change_pct": round((current - prev_close) / prev_close * 100, 2)
                if prev_close > 0
                else 0,
            }
            self._cache_set(cache_key, data, ttl=10)
            return data
        except Exception as e:
            logger.error(f"腾讯行情失败 ({stock_code}): {e}")
            return {}

    def get_kline(self, stock_code: str, days: int = 60) -> list[dict[str, Any]]:
        """获取日K线数据。"""
        cache_key = f"kline_{stock_code}_{days}"
        cached = self._cache_get(cache_key, ttl=300)
        if cached is not None:
            return cached

        try:
            symbol = _sina_symbol(stock_code)
            resp = self._session.get(
                "https://quotes.sina.cn/cn/api/jsonp_v2.php/var/CN_MarketDataService.getKLineData",
                params={
                    "symbol": symbol,
                    "scale": "240",  # 日K
                    "ma": "no",
                    "datalen": str(days),
                },
                timeout=10,
            )
            match = re.search(r"\[.*\]", resp.text)
            if not match:
                return []

            raw = json.loads(match.group())
            result = []
            for item in raw:
                result.append(
                    {
                        "date": item.get("day", ""),
                        "open": float(item.get("open", 0)),
                        "close": float(item.get("close", 0)),
                        "high": float(item.get("high", 0)),
                        "low": float(item.get("low", 0)),
                        "volume": int(float(item.get("volume", 0))),
                    }
                )

            self._cache_set(cache_key, result, ttl=300)
            return result

        except Exception as e:
            logger.error(f"获取K线失败 ({stock_code}): {e}")
            return []

    @staticmethod
    def _em_secid(stock_code: str) -> str:
        """东方财富 secid：沪市 1.x，深市 0.x。"""
        return f"1.{stock_code}" if stock_code.startswith(("6", "9")) else f"0.{stock_code}"

    def get_intraday(self, stock_code: str) -> list[dict[str, Any]]:
        """获取当日分时（1分钟K线）—— 东方财富 → 腾讯 fallback。"""
        cache_key = f"intraday_{stock_code}"
        cached = self._cache_get(cache_key, ttl=30)
        if cached is not None:
            return cached

        # 优先腾讯（稳定、快速），东方财富作为增强（有OHLC/VWAP但经常挂）
        result = self._get_intraday_tencent(stock_code)
        if not result:
            result = self._get_intraday_eastmoney(stock_code)
        if result:
            self._cache_set(cache_key, result, ttl=30)
            return result

        return []

    def _get_intraday_eastmoney(self, stock_code: str) -> list[dict[str, Any]]:
        """东方财富分时API。"""
        try:
            self._rate_limit()
            secid = self._em_secid(stock_code)
            resp = requests.get(
                "https://push2his.eastmoney.com/api/qt/stock/trends2/get",
                params={
                    "secid": secid,
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
                    "klt": "1",
                    "ut": "fa5fd1943c7b386f172d6893dbbd1180",
                    "ndays": "1",
                },
                timeout=10,
            )
            klines = resp.json().get("data", {}).get("trends", [])
            result = []
            for line in klines:
                p = line.split(",")
                if len(p) < 8:
                    continue
                # 时间,开,收,高,低,量,额,累计均价
                result.append(
                    {
                        "time": p[0],
                        "price": float(p[2]),
                        "high": float(p[3]),
                        "low": float(p[4]),
                        "volume": float(p[5]),
                        "vwap": float(p[7]),
                    }
                )
            return result
        except Exception as e:
            logger.debug(f"东方财富分时失败 ({stock_code}): {e}")
            return []

    def _get_intraday_tencent(self, stock_code: str) -> list[dict[str, Any]]:
        """腾讯分时API fallback —— 只有每分钟收盘价+量+额，无OHLC/VWAP。"""
        try:
            self._rate_limit()
            symbol = (
                ("sh" + stock_code) if stock_code.startswith(("6", "9")) else ("sz" + stock_code)
            )
            resp = self._session.get(
                "https://ifzq.gtimg.cn/appstock/app/minute/query",
                params={"_var": "min_data", "code": symbol},
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            # 响应格式: min_data={...}
            text = resp.text
            import json

            json_start = text.find("{")
            json_end = text.rfind("}") + 1
            if json_start < 0 or json_end <= json_start:
                return []
            data = json.loads(text[json_start:json_end])
            minutes = data.get("data", {}).get(symbol, {}).get("data", {}).get("data", [])
            if not minutes:
                return []

            result = []
            for line in minutes:
                parts = line.split()
                if len(parts) < 3:
                    continue
                # 格式: "0930 26.10 42184 110100240.00"
                t = parts[0]
                t_str = t[:2] + ":" + t[2:]
                price = float(parts[1])
                vol = int(parts[2])
                # 腾讯无 OHLC/VWAP，用 price 填充
                result.append(
                    {
                        "time": t_str,
                        "price": price,
                        "high": price,
                        "low": price,
                        "volume": vol,
                        "vwap": price,
                    }
                )
            return result
        except Exception as e:
            logger.debug(f"腾讯分时失败 ({stock_code}): {e}")
            return []

    def get_intraday_hist(self, stock_code: str, day: str = "") -> list[dict[str, Any]]:
        """获取指定日期分时；历史日期必须由返回时间戳严格确认。"""
        cache_key = f"intraday_hist_{stock_code}_{day}"
        cached = self._cache_get(cache_key, ttl=3600)
        if cached is not None:
            return cached

        if day:
            normalized_day = day.replace("-", "")[:8]
            if not re.fullmatch(r"\d{8}", normalized_day):
                return []
            result = self._get_historical_intraday_eastmoney(
                stock_code,
                normalized_day,
                period="1",
            )
            if not result:
                result = self._get_historical_intraday_eastmoney(
                    stock_code,
                    normalized_day,
                    period="5",
                )
            self._cache_set(cache_key, result, ttl=86400)
            return result

        try:
            self._rate_limit()
            symbol = (
                ("sh" + stock_code) if stock_code.startswith(("6", "9")) else ("sz" + stock_code)
            )
            # 腾讯历史分时: 加 &date=20260703 参数
            params = {"_var": "min_data", "code": symbol}
            if day:
                params["date"] = day
            resp = self._session.get(
                "https://ifzq.gtimg.cn/appstock/app/minute/query",
                params=params,
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            import json

            text = resp.text
            json_start = text.find("{")
            json_end = text.rfind("}") + 1
            if json_start < 0:
                return []
            data = json.loads(text[json_start:json_end])
            minutes = data.get("data", {}).get(symbol, {}).get("data", {}).get("data", [])
            if not minutes:
                return []

            result = []
            for line in minutes:
                parts = line.split()
                if len(parts) < 3:
                    continue
                t = parts[0]
                t_str = t[:2] + ":" + t[2:]
                price = float(parts[1])
                vol = int(parts[2])
                result.append(
                    {
                        "time": t_str,
                        "price": price,
                        "high": price,
                        "low": price,
                        "volume": vol,
                        "vwap": price,
                    }
                )
            self._cache_set(cache_key, result, ttl=3600)
            return result
        except Exception as e:
            logger.warning(f"历史分时获取失败 ({stock_code} {day}): {e}")
            return []

    def _get_historical_intraday_eastmoney(
        self,
        stock_code: str,
        day: str,
        *,
        period: str,
    ) -> list[dict[str, Any]]:
        """读取东方财富历史分时，并丢弃不属于请求日期的返回行。"""
        expected_day = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
        try:
            self._rate_limit()
            secid = self._em_secid(stock_code)
            if period == "1":
                url = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
                params = {
                    "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
                    "ut": "7eea3edcaed734bea9cbfc24409ed989",
                    "ndays": "5",
                    "iscr": "0",
                    "secid": secid,
                }
                response = self._session.get(url, params=params, timeout=15)
                response.raise_for_status()
                lines = (response.json().get("data") or {}).get("trends") or []
            else:
                url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
                params = {
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                    "ut": "7eea3edcaed734bea9cbfc24409ed989",
                    "klt": "5",
                    "fqt": "0",
                    "secid": secid,
                    "beg": day,
                    "end": day,
                }
                response = self._session.get(url, params=params, timeout=15)
                response.raise_for_status()
                lines = (response.json().get("data") or {}).get("klines") or []

            result: list[dict[str, Any]] = []
            for line in lines:
                parts = str(line).split(",")
                if len(parts) < 6 or not parts[0].startswith(f"{expected_day} "):
                    continue
                result.append(
                    {
                        "date": expected_day,
                        "time": parts[0][11:16],
                        "price": float(parts[2]),
                        "high": float(parts[3]),
                        "low": float(parts[4]),
                        "volume": float(parts[5]),
                        "vwap": float(parts[7]) if period == "1" and len(parts) > 7 else 0.0,
                        "period_minutes": int(period),
                    }
                )
            return result
        except Exception as exc:
            logger.warning(
                "东方财富历史分时失败 (%s %s %s分钟): %s",
                stock_code,
                expected_day,
                period,
                exc,
            )
            return []

    def _intraday_stable(self, intraday: list[dict[str, Any]]) -> tuple[bool, float]:
        """分时企稳判断：最近15分钟不创分时新低。

        博主常说"分时企稳"，量化为：最近15根1分钟K的最低价 ≥ 之前的最低价
        （不再创新低）。

        Returns:
            (is_stable, vwap)
        """
        if len(intraday) < 15:
            return False, 0.0
        recent = intraday[-15:]
        before = intraday[:-15]
        recent_low = min(k["low"] for k in recent)
        before_low = min((k["low"] for k in before), default=recent_low)
        not_new_low = recent_low >= before_low
        current = intraday[-1]["price"]
        vwap = intraday[-1].get("vwap") or current
        return not_new_low, round(vwap, 3)

    def _stable_breakpoint(self, intraday: list[dict[str, Any]]) -> tuple[bool, float]:
        """企稳转折点：当前企稳 且 最近3分钟内曾出现过「未企稳」。

        只在「从不企稳 → 企稳」的转折时刻，或「开盘即企稳」的确认时刻
        返回 True。避免强势股全天在均线上方导致条件 100% 持续满足。

        两种触发场景：
        1. 转折企稳：最近3分钟内曾不企稳→现在企稳（止跌转折）
        2. 开盘企稳：开盘后从未出现不企稳，首次满15分钟确认（如盛剑7.1）
        """
        if len(intraday) < 15:
            return False, 0.0
        now_stable, vwap = self._intraday_stable(intraday)
        if not now_stable:
            return False, vwap
        # 最近3分钟内曾有不企稳 = 刚转折（3分钟容差匹配 matcher 每3分钟检查）
        n = len(intraday)
        for i in range(max(15, n - 3), n - 1):
            s, _ = self._intraday_stable(intraday[: i + 1])
            if not s:
                return True, vwap
        # 没有转折 → 检查是否"开盘即企稳"（从未不企稳过）
        # 只在首次满15-18分钟时返回True，避免持续触发
        if 15 <= n <= 18:
            ever_unstable = False
            for i in range(15, n):
                s, _ = self._intraday_stable(intraday[: i + 1])
                if not s:
                    ever_unstable = True
                    break
            if not ever_unstable:
                return True, vwap
        return False, vwap

    def get_technical_indicators(self, stock_code: str) -> dict[str, Any]:
        """获取技术指标。"""
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

        recent_lows = lows[-20:]
        recent_highs = highs[-20:]
        support = min(recent_lows)
        resistance = max(recent_highs)

        # 企稳判断：盘中用「转折点企稳」（从不企稳→企稳的时刻），盘后/无分时用日线级
        stable = False
        vwap_val = 0.0
        intraday = self.get_intraday(stock_code)
        if intraday and len(intraday) >= 15:
            stable, vwap_val = self._stable_breakpoint(intraday)
        elif len(closes) >= 4 and len(lows) >= 4:
            stable = min(lows[-3:]) >= lows[-4] and closes[-1] >= closes[-2]

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
            "vwap": vwap_val,
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

    def search_stock_candidates(self, keyword: str, limit: int = 10) -> list[dict[str, str]]:
        """按名称片段返回新浪联想接口中的全部 A 股候选。"""
        keyword = (keyword or "").strip()
        if not keyword:
            return []

        cache_key = f"search_{keyword}_{limit}"
        cached = self._cache_get(cache_key, ttl=86400)
        if cached is not None:
            return cached

        try:
            self._rate_limit()
            resp = self._session.get(
                "https://suggest3.sinajs.cn/suggest/type=11",
                params={"key": keyword},
                timeout=8,
            )
            resp.raise_for_status()
            text = resp.content.decode("gb18030", errors="replace")
            match = re.search(r'"(.*)"', text)
            if not match:
                return []

            results: list[dict[str, str]] = []
            seen: set[str] = set()
            for entry in match.group(1).split(";"):
                parts = entry.split(",")
                if len(parts) < 4:
                    continue
                name = parts[0].strip()
                code = parts[2].strip()
                market_code = parts[3].strip().lower()
                if not (code.isdigit() and len(code) == 6):
                    if re.fullmatch(r"(?:sh|sz)\d{6}", market_code):
                        code = market_code[2:]
                if not name or code in seen or not is_a_share(code):
                    continue
                seen.add(code)
                results.append({"name": name, "code": code})
                if len(results) >= max(1, limit):
                    break
            self._cache_set(cache_key, results, ttl=86400)
            return results
        except Exception as e:
            logger.error("股票名称候选检索失败 (%s): %s", keyword, e)
            return []

    def get_close_on_or_before(
        self,
        stock_code: str,
        target_day: date | datetime | str | None,
    ) -> float | None:
        """返回目标日或此前最近交易日的收盘价。"""
        if isinstance(target_day, datetime):
            target = target_day.date().isoformat()
        elif isinstance(target_day, date):
            target = target_day.isoformat()
        elif target_day:
            target = str(target_day)[:10]
        else:
            target = "9999-12-31"

        klines = self.get_kline(stock_code, days=180)
        eligible = [
            item
            for item in klines
            if str(item.get("date") or "")[:10] <= target and float(item.get("close") or 0) > 0
        ]
        if not eligible:
            return None
        latest = max(eligible, key=lambda item: str(item.get("date") or "")[:10])
        return float(latest["close"])

    def get_price_at_or_before(
        self,
        stock_code: str,
        target_time: datetime | date | None,
    ) -> float | None:
        """获取发布时间之前最近的可见行情价，盘中优先使用历史分时。"""
        if isinstance(target_time, datetime):
            minute_rows = self.get_intraday_hist(
                stock_code,
                day=target_time.strftime("%Y%m%d"),
            )
            target_minute = target_time.strftime("%H:%M")
            visible_rows = [
                row
                for row in minute_rows
                if str(row.get("time") or "")[:5] <= target_minute
                and float(row.get("price") or 0) > 0
            ]
            if visible_rows:
                latest = max(visible_rows, key=lambda row: str(row.get("time") or "")[:5])
                return float(latest["price"])

            # 盘中分时源失败时不能拿尚未发生的当日收盘价做核验。
            fallback_day = target_time.date()
            if target_time.hour < 15:
                fallback_day -= timedelta(days=1)
            return self.get_close_on_or_before(stock_code, fallback_day)
        return self.get_close_on_or_before(stock_code, target_time)

    def resolve_stock_code(self, stock_name: str) -> str | None:
        """按名称查股票代码。"""
        if not stock_name or stock_name == "unknown":
            return None

        cache_key = f"resolve_{stock_name}"
        cached = self._cache_get(cache_key, ttl=86400)
        if cached is not None:
            return cached

        candidates = self.search_stock_candidates(stock_name, limit=10)
        exact = next(
            (
                item
                for item in candidates
                if item["name"].lstrip("*").upper() == stock_name.lstrip("*").upper()
            ),
            None,
        )
        matched = exact or next(
            (
                item
                for item in candidates
                if self._name_similar(stock_name, item["name"].lstrip("*"))
            ),
            None,
        )
        if not matched:
            matched = self._resolve_pinyin_candidate(stock_name)
        if matched:
            self._cache_set(cache_key, matched["code"], ttl=86400)
            return matched["code"]
        return None

    @staticmethod
    def _name_to_pinyin(name: str) -> str:
        """Convert a Chinese stock name to plain pinyin for ASR homophone recovery."""
        try:
            from pypinyin import Style, lazy_pinyin
        except ImportError:
            return ""
        normalized = re.sub(r"^(?:S\*?ST|\*?ST)", "", name or "", flags=re.IGNORECASE)
        return "".join(lazy_pinyin(normalized, style=Style.NORMAL, errors="ignore")).lower()

    def _resolve_pinyin_candidate(self, stock_name: str) -> dict[str, str] | None:
        query = self._name_to_pinyin(stock_name)
        if len(query) < 6:
            return None
        candidates = self.search_stock_candidates(query, limit=10)
        scored = sorted(
            (
                (
                    SequenceMatcher(None, query, self._name_to_pinyin(item["name"])).ratio(),
                    item,
                )
                for item in candidates
                if self._name_to_pinyin(item["name"])
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )
        if not scored or scored[0][0] < 0.86:
            return None
        if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.05:
            return None
        logger.info("股票名称拼音纠错: %s → %s", stock_name, scored[0][1]["name"])
        return scored[0][1]

    @staticmethod
    def _name_similar(a: str, b: str) -> bool:
        """股票名称相似度判断（容忍简称/全称差异，如"彩虹" vs "彩虹股份"）。"""
        a, b = (a or "").strip(), (b or "").strip()
        if not a or not b:
            return False
        if a in b or b in a:
            return True
        # 去掉常见行业后缀后比较核心词
        suffixes = [
            "股份",
            "科技",
            "集团",
            "控股",
            "实业",
            "电气",
            "电子",
            "通讯",
            "通信",
            "技术",
            "新材",
            "材料",
            "光电",
            "生物",
            "医药",
            "能源",
            "重工",
            "工业",
            "半导体",
            "智能",
            "信息",
        ]
        a_core, b_core = a, b
        for s in suffixes:
            a_core = a_core.replace(s, "")
            b_core = b_core.replace(s, "")
        a_core, b_core = a_core.strip(), b_core.strip()
        return bool(a_core) and bool(b_core) and (a_core in b_core or b_core in a_core)

    def validate_stock_code(self, name: str, code: str) -> tuple[str, str]:
        """校验并修正股票代码（解决 LLM 代码推断错误的问题）。

        两层校验：
        1. 代码非法/unknown → 按名称解析
        2. 代码合法 → 查实时行情验名，名称对不上则重新解析

        Returns:
            (corrected_code, note) - note 说明修正原因，用于审计/展示
        """
        name = (name or "").strip()
        code = (code or "").strip()

        # 1. 非法代码 → 按名解析
        if not code or code == "unknown" or not (code.isdigit() and len(code) == 6):
            resolved = self.resolve_stock_code(name) if name else None
            if resolved:
                return resolved, f"按名称「{name}」解析为 {resolved}"
            return (code or "unknown"), "代码缺失且无法解析"

        # 2. 合法代码 → 查行情验名
        try:
            quote = self.get_realtime_quote(code)
            real_name = (quote.get("stock_name") or "").strip()
            if real_name:
                if self._name_similar(name, real_name) or self._name_similar(real_name, name):
                    return code, f"名称匹配（{real_name}）"
                # 名称对不上，重新按名解析
                resolved = self.resolve_stock_code(name) if name else None
                if resolved and resolved != code:
                    return resolved, f"原 {code} 实为「{real_name}」，按名修正为 {resolved}"
                return code, f"⚠ 代码对应「{real_name}」，与「{name}」不符"
        except Exception as e:
            logger.debug(f"校验代码 {code} 异常: {e}")

        return code, "未校验（无行情）"


# 全局单例
_stock_client: StockClient | None = None


def get_stock_client() -> StockClient:
    global _stock_client
    if _stock_client is None:
        _stock_client = StockClient()
    return _stock_client
