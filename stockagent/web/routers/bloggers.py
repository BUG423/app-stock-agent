"""博主管理 API 路由。"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from stockagent.core.db import get_session
from stockagent.core.models import Blogger
from stockagent.core.schemas import (
    BloggerCreate,
    BloggerResponse,
    BloggerUpdate,
    PaginatedResponse,
)
from stockagent.scrapers.registry import scraper_registry

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=PaginatedResponse)
async def list_bloggers(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    enabled_only: bool = Query(False),
    session: AsyncSession = Depends(get_session),
):
    """获取博主列表。"""
    query = select(Blogger)
    count_query = select(func.count(Blogger.id))

    if enabled_only:
        query = query.where(Blogger.enabled.is_(True))
        count_query = count_query.where(Blogger.enabled.is_(True))

    # 分页
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size).order_by(Blogger.created_at.desc())

    result = await session.execute(query)
    bloggers = result.scalars().all()

    count_result = await session.execute(count_query)
    total = count_result.scalar() or 0

    return PaginatedResponse(
        items=[BloggerResponse.model_validate(b) for b in bloggers],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if total > 0 else 1,
    )


@router.post("", response_model=BloggerResponse)
async def create_blogger(
    data: BloggerCreate,
    session: AsyncSession = Depends(get_session),
):
    """添加新博主。"""
    blogger = Blogger(
        name=data.name,
        platform=data.platform,
        platform_user_id=data.platform_user_id,
        profile_url=data.profile_url,
        enabled=data.enabled,
        scrape_frequency=data.scrape_frequency,
        notes=data.notes,
    )
    session.add(blogger)
    await session.commit()
    await session.refresh(blogger)
    logger.info(f"已添加博主: {blogger.name} ({blogger.platform})")
    return BloggerResponse.model_validate(blogger)


@router.get("/{blogger_id}", response_model=BloggerResponse)
async def get_blogger(
    blogger_id: str,
    session: AsyncSession = Depends(get_session),
):
    """获取博主详情。"""
    blogger = await session.get(Blogger, blogger_id)
    if not blogger:
        raise HTTPException(status_code=404, detail="博主不存在")
    return BloggerResponse.model_validate(blogger)


@router.put("/{blogger_id}", response_model=BloggerResponse)
async def update_blogger(
    blogger_id: str,
    data: BloggerUpdate,
    session: AsyncSession = Depends(get_session),
):
    """更新博主配置。"""
    blogger = await session.get(Blogger, blogger_id)
    if not blogger:
        raise HTTPException(status_code=404, detail="博主不存在")

    if data.name is not None:
        blogger.name = data.name
    if data.enabled is not None:
        blogger.enabled = data.enabled
    if data.scrape_frequency is not None:
        blogger.scrape_frequency = data.scrape_frequency
    if data.notes is not None:
        blogger.notes = data.notes

    await session.commit()
    await session.refresh(blogger)
    return BloggerResponse.model_validate(blogger)


@router.delete("/{blogger_id}")
async def delete_blogger(
    blogger_id: str,
    session: AsyncSession = Depends(get_session),
):
    """删除博主（软删除）。"""
    blogger = await session.get(Blogger, blogger_id)
    if not blogger:
        raise HTTPException(status_code=404, detail="博主不存在")
    blogger.enabled = False
    await session.commit()
    return {"success": True, "message": f"博主 '{blogger.name}' 已禁用"}


@router.post("/{blogger_id}/scrape")
async def trigger_scrape_blogger(
    blogger_id: str,
    session: AsyncSession = Depends(get_session),
):
    """手动触发抓取单个博主。"""
    blogger = await session.get(Blogger, blogger_id)
    if not blogger:
        raise HTTPException(status_code=404, detail="博主不存在")

    scraper_registry.discover_plugins()
    scraper = scraper_registry.get(blogger.platform)
    if not scraper:
        raise HTTPException(status_code=400, detail=f"不支持的平台: {blogger.platform}")

    contents = await scraper.scrape_user_posts(
        user_id=blogger.platform_user_id,
        max_items=10,
    )

    return {"success": True, "items_scraped": len(contents), "blogger": blogger.name}


@router.get("/{blogger_id}/stats")
async def get_blogger_stats(
    blogger_id: str,
    session: AsyncSession = Depends(get_session),
):
    """获取博主表现统计（盈亏度量层已下线，返回占位）。"""
    return {"blogger_id": blogger_id, "message": "盈亏统计已下线"}
