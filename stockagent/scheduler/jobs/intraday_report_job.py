"""10:30 盘中盯盘汇总：按股票去重后统一报告满足/不满足状态。"""

import asyncio
import logging
from html import escape

logger = logging.getLogger(__name__)

HUMAN_PLATFORMS = ("bilibili", "weibo", "wechat", "user")


def _merge_candidates(rows: list[tuple]) -> list[dict]:
    """同一股票可能被多个视频重复提及；邮件中只保留一行并合并来源。"""
    merged: dict[str, dict] = {}
    for monitored, blogger in rows:
        code = (monitored.stock_code or "").strip()
        if not (code.isdigit() and len(code) == 6):
            continue
        item = merged.setdefault(
            code,
            {
                "code": code,
                "name": monitored.stock_name or code,
                "bloggers": [],
                "conditions": [],
            },
        )
        if blogger.name not in item["bloggers"]:
            item["bloggers"].append(blogger.name)
        for condition in monitored.trigger_conditions or []:
            text = condition.get("plain_description") or condition.get("description") or ""
            if text and text not in item["conditions"]:
                item["conditions"].append(text)
    return list(merged.values())


def _report_html(day: str, met: list[dict], unmet: list[dict], unknown: list[dict]) -> str:
    from stockagent.notification.email_format import standard_email

    def rows(items: list[dict], state: str, color: str) -> str:
        rendered = []
        for item in items:
            conditions = "；".join(item.get("conditions") or []) or "未给出明确前置条件"
            rendered.append(
                "<tr>"
                f"<td style='padding:8px'><b>{escape(item['name'])}</b><br>"
                f"<span style='color:#868e96'>{escape(item['code'])}</span></td>"
                f"<td style='padding:8px'>{escape('、'.join(item['bloggers']))}</td>"
                f"<td style='padding:8px;color:{color};font-weight:600'>{escape(state)}</td>"
                f"<td style='padding:8px'>{item.get('change_pct', 0):+.2f}%</td>"
                f"<td style='padding:8px'>{escape(item.get('note') or '')}<br>"
                f"<span style='font-size:12px;color:#868e96'>{escape(conditions)}</span></td>"
                "</tr>"
            )
        return "".join(rendered)

    all_rows = (
        rows(unmet, "已不满足", "#c92a2a")
        + rows(met, "仍满足", "#2b8a3e")
        + rows(unknown, "待确认", "#868e96")
    )
    if not all_rows:
        all_rows = "<tr><td colspan='5' style='padding:12px;color:#868e96'>今日暂无在盯股票</td></tr>"
    content = f"""
<h3 style="font-size:15px;margin:0 0 8px">当前在盯</h3>
<table style="width:100%;border-collapse:collapse;font-size:13px;border:1px solid #eee">
<tr style="background:#fafafa"><th style="padding:8px;text-align:left">标的</th>
<th style="padding:8px;text-align:left">来源</th><th style="padding:8px;text-align:left">状态</th>
<th style="padding:8px;text-align:left">涨跌</th><th style="padding:8px;text-align:left">判断依据</th></tr>
{all_rows}</table>"""
    summary = (
        f"共 <b>{len(met) + len(unmet) + len(unknown)}</b> 只；"
        f"<span style='color:#2b8a3e'>仍满足 <b>{len(met)}</b></span>；"
        f"<span style='color:#c92a2a'>已不满足 <b>{len(unmet)}</b></span>；"
        f"待确认 <b>{len(unknown)}</b>。"
    )
    unmet_names = "、".join(f"{item['name']}({item['code']})" for item in unmet) or "暂无"
    observing_names = "、".join(f"{item['name']}({item['code']})" for item in met) or "暂无"
    summary += (
        f"<br><b>已不满足：</b>{escape(unmet_names)}"
        f"<br><b>仍需观望/继续盯盘：</b>{escape(observing_names)}"
    )
    return standard_email(
        category="盘中盯盘",
        day=day,
        headline="10:30 盘中盯盘",
        summary_html=summary,
        content_html=content,
        note="口径：持续踩破分时均价线且未见承接，归为已不满足；行情取数失败归为待确认。",
    )


async def intraday_report_job(job_name: str = "intraday_report") -> dict:
    """生成并发送每日唯一一封 10:30 盯盘汇总。"""
    from sqlalchemy import select

    from stockagent.core.base_notifier import NotificationMessage
    from stockagent.core.db import get_sessionmaker
    from stockagent.core.enums import MonitorStatus, NotificationType
    from stockagent.core.models import AnalysisResult, Blogger, MonitoredStock
    from stockagent.engine.intraday_watch import vwap_break_status
    from stockagent.market.stock_client import get_stock_client
    from stockagent.notification.email_format import standard_subject
    from stockagent.notification.email_notifier import EmailNotifier
    from stockagent.utils.time_utils import is_trading_day, today_cst

    today = today_cst()
    if not is_trading_day(today):
        logger.info("[定时任务] %s 今日非交易日, 跳过", job_name)
        return {"sent": False, "reason": "non_trading_day"}

    maker = await get_sessionmaker()
    active_statuses = (
        MonitorStatus.PENDING,
        MonitorStatus.MONITORING,
        MonitorStatus.TRIGGERED,
    )
    async with maker() as session:
        rows = (
            await session.execute(
                select(MonitoredStock, Blogger)
                .join(AnalysisResult, MonitoredStock.analysis_result_id == AnalysisResult.id)
                .join(Blogger, AnalysisResult.blogger_id == Blogger.id)
                .where(
                    MonitoredStock.monitor_date == today,
                    MonitoredStock.status.in_(active_statuses),
                    Blogger.platform.in_(HUMAN_PLATFORMS),
                )
            )
        ).all()

    candidates = _merge_candidates(rows)
    client = get_stock_client()
    met: list[dict] = []
    unmet: list[dict] = []
    unknown: list[dict] = []
    for item in candidates:
        try:
            intraday, quote = await asyncio.gather(
                asyncio.to_thread(client.get_intraday, item["code"]),
                asyncio.to_thread(client.get_realtime_quote, item["code"]),
            )
            status = vwap_break_status(intraday)
            item["change_pct"] = float((quote or {}).get("change_pct") or 0)
            item["note"] = status.get("note") or "分时状态正常"
            (unmet if status.get("deteriorating") else met).append(item)
        except Exception as exc:
            item["note"] = f"行情取数失败：{str(exc)[:80]}"
            item["change_pct"] = 0
            unknown.append(item)
            logger.warning("%s 10:30 盯盘取数失败: %s", item["code"], exc)

    # 汇总也代表一次状态结算：已明确不满足的标的从当前盯盘集合移出，保留历史状态。
    unmet_codes = {item["code"] for item in unmet}
    if unmet_codes:
        async with maker() as session:
            active_rows = (
                await session.execute(
                    select(MonitoredStock).where(
                        MonitoredStock.monitor_date == today,
                        MonitoredStock.status.in_(active_statuses),
                        MonitoredStock.stock_code.in_(unmet_codes),
                    )
                )
            ).scalars().all()
            for monitored in active_rows:
                monitored.status = MonitorStatus.EXPIRED
            await session.commit()

    day = today.isoformat()
    notifier = EmailNotifier()
    sent = await notifier.queue_and_send(
        NotificationMessage(
            type=NotificationType.SIGNAL_ALERT.value,
            recipient=notifier._to[0] if notifier._to else "",
            subject=standard_subject(
                "盘中盯盘",
                day,
                f"仍满足 {len(met)}｜已不满足 {len(unmet)}",
            ),
            body=_report_html(day, met, unmet, unknown),
        ),
        related_type="intraday_report",
        related_id=day,
    )
    logger.info(
        "[定时任务] %s 完成: 仍满足%s 已不满足%s 待确认%s",
        job_name,
        len(met),
        len(unmet),
        len(unknown),
    )
    return {"sent": sent, "met": len(met), "unmet": len(unmet), "unknown": len(unknown)}
