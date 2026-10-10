"""盘中分时线盯盘任务 —— 真人博主/手工候选股"持续踩破分时线"预警。

定位(用户要求): 只做信息收集/盯盘预警, 不自主买卖。盘中每隔几分钟检查博主提到的
候选股是否"符合预期、没有持续恶化"; 红线 = 不能持续踩破分时均价线(锚定跳跳虎纪律)。
恶化即邮件通知用户, 当日每只只报一次(去重)。
"""

import logging

logger = logging.getLogger(__name__)

# 进入买入触发前的候选来源：只盯真人博主和用户手工加入的标的。
WATCH_PLATFORMS = ("bilibili", "weibo", "wechat", "user")


async def intraday_watch_job(job_name: str = "intraday_watch") -> None:
    logger.info(f"[定时任务] {job_name} 盘中分时线盯盘...")
    try:
        from stockagent.notification.email_notifier import email_policy_enabled

        if not email_policy_enabled("intraday_break_alerts", True):
            logger.debug("[定时任务] %s 即时逐股预警已关闭，状态统一在10:30汇总", job_name)
            return

        from sqlalchemy import func, select

        from stockagent.core.base_notifier import NotificationMessage
        from stockagent.core.db import get_sessionmaker
        from stockagent.core.enums import MonitorStatus, NotificationType
        from stockagent.core.models import (
            AnalysisResult,
            Blogger,
            MonitoredStock,
            Notification,
        )
        from stockagent.engine.intraday_watch import condition_watch, vwap_break_status
        from stockagent.market.stock_client import get_stock_client
        from stockagent.market.trading_calendar import is_trading_time
        from stockagent.notification.email_format import standard_subject
        from stockagent.notification.email_notifier import (
            EmailNotifier,
            notification_scope_id,
        )
        from stockagent.utils.time_utils import today_cst

        if not is_trading_time():
            logger.debug(f"[定时任务] {job_name} 非交易时间, 跳过")
            return

        today = today_cst()
        client = get_stock_client()
        maker = await get_sessionmaker()

        async with maker() as session:
            # 今日候选股(真人博主/用户手工加入, 仍在监控中, 合法6位代码)
            rows = (
                await session.execute(
                    select(MonitoredStock, Blogger)
                    .join(AnalysisResult, MonitoredStock.analysis_result_id == AnalysisResult.id)
                    .join(Blogger, AnalysisResult.blogger_id == Blogger.id)
                    .where(
                        MonitoredStock.monitor_date == today,
                        MonitoredStock.status == MonitorStatus.PENDING,
                        Blogger.platform.in_(WATCH_PLATFORMS),
                    )
                )
            ).all()

            if not rows:
                logger.info(f"[定时任务] {job_name} 今日无待盯盘候选股")
                return

            checked = alerted = 0
            for ms, blg in rows:
                code = (ms.stock_code or "").strip()
                if not (code.isdigit() and len(code) == 6):
                    continue
                checked += 1
                dedupe_id = notification_scope_id(today.isoformat(), code)

                # 当日是否已就该股发过分时线预警 → 去重
                dup = (
                    await session.execute(
                        select(func.count())
                        .select_from(Notification)
                        .where(
                            Notification.related_entity_type == "intraday_break",
                            Notification.related_entity_id == dedupe_id,
                        )
                    )
                ).scalar() or 0
                if dup:
                    continue

                import asyncio

                intraday = await asyncio.to_thread(client.get_intraday, code)
                status = vwap_break_status(intraday)

                # 忠实评估博主条件: 回踩<参考线> + 分时企稳/承接(真·分时均价线)
                tech = await asyncio.to_thread(client.get_technical_indicators, code)
                conds = ms.trigger_conditions or []
                ref_line, ref_label = _ref_line(conds, tech or {})
                quote = await asyncio.to_thread(client.get_realtime_quote, code)
                cur_price = (quote or {}).get("current_price") or status.get("price") or 0
                cw = condition_watch(cur_price, ref_line, status)
                logger.info(
                    f"[盯盘] {ms.stock_name}({code}) {ref_label}={ref_line:.2f} | {cw['verdict']}"
                )

                # 只在"持续踩破分时线"(红线破位)时邮件预警, 避免刷屏
                if not status.get("deteriorating"):
                    continue

                chg = (quote or {}).get("change_pct", 0) or 0
                cond_txt = (
                    "；".join(
                        c.get("plain_description") or c.get("description") or "" for c in conds
                    )
                    if conds
                    else ""
                )

                observing = [
                    f"{item.stock_name or item.stock_code}({item.stock_code})"
                    for item, _ in rows
                    if item.id != ms.id
                ]
                body = _alert_html(ms, blg, status, chg, cond_txt, cw, ref_label, observing)
                n = EmailNotifier()
                await n.queue_and_send(
                    NotificationMessage(
                        type=NotificationType.SIGNAL_ALERT.value,
                        recipient=n._to[0] if n._to else "",
                        subject=standard_subject(
                            "盘中预警",
                            today.isoformat(),
                            f"{ms.stock_name or code} 已不满足",
                        ),
                        body=body,
                    ),
                    related_type="intraday_break",
                    related_id=dedupe_id,
                )
                # 这不是单纯的告警：既然已确认不满足，立即退出当前盯盘集合。
                # 记录仍保留为 EXPIRED，供复盘审计，但不会再次出现在当前总览。
                ms.status = MonitorStatus.EXPIRED
                alerted += 1
                logger.info(f"[盯盘] {ms.stock_name}({code}) 分时线恶化, 已预警: {status['note']}")

            await session.commit()

        logger.info(f"[定时任务] {job_name} 完成: 检查{checked} 预警{alerted}")
    except Exception as e:
        logger.error(f"[定时任务] {job_name} 失败: {e}", exc_info=True)


def _ref_line(conds: list, tech: dict) -> tuple[float, str]:
    """从博主条件+技术面取回踩参考位。support_bounce/ma_cross 的 value→MA5/10/20, 0→支撑位。

    对应跳跳虎"回踩5日线/支撑位"——条件里指明了哪条线就用哪条, 没指明用支撑位/5日线兜底。
    """
    for c in conds or []:
        if c.get("condition_type") in ("support_bounce", "ma_cross"):
            try:
                v = int(c.get("value") or 0)
            except (TypeError, ValueError):
                v = 0
            if v in (5, 10, 20) and tech.get(f"ma{v}", 0) > 0:
                return tech[f"ma{v}"], f"{v}日线"
            return (tech.get("support", 0) or tech.get("ma5", 0)), "支撑位/5日线"
    return tech.get("ma5", 0), "5日线(默认)"


def _alert_html(
    ms, blg, status: dict, chg: float, cond_txt: str, cw: dict | None = None,
    ref_label: str = "", observing: list[str] | None = None,
) -> str:
    color = "#e03131"  # 走弱, A股红
    cond_line = (
        f"<b>博主给的盯盘条件</b>：{cond_txt}"
        if cond_txt
        else "<b>盯盘方式</b>：博主未给明确条件，AI 按分时线基本盯盘"
    )
    reason = (ms.reason or "")[:120]
    observing_text = "、".join(dict.fromkeys(observing or [])) or "暂无"
    verdict_line = ""
    if cw:
        dev = cw.get("dev_from_ref_pct")
        dev_txt = f"（现价离{ref_label} {dev:+.1f}%）" if dev is not None else ""
        verdict_line = (
            f"<b>忠实条件判定</b>：<span style='color:{color};font-weight:600'>"
            f"{cw.get('verdict', '')}</span>{dev_txt}<br>"
        )
    return f"""<div style="font-family:'Microsoft YaHei',sans-serif;max-width:560px;margin:0 auto;padding:16px;color:#222">
<h3 style="margin:0 0 8px">⚠️ 盯盘预警 · {ms.stock_name or ms.stock_code}（{ms.stock_code}）</h3>
<div style="background:#fff8e1;border:1px solid #ffe8a1;padding:8px 10px;border-radius:4px;font-size:13px;line-height:1.7;margin-bottom:8px">
<b>盘中总览</b><br>已不满足：{ms.stock_name or ms.stock_code}（{ms.stock_code}）<br>
仍需观望/继续盯盘：{observing_text}</div>
<p style="background:#fff5f5;border-left:4px solid {color};padding:10px 12px;border-radius:4px;line-height:1.7">
<b>来源博主</b>：{blg.name}<br>
{cond_line}<br>
{verdict_line}<b>分时线状态</b>：<span style="color:{color};font-weight:600">{status.get("note", "")}</span><br>
当前价 {status.get("price")} · 分时均价 {status.get("vwap")} · 现低于均价 {abs(status.get("cur_vs_vwap_pct", 0)):.2f}% · 当日 {chg:+.2f}%
</p>
<p style="color:#555;line-height:1.6"><b>博主原始理由</b>：{reason}</p>
<p style="color:#888;font-size:12px">该候选股盘中持续走弱、未见承接回升，可能已不符合预期，请留意。本提醒仅供参考，不构成投资建议。</p>
</div>"""
