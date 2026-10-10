"""邮件通知器 —— SMTP 发送 + HTML 模板渲染。"""

import logging
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import aiosmtplib
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from stockagent.core.base_notifier import AbstractNotifier, NotificationMessage
from stockagent.core.config import config
from stockagent.core.db import get_sessionmaker
from stockagent.core.enums import NotificationType
from stockagent.core.models import MonitoredStock, TradeSignal
from stockagent.core.models import Notification as NotifModel
from stockagent.notification.email_format import standard_subject
from stockagent.utils.time_utils import now_cst

logger = logging.getLogger(__name__)

# Jinja2 模板环境
_template_dir = Path(__file__).parent / "templates"
_template_env = Environment(
    loader=FileSystemLoader(str(_template_dir)) if _template_dir.exists() else None,
    autoescape=True,
)

_DELIVERY_LOCK_MINUTES = 5


_IDEMPOTENT_NOTIFICATION_TYPES = {
    "raw_content",
    "intraday_break",
    "intraday_report",
    "eod_review",
}


def notification_scope_id(*parts: object) -> str:
    """把日期、股票等业务范围压成数据库可存的 12 位稳定 ID。"""
    value = "|".join(str(part) for part in parts)
    return sha256(value.encode("utf-8")).hexdigest()[:12]


def _notification_dedupe_key(related_type: str, related_id: str) -> str | None:
    """为只应发送一次的业务通知生成数据库唯一幂等键。"""
    if related_type in _IDEMPOTENT_NOTIFICATION_TYPES and related_id:
        return f"{related_type}:{related_id}"
    return None


def email_policy_enabled(name: str, default: bool = True) -> bool:
    policy = config.get("notification", "email", "policy", default={})
    return bool(policy.get(name, default)) if isinstance(policy, dict) else default


class EmailNotifier(AbstractNotifier):
    """SMTP 邮件通知发送器。"""

    def __init__(self) -> None:
        email_cfg = config.get("notification", "email", default={})
        self._smtp_server: str = email_cfg.get("smtp_server", "")
        self._smtp_port: int = int(email_cfg.get("smtp_port", 465))
        self._use_ssl: bool = email_cfg.get("smtp_use_ssl", True)
        self._username: str = email_cfg.get("username", "")
        self._password: str = email_cfg.get("password", "")
        self._from: str = email_cfg.get("from_address", self._username)
        self._to: list[str] = email_cfg.get("to_addresses", [])

    async def _send_raw(self, notification: NotificationMessage) -> bool:
        """发送一封邮件（底层实现，不含重试）。"""
        if not self._smtp_server:
            logger.warning("SMTP 服务器未配置，跳过邮件发送")
            return False

        from email.message import EmailMessage

        message = EmailMessage()
        message["Subject"] = notification.subject
        message["From"] = self._from
        message["To"] = notification.recipient
        # 抄送：to_addresses 里除主收件人外的其他地址(以主号为主, 其余抄送)。
        # aiosmtplib 会自动把 To+Cc 都作为投递对象。
        cc = [a for a in self._to if a and a != notification.recipient]
        if cc:
            message["Cc"] = ", ".join(cc)
        message.set_content(notification.body, subtype="html")

        if self._use_ssl:
            await aiosmtplib.send(
                message,
                hostname=self._smtp_server,
                port=self._smtp_port,
                username=self._username,
                password=self._password,
                use_tls=True,
            )
        else:
            await aiosmtplib.send(
                message,
                hostname=self._smtp_server,
                port=self._smtp_port,
                username=self._username,
                password=self._password,
                start_tls=True,
            )

        logger.info(f"邮件已发送: {notification.subject} -> {notification.recipient}")
        return True

    async def send(
        self,
        notification: NotificationMessage,
        max_retries: int = 3,
        alert_on_failure: bool = True,
    ) -> bool:
        """发送一封邮件（含重试：最多3次，指数退避 2s/4s/8s）。

        重试耗尽后会触发健康告警邮件通知用户。
        """
        last_error = None
        for attempt in range(max_retries):
            try:
                return await self._send_raw(notification)
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait = 2 ** (attempt + 1)  # 2s, 4s
                    logger.warning(
                        f"邮件发送失败 (第{attempt + 1}/{max_retries}次): {e}，{wait}s 后重试..."
                    )
                    import asyncio

                    await asyncio.sleep(wait)
                else:
                    logger.error(
                        f"邮件发送最终失败 (已重试{max_retries}次): "
                        f"subject={notification.subject}, error={e}"
                    )

        # 重试耗尽 → 发健康告警
        if alert_on_failure:
            try:
                await self.send_health_alert(
                    component="邮件通知",
                    error=f"发送失败(重试{max_retries}次): {str(last_error)[:100]}",
                    detail=f"主题: {notification.subject}",
                )
            except Exception:
                logger.error("健康告警邮件也发送失败，请检查 SMTP 配置")
        return False

    async def _claim_queued_notification(
        self, notification_id: str
    ) -> tuple[NotifModel, NotificationMessage] | None:
        """以短租约原子领取一封待发送邮件，避免多个发送方重复投递。"""
        maker = await get_sessionmaker()
        now = now_cst()
        lock_until = now + timedelta(minutes=_DELIVERY_LOCK_MINUTES)

        async with maker() as session:
            result = await session.execute(
                update(NotifModel)
                .where(
                    NotifModel.id == notification_id,
                    NotifModel.sent.is_(False),
                    NotifModel.dead_letter.is_(False),
                    or_(
                        NotifModel.delivery_lock_until.is_(None),
                        NotifModel.delivery_lock_until <= now,
                    ),
                )
                .values(delivery_lock_until=lock_until)
            )
            if not result.rowcount:
                await session.rollback()
                return None

            notif = await session.get(NotifModel, notification_id)
            if not notif:
                await session.rollback()
                return None
            message = NotificationMessage(
                type=notif.type,
                recipient=notif.recipient,
                subject=notif.subject,
                body=notif.body,
                metadata={},
            )
            await session.commit()
            return notif, message

    async def deliver_queued_notification(
        self,
        notification_id: str,
        *,
        alert_on_failure: bool = True,
    ) -> bool | None:
        """发送已入队邮件；返回 None 表示已被另一个发送方领取。"""
        claimed = await self._claim_queued_notification(notification_id)
        if claimed is None:
            return None

        _notif, message = claimed
        success = await self.send(message, alert_on_failure=alert_on_failure)
        maker = await get_sessionmaker()
        async with maker() as session:
            notif = await session.get(NotifModel, notification_id)
            if not notif:
                return success

            notif.delivery_lock_until = None
            if success:
                notif.sent = True
                notif.sent_at = now_cst()
                notif.error_message = None
                notif.next_retry_at = None
            else:
                notif.error_message = "发送失败(含重试)"
                notif.attempt_count = (notif.attempt_count or 0) + 1
                if notif.attempt_count >= 5:
                    notif.dead_letter = True
                else:
                    notif.next_retry_at = now_cst() + timedelta(
                        minutes=min(2**notif.attempt_count, 60)
                    )
            await session.commit()
        return success

    async def queue_and_send(
        self,
        notification: NotificationMessage,
        related_type: str = "",
        related_id: str = "",
        *,
        defer_send: bool = False,
        replace_pending: bool = False,
    ) -> bool:
        """原子入队并按需立即发送。

        ``raw_content`` 会写入唯一幂等键：并发的成功分析和降级分析只能共用一条
        通知。降级通知可延后发送，让同时完成的成功分析先把队列内容升级为完整版。
        """
        if related_type == "raw_content" and not email_policy_enabled("new_content", True):
            logger.info("邮件策略已关闭即时博主动态邮件: %s", notification.subject)
            return True
        dedupe_key = _notification_dedupe_key(related_type, related_id)
        notif_id: str | None = None
        should_deliver = False

        maker = await get_sessionmaker()
        try:
            async with maker() as session:
                notif = NotifModel(
                    type=notification.type,
                    recipient=notification.recipient,
                    subject=notification.subject,
                    body=notification.body,
                    related_entity_type=related_type or None,
                    related_entity_id=related_id or None,
                    dedupe_key=dedupe_key,
                    sent=False,
                )
                session.add(notif)
                await session.commit()
                notif_id = notif.id
                should_deliver = not defer_send
        except IntegrityError:
            # 并发任务已为同一原文创建通知。此处绝不能再裸发邮件，否则唯一索引
            # 反而会变成“入库失败但仍重复投递”。
            if not dedupe_key:
                logger.error("写入通知队列违反约束，但未提供幂等键")
                return False
            async with maker() as session:
                existing = await session.scalar(
                    select(NotifModel).where(NotifModel.dedupe_key == dedupe_key)
                )
                if not existing:
                    logger.error("通知幂等冲突后未找到已有记录: %s", dedupe_key)
                    return False

                notif_id = existing.id
                lock_active = bool(
                    existing.delivery_lock_until and existing.delivery_lock_until > now_cst()
                )
                if (
                    replace_pending
                    and not existing.sent
                    and not existing.dead_letter
                    and not lock_active
                ):
                    # 成功分析优先于“仅原文”降级版本；尚未投递时原地升级即可。
                    existing.type = notification.type
                    existing.recipient = notification.recipient
                    existing.subject = notification.subject
                    existing.body = notification.body
                    existing.error_message = None
                    existing.next_retry_at = None
                    await session.commit()
                    should_deliver = not defer_send
                    logger.info("已将待发降级通知升级为完整分析: %s", notification.subject)
                else:
                    return True
        except Exception as e:
            logger.error("写入通知队列失败: %s", e)
            return False

        if not notif_id or not should_deliver:
            return bool(notif_id)

        # 领取租约后发送；若定时任务已先领取则不再重复投递。
        result = await self.deliver_queued_notification(notif_id, alert_on_failure=False)
        return result is not False

    async def send_bulk(self, notifications: list[NotificationMessage]) -> list[bool]:
        """批量发送邮件。"""
        results = []
        for n in notifications:
            result = await self.send(n)
            results.append(result)
        return results

    async def send_daily_advice(
        self,
        summary: dict[str, Any],
        recipient: str | None = None,
    ) -> bool:
        """发送每日建议汇总邮件。"""
        to = recipient or (self._to[0] if self._to else "")
        if not to:
            logger.warning("无收件人，跳过邮件发送")
            return False

        body = self._render_daily_advice_html(summary)

        notification = NotificationMessage(
            type=NotificationType.DAILY_ADVICE.value,
            recipient=to,
            subject=standard_subject("每日汇总", summary.get("date", "")),
            body=body,
            metadata={"date": summary.get("date", "")},
        )

        return await self.send(notification)

    async def send_signal_alert(
        self,
        signal: TradeSignal,
        monitored: MonitoredStock | None = None,
        recipient: str | None = None,
    ) -> bool:
        """发送交易信号警报邮件。"""
        action_value = signal.action.value if hasattr(signal.action, "value") else str(signal.action)
        if action_value == "buy" and not email_policy_enabled("buy_signals", True):
            logger.info("邮件策略已关闭购入信号邮件: %s", signal.stock_code)
            return True
        to = recipient or (self._to[0] if self._to else "")
        if not to:
            return False

        action_cn = {"buy": "买入", "sell": "卖出", "hold": "观望"}
        action_label = action_cn.get(signal.action.value, signal.action.value)

        body = self._render_signal_alert_html(signal, monitored)

        notification = NotificationMessage(
            type=NotificationType.SIGNAL_ALERT.value,
            recipient=to,
            subject=standard_subject(
                "信号提醒",
                now_cst().strftime("%Y-%m-%d %H:%M"),
                f"{action_label} {signal.stock_name or signal.stock_code}",
            ),
            body=body,
            metadata={
                "signal_id": signal.id,
                "stock_code": signal.stock_code,
                "action": signal.action.value,
            },
        )

        return await self.send(notification)

    async def send_new_content_alert(
        self,
        blogger_name: str,
        title: str,
        stocks_detail: list[dict],
        conditions: list[dict],
        watching: int,
        raw_text: str,
        url: str,
        recipient: str | None = None,
    ) -> bool:
        """发送「博主新内容」提醒邮件 —— 两部分：① AI提取(股票/条件/规划/在盯) ② 博主原文。

        每条新图文只提醒一次（content_id 去重）。无具体标的的不发（调用方过滤）。
        """
        if not email_policy_enabled("new_content", True):
            logger.info("邮件策略已关闭即时博主动态邮件: %s", title)
            return True
        to = recipient or (self._to[0] if self._to else "")
        if not to:
            return False
        dir_cn = {"bullish": "📈看多", "bearish": "📉看空", "neutral": "➡️观望"}
        sig_cn = {
            "follow_now": "<span style='color:#e03131;font-weight:600'>🔴立即可跟</span>",
            "conditional": "<span style='color:#f08c00'>🟡盯盘待触发</span>",
            "exited": "<span style='color:#868e96'>⚪已清仓</span>",
        }
        stance_cn = {"support": "✅数据支持", "neutral": "➖中性", "against": "⚠️数据存疑"}
        _rows = []
        for s in stocks_detail[:8]:
            sig = sig_cn.get(s.get("signal_type", "conditional"), "")
            rv = s.get("review") or {}
            review_line = ""
            if rv:
                sc = int(rv.get("score", 0) or 0)
                stars = "★" * sc + "☆" * (5 - sc)
                review_line = (
                    "<tr><td colspan='4' style='padding:2px 6px 8px;background:#fafafa'>"
                    f"<span style='font-size:12px;color:#3b5bdb'>🔍 AI复核 {stars} "
                    f"{stance_cn.get(rv.get('stance'), '')}</span>"
                    f"<div style='font-size:12px;color:#666'>{rv.get('verdict', '')}"
                    + (f" · 风险:{rv.get('risk')}" if rv.get("risk") else "")
                    + "</div></td></tr>"
                )
            _rows.append(
                f"<tr><td style='padding:6px'><b>{s.get('name', '?')}</b></td>"
                f"<td style='padding:6px;color:#666;font-variant-numeric:tabular-nums'>{s.get('code', '?')}</td>"
                f"<td style='padding:6px'>{dir_cn.get(s.get('direction'), s.get('direction', ''))}"
                f"<br>{sig}</td>"
                f"<td style='padding:6px;color:#666;font-size:13px'>{s.get('reason', '')}</td></tr>"
                + review_line
            )
        stocks_html = "".join(_rows)
        cond_html = (
            "".join(
                f"<div style='margin:4px 0'>• {c.get('description', '')}"
                + (
                    f"<div style='color:#999;font-size:12px;padding-left:14px'>{c['plain_description']}</div>"
                    if c.get("plain_description")
                    else ""
                )
                + "</div>"
                for c in conditions[:4]
            )
            or "<div style='color:#999'>无明确条件</div>"
        )
        body = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
<div style="font-family:'Microsoft YaHei',sans-serif;max-width:640px;margin:0 auto;padding:24px;color:#18181b">
<h2 style="margin:0 0 4px">📢 {blogger_name} 新内容</h2>
<p style="color:#666;font-size:14px;margin:0 0 16px">{title}</p>
<div style="background:#f0f4ff;border-radius:8px;padding:16px;margin:16px 0">
<div style="font-size:13px;color:#3b5bdb;font-weight:600;margin-bottom:8px">📌 AI 提取</div>
<div style="font-size:13px;color:#999;margin:8px 0 4px">提及标的（全称+代码）</div>
<table style="width:100%;border-collapse:collapse;font-size:14px;border:1px solid #e5e7eb">{stocks_html}</table>
<div style="font-size:13px;color:#999;margin:12px 0 4px">购入条件</div>
<div style="font-size:14px;line-height:1.6">{cond_html}</div>
<div style="font-size:13px;color:#999;margin:12px 0 4px">系统规划</div>
<div style="font-size:14px">次日 9:30-15:00 自动盯盘，满足上述条件即触发交易信号邮件</div>
<div style="font-size:13px;color:#999;margin:12px 0 4px">目前在盯</div>
<div style="font-size:14px">{watching} 只标的</div>
</div>
<div style="border-top:1px dashed #ddd;margin:16px 0;padding-top:12px">
<div style="font-size:13px;color:#999;margin-bottom:6px">📄 博主原文</div>
<div style="font-size:13px;color:#555;white-space:pre-wrap;background:#fafafa;padding:12px;border-radius:6px;max-height:320px;overflow:auto">{(raw_text or "")[:1500]}</div>
</div>
<p style="margin:16px 0 0"><a href="{url}" style="color:#3b5bdb">查看原文 →</a></p>
<p style="color:#bbb;font-size:12px;margin-top:20px">StockAgent · 仅供参考，不构成投资建议</p>
</div></body></html>"""
        notification = NotificationMessage(
            type=NotificationType.DAILY_ADVICE.value,
            recipient=to,
            subject=standard_subject("博主动态", now_cst().strftime("%Y-%m-%d %H:%M"), blogger_name),
            body=body,
            metadata={"blogger": blogger_name, "url": url, "stocks": len(stocks_detail)},
        )
        return await self.send(notification)

    async def send_sell_alert(
        self,
        stock_code: str,
        stock_name: str | None,
        entry_price: float,
        current_price: float,
        quantity: int,
        pnl_pct: float,
        realized_pnl: float,
        reason: str,
        held_days: int,
        recipient: str | None = None,
    ) -> bool:
        """发送「卖出提醒」—— 持仓触发离场条件时及时通知用户。"""
        to = recipient or (self._to[0] if self._to else "")
        if not to:
            return False
        win = pnl_pct >= 0
        color = "#e03131" if win else "#2f9e44"  # A股: 红涨绿跌
        emoji = (
            "🎯"
            if "止盈" in reason
            else "🛑"
            if "止损" in reason
            else "⏰"
            if "持满" in reason
            else "⚠️"
        )
        body = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
<div style="font-family:'Microsoft YaHei',sans-serif;max-width:560px;margin:0 auto;padding:24px;color:#18181b">
<h2 style="margin:0 0 4px">{emoji} 卖出提醒</h2>
<p style="color:#666;font-size:14px;margin:0 0 16px">系统建议卖出 —— {reason}</p>
<div style="background:{"#fff5f5" if win else "#ebfbee"};border:2px solid {color};border-radius:8px;padding:16px;margin:12px 0;text-align:center">
  <div style="font-size:20px;font-weight:bold">{stock_name or ""} ({stock_code})</div>
  <div style="font-size:32px;font-weight:bold;color:{color};margin:8px 0">{pnl_pct:+.2f}%</div>
  <div style="font-size:13px;color:#666">买入 ¥{entry_price:.2f} → 现价 ¥{current_price:.2f} · {quantity}股 · 已持{held_days}个交易日</div>
  <div style="font-size:14px;color:{color};margin-top:6px">触发价至现价 {pnl_pct:+.2f}%</div>
</div>
<p style="font-size:14px;color:#555">卖出理由：<b>{reason}</b></p>
<p style="color:#bbb;font-size:12px;margin-top:20px">StockAgent · 仅供参考，不构成投资建议</p>
</div></body></html>"""
        notification = NotificationMessage(
            type=NotificationType.SIGNAL_ALERT.value,
            recipient=to,
            subject=standard_subject(
                "离场提醒",
                now_cst().strftime("%Y-%m-%d %H:%M"),
                f"{stock_name or stock_code} {pnl_pct:+.1f}%",
            ),
            body=body,
            metadata={"stock_code": stock_code, "reason": reason},
        )
        return await self.send(notification)

    async def send_health_alert(
        self,
        component: str,
        error: str,
        detail: str = "",
        recipient: str | None = None,
    ) -> bool:
        """发送系统健康告警 —— 关键组件异常时通知用户。"""
        to = recipient or (self._to[0] if self._to else "")
        if not to:
            return False
        from stockagent.utils.time_utils import now_cst

        ts = now_cst().strftime("%Y-%m-%d %H:%M:%S")  # A股北京时间
        body = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
<div style="font-family:'Microsoft YaHei',sans-serif;max-width:560px;margin:0 auto;padding:24px">
<div style="background:#fff3e0;border-left:4px solid #ff5722;padding:16px;border-radius:4px">
<h3 style="color:#e65100;margin:0 0 8px">⚠️ StockAgent 系统告警</h3>
<p style="font-size:14px;color:#666;margin:0">组件 <b>{component}</b> 发生异常</p>
</div>
<div style="background:#fafafa;padding:12px;margin:12px 0;border-radius:4px">
<p style="font-size:13px;color:#999;margin:0 0 4px">错误信息</p>
<pre style="font-size:12px;color:#e65100;white-space:pre-wrap;margin:0">{error}</pre>
</div>
{'<div style="background:#fafafa;padding:12px;margin:12px 0;border-radius:4px"><p style="font-size:13px;color:#999;margin:0 0 4px">详细信息</p><pre style="font-size:12px;color:#555;white-space:pre-wrap;margin:0">' + detail + "</pre></div>" if detail else ""}
<p style="font-size:12px;color:#bbb;margin:12px 0">触发时间: {ts}</p>
</div></body></html>"""
        notification = NotificationMessage(
            type=NotificationType.TEST.value,
            recipient=to,
            subject=standard_subject("系统告警", ts[:16], component),
            body=body,
        )
        success = await self.send(notification)
        if success:
            logger.warning(f"健康告警已发送: {component}")
        return success

    def _render_daily_advice_html(self, summary: dict[str, Any]) -> str:
        """渲染每日建议 HTML。"""
        bloggers = summary.get("bloggers", {})
        all_stocks = summary.get("all_stocks", [])
        sentiment = summary.get("market_sentiment", "neutral")
        sentiment_cn = {"bullish": "偏多 😄", "bearish": "偏空 😟", "neutral": "中性 😐"}

        rows = ""
        for blogger_name, items in bloggers.items():
            for item in items:
                direction_cn = {"bullish": "📈看多", "bearish": "📉看空", "neutral": "➡️观望"}
                confidence_cn = {"high": "⭐⭐⭐", "medium": "⭐⭐", "low": "⭐"}
                stocks_str = (
                    ", ".join(f"{s['name']}({s['code']})" for s in item.get("stocks", []))
                    or "无具体标的"
                )

                rows += f"""
                <tr>
                    <td>{blogger_name}</td>
                    <td>{direction_cn.get(item["direction"], item["direction"])}</td>
                    <td>{stocks_str}</td>
                    <td>{confidence_cn.get(item["confidence"], item["confidence"])}</td>
                    <td style="max-width:300px">{item["summary"]}</td>
                </tr>"""

        stocks_list = (
            "".join(
                f"<li>{s['name']}({s['code']}) - {s.get('direction', '')} (via {s.get('blogger', '')})</li>"
                for s in all_stocks
            )
            or "<li>今日无推荐标的</li>"
        )

        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
body {{ font-family: 'Microsoft YaHei', sans-serif; background: #f5f5f5; padding: 20px; }}
.container {{ max-width: 700px; margin: 0 auto; background: #fff; border-radius: 8px; padding: 30px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
h2 {{ color: #333; border-bottom: 2px solid #4CAF50; padding-bottom: 10px; }}
table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
th, td {{ padding: 10px; text-align: left; border-bottom: 1px solid #eee; }}
th {{ background: #f9f9f9; }}
.footer {{ margin-top: 30px; color: #999; font-size: 12px; text-align: center; }}
.sentiment {{ font-size: 18px; font-weight: bold; margin: 10px 0; }}
</style></head><body>
<div class="container">
    <h2>📊 StockAgent 每日投资建议汇总</h2>
    <p>日期: {summary.get("date", "")} | 分析数: {summary.get("total_analyses", 0)}</p>
    <p class="sentiment">市场情绪: {sentiment_cn.get(sentiment, sentiment)}</p>

    <h3>📋 博主建议明细</h3>
    <table>
        <tr><th>博主</th><th>方向</th><th>标的</th><th>置信度</th><th>摘要</th></tr>
        {rows}
    </table>

    <h3>🎯 关注标的汇总</h3>
    <ul>{stocks_list}</ul>

    <div class="footer">
        <p>StockAgent - 自动投资信号聚合系统</p>
        <p>此邮件由系统自动生成，仅供参考，不构成投资建议。</p>
    </div>
</div>
</body></html>"""

    def _render_signal_alert_html(
        self,
        signal: TradeSignal,
        monitored: MonitoredStock | None = None,
    ) -> str:
        """渲染信号警报 HTML。"""
        action_cn = {"buy": "买入", "sell": "卖出", "hold": "观望"}
        action = action_cn.get(signal.action.value, signal.action.value)

        conditions_html = ""
        if signal.conditions_met:
            conditions_html = (
                "<ul>"
                + "".join(f"<li>{c.get('description', str(c))}</li>" for c in signal.conditions_met)
                + "</ul>"
            )

        price_min = signal.suggested_price_min or 0
        price_max = signal.suggested_price_max or 0
        price_html = ""
        if price_min > 0 and price_max > 0:
            price_html = f"""
        <div style="background:#e8f5e9;border:2px solid #4caf50;border-radius:8px;padding:16px;margin:12px 0;text-align:center">
            <div style="font-size:13px;color:#666;margin-bottom:4px">💰 建议买入区间</div>
            <div style="font-size:28px;font-weight:bold;color:#2e7d32">
                ¥{price_min:.2f} ~ ¥{price_max:.2f}
            </div>
            <div style="font-size:12px;color:#999;margin-top:4px">
                基于 VWAP 成交量均价 ~ 触发价，区间宽度 {(price_max / price_min - 1) * 100:.1f}%
            </div>
        </div>"""

        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
body {{ font-family: 'Microsoft YaHei', sans-serif; background: #f5f5f5; padding: 20px; }}
.container {{ max-width: 600px; margin: 0 auto; background: #fff; border-radius: 8px; padding: 30px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
.alert {{ background: #fff3e0; border-left: 4px solid #ff9800; padding: 15px; margin: 20px 0; }}
h2 {{ color: #e65100; }}
.detail {{ margin: 10px 0; }}
.detail span {{ font-weight: bold; }}
.btn {{ display: inline-block; padding: 10px 20px; margin: 10px; border-radius: 4px; text-decoration: none; color: #fff; font-weight: bold; }}
.btn-confirm {{ background: #4CAF50; }}
.btn-reject {{ background: #f44336; }}
.footer {{ margin-top: 30px; color: #999; font-size: 12px; text-align: center; }}
.price-box {{ background: #e8f5e9; border: 2px solid #4caf50; border-radius: 8px; padding: 16px; margin: 12px 0; text-align: center; }}
</style></head><body>
<div class="container">
    <h2>🚨 交易信号触发</h2>
    <div class="alert">
        <p style="font-size:24px;margin:0">
            📊 {signal.stock_name or signal.stock_code} ({signal.stock_code})
            <strong>{action}</strong>
        </p>
        <p style="font-size:18px;color:#e65100">当前价格: {signal.current_price}</p>
    </div>

    {price_html}
    <div class="detail">
        <p><span>触发时间:</span> {signal.triggered_at}</p>
        <p><span>触发条件:</span></p>
        {conditions_html}
        {"<p><span>理由:</span> " + monitored.reason + "</p>" if monitored and monitored.reason else ""}
    </div>

    <p>请登录 Web 面板确认或拒绝此信号。</p>
    <div class="footer">
        <p>StockAgent - 自动投资信号聚合系统</p>
        <p>此邮件由系统自动生成，仅供参考，不构成投资建议。</p>
    </div>
</div>
</body></html>"""
