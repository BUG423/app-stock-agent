"""AI 复核官（二次筛选）—— 用个股真实历史数据独立复核博主观点是否靠谱。

区别于 content_filter（判断内容有没有价值）和 pipeline（提取博主说了啥），
这里是**独立**地拉该股的真实 K 线/均线/量能，判断博主的观点当前有没有数据支撑、
值不值得跟、主要风险，产出 1-5 的靠谱度评分。相当于给用户加一道"数据把关"。
"""

import asyncio
import logging
from typing import Any

from stockagent.core.enums import Direction
from stockagent.llm.client import get_llm_client
from stockagent.llm.parsers import extract_json_from_response
from stockagent.llm.prompts import STOCK_REVIEW_SYSTEM, STOCK_REVIEW_USER
from stockagent.market.stock_client import get_stock_client

logger = logging.getLogger(__name__)

_DIR_CN = {"bullish": "看多", "bearish": "看空", "neutral": "观望"}


def _dir_str(direction: Any) -> str:
    """把 Direction 枚举 / 字符串统一成中文。"""
    if isinstance(direction, Direction):
        direction = direction.value
    return _DIR_CN.get(str(direction), "观望")


def _pct(a: float, b: float) -> float:
    """(a/b - 1) * 100，b 无效时返回 0。"""
    return round((a / b - 1) * 100, 1) if b else 0.0


def build_tech_facts(code: str, tech: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """把技术指标 + K线 加工成 (给LLM看的事实文本, 存库快照dict)。

    tech 来自 StockClient.get_technical_indicators（含 klines）。
    """
    klines = tech.get("klines") or []
    closes = [k["close"] for k in klines]
    highs = [k["high"] for k in klines]
    lows = [k["low"] for k in klines]
    vols = [k["volume"] for k in klines]

    price = tech.get("current_price") or (closes[-1] if closes else 0)
    ma5 = tech.get("ma5", 0)
    ma10 = tech.get("ma10", 0)
    ma20 = tech.get("ma20", 0)
    ma60 = tech.get("ma60", 0)

    # 均线排列
    if ma5 and ma10 and ma20 and ma5 > ma10 > ma20:
        ma_align = "多头排列（5>10>20）"
    elif ma5 and ma10 and ma20 and ma5 < ma10 < ma20:
        ma_align = "空头排列（5<10<20）"
    else:
        ma_align = "均线缠绕/方向不明"

    ret_5d = _pct(price, closes[-6]) if len(closes) >= 6 else 0.0
    ret_20d = _pct(price, closes[-21]) if len(closes) >= 21 else 0.0
    dist_ma20 = _pct(price, ma20) if ma20 else 0.0

    high_60 = max(highs) if highs else 0
    low_60 = min(lows) if lows else 0
    pos_in_range = round((price - low_60) / (high_60 - low_60) * 100, 0) if high_60 > low_60 else 0

    avg_vol20 = sum(vols[-20:]) / len(vols[-20:]) if vols else 0
    vol_ratio = round(vols[-1] / avg_vol20, 2) if avg_vol20 and vols else 0.0
    near_high = high_60 > 0 and price >= high_60 * 0.98
    near_low = low_60 > 0 and price <= low_60 * 1.02

    snapshot = {
        "price": round(price, 2),
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma60": ma60,
        "ma_align": ma_align,
        "dist_ma20_pct": dist_ma20,
        "ret_5d_pct": ret_5d,
        "ret_20d_pct": ret_20d,
        "pos_in_60d_range_pct": pos_in_range,
        "vol_ratio_vs_20d": vol_ratio,
        "support": tech.get("support", 0),
        "resistance": tech.get("resistance", 0),
        "is_stable": tech.get("is_stable", False),
        "near_60d_high": near_high,
        "near_60d_low": near_low,
        "kline_days": len(klines),
    }

    facts = (
        f"- 现价 {snapshot['price']}，MA5={ma5} MA10={ma10} MA20={ma20} MA60={ma60}\n"
        f"- 均线形态：{ma_align}，现价距MA20 {dist_ma20:+.1f}%\n"
        f"- 近5日涨跌 {ret_5d:+.1f}%，近20日涨跌 {ret_20d:+.1f}%\n"
        f"- 位置：处于近60日区间的 {pos_in_range:.0f}% 分位"
        f"（60日高 {high_60}，60日低 {low_60}）"
        f"{'，逼近60日高点' if near_high else ''}{'，逼近60日低点' if near_low else ''}\n"
        f"- 最新量能是20日均量的 {vol_ratio} 倍"
        f"（{'放量' if vol_ratio >= 1.5 else '缩量' if vol_ratio and vol_ratio < 0.7 else '平量'}）\n"
        f"- 支撑位≈{snapshot['support']}，压力位≈{snapshot['resistance']}，"
        f"近端{'已企稳' if snapshot['is_stable'] else '未见明确企稳'}"
    )
    return facts, snapshot


def _fallback_review(reason: str) -> dict[str, Any]:
    """无法复核时的中性兜底（不阻断主流程）。"""
    return {
        "score": 3,
        "stance": "neutral",
        "verdict": f"数据不足，未能有效复核（{reason}）",
        "risk": "缺少行情数据，建议人工确认",
        "reasoning": "",
        "tech_snapshot": None,
        "llm_model": None,
        "llm_provider": None,
    }


async def review_stock(
    code: str,
    name: str | None,
    blogger_view: str | None,
    direction: Any = "bullish",
) -> dict[str, Any]:
    """对单只股票做一次 AI 复核。返回评分/立场/结论/风险等。

    永不抛异常（失败返回中性兜底），以免影响主流水线。
    """
    if not code or code == "unknown" or not code.isdigit():
        return _fallback_review("股票代码未确定")

    client = get_stock_client()
    try:
        tech = await asyncio.to_thread(client.get_technical_indicators, code)
    except Exception as e:
        logger.warning(f"[复核] 获取 {code} 技术指标失败: {e}")
        return _fallback_review("行情获取失败")

    if not tech or not tech.get("klines"):
        return _fallback_review("无K线数据")

    facts, snapshot = build_tech_facts(code, tech)

    messages = [
        {"role": "system", "content": STOCK_REVIEW_SYSTEM},
        {
            "role": "user",
            "content": STOCK_REVIEW_USER.format(
                stock_name=name or code,
                stock_code=code,
                direction=_dir_str(direction),
                blogger_view=(blogger_view or "（博主未给出明确理由）")[:300],
                tech_facts=facts,
            ),
        },
    ]

    try:
        llm = get_llm_client()
        resp = await llm.chat(messages=messages, response_format={"type": "json_object"})
        if not resp.success:
            logger.warning(f"[复核] LLM 复核 {code} 失败: {resp.error}")
            out = _fallback_review("LLM调用失败")
            out["tech_snapshot"] = snapshot
            return out

        data = extract_json_from_response(resp.content)
        score = int(data.get("score", 3))
        score = max(1, min(5, score))
        stance = str(data.get("stance", "neutral")).strip().lower()
        if stance not in ("support", "neutral", "against"):
            stance = "neutral"
        return {
            "score": score,
            "stance": stance,
            "verdict": str(data.get("verdict", ""))[:200],
            "risk": str(data.get("risk", ""))[:200],
            "reasoning": resp.content[:2000],
            "tech_snapshot": snapshot,
            "llm_model": resp.model,
            "llm_provider": resp.provider or llm.provider_name(),
        }
    except Exception as e:
        logger.warning(f"[复核] 解析 {code} 复核结果失败: {e}")
        out = _fallback_review("结果解析失败")
        out["tech_snapshot"] = snapshot
        return out


async def batch_review(
    items: list[dict[str, Any]],
    max_concurrent: int = 3,
) -> list[dict[str, Any]]:
    """批量复核。items 每项含 code/name/blogger_view/direction。返回与输入等长的结果列表。"""
    sem = asyncio.Semaphore(max_concurrent)

    async def _one(it: dict) -> dict:
        async with sem:
            return await review_stock(
                code=str(it.get("code", "")),
                name=it.get("name"),
                blogger_view=it.get("blogger_view") or it.get("reason"),
                direction=it.get("direction", "bullish"),
            )

    results = await asyncio.gather(*[_one(i) for i in items], return_exceptions=True)
    final: list[dict[str, Any]] = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning(f"[复核] 批量复核异常: {r}")
            final.append(_fallback_review("批量异常"))
        else:
            final.append(r)
    return final
