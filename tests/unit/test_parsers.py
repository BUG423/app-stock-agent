"""LLM 响应解析器单元测试。"""

import pytest

from stockagent.core.exceptions import LLMParseError
from stockagent.llm.parsers import extract_json_from_response, parse_analysis_result


class TestExtractJson:
    def test_direct_json(self):
        content = '{"summary": "测试", "direction": "bullish"}'
        result = extract_json_from_response(content)
        assert result["summary"] == "测试"

    def test_json_in_code_block(self):
        content = '```json\n{"summary": "测试", "direction": "bullish"}\n```'
        result = extract_json_from_response(content)
        assert result["summary"] == "测试"

    def test_json_without_lang_specifier(self):
        content = '```\n{"summary": "测试", "direction": "bullish"}\n```'
        result = extract_json_from_response(content)
        assert result["summary"] == "测试"

    def test_invalid_json_raises_error(self):
        content = "这不是JSON内容"
        with pytest.raises(LLMParseError):
            extract_json_from_response(content)

    def test_json_with_surrounding_text(self):
        content = '分析结果如下：\n{"summary": "测试", "direction": "bullish"}\n以上为分析结果。'
        result = extract_json_from_response(content)
        assert result["summary"] == "测试"


class TestParseAnalysisResult:
    def test_complete_result(self):
        raw = {
            "summary": "茅台明日看涨",
            "market_sentiment": "bullish",
            "direction": "bullish",
            "time_horizon": "next_day",
            "confidence": "high",
            "stock_mentions": [
                {
                    "code": "600519",
                    "name": "贵州茅台",
                    "direction": "bullish",
                    "reason": "技术面反弹",
                }
            ],
            "conditions": [
                {
                    "condition_type": "price_drop_pct",
                    "operator": "lt",
                    "value": 1600,
                    "unit": "元",
                    "description": "跌破1600买入",
                }
            ],
        }
        result = parse_analysis_result(raw)
        assert result["summary"] == "茅台明日看涨"
        assert len(result["stock_mentions"]) == 1
        assert result["stock_mentions"][0]["code"] == "600519"
        assert len(result["conditions"]) == 1

    def test_empty_mentions(self):
        raw = {
            "summary": "观望为主",
            "market_sentiment": "neutral",
            "direction": "neutral",
            "time_horizon": "next_day",
            "confidence": "low",
            "stock_mentions": [],
            "conditions": [],
        }
        result = parse_analysis_result(raw)
        assert result["direction"] == "neutral"
        assert len(result["stock_mentions"]) == 0

    def test_infer_direction_from_mentions(self):
        raw = {
            "summary": "部分看多",
            "market_sentiment": "bullish",
            "direction": "",
            "time_horizon": "next_day",
            "confidence": "medium",
            "stock_mentions": [
                {"code": "600519", "name": "茅台", "direction": "bullish", "reason": "好"},
                {"code": "000001", "name": "平安", "direction": "bullish", "reason": "好"},
            ],
            "conditions": [],
        }
        result = parse_analysis_result(raw)
        assert result["direction"] == "bullish"

    def test_preserves_verbatim_stock_source_name_for_alias_audit(self):
        result = parse_analysis_result(
            {
                "summary": "测试缩写",
                "stock_mentions": [
                    {
                        "source_name": "zg软件",
                        "name": "中国软件",
                        "code": "600536",
                        "direction": "bullish",
                        "reason": "回踩5日线",
                    }
                ],
                "conditions": [],
            }
        )

        assert result["stock_mentions"][0]["source_name"] == "zg软件"
