"""用户操作 API 路由。"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from stockagent.core.db import get_session
from stockagent.core.enums import SignalStatus
from stockagent.core.models import TradeSignal, UserAction
from stockagent.core.schemas import UserActionConfirm, UserActionResponse
from stockagent.utils.time_utils import now_cst

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/confirm", response_model=UserActionResponse)
async def confirm_signal(
    data: UserActionConfirm,
    session: AsyncSession = Depends(get_session),
):
    """确认或拒绝交易信号。"""
    signal = await session.get(TradeSignal, data.signal_id)
    if not signal:
        raise HTTPException(status_code=404, detail="信号不存在")

    if signal.status != SignalStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"信号状态为 '{signal.status.value}'，无法确认",
        )

    # 创建用户操作记录
    action = UserAction(
        trade_signal_id=signal.id,
        user_confirmed=data.confirmed,
        actual_action=data.actual_action,
        actual_price=data.actual_price,
        actual_quantity=data.actual_quantity,
        feedback_text=data.feedback_text,
        feedback_rating=data.feedback_rating,
    )
    session.add(action)

    if data.confirmed:
        # 仅记录用户反馈，不执行交易或纸上账本。
        signal.status = SignalStatus.CONFIRMED
        signal.confirmed_at = now_cst()
        await session.commit()
        logger.info(f"用户确认信号 {signal.id}，已记录反馈")
    else:
        signal.status = SignalStatus.REJECTED
        await session.commit()
        logger.info(f"用户拒绝信号 {signal.id}")

    return UserActionResponse.model_validate(action)


@router.get("", response_model=list[UserActionResponse])
async def list_actions(
    signal_id: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """获取用户操作历史。"""
    from sqlalchemy import select

    query = select(UserAction).order_by(UserAction.created_at.desc())
    if signal_id:
        query = query.where(UserAction.trade_signal_id == signal_id)

    result = await session.execute(query)
    actions = result.scalars().all()

    return [UserActionResponse.model_validate(a) for a in actions]
