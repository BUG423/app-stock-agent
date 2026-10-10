"""SQLAlchemy ORM 模型定义。"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from stockagent.core.db import Base
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
from stockagent.utils.time_utils import (
    now_cst,  # A股必须北京时间: 时间戳默认值用北京, 不用SQLite的UTC func.now()
)


def _uuid_pk() -> str:
    return uuid.uuid4().hex[:12]


class Blogger(Base):
    """博主配置表。"""

    __tablename__ = "bloggers"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_uuid_pk)
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="博主名称")
    platform: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="平台标识: bilibili, weibo, wechat"
    )
    platform_user_id: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="平台用户ID / UID"
    )
    profile_url: Mapped[str] = mapped_column(String(512), nullable=False, comment="博主主页URL")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")
    process_mode: Mapped[str] = mapped_column(
        String(16),
        default="advice",
        comment="内容处理模式（兼容字段，当前只使用 advice）",
    )
    scrape_frequency: Mapped[str] = mapped_column(String(32), default="daily", comment="抓取频率")
    last_scraped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="上次抓取时间"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_cst)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_cst, onupdate=now_cst
    )

    # 关联
    raw_contents: Mapped[list["RawContent"]] = relationship(
        back_populates="blogger", lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("platform", "platform_user_id", name="uq_blogger_platform_user"),
    )


class RawContent(Base):
    """抓取的原始内容表。"""

    __tablename__ = "raw_contents"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_uuid_pk)
    blogger_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("bloggers.id", ondelete="CASCADE"), nullable=False
    )
    content_id: Mapped[str] = mapped_column(String(128), nullable=False, comment="平台原生内容ID")
    title: Mapped[str] = mapped_column(String(512), nullable=False, comment="标题")
    text: Mapped[str] = mapped_column(Text, nullable=False, comment="提取的文本内容")
    url: Mapped[str] = mapped_column(String(1024), nullable=False, comment="内容URL")
    content_type: Mapped[ContentType] = mapped_column(
        Enum(ContentType), default=ContentType.UNKNOWN, comment="内容类型"
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="平台发布时间"
    )
    extra_data: Mapped[dict | None] = mapped_column(
        "metadata", JSON, nullable=True, comment="平台原始元数据"
    )
    analysis_status: Mapped[AnalysisStatus] = mapped_column(
        Enum(AnalysisStatus),
        default=AnalysisStatus.PENDING,
        comment="分析状态",
    )
    filter_status: Mapped[str] = mapped_column(
        String(16),
        default="pending",
        comment="过滤状态: pending/useful/filtered",
    )
    filter_reason: Mapped[str | None] = mapped_column(Text, nullable=True, comment="过滤理由")
    filter_category: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="内容分类"
    )
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_cst, comment="抓取时间"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_cst)

    # 关联
    blogger: Mapped["Blogger"] = relationship(back_populates="raw_contents")
    analysis_result: Mapped["AnalysisResult | None"] = relationship(
        back_populates="raw_content", uselist=False, lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("blogger_id", "content_id", name="uq_raw_content_blogger_content"),
    )


class AnalysisResult(Base):
    """LLM 分析结果表。"""

    __tablename__ = "analysis_results"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_uuid_pk)
    raw_content_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("raw_contents.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    blogger_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("bloggers.id", ondelete="CASCADE"), nullable=False
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False, comment="投资建议摘要")
    stock_mentions: Mapped[list[dict]] = mapped_column(
        JSON, nullable=False, default=list, comment="提及的股票列表"
    )
    conditions: Mapped[list[dict]] = mapped_column(
        JSON, nullable=False, default=list, comment="触发条件列表"
    )
    time_horizon: Mapped[TimeHorizon] = mapped_column(
        Enum(TimeHorizon), default=TimeHorizon.NEXT_DAY, comment="时间维度"
    )
    confidence: Mapped[ConfidenceLevel] = mapped_column(
        Enum(ConfidenceLevel), default=ConfidenceLevel.MEDIUM, comment="置信度"
    )
    market_sentiment: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="市场情绪: bullish/bearish/neutral"
    )
    direction: Mapped[Direction] = mapped_column(
        Enum(Direction), default=Direction.NEUTRAL, comment="整体方向"
    )
    llm_model: Mapped[str] = mapped_column(String(64), nullable=False, comment="使用的LLM模型")
    llm_provider: Mapped[str] = mapped_column(String(64), nullable=False, comment="LLM提供商")
    raw_llm_output: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="LLM原始输出（审计用）"
    )
    token_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="Token用量")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_cst)

    # 关联
    raw_content: Mapped["RawContent"] = relationship(back_populates="analysis_result")
    monitored_stocks: Mapped[list["MonitoredStock"]] = relationship(
        back_populates="analysis_result", lazy="selectin"
    )


class MonitoredStock(Base):
    """待监控股票表。"""

    __tablename__ = "monitored_stocks"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_uuid_pk)
    analysis_result_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("analysis_results.id", ondelete="CASCADE"), nullable=False
    )
    stock_code: Mapped[str] = mapped_column(String(16), nullable=False, comment="股票代码")
    stock_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="股票名称")
    direction: Mapped[Direction] = mapped_column(
        Enum(Direction), default=Direction.NEUTRAL, comment="建议方向"
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True, comment="博主推荐理由")
    signal_type: Mapped[str] = mapped_column(
        String(16),
        default="conditional",
        comment="信号类型: follow_now(博主已建仓/直接推荐→立即可跟单) / "
        "conditional(满足条件才操作→盯盘) / exited(博主已清仓→可跟卖出)",
    )
    trigger_conditions: Mapped[list[dict]] = mapped_column(
        JSON, nullable=False, default=list, comment="序列化的触发条件"
    )
    monitor_date: Mapped[date] = mapped_column(Date, nullable=False, comment="监控日期")
    status: Mapped[MonitorStatus] = mapped_column(
        Enum(MonitorStatus), default=MonitorStatus.PENDING, comment="监控状态"
    )
    # AI 复核官（二次筛选）结果
    review_score: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="AI复核靠谱度评分 1-5"
    )
    review_stance: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="AI复核立场: support/neutral/against"
    )
    review_note: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="AI复核一句话结论+风险"
    )
    triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="触发时间"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_cst)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_cst, onupdate=now_cst
    )

    # 关联
    analysis_result: Mapped["AnalysisResult"] = relationship(back_populates="monitored_stocks")
    trade_signal: Mapped["TradeSignal | None"] = relationship(
        back_populates="monitored_stock", uselist=False, lazy="selectin"
    )
    review: Mapped["StockReview | None"] = relationship(
        back_populates="monitored_stock", uselist=False, lazy="selectin"
    )


class TradeSignal(Base):
    """交易信号表。"""

    __tablename__ = "trade_signals"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_uuid_pk)
    monitored_stock_id: Mapped[str] = mapped_column(
        String(12),
        ForeignKey("monitored_stocks.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    action: Mapped[TradeAction] = mapped_column(
        Enum(TradeAction), nullable=False, comment="建议动作"
    )
    stock_code: Mapped[str] = mapped_column(String(16), nullable=False)
    stock_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    suggested_price_min: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="建议价格下限"
    )
    suggested_price_max: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="建议价格上限"
    )
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True, comment="触发时市价")
    conditions_met: Mapped[list[dict]] = mapped_column(
        JSON, nullable=False, default=list, comment="已满足的条件"
    )
    status: Mapped[SignalStatus] = mapped_column(
        Enum(SignalStatus), default=SignalStatus.PENDING, comment="信号状态"
    )
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_cst, comment="触发时间"
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="确认时间"
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="过期时间"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_cst)

    # 关联
    monitored_stock: Mapped["MonitoredStock"] = relationship(back_populates="trade_signal")
    user_action: Mapped["UserAction | None"] = relationship(
        back_populates="trade_signal", uselist=False, lazy="selectin"
    )


class UserAction(Base):
    """用户操作记录表。"""

    __tablename__ = "user_actions"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_uuid_pk)
    trade_signal_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("trade_signals.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    user_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, comment="用户是否确认")
    actual_action: Mapped[TradeAction | None] = mapped_column(
        Enum(TradeAction), nullable=True, comment="用户实际操作"
    )
    actual_price: Mapped[float | None] = mapped_column(Float, nullable=True, comment="实际成交价格")
    actual_quantity: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="实际成交数量"
    )
    feedback_text: Mapped[str | None] = mapped_column(Text, nullable=True, comment="用户反馈文本")
    feedback_rating: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="反馈评分 1-5"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_cst)

    # 关联
    trade_signal: Mapped["TradeSignal"] = relationship(back_populates="user_action")


class Notification(Base):
    """通知记录表。"""

    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_uuid_pk)
    type: Mapped[NotificationType] = mapped_column(
        Enum(NotificationType), nullable=False, comment="通知类型"
    )
    recipient: Mapped[str] = mapped_column(String(256), nullable=False, comment="收件人")
    subject: Mapped[str] = mapped_column(String(512), nullable=False, comment="邮件主题")
    body: Mapped[str] = mapped_column(Text, nullable=False, comment="HTML正文")
    related_entity_type: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="关联实体类型"
    )
    related_entity_id: Mapped[str | None] = mapped_column(
        String(12), nullable=True, comment="关联实体ID"
    )
    # 同一原始内容只允许对应一条通知。其他通知类型保持为空，避免误伤可重复的交易信号。
    dedupe_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    sent: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否已发送")
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="发送时间"
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True, comment="发送失败原因")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, comment="发送尝试次数")
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_letter: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否进入死信")
    # 发送方领取邮件后的短租约，防止即时发送和定时补发并发投递同一封邮件。
    delivery_lock_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_cst)

class StockReview(Base):
    """AI 复核官（二次筛选）结果 —— 用真实历史数据独立复核博主观点是否靠谱。"""

    __tablename__ = "stock_reviews"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_uuid_pk)
    monitored_stock_id: Mapped[str | None] = mapped_column(
        String(12),
        ForeignKey("monitored_stocks.id", ondelete="CASCADE"),
        nullable=True,
        unique=True,
    )
    analysis_result_id: Mapped[str | None] = mapped_column(
        String(12), ForeignKey("analysis_results.id", ondelete="SET NULL"), nullable=True
    )
    stock_code: Mapped[str] = mapped_column(String(16), nullable=False)
    stock_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    blogger_view: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="被复核的博主观点/理由"
    )
    score: Mapped[int] = mapped_column(Integer, default=3, comment="靠谱度评分 1-5（5=数据强支撑）")
    stance: Mapped[str] = mapped_column(
        String(16), default="neutral", comment="立场: support/neutral/against"
    )
    verdict: Mapped[str] = mapped_column(Text, default="", comment="一句话复核结论")
    risk: Mapped[str | None] = mapped_column(Text, nullable=True, comment="主要风险点")
    tech_snapshot: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="复核时的技术面快照"
    )
    reasoning: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="AI复核推理（审计用）"
    )
    llm_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    llm_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_cst)

    # 关联
    monitored_stock: Mapped["MonitoredStock | None"] = relationship(back_populates="review")
