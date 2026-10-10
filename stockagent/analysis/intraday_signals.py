"""分时线策略事件检测 —— 把跳跳虎话术标到分时图上。

纯函数：输入当日分时序列（含已算好的分时均价线 avg、每分钟量 vol）+ 日线 5 日均价 + 昨收，
输出可标注的事件（点/区间）。口径锚定 engine/intraday_watch 的分时纪律。

**只检测博主盘前买入条件里出现的术语**（回踩五日线 / 分时企稳承接 / 二次回踩 / 持续踩破 / 冲高先抛后吸）。
完整术语与严谨计算规则见 docs/intraday_signals.md。

检测的事件 kind：
  break      持续踩破分时线（没承接住 = 企稳失败）—— 区间
  support    回踩分时企稳·承接住（含 放量承接 / 二次回踩）—— 点
  ma5        回踩五日线（企稳与否）—— 点
  high_fade  冲高回落（对应"冲高就先抛后吸"）—— 点（标在高点）
"""

from __future__ import annotations

from typing import Any


def classify_intraday_state(
    data: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    persist_min: int = 12,
) -> dict[str, Any]:
    """给 UI 返回跳跳虎话术口径的当前分时状态。"""
    pts = [p for p in (data or []) if p.get("price")]
    if not pts:
        return {"code": "unknown", "label": "暂无分时数据", "detail": ""}

    price = float(pts[-1]["price"])
    avg = float(pts[-1].get("avg") or price)
    below_run = 0
    for p in reversed(pts):
        p_price = float(p.get("price") or 0)
        p_avg = float(p.get("avg") or p_price)
        if p_price >= p_avg:
            break
        below_run += 1

    latest_support = next((s for s in reversed(signals or []) if s.get("kind") == "support"), None)
    if below_run >= persist_min:
        return {
            "code": "broken",
            "label": "持续踩破·没承接",
            "detail": f"已连续{below_run}分钟位于分时均价线下方",
        }
    if price < avg:
        return {
            "code": "testing",
            "label": "回踩分时线·等待企稳",
            "detail": f"当前低于分时均价线，连续{below_run}分钟",
        }
    if latest_support:
        return {
            "code": "stable",
            "label": "分时企稳·承接住",
            "detail": f"最近一次企稳发生在{latest_support['start_time']}",
        }
    return {
        "code": "above",
        "label": "站稳分时均价线",
        "detail": "当前价格位于分时均价线上方",
    }


def detect_intraday_signals(
    data: list[dict[str, Any]],
    ma5: float = 0.0,
    prev_close: float = 0.0,
    persist_min: int = 12,
    dip_min: int = 3,
    ma5_band: tuple[float, float] = (-1.2, 0.6),
    fade_window: int = 20,
    fade_drop: float = 1.0,
    surge_mult: float = 2.0,
    max_each: int = 3,
) -> list[dict[str, Any]]:
    """检测分时事件。data: [{time, price, avg, vol}]（avg=分时均价线，与图一致；vol=每分钟量）。

    参数见 docs/intraday_signals.md。返回 [{kind, start_time, end_time?, price, label}]。
    """
    pts = [p for p in (data or []) if p.get("price")]
    n = len(pts)
    if n < 15:
        return []
    price = [float(p["price"]) for p in pts]
    avg = [float(p.get("avg") or price[i]) for i, p in enumerate(pts)]
    vol = [float(p.get("vol") or 0) for p in pts]
    time = [p["time"] for p in pts]
    below = [price[i] < avg[i] for i in range(n)]
    out: list[dict[str, Any]] = []

    # A. 持续踩破分时线（没承接住）：连续在均价线下方 >= persist_min 分钟 —— 区间
    breaks = 0
    i = 0
    while i < n:
        if below[i]:
            j = i
            while j < n and below[j]:
                j += 1
            run = j - i
            if run >= persist_min and breaks < max_each:
                low_i = min(range(i, j), key=lambda k: price[k])
                out.append(
                    {
                        "kind": "break",
                        "start_time": time[i],
                        "end_time": time[j - 1],
                        "price": round(price[low_i], 3),
                        "label": f"持续踩破分时线{run}分钟·没承接住",
                    }
                )
                breaks += 1
            i = j
        else:
            i += 1

    # B. 回踩企稳·承接住：曾回踩到均价线下方 >=dip_min 分钟，之后站回上方并保持
    #    附加：放量承接（该分钟量 >= surge_mult×近10分钟均量）/ 二次回踩（当日第2次及以上）
    sups = 0
    sup_count = 0
    i = 1
    while i < n and sups < max_each:
        if (not below[i]) and below[i - 1]:
            back = i - 1
            while back >= 0 and below[back]:
                back -= 1
            dip_len = (i - 1) - back
            hold = below[i : i + 5]
            # 实时尾端至少要有 3 个点确认，避免刚上穿一两分钟就误报“企稳”。
            if dip_len >= dip_min and len(hold) >= 3 and sum(hold) / len(hold) < 0.4:
                sup_count += 1
                recent = vol[max(0, i - 10) : i]
                avgv = sum(recent) / len(recent) if recent else 0
                heavy = avgv > 0 and vol[i] >= surge_mult * avgv
                order = {2: "二次回踩", 3: "三次回踩"}.get(
                    sup_count, "回踩" if sup_count == 1 else "多次回踩"
                )
                out.append(
                    {
                        "kind": "support",
                        "start_time": time[i],
                        "price": round(price[i], 3),
                        "label": f"分时企稳·{order}{dip_len}分钟·{'放量承接' if heavy else '承接住'}",
                    }
                )
                sups += 1
        i += 1

    # C. 回踩五日线：price 进入 5 日线附近 band（首个进入点标一次；是否企稳看之后是否持续破位）
    if ma5 and ma5 > 0:
        m5 = 0
        near_prev = False
        for i in range(n):
            dev = (price[i] / ma5 - 1) * 100
            near = ma5_band[0] <= dev <= ma5_band[1]
            if near and (not near_prev) and m5 < max_each:
                hold = below[i : i + 5]
                stab = (not hold) or (sum(hold) / len(hold) < 0.5)
                out.append(
                    {
                        "kind": "ma5",
                        "start_time": time[i],
                        "price": round(price[i], 3),
                        "label": "回踩5日线企稳" if stab else "回踩5日线",
                    }
                )
                m5 += 1
            near_prev = near

    # D. 冲高回落（对应买入条件"冲高就先抛后吸/冲高不追"）：
    #    局部顶（当前为日内最高且下一分钟转跌）+ 冲高≥1%（高于昨收/今开）+ 其后 fade_window 内回落≥fade_drop%
    fades = 0
    last_hi = -1e18
    run_max = -1e18
    up_ref = max(prev_close or 0.0, price[0])
    for i in range(n - 1):
        run_max = max(run_max, price[i])
        is_top = price[i] >= run_max and price[i + 1] < price[i]
        is_surge = up_ref > 0 and price[i] >= up_ref * 1.01
        if is_top and is_surge and price[i] >= last_hi * 1.003 and fades < max_each:
            fut = price[i + 1 : i + 1 + fade_window]
            if fut and (price[i] - min(fut)) / price[i] >= fade_drop / 100:
                out.append(
                    {
                        "kind": "high_fade",
                        "start_time": time[i],
                        "price": round(price[i], 3),
                        "label": "冲高回落·不追高",
                    }
                )
                fades += 1
                last_hi = price[i]

    return out
