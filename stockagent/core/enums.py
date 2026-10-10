"""共享枚举定义。"""

from enum import Enum


class Direction(str, Enum):
    """投资方向。"""

    BULLISH = "bullish"  # 看多
    BEARISH = "bearish"  # 看空
    NEUTRAL = "neutral"  # 观望


class TimeHorizon(str, Enum):
    """投资时间维度。"""

    NEXT_DAY = "next_day"  # 下一日
    NEXT_WEEK = "next_week"  # 下一周
    NEXT_PHASE = "next_phase"  # 下一阶段
    UNKNOWN = "unknown"


class ConfidenceLevel(str, Enum):
    """置信度。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ContentType(str, Enum):
    """内容类型。"""

    VIDEO = "video"
    ARTICLE = "article"
    POST = "post"
    IMAGE_TEXT = "image_text"  # 图文
    UNKNOWN = "unknown"


class AnalysisStatus(str, Enum):
    """分析状态。"""

    PENDING = "pending"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    FAILED = "failed"


class MonitorStatus(str, Enum):
    """监控状态。"""

    PENDING = "pending"  # 等待监控
    MONITORING = "monitoring"  # 监控中
    TRIGGERED = "triggered"  # 条件已触发
    EXPIRED = "expired"  # 条件过期（未触发）
    REJECTED = "rejected"  # 用户拒绝
    CANCELLED = "cancelled"  # 已取消


class SignalStatus(str, Enum):
    """交易信号状态。"""

    PENDING = "pending_user_confirm"  # 等待用户确认
    CONFIRMED = "confirmed"  # 用户确认
    REJECTED = "rejected"  # 用户拒绝
    EXPIRED = "expired"  # 超时未确认


class TradeAction(str, Enum):
    """交易动作。"""

    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class NotificationType(str, Enum):
    """通知类型。"""

    DAILY_ADVICE = "daily_advice"
    SIGNAL_ALERT = "signal_alert"
    TRADE_CONFIRM = "trade_confirm"
    PNL_REPORT = "pnl_report"
    TEST = "test"


class FilterStatus(str, Enum):
    """内容过滤状态。"""

    PENDING = "pending"  # 待过滤
    USEFUL = "useful"  # 有价值
    FILTERED = "filtered"  # 已过滤（无价值）


class ContentCategory(str, Enum):
    """内容分类（过滤结果）。"""

    PREDICTION = "prediction"  # 条件预测（回踩XX线企稳就买）
    RECOMMENDATION = "recommendation"  # 明确推荐（直接买入XX）
    ANALYSIS = "analysis"  # 市场分析（只有分析，无操作）
    NON_INVESTMENT = "non_investment"  # 非投资内容（吐槽、娱乐、广告）
