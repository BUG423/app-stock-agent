"""AKShare 行情数据客户端 —— 封装实时行情、K线、交易日历等接口。"""

import logging
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# AKShare 导入（延迟加载，避免启动时的网络请求）
_ak = None


def _get_ak():
    global _ak
    if _ak is None:
        import akshare as ak

        _ak = ak
    return _ak


class AKShareClient:
    """AKShare 行情数据客户端。"""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[Any, float]] = {}
        self._cache_ttl: dict[str, float] = {}
        self._stock_list_df = None  # 缓存全量 A 股列表
        self._stock_list_ts: float = 0

    def _is_cache_valid(self, key: str) -> bool:
        """检查缓存是否有效。"""
        if key not in self._cache:
            return False
        import time

        _, cached_at = self._cache[key]
        ttl = self._cache_ttl.get(key, 30)
        return (time.time() - cached_at) < ttl

    def _set_cache(self, key: str, value: Any, ttl_seconds: float = 30) -> None:
        import time

        self._cache[key] = (value, time.time())
        self._cache_ttl[key] = ttl_seconds

    async def _get_stock_list(self):
        """获取全量 A 股列表（带缓存，TTL 1 小时，失败时重试 3 次）。"""
        import time

        now = time.time()
        if self._stock_list_df is not None and (now - self._stock_list_ts) < 3600:
            return self._stock_list_df

        ak = _get_ak()
        last_err = None
        for attempt in range(3):
            try:
                self._stock_list_df = ak.stock_zh_a_spot_em()
                self._stock_list_ts = now
                return self._stock_list_df
            except Exception as e:
                last_err = e
                if attempt < 2:
                    import asyncio

                    await asyncio.sleep(2 * (attempt + 1))
        logger.warning(f"获取 A 股列表失败（已重试 3 次）: {last_err}")
        return self._stock_list_df or __import__("pandas").DataFrame()

    async def get_realtime_quote(self, stock_code: str) -> dict[str, Any]:
        """获取个股实时行情（A股）。

        Args:
            stock_code: 6位股票代码，如 '600519'

        Returns:
            dict 包含 current_price, prev_close, change_pct, volume, etc.
        """
        cache_key = f"realtime_{stock_code}"
        if self._is_cache_valid(cache_key):
            data, _ = self._cache[cache_key]
            return data

        try:
            # 使用缓存的全量 A 股列表
            df = await self._get_stock_list()
            # 按代码过滤
            row = df[df["代码"] == stock_code]

            if row.empty:
                logger.warning(f"未找到股票 {stock_code} 的实时行情")
                return {}

            r = row.iloc[0]
            data = {
                "stock_code": stock_code,
                "stock_name": r.get("名称", ""),
                "current_price": float(r.get("最新价", 0)),
                "prev_close": float(r.get("昨收", 0)),
                "open": float(r.get("今开", 0)),
                "high": float(r.get("最高", 0)),
                "low": float(r.get("最低", 0)),
                "volume": float(r.get("成交量", 0)),
                "amount": float(r.get("成交额", 0)),
                "change_pct": float(r.get("涨跌幅", 0)),
                "change_amount": float(r.get("涨跌额", 0)),
                "turnover_rate": float(r.get("换手率", 0)),
                "pe_ratio": float(r.get("市盈率-动态", 0)) if r.get("市盈率-动态") else None,
            }

            self._set_cache(cache_key, data, ttl_seconds=30)
            return data

        except Exception as e:
            logger.error(f"获取实时行情失败 ({stock_code}): {e}")
            return {}

    async def get_daily_kline(self, stock_code: str, lookback: int = 20) -> list[dict[str, Any]]:
        """获取个股日K线数据。

        Args:
            stock_code: 股票代码
            lookback: 回溯天数

        Returns:
            list[dict] 包含 date, open, high, low, close, volume
        """
        cache_key = f"kline_{stock_code}_{lookback}"
        if self._is_cache_valid(cache_key):
            data, _ = self._cache[cache_key]
            return data

        try:
            ak = _get_ak()
            end_date = date.today().strftime("%Y%m%d")
            start_date = (date.today() - timedelta(days=lookback * 2)).strftime("%Y%m%d")

            df = ak.stock_zh_a_hist(
                symbol=stock_code,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq",  # 前复权
            )

            if df.empty:
                return []

            # 取最近 lookback 条
            df = df.tail(lookback)
            data = []
            for _, row in df.iterrows():
                data.append(
                    {
                        "date": str(row.get("日期", "")),
                        "open": float(row.get("开盘", 0)),
                        "high": float(row.get("最高", 0)),
                        "low": float(row.get("最低", 0)),
                        "close": float(row.get("收盘", 0)),
                        "volume": float(row.get("成交量", 0)),
                        "amount": float(row.get("成交额", 0)),
                        "change_pct": float(row.get("涨跌幅", 0)),
                    }
                )

            self._set_cache(cache_key, data, ttl_seconds=3600)
            return data

        except Exception as e:
            logger.error(f"获取日K线失败 ({stock_code}): {e}")
            return []

    async def get_index_realtime(self, index_code: str = "000001") -> dict[str, Any]:
        """获取指数实时行情。

        Args:
            index_code: 上证指数=000001, 深证成指=399001, 创业板指=399006

        Returns:
            dict 包含 current_point, change_pct, etc.
        """
        cache_key = f"index_{index_code}"
        if self._is_cache_valid(cache_key):
            data, _ = self._cache[cache_key]
            return data

        try:
            ak = _get_ak()
            df = ak.stock_zh_index_spot_em()

            # 指数代码映射
            name_map = {
                "000001": "上证指数",
                "399001": "深证成指",
                "399006": "创业板指",
                "000688": "科创50",
                "000300": "沪深300",
            }
            index_name = name_map.get(index_code, index_code)
            row = df[df["名称"] == index_name]

            if row.empty:
                # 尝试用代码匹配
                row = df[df["代码"] == index_code]

            if row.empty:
                return {}

            r = row.iloc[0]
            data = {
                "index_code": index_code,
                "index_name": str(r.get("名称", "")),
                "current_point": float(r.get("最新价", 0)),
                "change_pct": float(r.get("涨跌幅", 0)),
                "change_amount": float(r.get("涨跌额", 0)),
                "volume": float(r.get("成交量", 0)),
                "amount": float(r.get("成交额", 0)),
            }

            self._set_cache(cache_key, data, ttl_seconds=60)
            return data

        except Exception as e:
            logger.error(f"获取指数行情失败 ({index_code}): {e}")
            return {}

    async def get_avg_volume(self, stock_code: str, days: int = 20) -> float:
        """获取个股最近 N 日的平均成交量。"""
        klines = await self.get_daily_kline(stock_code, lookback=days)
        if not klines:
            return 0.0

        volumes = [k["volume"] for k in klines if k["volume"] > 0]
        if not volumes:
            return 0.0

        return sum(volumes) / len(volumes)

    async def get_technical_indicators(self, stock_code: str, lookback: int = 60) -> dict[str, Any]:
        """获取个股技术指标（均线、支撑位、压力位）。

        Returns:
            dict 包含 ma5, ma10, ma20, ma60, support, resistance,
            current_price, prev_close, klines 等
        """
        cache_key = f"tech_{stock_code}"
        if self._is_cache_valid(cache_key):
            data, _ = self._cache[cache_key]
            return data

        klines = await self.get_daily_kline(stock_code, lookback=lookback)
        if not klines or len(klines) < 5:
            return {}

        closes = [k["close"] for k in klines]
        highs = [k["high"] for k in klines]
        lows = [k["low"] for k in klines]

        def _ma(values: list[float], n: int) -> float:
            if len(values) < n:
                return 0.0
            return sum(values[-n:]) / n

        # 近20日最低价作为支撑位（简化版）
        recent_lows = lows[-20:] if len(lows) >= 20 else lows
        support = min(recent_lows)

        # 近20日最高价作为压力位
        recent_highs = highs[-20:] if len(highs) >= 20 else highs
        resistance = max(recent_highs)

        # 判断是否"企稳"：最近3天低点不再创新低，且收盘价 > 前一天收盘价
        stable = False
        if len(closes) >= 4 and len(lows) >= 4:
            recent_3_lows = lows[-3:]
            stable = (
                min(recent_3_lows) >= lows[-4]  # 最近3天低点没有跌破第4天
                and closes[-1] >= closes[-2]  # 今天收盘 >= 昨天收盘
            )

        # 判断是否"放量"：今天成交量 > 5日均量的1.5倍
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

        self._set_cache(cache_key, data, ttl_seconds=300)  # 5分钟缓存
        return data

    async def resolve_stock_code(self, stock_name: str) -> str | None:
        """通过股票名称模糊查找股票代码。

        Args:
            stock_name: 股票名称或简称，如 "彩虹"、"贵州茅台"

        Returns:
            6位股票代码，找不到返回 None
        """
        if not stock_name or stock_name == "unknown":
            return None

        cache_key = f"resolve_{stock_name}"
        if self._is_cache_valid(cache_key):
            data, _ = self._cache[cache_key]
            return data

        try:
            df = await self._get_stock_list()

            # 精确匹配名称
            exact = df[df["名称"] == stock_name]
            if not exact.empty:
                code = str(exact.iloc[0]["代码"])
                self._set_cache(cache_key, code, ttl_seconds=86400)
                return code

            # 模糊匹配：名称包含关键词
            fuzzy = df[df["名称"].str.contains(stock_name, na=False)]
            if not fuzzy.empty:
                code = str(fuzzy.iloc[0]["代码"])
                self._set_cache(cache_key, code, ttl_seconds=86400)
                logger.info(f"模糊匹配: '{stock_name}' → {code} ({fuzzy.iloc[0]['名称']})")
                return code

            logger.debug(f"未找到匹配股票: '{stock_name}'")
            self._set_cache(cache_key, None, ttl_seconds=3600)
            return None

        except Exception as e:
            logger.error(f"股票名称查代码失败 ({stock_name}): {e}")
            return None

    def clear_cache(self) -> None:
        """清空缓存。"""
        self._cache.clear()
        self._cache_ttl.clear()


# 全局单例
_akshare_client: AKShareClient | None = None


def get_akshare_client() -> AKShareClient:
    global _akshare_client
    if _akshare_client is None:
        _akshare_client = AKShareClient()
    return _akshare_client
