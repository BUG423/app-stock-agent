"""用 OCR 名称片段、持仓数/市值和历史行情交叉确认股票。"""

import re
from datetime import date, datetime
from typing import Any

_NUMBER_PATTERN = re.compile(r"(?<!\d)(\d[\d,]*(?:\.\d+)?)(?!\d)")
_CHINESE_PATTERN = re.compile(r"[\u4e00-\u9fff]+")
_POSITION_LABELS = ("成本/现价", "成本现价")
_UI_WORDS = {
    "持仓股",
    "市值",
    "盈亏",
    "持仓",
    "可用",
    "成本",
    "现价",
    "总资产",
    "总市值",
    "浮动盈亏",
    "逆回购",
    "可取",
    "转账",
    "买入",
    "卖出",
    "撤单",
    "查询",
    "行情",
    "自选",
    "交易",
    "资讯",
    "理财",
    "首页",
    "观看",
    "谢谢",
}


def _is_subsequence(observed: str, candidate: str) -> bool:
    iterator = iter(candidate)
    return bool(observed) and all(char in iterator for char in observed)


def _position_fragments(lines: list[str]) -> list[str]:
    start = -1
    for index, line in enumerate(lines):
        compact = line.replace(" ", "")
        if any(label in compact for label in _POSITION_LABELS):
            start = index + 1
            break
    if start < 0:
        return []
    fragments: list[str] = []
    for line in lines[start : start + 8]:
        for value in _CHINESE_PATTERN.findall(line):
            if value in _UI_WORDS or any(word in value for word in ("证券", "账户", "查看")):
                continue
            if len(value) <= 4:
                fragments.append(value)
        if fragments and re.search(r"\d", line):
            break
    return list(dict.fromkeys(fragments))


def extract_ocr_market_evidence(text: str) -> dict[str, list]:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    prices: list[float] = []
    large_values: list[float] = []
    quantities: list[int] = []
    for line in lines:
        for raw in _NUMBER_PATTERN.findall(line):
            value = float(raw.replace(",", ""))
            if "%" not in line and 1 <= value <= 500 and "." in raw:
                prices.append(value)
            if value >= 1000:
                large_values.append(value)
            # A 股持仓画面通常按 100 股整手显示。只把无千分位且为整手的整数
            # 当作候选持仓数，避免账号尾号、盈亏金额等参与“市值÷股数”。
            if (
                "." not in raw
                and "," not in raw
                and 100 <= value <= 10_000_000
                and int(value) % 100 == 0
            ):
                quantities.append(int(value))
    implied_prices = []
    for market_value in large_values:
        for quantity in quantities:
            if market_value == quantity:
                continue
            ratio = market_value / quantity
            if 1 <= ratio <= 500:
                implied_prices.append(round(ratio, 4))
    return {
        "fragments": _position_fragments(lines),
        "prices": list(dict.fromkeys(round(value, 4) for value in prices)),
        "implied_prices": list(dict.fromkeys(implied_prices)),
    }


def resolve_ocr_holding_stock(
    text: str,
    published_at: datetime | date | None,
    market_client: Any,
) -> dict | None:
    """返回高置信度唯一候选；证据不足或候选接近时返回 None。"""
    evidence = extract_ocr_market_evidence(text)
    fragments = evidence["fragments"]
    search_terms = [fragment for fragment in fragments if len(fragment) >= 2]
    if not search_terms:
        return None

    candidates: dict[str, dict] = {}
    for term in search_terms[:3]:
        for candidate in market_client.search_stock_candidates(term, limit=10):
            code = str(candidate.get("code") or "")
            name = str(candidate.get("name") or "").strip()
            if code and name:
                candidates[code] = {"code": code, "name": name}
    if not candidates:
        return None

    target_day = published_at.date() if isinstance(published_at, datetime) else published_at
    joined_fragments = "".join(fragments)
    scored: list[dict] = []
    for candidate in candidates.values():
        name = candidate["name"]
        matched = [fragment for fragment in fragments if fragment in name]
        if not any(len(fragment) >= 2 for fragment in matched):
            continue
        score = 3.0
        reasons = [f"名称片段 {'/'.join(matched)} 与 {name} 匹配"]
        if len(joined_fragments) >= 3 and _is_subsequence(joined_fragments, name):
            score += 1.0
            reasons.append(f"画面片段顺序 {joined_fragments} 与全名一致")

        price_lookup = getattr(market_client, "get_price_at_or_before", None)
        market_price = (
            price_lookup(candidate["code"], published_at)
            if callable(price_lookup)
            else market_client.get_close_on_or_before(candidate["code"], target_day)
        )
        if market_price and market_price > 0:
            score += 1.0
            reasons.append(f"{published_at or target_day or '近期'}可见行情价 {market_price:.2f}")
            explicit_diff = min(
                (abs(price - market_price) / market_price for price in evidence["prices"]),
                default=999.0,
            )
            implied_diff = min(
                (abs(price - market_price) / market_price for price in evidence["implied_prices"]),
                default=999.0,
            )
            if explicit_diff <= 0.008:
                score += 4.0
                reasons.append("OCR现价与发布时间可见行情价误差不超过0.8%")
            elif explicit_diff <= 0.02:
                score += 2.0
                reasons.append("OCR现价与发布时间可见行情价误差不超过2%")
            if implied_diff <= 0.008:
                score += 4.0
                reasons.append("OCR市值÷持仓数与发布时间可见行情价误差不超过0.8%")
            elif implied_diff <= 0.02:
                score += 2.0
                reasons.append("OCR市值÷持仓数与发布时间可见行情价误差不超过2%")
        scored.append({**candidate, "score": score, "evidence": reasons})

    if not scored:
        return None
    scored.sort(key=lambda item: item["score"], reverse=True)
    best = scored[0]
    runner_up_score = scored[1]["score"] if len(scored) > 1 else 0.0
    if best["score"] < 7.0 or best["score"] - runner_up_score < 2.0:
        return None
    return {
        "code": best["code"],
        "name": best["name"].lstrip("*"),
        "confidence": "high",
        "score": best["score"],
        "runner_up_score": runner_up_score,
        "evidence": best["evidence"],
        "observed_prices": evidence["prices"],
        "implied_prices": evidence["implied_prices"],
    }
