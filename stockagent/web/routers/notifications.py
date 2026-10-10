"""通知 API 路由。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from stockagent.core.base_notifier import NotificationMessage
from stockagent.core.db import get_session
from stockagent.core.enums import NotificationType
from stockagent.core.models import Notification
from stockagent.core.schemas import NotificationResponse, TestEmailRequest
from stockagent.notification.email_notifier import EmailNotifier

router = APIRouter()


@router.get("")
async def list_notifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """获取通知历史。"""
    from sqlalchemy import func, select

    query = select(Notification).order_by(Notification.created_at.desc())
    count_query = select(func.count(Notification.id))

    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await session.execute(query)
    notifications = result.scalars().all()

    count_result = await session.execute(count_query)
    total = count_result.scalar() or 0

    return {
        "items": [NotificationResponse.model_validate(n) for n in notifications],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/test")
async def send_test_email(
    data: TestEmailRequest,
):
    """发送测试邮件。"""
    notifier = EmailNotifier()
    notification = NotificationMessage(
        type=NotificationType.TEST.value,
        recipient=data.recipient,
        subject="StockAgent 测试邮件",
        body="<h2>✅ StockAgent 邮件通知正常</h2><p>如果你收到此邮件，说明 SMTP 配置正确。</p>",
    )
    success = await notifier.send(notification)
    return {"success": success, "message": "邮件已发送" if success else "邮件发送失败"}


@router.post("/{notification_id}/retry")
async def retry_notification(
    notification_id: str,
    session: AsyncSession = Depends(get_session),
):
    """将失败/死信邮件重新放回发送队列。"""
    notification = await session.get(Notification, notification_id)
    if not notification:
        raise HTTPException(status_code=404, detail="通知不存在")
    notification.sent = False
    notification.dead_letter = False
    notification.attempt_count = 0
    notification.next_retry_at = None
    notification.error_message = None
    await session.commit()
    return {"success": True}
