"""盘后复盘定时任务 —— 收盘后汇总两位真人博主候选股是否符合预期，邮件发给用户。

用户要求(点4①): 闭盘后查看整体预估是否满足博主的预估条件。
(点4③ 盘中/盘后博主新动态的拉取通知，由 frequent_fetch + 新内容邮件已覆盖。)
"""

import logging

logger = logging.getLogger(__name__)

HUMAN_PLATFORMS = ("bilibili", "weibo", "wechat")


async def post_market_review_job(job_name: str = "post_market_review") -> None:
    logger.info(f"[定时任务] {job_name} 盘后复盘...")
    try:
        import asyncio

        from sqlalchemy import select

        from stockagent.analysis.eod_review import assess_candidate, summarize
        from stockagent.core.base_notifier import NotificationMessage
        from stockagent.core.db import get_sessionmaker
        from stockagent.core.enums import NotificationType
        from stockagent.core.models import AnalysisResult, Blogger, MonitoredStock
        from stockagent.engine.intraday_watch import vwap_break_status
        from stockagent.market.stock_client import get_stock_client
        from stockagent.notification.email_format import standard_subject
        from stockagent.notification.email_notifier import EmailNotifier
        from stockagent.utils.time_utils import is_trading_day, today_cst

        today = today_cst()
        if not is_trading_day(today):
            logger.info(f"[定时任务] {job_name} 今日非交易日, 跳过")
            return

        client = get_stock_client()
        maker = await get_sessionmaker()
        async with maker() as session:
            rows = (
                await session.execute(
                    select(MonitoredStock, Blogger)
                    .join(AnalysisResult, MonitoredStock.analysis_result_id == AnalysisResult.id)
                    .join(Blogger, AnalysisResult.blogger_id == Blogger.id)
                    .where(
                        MonitoredStock.monitor_date == today, Blogger.platform.in_(HUMAN_PLATFORMS)
                    )
                )
            ).all()

        if not rows:
            logger.info(f"[定时任务] {job_name} 今日无真人博主候选股, 跳过")
            return

        # 按代码去重：同一博主/多条动态常对同一只票建多条记录，复盘邮件应只列一次。
        # 优先保留有明确方向(看多/看空)的记录，避免被"观望"覆盖而无法评估。
        def _dir_of(ms) -> str:
            d = ms.direction.value if hasattr(ms.direction, "value") else ms.direction
            return (d or "").lower()

        picked: dict = {}
        for ms, blg in rows:
            code = (ms.stock_code or "").strip()
            if not (code.isdigit() and len(code) == 6):
                continue
            cur = picked.get(code)
            if cur is None or (
                _dir_of(cur[0]) == "neutral" and _dir_of(ms) in ("bullish", "bearish")
            ):
                picked[code] = (ms, blg)

        results = []
        for code, (ms, blg) in picked.items():
            quote = await asyncio.to_thread(client.get_realtime_quote, code)
            change_pct = (quote.get("change_pct", 0) if quote else 0) or 0
            intraday = await asyncio.to_thread(client.get_intraday, code)
            deteriorating = vwap_break_status(intraday).get("deteriorating", False)
            met, note = assess_candidate(
                ms.direction.value if hasattr(ms.direction, "value") else ms.direction,
                change_pct,
                deteriorating,
            )
            results.append(
                {
                    "blogger": blg.name,
                    "name": ms.stock_name or code,
                    "code": code,
                    "change_pct": round(change_pct, 2),
                    "met": met,
                    "note": note,
                }
            )

        if not results:
            logger.info(f"[定时任务] {job_name} 无有效标的")
            return

        summ = summarize(results)
        body = _review_html(today.isoformat(), summ)
        n = EmailNotifier()
        await n.queue_and_send(
            NotificationMessage(
                type=NotificationType.PNL_REPORT.value,
                recipient=n._to[0] if n._to else "",
                subject=standard_subject(
                    "盘后总结",
                    today.isoformat(),
                    f"符合 {summ['met']}｜未达 {summ['unmet']}｜观望 {summ['watch_only']}",
                ),
                body=body,
            ),
            related_type="eod_review",
            related_id=today.isoformat(),
        )
        logger.info(f"[定时任务] {job_name} 完成: {summ['met']}/{summ['graded']} 符合预期")
    except Exception as e:
        logger.error(f"[定时任务] {job_name} 失败: {e}", exc_info=True)


def _review_html(day: str, summ: dict) -> str:
    from stockagent.notification.email_format import standard_email

    rows = ""
    for r in summ["results"]:
        met = r["met"]
        color = "#2b8a3e" if met else ("#c92a2a" if met is False else "#868e96")
        chg_color = (
            "#e03131" if r["change_pct"] > 0 else ("#2f9e44" if r["change_pct"] < 0 else "#666")
        )
        rows += (
            f"<tr><td style='padding:6px 8px'>{r['name']}({r['code']})</td>"
            f"<td style='padding:6px 8px;color:#888'>{r['blogger']}</td>"
            f"<td style='padding:6px 8px;text-align:right;color:{chg_color};font-weight:600'>{r['change_pct']:+.2f}%</td>"
            f"<td style='padding:6px 8px;color:{color}'>{r['note']}</td></tr>"
        )
    rate = f"{summ['met_rate']}%" if summ["met_rate"] is not None else "-"
    summary = f"""候选股 <b>{summ["total"]}</b> 只 · 可评估 <b>{summ["graded"]}</b> 只 ·
<span style="color:#2b8a3e;font-weight:600">符合预期 {summ["met"]}</span> /
<span style="color:#c92a2a;font-weight:600">未达 {summ["unmet"]}</span> ·
符合率 <b>{rate}</b>（观望 {summ["watch_only"]} 只仅记录）"""
    content = f"""
<h3 style="font-size:15px;margin:0 0 8px">今日总结</h3>
<table style="width:100%;border-collapse:collapse;font-size:13px;border:1px solid #eee">
<tr style="background:#fafafa"><th style='padding:6px 8px;text-align:left'>标的</th>
<th style='padding:6px 8px;text-align:left'>博主</th>
<th style='padding:6px 8px;text-align:right'>当日</th>
<th style='padding:6px 8px;text-align:left'>是否符合预期(收盘+分时线)</th></tr>
{rows}</table>"""
    return standard_email(
        category="盘后总结",
        day=day,
        headline="今日盘后总结",
        summary_html=summary,
        content_html=content,
        note="口径：看多=收盘不明显下跌且未持续踩破分时线；看空=收跌即方向正确。",
    )
