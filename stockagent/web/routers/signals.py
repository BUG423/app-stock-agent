"""交易信号 API 路由。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from stockagent.core.db import get_session
from stockagent.core.enums import MonitorStatus
from stockagent.core.models import MonitoredStock, TradeSignal
from stockagent.core.schemas import (
    PaginatedResponse,
    TradeSignalDetail,
    TradeSignalResponse,
)

router = APIRouter()


@router.get("", response_model=PaginatedResponse)
async def list_signals(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    stock_code: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """获取交易信号列表。"""
    query = select(TradeSignal)
    count_query = select(func.count(TradeSignal.id))

    if status:
        query = query.where(TradeSignal.status == status)
        count_query = count_query.where(TradeSignal.status == status)
    if stock_code:
        query = query.where(TradeSignal.stock_code == stock_code)
        count_query = count_query.where(TradeSignal.stock_code == stock_code)

    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size).order_by(TradeSignal.triggered_at.desc())

    result = await session.execute(query)
    signals = result.scalars().all()

    count_result = await session.execute(count_query)
    total = count_result.scalar() or 0

    return PaginatedResponse(
        items=[TradeSignalResponse.model_validate(s) for s in signals],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if total > 0 else 1,
    )


@router.get("/{signal_id}", response_model=TradeSignalDetail)
async def get_signal(
    signal_id: str,
    session: AsyncSession = Depends(get_session),
):
    """获取信号详情（含用户反馈）。"""
    signal = await session.get(TradeSignal, signal_id)
    if not signal:
        raise HTTPException(status_code=404, detail="信号不存在")

    # 加载关联数据
    await session.refresh(signal, ["user_action", "monitored_stock"])

    return TradeSignalDetail(
        **TradeSignalResponse.model_validate(signal).model_dump(),
        user_action=signal.user_action,
    )


@router.get("/monitored/active")
async def get_active_monitored(
    session: AsyncSession = Depends(get_session),
):
    """获取今日盯盘列表（仅针对今天的盘前建议，不含历史）。"""
    from stockagent.utils.time_utils import today_cst

    today = today_cst()

    stmt = (
        select(MonitoredStock)
        .where(MonitoredStock.monitor_date == today)
        .where(MonitoredStock.status == MonitorStatus.PENDING)
        .order_by(MonitoredStock.created_at.desc())
    )
    result = await session.execute(stmt)
    monitored = result.scalars().all()

    return {
        "date": str(today),
        "count": len(monitored),
        "items": [
            {
                "id": m.id,
                "stock_code": m.stock_code,
                "stock_name": m.stock_name,
                "direction": m.direction.value if m.direction else "neutral",
                "reason": m.reason,
                "conditions": m.trigger_conditions,
            }
            for m in monitored
        ],
    }
