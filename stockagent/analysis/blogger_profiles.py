"""真人博主的稳定表达习惯与提醒纪律。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class BloggerProfile:
    key: str
    user_ids: tuple[str, ...]
    names: tuple[str, ...]
    default_signal_type: str
    email_title: str
    email_rule: str
    prompt_rule: str
    requires_verified_stock: bool = False


DEFAULT_PROFILE = BloggerProfile(
    key="default",
    user_ids=(),
    names=(),
    default_signal_type="conditional",
    email_title="本次提醒纪律",
    email_rule="严格按本条原文判断；条件不明确时只转发内容，不生成买入触发。",
    prompt_rule="忠实提取本条内容；没有明确买入条件时不要自行补充。",
)

BLOGGER_PROFILES = (
    BloggerProfile(
        key="early_retirement",
        user_ids=("3707010912618582",),
        names=("提前炒股早退休", "提前早退休"),
        default_signal_type="follow_now",
        email_title="提前早退休 · 直接购入型",
        email_rule=(
            "该博主通常用实盘持仓画面表达直接购入/继续持有，不设置回踩等前置条件。"
            "只有股票名称、代码或价格证据被交叉确认后才显示直接购入倾向；系统只提醒，不自动下单。"
        ),
        prompt_rule=(
            "该博主通常以实盘持仓截图表达直接购入或持有。标的被可靠确认且未表达卖出时，"
            "signal_type 使用 follow_now、conditions 为空；标的不能确认时 stock_mentions 必须为空，禁止猜测。"
        ),
        requires_verified_stock=True,
    ),
    BloggerProfile(
        key="tiaotiaohu",
        user_ids=("21119247",),
        names=("跳跳舞复盘", "跳跳虎复盘", "跳跳虎"),
        default_signal_type="conditional",
        email_title="跳跳舞 · 条件触发型",
        email_rule=(
            "必须先满足本条原文条件，常见纪律是回踩指定均线/支撑位后分时企稳、有承接，"
            "且不能持续跌破分时均价线；条件未满足时不追。具体均线和价位以本条原文为准。"
        ),
        prompt_rule=(
            "该博主通常给出购入前置条件。逐只提取回踩均线、支撑位、分时企稳/承接、低开等条件；"
            "有前置条件时 signal_type 必须为 conditional，不能改成直接买入。"
        ),
    ),
    BloggerProfile(
        key="yaoban",
        user_ids=("3546957274417333",),
        names=("妖板选手", "腰板选手"),
        default_signal_type="conditional",
        email_title="妖板选手 · 条件触发型",
        email_rule=(
            "按本条给出的低吸、回档、转强、量价或指数条件等待触发，并观察分时承接；"
            "条件缺失或只是在复述既往盈利时，不生成买入提醒。具体阈值以本条原文为准。"
        ),
        prompt_rule=(
            "该博主的候选通常附带低吸、回档、转强、量价或指数条件。每只股票分别保留自己的条件，"
            "条件存在时 signal_type 使用 conditional；纯获利回顾不得作为新买入信号。"
        ),
    ),
)


def get_blogger_profile(name: str = "", user_id: str = "") -> BloggerProfile:
    normalized_name = (name or "").strip()
    normalized_uid = str(user_id or "").strip()
    for profile in BLOGGER_PROFILES:
        if normalized_uid and normalized_uid in profile.user_ids:
            return profile
        if normalized_name and any(alias in normalized_name for alias in profile.names):
            return profile
    return DEFAULT_PROFILE


def verified_direct_mention(profile: BloggerProfile, metadata: dict | None) -> dict | None:
    """把高置信度外部核验结果转换成直接提醒标的。"""
    if profile.default_signal_type != "follow_now":
        return None
    verified = (metadata or {}).get("verified_stock") or {}
    if verified.get("confidence") != "high":
        return None
    code = str(verified.get("code") or "")
    name = str(verified.get("name") or "").strip()
    if not (code.isdigit() and len(code) == 6 and name):
        return None
    return {
        "code": code,
        "name": name,
        "direction": "bullish",
        "signal_type": "follow_now",
        "reason": "实盘持仓画面经名称与价格多证据确认",
        "conditions": [],
        "resolution_confidence": "high",
        "resolution_evidence": verified.get("evidence") or [],
    }
