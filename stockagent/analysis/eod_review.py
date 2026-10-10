"""盘后复盘 —— 收盘后逐只候选股对照博主预期，判定是否符合预期。

用户要求(点4①): 闭盘后查看整体预估是否满足博主提到的预估条件。
判定口径(诚实、可解释)：
  · 博主看多(bullish): 收盘不明显下跌(>= -1%) 且 未持续踩破分时线 → 符合预期；
  · 博主看空(bearish): 收盘 <= 0 → 博主方向正确；
  · 观望(neutral): 仅记录当日表现。
分时线红线锚定 [[intraday_watch]] 的 vwap_break_status（跳跳虎"承接住/没承接住"纪律）。

纯函数便于单测；聚合/邮件在 scheduler/jobs/post_market_review_job.py。
"""

from __future__ import annotations

from typing import Any


def assess_candidate(
    direction: str | None, change_pct: float, deteriorating: bool
) -> tuple[bool | None, str]:
    """判断单只候选股收盘是否符合博主预期。

    Returns:
        (met, note): met=True符合 / False不符 / None仅记录(观望)；note为一句话说明。
    """
    d = (direction or "").lower()
    warn = "，但盘中持续踩破分时线" if deteriorating else ""
    if d == "bullish":
        # 收盘结果为主(方向预估是否兑现); 走平时才用分时线裁决是否守住
        if change_pct >= 1.0:
            met = True  # 明显上涨 → 预期兑现(盘中破线仅作附注)
        elif change_pct >= -1.0:
            met = not deteriorating  # 基本走平 → 需守住分时线
        else:
            met = False  # 明显下跌 → 未兑现
        return met, f"看多{'✓' if met else '✗'} 收{change_pct:+.1f}%{warn}"
    if d == "bearish":
        met = change_pct <= 0
        return met, (
            f"看空{'✓' if met else '✗'} 收{change_pct:+.1f}%，"
            f"{'方向正确' if met else '反而上涨、判断偏差'}"
        )
    return None, f"观望 收{change_pct:+.1f}%（仅记录）"


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """聚合当日复盘：符合/不符/观望计数 + 明细。results 元素含 met 字段。"""
    graded = [r for r in results if r.get("met") is not None]
    met = sum(1 for r in graded if r["met"])
    n = len(graded)
    return {
        "total": len(results),
        "graded": n,
        "met": met,
        "unmet": n - met,
        "watch_only": len(results) - n,
        "met_rate": round(met / n * 100, 1) if n else None,
        "results": results,
    }
