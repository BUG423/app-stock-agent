"""盘中分时线盯盘 —— 候选股"持续踩破分时线"恶化预警。

锚定博主"跳跳虎复盘"的原话纪律：
  · "回踩分时不跌破，可以考虑" / "分时企稳向上攻" / "站稳分时上攻" → 健康
  · "祥鑫分时完全没有承接住" → 恶化
即：股价可以瞬间踩破分时均价线(VWAP)，但**不能持续待在下方**，踩破一定量后应回升。

本模块把这条红线量化成纯函数 `vwap_break_status`，供盘中盯盘判断候选股是否恶化。
不做买卖，只做"是否符合预期/是否持续恶化"的预警——符合用户"只收集/盯盘不自主交易"的定位。
"""

from __future__ import annotations

from typing import Any


def per_minute_volumes(pts: list[dict[str, Any]]) -> list[float]:
    """把行情源的成交量列还原成"每分钟增量量"。

    数据源不统一：腾讯分时给的是**累计**成交量(单调不减)，东财给的是每分钟量。
    自动识别：若整列单调不减(且非全等)判为累计→取相邻差；否则视为每分钟量直接用。
    这样 VWAP 无论源是累计还是每分钟都能算对（此前把累计量当每分钟量导致均价线偏向盘尾）。
    """
    vols = [float(k.get("volume") or 0) for k in pts]
    if len(vols) < 3:
        return vols
    non_decreasing = all(vols[i] >= vols[i - 1] for i in range(1, len(vols)))
    has_growth = vols[-1] > vols[0]
    if not (non_decreasing and has_growth):  # 每分钟量：直接用
        return vols
    out, prev = [], 0.0  # 累计量：取相邻差
    for v in vols:
        dv = v - prev
        out.append(dv if dv >= 0 else v)
        prev = v
    return out


def _cum_vwap(pts: list[dict[str, Any]]) -> list[float]:
    """逐分钟累计成交均价(分时均价线) = Σ(price×每分钟量) / Σ(每分钟量)。

    自己算而非信任数据源的 vwap 字段：腾讯历史分时把 vwap 直接置为 price(无真实均价)，
    自算才能在历史/实时都得到正确分时均价线。每分钟量经 per_minute_volumes 归一(处理累计/每分钟两种源)。
    某分钟无量时沿用上一均价；全程无量退回 vwap 字段/price。
    """
    out: list[float] = []
    cum_pv = cum_v = 0.0
    vols = per_minute_volumes(pts)
    for i, k in enumerate(pts):
        price = float(k.get("price") or 0)
        dv = vols[i] if i < len(vols) else 0.0
        if price > 0 and dv > 0:
            cum_pv += price * dv
            cum_v += dv
            out.append(cum_pv / cum_v)
        elif cum_v > 0:
            out.append(cum_pv / cum_v)  # 该分钟无量, 沿用上一累计均价
        else:
            out.append(float(k.get("vwap") or price or (out[-1] if out else 0)))
    return out


def vwap_break_status(
    intraday: list[dict[str, Any]],
    lookback: int = 30,
    persist_min: int = 12,
    below_ratio_thr: float = 0.6,
    cur_below_pct_thr: float = 1.0,
) -> dict[str, Any]:
    """判断个股当前是否"持续踩破分时线(VWAP)"。

    Args:
        intraday: 当日1分钟序列 [{price, vwap, ...}]（stock_client.get_intraday）。
        lookback: 回看窗口(分钟)。
        persist_min: 连续低于分时线达到此分钟数即算"持续"。
        below_ratio_thr: 窗口内低于分时线的分钟占比阈值。
        cur_below_pct_thr: 当前价须低于分时线至少此百分比才算真破位(滤噪)。

    判定"持续踩破"(deteriorating=True)条件：
      1) 当天曾在分时线下方持续一段时间（全天占比或连续分钟数达标）;
      2) 近窗口内 低于分时线占比 >= below_ratio_thr  或  最长连续低于 >= persist_min。

    Returns: {deteriorating, below_ratio, max_consec_below, cur_vs_vwap_pct,
              vwap, price, n, note}
    """
    pts = [k for k in (intraday or []) if k.get("price")]
    n = len(pts)
    if n < 15:
        return {"deteriorating": False, "note": "分时数据不足", "n": n}

    vwaps = _cum_vwap(pts)  # 自算的分时均价线(全序列)

    # ---- 全天维度：判断是否曾经持续踩破 ----
    # 用户要求：先下到均线、长期在下方、后来才勉强站上去→也要排除
    all_below_flags = [1 if pts[i]["price"] < vwaps[i] else 0 for i in range(n)]
    all_below_ratio = sum(all_below_flags) / n if n else 0
    all_max_consec = consec = 0
    for f in all_below_flags:
        consec = consec + 1 if f else 0
        all_max_consec = max(all_max_consec, consec)

    # ---- 近窗口维度：用于状态分类(站稳/回踩/贴线) ----
    idx = list(range(n))[-lookback:] if n > lookback else list(range(n))
    below_flags = [1 if pts[i]["price"] < vwaps[i] else 0 for i in idx]
    below_ratio = sum(below_flags) / len(idx)

    # 最长连续低于分时线的分钟数(近窗口)
    max_consec = consec = 0
    for f in below_flags:
        consec = consec + 1 if f else 0
        max_consec = max(max_consec, consec)

    price, vwap = pts[-1]["price"], vwaps[-1]
    cur_vs_vwap_pct = (price / vwap - 1) * 100 if vwap else 0.0

    # 恶化判定：用全天数据——只要当天曾持续踩破且当前仍在下方，即判定恶化
    persistent = all_below_ratio >= below_ratio_thr or all_max_consec >= persist_min
    deteriorating = persistent

    # 分时均价线状态分类(锚定跳跳虎原话)：
    #   持续踩破 = 没承接住(恶化)；站稳上方 = 分时企稳向上攻；
    #   回踩企稳 = 曾回踩到线下但已收回上方(承接住/回踩不破)；贴线回踩 = 略低于线未持续破位。
    if deteriorating:
        state = "持续踩破"
    elif cur_vs_vwap_pct >= 0:
        state = "站稳上方" if below_ratio < 0.4 else "回踩企稳"
    else:
        state = "贴线回踩"
    on_track = not deteriorating  # 只要没持续踩破就算"未恶化/在轨"

    if deteriorating:
        note = (
            f"持续踩破分时线: 全天{all_below_ratio * 100:.0f}%时间在均价线下方"
            f"(最长连续{all_max_consec}分钟), 当前低于均价线{-cur_vs_vwap_pct:.2f}%, 未见承接回升"
        )
    elif state == "站稳上方":
        note = f"分时企稳: 站稳均价线上方(当前{cur_vs_vwap_pct:+.2f}%)"
    elif state == "回踩企稳":
        note = f"回踩企稳: 曾踩线但已收回均价线上方(当前{cur_vs_vwap_pct:+.2f}%, 有承接)"
    else:
        note = (
            f"贴线回踩(当前{cur_vs_vwap_pct:+.2f}%, 下方占比{below_ratio * 100:.0f}%), 暂未持续破位"
        )

    return {
        "deteriorating": deteriorating,
        "state": state,
        "on_track": on_track,
        "below_ratio": round(below_ratio, 2),
        "max_consec_below": max_consec,
        "cur_vs_vwap_pct": round(cur_vs_vwap_pct, 2),
        "vwap": round(vwap, 3),
        "price": round(price, 3),
        "n": n,
        "note": note,
    }


def condition_watch(
    current_price: float,
    ref_line: float,
    vwap_status: dict,
    band: tuple[float, float] = (-3.0, 1.0),
) -> dict[str, Any]:
    """忠实评估跳跳虎"回踩<ref_line>分时企稳"条件的实时状态。

    跳跳虎的条件=两段：回踩到某条日线(5日线/支撑位) + 分时企稳/承接住。
    这里把两段合一：
      ref_line: 回踩参考位(博主说的5日线/支撑位, 由 job 从 condition+技术面取)。
      band: 相对参考位的回踩买点容差(下方3%~上方1%, 跳跳虎"回踩低吸不追高")。
      vwap_status: vwap_break_status 的返回(提供真·分时均价线 state)。

    Returns: {verdict, on_track, near_ref, dev_from_ref_pct, vwap_state}
    """
    state = vwap_status.get("state", "")
    deteriorating = vwap_status.get("deteriorating", False)
    if ref_line <= 0 or current_price <= 0:
        # 无日线参考位(博主没指明) → 退化为纯分时均价线盯盘(AI基本盯盘)
        return {
            "verdict": f"分时线盯盘: {state or '数据不足'}",
            "on_track": not deteriorating,
            "near_ref": None,
            "dev_from_ref_pct": None,
            "vwap_state": state,
        }
    dev = round((current_price / ref_line - 1) * 100, 2)  # 先规整, 避免浮点边界误判
    near_ref = band[0] <= dev <= band[1]
    if deteriorating:
        verdict, on_track = f"已破位: 持续踩破分时线(离参考位{dev:+.1f}%)，不符合", False
    elif not near_ref and dev > band[1]:
        verdict, on_track = f"尚未回踩到位: 高于参考位{dev:+.1f}%，勿追高", False
    elif near_ref and state in ("站稳上方", "回踩企稳"):
        verdict, on_track = f"符合: 回踩到位({dev:+.1f}%) + 分时{state}，可关注", True
    elif near_ref:
        verdict, on_track = f"回踩到位({dev:+.1f}%)但分时未企稳({state})，再等承接", False
    else:
        verdict, on_track = f"回踩偏深({dev:+.1f}%)，观察分时{state}", not deteriorating
    return {
        "verdict": verdict,
        "on_track": on_track,
        "near_ref": near_ref,
        "dev_from_ref_pct": round(dev, 2),
        "vwap_state": state,
    }
