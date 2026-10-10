"""博主缩写股票名的高置信度映射回归测试。"""

from stockagent.analysis.pipeline import (
    _apply_exit_intent_direction,
    _apply_stock_alias_hints,
    _stock_alias_hints_for_text,
)
from stockagent.core.config import config


def test_known_mixed_initial_aliases_override_model_misidentification():
    if not config._loaded:
        config.load()

    text = "亚联jx、依依gf、zg软件：回踩5日，分时企稳"
    hints = _stock_alias_hints_for_text(text)
    result = _apply_stock_alias_hints(
        [
            {
                "source_name": "亚联jx",
                "name": "亚联发展",
                "code": "002316",
                "conditions": [],
            },
            {
                "source_name": "依依gf",
                "name": "依依股份",
                "code": "001206",
                "conditions": [],
            },
            {
                "source_name": "zg软件",
                "name": "中软国际",
                "code": "unknown",
                "conditions": [],
            },
        ],
        hints,
    )

    assert [(item["name"], item["code"]) for item in result] == [
        ("亚联机械", "001395"),
        ("依依股份", "001206"),
        ("中国软件", "600536"),
    ]
    assert all(item["resolution_source"] == "configured_alias" for item in result)


def test_conditional_exit_intent_is_not_downgraded_to_hold():
    result = _apply_exit_intent_direction(
        [
            {
                "name": "生益科技",
                "code": "600183",
                "direction": "neutral",
                "signal_type": "conditional",
                "reason": "明天不能转强就考虑撤",
            }
        ]
    )

    assert result[0]["direction"] == "bearish"
    assert result[0]["signal_type"] == "conditional"
