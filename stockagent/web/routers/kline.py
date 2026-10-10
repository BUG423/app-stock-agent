"""股票K线数据 API 路由（日K + 分时）。支持按代码或名称查询。"""

import asyncio

from fastapi import APIRouter, HTTPException, Query

from stockagent.analysis.intraday_signals import classify_intraday_state, detect_intraday_signals
from stockagent.engine.intraday_watch import per_minute_volumes
from stockagent.market.stock_client import get_stock_client
from stockagent.utils.time_utils import now_cst

router = APIRouter()


def _resolve(client, q: str) -> str | None:
    """把用户输入（6位代码 或 股票名称）解析成股票代码。"""
    q = (q or "").strip()
    if not q:
        return None
    if q.isdigit() and len(q) == 6:
        return q
    return client.resolve_stock_code(q)  # 按名称查（新浪 suggest）


def _ma(values: list[float], n: int) -> list[float | None]:
    """算 N 周期均线数组，窗口不足处填 None（与原序列对齐）。收盘价→均线，量→量能均线通用。"""
    out: list[float | None] = []
    for i in range(len(values)):
        if i + 1 < n:
            out.append(None)
        else:
            out.append(round(sum(values[i + 1 - n : i + 1]) / n, 3))
    return out


def _live_ma5(daily: list[dict], current_price: float, today: str | None = None) -> float:
    """盘中 MA5：行情日K未含今天时，用前4日收盘+当前价；已含今天则直接取最后5日。"""
    if not daily:
        return 0
    today = today or now_cst().date().isoformat()
    has_today = str(daily[-1].get("date") or "")[:10] == today
    closes = [float(k["close"]) for k in daily]
    values = closes[-5:] if has_today else closes[-4:] + ([current_price] if current_price else [])
    return round(sum(values) / 5, 3) if len(values) == 5 else 0


@router.get("/{query}")
async def get_kline(
    query: str,
    days: int = Query(60, ge=1, le=365),
):
    """获取股票日K线 + 名称 + MA5/10/20/30/60 + 量能均线(5)。query 可为代码或名称。"""
    client = get_stock_client()
    try:
        code = await asyncio.to_thread(_resolve, client, query)
        if not code:
            raise HTTPException(status_code=404, detail=f"未找到股票：{query}")
        kline = await asyncio.to_thread(client.get_kline, code, days)
        if not kline:
            raise HTTPException(status_code=404, detail="K线数据不存在")
        quote = await asyncio.to_thread(client.get_realtime_quote, code)
        closes = [k["close"] for k in kline]
        volumes = [k["volume"] for k in kline]
        return {
            "stock_code": code,
            "stock_name": (quote.get("stock_name") if quote else "") or "",
            "days": days,
            "data": kline,
            "ma5": _ma(closes, 5),
            "ma10": _ma(closes, 10),
            "ma20": _ma(closes, 20),
            "ma30": _ma(closes, 30),
            "ma60": _ma(closes, 60),
            "vol_ma5": _ma(volumes, 5),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取K线数据失败: {str(e)}")


@router.get("/{query}/intraday")
async def get_intraday(query: str):
    """获取当日分时：价格线 + 分时均价线 + 每分钟量能 + 实时均价 + 昨收基准。"""
    client = get_stock_client()
    try:
        code = await asyncio.to_thread(_resolve, client, query)
        if not code:
            raise HTTPException(status_code=404, detail=f"未找到股票：{query}")
        points = await asyncio.to_thread(client.get_intraday, code)
        if not points:
            raise HTTPException(status_code=404, detail="分时数据不存在")
        quote = await asyncio.to_thread(client.get_realtime_quote, code)
        vols = per_minute_volumes(points)  # 归一为每分钟量(处理累计/每分钟两种源)
        # 分时均价线：Σ(price×每分钟量)/累计量（腾讯源无成交额，用增量量近似，业界标准）
        cum_pv = cum_v = 0.0
        data = []
        for i, p in enumerate(points):
            price = float(p["price"])
            cum_pv += price * vols[i]
            cum_v += vols[i]
            data.append(
                {
                    "time": p["time"],
                    "price": round(price, 3),
                    "avg": round(cum_pv / cum_v, 3) if cum_v > 0 else round(price, 3),
                    "vol": round(vols[i], 1),
                }
            )
        # 5 日线（回踩五日线参考）：盘中必须纳入当前价，不能少算今天这一日。
        daily = await asyncio.to_thread(client.get_kline, code, 6)
        prev_close = (quote.get("prev_close") if quote else 0) or 0
        current = data[-1]["price"] if data else 0
        ma5 = _live_ma5(daily, current)
        # 分时策略事件检测（跳跳虎话术：回踩五日线/承接/持续踩破/翻红翻绿/冲高回落/探底回升）
        signals = detect_intraday_signals(data, ma5, prev_close)
        signal_state = classify_intraday_state(data, signals)
        return {
            "stock_code": code,
            "stock_name": (quote.get("stock_name") if quote else "") or "",
            "prev_close": prev_close,
            "current_price": current,
            "avg_price": data[-1]["avg"] if data else 0,  # 实时均价 = 最新分时均价
            "change_pct": round((current - prev_close) / prev_close * 100, 2) if prev_close else 0,
            "ma5": ma5,
            "signals": signals,
            "signal_state": signal_state,
            "data": data,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取分时数据失败: {str(e)}")
