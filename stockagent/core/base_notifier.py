"""通知器抽象基类。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class NotificationMessage:
    """通知消息。"""

    type: str  # daily_advice / signal_alert / trade_confirm / pnl_report
    recipient: str  # 邮箱地址
    subject: str
    body: str  # HTML 正文
    metadata: dict[str, Any] = field(default_factory=dict)


class AbstractNotifier(ABC):
    """通知发送器抽象基类。"""

    @abstractmethod
    async def send(self, notification: NotificationMessage) -> bool:
        """发送一条通知。返回 True 表示成功。"""
        ...

    @abstractmethod
    async def send_bulk(self, notifications: list[NotificationMessage]) -> list[bool]:
        """批量发送通知。返回每个通知的发送结果。"""
        ...
