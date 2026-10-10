"""分析结果 API 路由。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from stockagent.core.db import get_session
from stockagent.core.models import AnalysisResult, RawContent
from stockagent.core.schemas import AnalysisResultResponse, PaginatedResponse

router = APIRouter()


@router.get("", response_model=PaginatedResponse)
async def list_analysis(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    blogger_id: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """获取分析结果列表。"""
    query = select(AnalysisResult)
    count_query = select(func.count(AnalysisResult.id))

    if blogger_id:
        query = query.where(AnalysisResult.blogger_id == blogger_id)
        count_query = count_query.where(AnalysisResult.blogger_id == blogger_id)

    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size).order_by(AnalysisResult.created_at.desc())

    result = await session.execute(query)
    analyses = result.scalars().all()

    count_result = await session.execute(count_query)
    total = count_result.scalar() or 0

    return PaginatedResponse(
        items=[AnalysisResultResponse.model_validate(a) for a in analyses],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if total > 0 else 1,
    )


@router.get("/{analysis_id}", response_model=AnalysisResultResponse)
async def get_analysis(
    analysis_id: str,
    session: AsyncSession = Depends(get_session),
):
    """获取单条分析详情。"""
    analysis = await session.get(AnalysisResult, analysis_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="分析结果不存在")
    return AnalysisResultResponse.model_validate(analysis)


@router.get("/{analysis_id}/raw")
async def get_analysis_raw(
    analysis_id: str,
    session: AsyncSession = Depends(get_session),
):
    """获取分析对应的原始内容。"""
    analysis = await session.get(AnalysisResult, analysis_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="分析结果不存在")

    raw = await session.get(RawContent, analysis.raw_content_id)
    if not raw:
        raise HTTPException(status_code=404, detail="原始内容不存在")

    return {
        "id": raw.id,
        "title": raw.title,
        "text": raw.text,
        "url": raw.url,
        "content_type": raw.content_type.value if raw.content_type else "unknown",
        "published_at": str(raw.published_at) if raw.published_at else None,
        "scraped_at": str(raw.scraped_at),
    }


@router.get("/today/summary")
async def get_today_summary(
    session: AsyncSession = Depends(get_session),
):
    """获取今日投资建议汇总。"""
    from stockagent.analysis.summarizer import get_daily_summary

    return await get_daily_summary()
