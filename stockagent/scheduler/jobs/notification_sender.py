"""邮件队列发送任务 —— 定期检查 Notification 表，发送所有未发送的通知。"""

import logging

from sqlalchemy import or_, select

from stockagent.core.db import get_sessionmaker
from stockagent.core.models import Notification as NotifModel
from stockagent.utils.time_utils import now_cst

logger = logging.getLogger(__name__)


async def send_queued_notifications(job_name: str = "notification_sender") -> dict:
    """发送所有未发送的队列邮件。每分钟运行，确保崩溃恢复后的邮件补发。"""
    maker = await get_sessionmaker()
    sent_count = 0
    fail_count = 0

    async with maker() as session:
        r = await session.execute(
            select(NotifModel)
            .where(
                NotifModel.sent.is_(False),
                NotifModel.dead_letter.is_(False),
                or_(NotifModel.next_retry_at.is_(None), NotifModel.next_retry_at <= now_cst()),
                or_(
                    NotifModel.delivery_lock_until.is_(None),
                    NotifModel.delivery_lock_until <= now_cst(),
                ),
            )
            .order_by(NotifModel.created_at.asc())
            .limit(10)  # 每轮最多10封，避免 SMTP 过载
        )
        pending_ids = [n.id for n in r.scalars().all()]

        if not pending_ids:
            return {"sent": 0, "failed": 0, "remaining": 0}

    from stockagent.notification.email_notifier import EmailNotifier

    notifier = EmailNotifier()
    for notification_id in pending_ids:
        try:
            # 邮件发送前会以数据库租约原子领取；即时发送者或另一实例已领取时直接跳过。
            result = await notifier.deliver_queued_notification(notification_id)
            if result is True:
                sent_count += 1
            elif result is False:
                fail_count += 1
        except Exception as e:
            fail_count += 1
            logger.warning("队列邮件发送异常 id=%s: %s", notification_id, e)

    # 检查剩余
    async with maker() as session:
        r = await session.execute(
            select(NotifModel).where(NotifModel.sent.is_(False), NotifModel.dead_letter.is_(False))
        )
        remaining = len(r.scalars().all())

    logger.info(f"队列发送完成: {sent_count}成功, {fail_count}失败, {remaining}剩余")
    return {"sent": sent_count, "failed": fail_count, "remaining": remaining}
