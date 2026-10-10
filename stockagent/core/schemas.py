"""Pydantic schemas —— API 输入输出验证和内部数据传输。"""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from stockagent.core.enums import (
    AnalysisStatus,
    ConfidenceLevel,
    ContentType,
    Direction,
    MonitorStatus,
    NotificationType,
    SignalStatus,
    TimeHorizon,
    TradeAction,
)

# ============================================================
# Blogger
# ============================================================


class BloggerCreate(BaseModel):
    name: str = Field(..., max_length=128, description="博主名称")
    platform: str = Field(..., max_length=32, description="平台标识")
    platform_user_id: str = Field(..., max_length=128, description="平台用户ID")
    profile_url: str = Field(..., max_length=512, description="博主主页URL")
    enabled: bool = Field(default=True)
    scrape_frequency: str = Field(default="daily", max_length=32)
    notes: str | None = Field(default=None)


class BloggerUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    enabled: bool | None = None
    scrape_frequency: str | None = Field(default=None, max_length=32)
    notes: str | None = None


class BloggerResponse(BaseModel):
    id: str
    name: str
    platform: str
    platform_user_id: str
    profile_url: str
    enabled: bool
    scrape_frequency: str
    last_scraped_at: datetime | None
    notes: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ============================================================
# RawContent
# ============================================================


class RawContentResponse(BaseModel):
    id: str
    blogger_id: str
    content_id: str
    title: str
    text: str
    url: str
    content_type: ContentType
    published_at: datetime | None
    extra_data: dict[str, Any] | None
    analysis_status: AnalysisStatus
    scraped_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


# ============================================================
# AnalysisResult
# ============================================================


class StockMention(BaseModel):
    """提及的股票。"""

    code: str = Field(..., description="股票代码，如 600519")
    name: str | None = Field(default=None, description="股票名称")
    source_name: str | None = Field(
        default=None,
        description="原文中出现的股票称谓/缩写，用于审计和别名校验",
    )
    direction: Direction = Field(default=Direction.NEUTRAL)
    reason: str | None = Field(default=None, description="推荐理由")
    signal_type: str = Field(
        default="conditional",
        description="信号类型: follow_now(博主已建仓/直接推荐→立即可跟单) / "
        "conditional(满足条件才操作→盯盘) / exited(博主已清仓→可跟卖出)",
    )
    conditions: list[dict[str, Any]] = Field(
        default_factory=list, description="该股票专属的触发条件"
    )


class ConditionClause(BaseModel):
    """触发条件子句。"""

    condition_type: str = Field(
        ..., description="条件类型: price_drop_pct, volume_surge, index_level"
    )
    operator: str = Field(default="lt", description="比较符: lt, gt, eq, gte, lte")
    value: float = Field(..., description="阈值")
    unit: str | None = Field(default=None, description="单位: %, 元, 手")
    description: str = Field(..., description="原始自然语言描述")
    plain_description: str | None = Field(default=None, description="口语化解释")


class AnalysisResultResponse(BaseModel):
    id: str
    raw_content_id: str
    blogger_id: str
    summary: str
    stock_mentions: list[dict[str, Any]]
    conditions: list[dict[str, Any]]
    time_horizon: TimeHorizon
    confidence: ConfidenceLevel
    market_sentiment: str | None
    direction: Direction
    llm_model: str
    llm_provider: str
    raw_llm_output: str | None
    token_usage: dict[str, Any] | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AnalysisResultDetail(AnalysisResultResponse):
    """包含关联的原始内容和监控股票。"""

    raw_content: RawContentResponse | None = None
    monitored_stocks: list["MonitoredStockResponse"] = []


# ============================================================
# MonitoredStock
# ============================================================


class MonitoredStockResponse(BaseModel):
    id: str
    analysis_result_id: str
    stock_code: str
    stock_name: str | None
    direction: Direction
    reason: str | None
    signal_type: str = "conditional"
    trigger_conditions: list[dict[str, Any]]
    monitor_date: date
    status: MonitorStatus
    review_score: int | None = None
    review_stance: str | None = None
    review_note: str | None = None
    triggered_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ============================================================
# TradeSignal
# ============================================================


class TradeSignalResponse(BaseModel):
    id: str
    monitored_stock_id: str
    action: TradeAction
    stock_code: str
    stock_name: str | None
    suggested_price_min: float | None
    suggested_price_max: float | None
    current_price: float | None
    conditions_met: list[dict[str, Any]]
    status: SignalStatus
    triggered_at: datetime
    confirmed_at: datetime | None
    expires_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TradeSignalDetail(TradeSignalResponse):
    """包含用户反馈记录。"""

    user_action: "UserActionResponse | None" = None


# ============================================================
# UserAction
# ============================================================


class UserActionConfirm(BaseModel):
    """用户确认/拒绝请求。"""

    signal_id: str = Field(..., description="交易信号ID")
    confirmed: bool = Field(..., description="是否确认")
    actual_action: TradeAction | None = Field(default=None, description="实际操作")
    actual_price: float | None = Field(default=None, description="实际成交价")
    actual_quantity: int | None = Field(default=None, description="实际数量")
    feedback_text: str | None = Field(default=None)
    feedback_rating: int | None = Field(default=None, ge=1, le=5)


class UserActionResponse(BaseModel):
    id: str
    trade_signal_id: str
    user_confirmed: bool
    actual_action: TradeAction | None
    actual_price: float | None
    actual_quantity: int | None
    feedback_text: str | None
    feedback_rating: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ============================================================
# Notification
# ============================================================


class NotificationResponse(BaseModel):
    id: str
    type: NotificationType
    recipient: str
    subject: str
    body: str
    related_entity_type: str | None
    related_entity_id: str | None
    sent: bool
    sent_at: datetime | None
    error_message: str | None
    attempt_count: int = 0
    next_retry_at: datetime | None = None
    dead_letter: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class TestEmailRequest(BaseModel):
    """测试邮件请求。"""

    recipient: str = Field(..., description="接收邮箱地址")


# ============================================================
# 手动触发
# ============================================================


class TriggerResponse(BaseModel):
    success: bool
    message: str
    details: dict[str, Any] | None = None


# ============================================================
# Pagination
# ============================================================


class PaginatedResponse(BaseModel):
    """分页响应包装。"""

    items: list[Any]
    total: int
    page: int
    page_size: int
    total_pages: int
