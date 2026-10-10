"""可执行的市场条件规则。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class AbstractRule(ABC):
    """条件规则基类。"""

    @abstractmethod
    async def evaluate(self, market_data: dict[str, Any]) -> bool:
        """评估规则是否触发。"""
        ...

    @abstractmethod
    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（存储到 DB）。"""
        ...

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "AbstractRule":
        """从字典反序列化。"""
        rule_type = data.get("condition_type", "")
        cls = _RULE_MAP.get(rule_type, GenericRule)
        return cls(
            condition_type=rule_type,
            operator=data.get("operator", "eq"),
            value=float(data.get("value", 0)),
            unit=data.get("unit", ""),
            description=data.get("description", ""),
        )


@dataclass
class PriceChangeRule(AbstractRule):
    """价格涨跌幅规则。"""

    condition_type: str = "price_drop_pct"
    operator: str = "lt"
    value: float = 0.0
    unit: str = "%"
    description: str = ""

    async def evaluate(self, market_data: dict[str, Any]) -> bool:
        current_price = market_data.get("current_price", 0)
        prev_close = market_data.get("prev_close", 0)

        if prev_close <= 0:
            return False

        # 绝对价位比较（"跌破XX元"）
        if self.unit == "元":
            if self.condition_type == "price_drop_pct":
                if self.operator in ("lt", "lte"):
                    return (
                        current_price <= self.value
                        if self.operator == "lte"
                        else current_price < self.value
                    )
                else:
                    return (
                        current_price >= self.value
                        if self.operator == "gte"
                        else current_price > self.value
                    )
            else:
                if self.operator in ("gt", "gte"):
                    return (
                        current_price >= self.value
                        if self.operator == "gte"
                        else current_price > self.value
                    )
                else:
                    return (
                        current_price <= self.value
                        if self.operator == "lte"
                        else current_price < self.value
                    )

        # 百分比比较
        change_pct = (current_price - prev_close) / prev_close * 100
        return _compare(change_pct, self.operator, self.value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_type": self.condition_type,
            "operator": self.operator,
            "value": self.value,
            "unit": self.unit,
            "description": self.description,
        }


@dataclass
class VolumeSurgeRule(AbstractRule):
    """成交量放大规则。"""

    condition_type: str = "volume_surge"
    operator: str = "gt"
    value: float = 2.0
    unit: str = "倍"
    description: str = ""

    async def evaluate(self, market_data: dict[str, Any]) -> bool:
        # 优先用预计算的 technical_indicators
        if "volume_surge" in market_data:
            return market_data["volume_surge"]

        current_volume = market_data.get("volume", 0)
        avg_volume = market_data.get("avg_volume_20d", 0)
        if avg_volume <= 0:
            return False
        ratio = current_volume / avg_volume
        return _compare(ratio, self.operator, self.value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_type": self.condition_type,
            "operator": self.operator,
            "value": self.value,
            "unit": self.unit,
            "description": self.description,
        }


@dataclass
class VolumeShrinkRule(AbstractRule):
    """成交量缩量规则。"""

    condition_type: str = "volume_shrink"
    operator: str = "lt"
    value: float = 0.5
    unit: str = "倍"
    description: str = ""

    async def evaluate(self, market_data: dict[str, Any]) -> bool:
        current_volume = market_data.get("volume", 0)
        avg_volume = market_data.get("avg_volume_20d", 0)
        if avg_volume <= 0:
            return False
        ratio = current_volume / avg_volume
        return _compare(ratio, self.operator, self.value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_type": self.condition_type,
            "operator": self.operator,
            "value": self.value,
            "unit": self.unit,
            "description": self.description,
        }


@dataclass
class IndexLevelRule(AbstractRule):
    """指数点位规则。"""

    condition_type: str = "index_below"
    operator: str = "lt"
    value: float = 0.0
    unit: str = "点"
    description: str = ""

    async def evaluate(self, market_data: dict[str, Any]) -> bool:
        index_point = market_data.get("index_point", 0)
        return _compare(index_point, self.operator, self.value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_type": self.condition_type,
            "operator": self.operator,
            "value": self.value,
            "unit": self.unit,
            "description": self.description,
        }


@dataclass
class SupportBounceRule(AbstractRule):
    """支撑反弹规则 —— 股价回踩支撑位附近且企稳。

    博主原话如："回踩5日线企稳"、"回踩支撑位有承接"
    判断逻辑：当前价在 MA(val) 附近 ±3%，且最近3天低点不再创新低（企稳）
    """

    condition_type: str = "support_bounce"
    operator: str = "eq"
    value: float = 5.0  # 均线天数，如 5日线
    unit: str = "日线"
    description: str = ""

    async def evaluate(self, market_data: dict[str, Any]) -> bool:
        current_price = market_data.get("current_price", 0)
        is_stable = market_data.get("is_stable", False)
        if current_price <= 0:
            return False

        # 参考回踩位:
        #   value>0(如5/10/20) → 对应均线 MA5/MA10/MA20;
        #   value=0 (LLM 抽取常给 0，未指定具体均线) → 近20日支撑位(support);
        #   都拿不到 → 兜底用 MA5(最常见回踩位)。
        # 说明: 此前 value=0 分支被前面的 "ma_val<=0 return False" 挡成死代码,
        #       导致 support_bounce 恒不触发(trade_signals 长期=0)，此处修复。
        period = int(self.value)
        ref = market_data.get(f"ma{period}", 0) if period > 0 else market_data.get("support", 0)
        if ref <= 0:
            ref = market_data.get("ma5", 0)
        if ref <= 0:
            return False

        # 回踩低吸: 只在参考位下方~附近买回踩低点, 绝不追反弹到上方>1%。
        # 带符号偏离: 负=下方(回踩到位), 正=上方(已反弹, 越高越贵)。
        # 2026全市场回测(连板≥4): 买在MA=72.3%/+1.82%, 买高2%只剩62%/+0.58%
        # ——对称±3%等于系统性追反弹买贵。上沿收到+1%, 宁踏空不追高。
        dev_pct = (current_price - ref) / ref * 100
        near = -3.0 <= dev_pct <= 1.0

        return near and is_stable

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_type": self.condition_type,
            "operator": self.operator,
            "value": self.value,
            "unit": self.unit,
            "description": self.description,
        }


@dataclass
class MAResistanceRule(AbstractRule):
    """均线压力/支撑规则 —— 股价站上/跌破某条均线。

    博主原话如："站上5日线"、"跌破20日线就走"
    """

    condition_type: str = "ma_cross"
    operator: str = "gt"  # gt=站上, lt=跌破
    value: float = 5.0  # 均线天数
    unit: str = "日线"
    description: str = ""

    async def evaluate(self, market_data: dict[str, Any]) -> bool:
        ma_key = f"ma{int(self.value)}"
        ma_val = market_data.get(ma_key, 0)
        current_price = market_data.get("current_price", 0)

        if ma_val <= 0 or current_price <= 0:
            return False

        if not _compare(current_price, self.operator, ma_val):
            return False

        # 描述含"企稳"时（如"5日线上分时企稳"），额外要求分时企稳确认
        # 避免全天在均线上方就持续触发
        if "企稳" in (self.description or ""):
            return market_data.get("is_stable", False)
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_type": self.condition_type,
            "operator": self.operator,
            "value": self.value,
            "unit": self.unit,
            "description": self.description,
        }


@dataclass
class ResistanceBreakRule(AbstractRule):
    """突破压力位规则 —— 股价突破近 N 日最高价。

    博主原话如："放量突破前高"、"突破压力位"
    """

    condition_type: str = "resistance_break"
    operator: str = "gt"
    value: float = 20.0  # 回溯天数
    unit: str = "日线"
    description: str = ""

    async def evaluate(self, market_data: dict[str, Any]) -> bool:
        resistance = market_data.get("resistance", 0)
        current_price = market_data.get("current_price", 0)
        volume_surge = market_data.get("volume_surge", False)

        if resistance <= 0 or current_price <= 0:
            return False

        # 突破压力位 + 放量
        broke = current_price > resistance
        return broke and volume_surge

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_type": self.condition_type,
            "operator": self.operator,
            "value": self.value,
            "unit": self.unit,
            "description": self.description,
        }


@dataclass
class TrendWeakeningRule(AbstractRule):
    """趋势转弱规则 —— 股价走弱，博主说"不转强就走"、"破位止损"。

    博主原话如："明天不转强无论如何也要走了"、"破位止损"
    判断逻辑：价格跌破 MA(value) 且分时未企稳（趋势+动量双弱）
    """

    condition_type: str = "trend_weakening"
    operator: str = "lt"
    value: float = 5.0  # 均线天数，默认MA5
    unit: str = "日线"
    description: str = ""

    async def evaluate(self, market_data: dict[str, Any]) -> bool:
        ma_key = f"ma{int(self.value)}"
        ma_val = market_data.get(ma_key, 0)
        current_price = market_data.get("current_price", 0)
        is_stable = market_data.get("is_stable", False)

        if ma_val <= 0 or current_price <= 0:
            return False

        # 价格在均线之下 → 趋势走弱
        below_ma = current_price < ma_val
        # 分时未企稳 → 无承接
        not_stable = not is_stable

        return below_ma and not_stable

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_type": self.condition_type,
            "operator": self.operator,
            "value": self.value,
            "unit": self.unit,
            "description": self.description,
        }


@dataclass
class GenericRule(AbstractRule):
    """通用规则（兜底，无法自动判断的条件不触发）。"""

    condition_type: str = "other"
    operator: str = "eq"
    value: float = 0.0
    unit: str = ""
    description: str = ""

    async def evaluate(self, market_data: dict[str, Any]) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_type": self.condition_type,
            "operator": self.operator,
            "value": self.value,
            "unit": self.unit,
            "description": self.description,
        }


# ---- 工具函数 ----


def _compare(a: float, op: str, b: float) -> bool:
    """通用比较。"""
    if op == "lt":
        return a < b
    elif op == "lte":
        return a <= b
    elif op == "gt":
        return a > b
    elif op == "gte":
        return a >= b
    elif op == "eq":
        return abs(a - b) < 0.001
    return False


# ---- 规则注册表 ----

_RULE_MAP: dict[str, type[AbstractRule]] = {
    "price_drop_pct": PriceChangeRule,
    "price_rise_pct": PriceChangeRule,
    "volume_surge": VolumeSurgeRule,
    "volume_shrink": VolumeShrinkRule,
    "index_below": IndexLevelRule,
    "index_above": IndexLevelRule,
    "support_bounce": SupportBounceRule,
    "ma_cross": MAResistanceRule,
    "resistance_break": ResistanceBreakRule,
    "trend_weakening": TrendWeakeningRule,
}
