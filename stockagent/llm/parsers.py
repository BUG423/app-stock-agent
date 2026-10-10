"""LLM 响应解析器 —— 从 LLM 输出中提取结构化 JSON。"""

import json
import logging
import re
from typing import Any

from stockagent.core.exceptions import LLMParseError
from stockagent.core.schemas import ConditionClause, StockMention

logger = logging.getLogger(__name__)


def infer_conditions_from_reason(reason: str) -> list[dict[str, Any]]:
    """根据 stock_mention 的 reason 关键词推断其专属条件（LLM 未给出时的兜底）。

    解决 glm-4-flash 等弱模型不稳定、未给 stock_mentions[i].conditions 的问题。
    按博主对每只股票的不同描述生成不同条件类型：
    - "回踩X日线" → support_bounce（回踩均线企稳）
    - "X日线上/上方" → ma_cross（均线之上企稳）
    - "低开/低吸" → price_drop_pct（逢低买入）
    - "不转强/破位/止损" → trend_weakening（趋势转弱，卖出信号）
    """
    if not reason:
        return []
    conds: list[dict[str, Any]] = []
    m = re.search(r"(\d+)\s*日线", reason)
    n = int(m.group(1)) if m else 5

    if "不转强" in reason or "破位" in reason or "转弱" in reason or "止损" in reason:
        conds.append(
            {
                "condition_type": "trend_weakening",
                "operator": "lt",
                "value": float(n),
                "unit": "日线",
                "description": f"不转强/破位止损（跌破{n}日线+未企稳）",
                "plain_description": f"股价跌破{n}日均线且分时未企稳，趋势走弱应止损离场",
            }
        )
    elif "回踩" in reason and ("日线" in reason or "均线" in reason):
        conds.append(
            {
                "condition_type": "support_bounce",
                "operator": "eq",
                "value": float(n),
                "unit": "日线",
                "description": f"回踩{n}日线企稳",
                "plain_description": f"股价回落到近{n}天平均价附近、止跌站稳",
            }
        )
    elif ("线上" in reason or "上方" in reason) and "日线" in reason:
        conds.append(
            {
                "condition_type": "ma_cross",
                "operator": "gt",
                "value": float(n),
                "unit": "日线",
                "description": f"{n}日线上分时企稳",
                "plain_description": f"股价运行在{n}日均线之上、盘中企稳",
            }
        )
    elif "企稳" in reason or "承接" in reason:
        conds.append(
            {
                "condition_type": "support_bounce",
                "operator": "eq",
                "value": float(n),
                "unit": "日线",
                "description": f"{n}日线附近分时企稳有承接",
                "plain_description": f"股价在{n}日均线附近止跌、有资金买入承接",
            }
        )

    if "低开" in reason or "低吸" in reason or "逢低" in reason:
        conds.append(
            {
                "condition_type": "price_drop_pct",
                "operator": "lt",
                "value": -3.0,
                "unit": "%",
                "description": "低开低吸",
                "plain_description": "股价走低时逢低买入",
            }
        )
    if "放量" in reason:
        conds.append(
            {
                "condition_type": "volume_surge",
                "operator": "gt",
                "value": 1.5,
                "unit": "倍",
                "description": "放量",
                "plain_description": "成交量明显放大",
            }
        )
    return conds


def extract_json_from_response(content: str) -> dict[str, Any]:
    """从 LLM 响应中提取 JSON 对象。容忍 markdown code blocks 等包装。"""
    # 尝试直接解析
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # 尝试提取 ```json ... ``` 代码块
    json_block_patterns = [
        r"```json\s*([\s\S]*?)\s*```",
        r"```\s*([\s\S]*?)\s*```",
    ]
    for pattern in json_block_patterns:
        match = re.search(pattern, content)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                continue

    # 尝试提取 { ... } 最外层
    brace_match = re.search(r"\{[\s\S]*\}", content)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    raise LLMParseError(f"无法从 LLM 响应中提取 JSON: {content[:300]}...")


def parse_analysis_result(raw_json: dict[str, Any]) -> dict[str, Any]:
    """解析并验证 LLM 返回的分析 JSON 为内部数据结构。

    返回:
        dict 包含 summary, market_sentiment, direction, time_horizon, confidence,
        stock_mentions (list[dict]), conditions (list[dict])
    """
    # 验证并清理 stock_mentions
    stock_mentions = []
    for sm in raw_json.get("stock_mentions", []):
        try:
            # 解析该股票专属的 conditions
            sm_conditions = []
            for cond in sm.get("conditions") or []:
                try:
                    clause = ConditionClause(
                        condition_type=cond.get("condition_type", "other"),
                        operator=cond.get("operator", "eq"),
                        value=float(cond.get("value", 0)),
                        unit=cond.get("unit"),
                        description=cond.get("description", ""),
                        plain_description=cond.get("plain_description"),
                    )
                    sm_conditions.append(clause.model_dump(exclude_none=True))
                except Exception as e:
                    logger.warning(f"跳过无效的 stock_mention.condition: {cond}, 错误: {e}")
            # 清洗：过滤掉无法自动判断的 other 类型条件（GenericRule 永远返回 False）
            # 以及 LLM 误塞的多股票条件（description 带"："说明是列表）
            sm_conditions = [
                c
                for c in sm_conditions
                if c.get("condition_type") != "other"
                and "：" not in (c.get("description") or "")
                and ":" not in (c.get("description") or "")
            ]
            # 信号类型归一化（follow_now/conditional/exited），非法值回退 conditional
            signal_type = str(sm.get("signal_type", "conditional")).strip().lower()
            if signal_type not in ("follow_now", "conditional", "exited"):
                signal_type = "conditional"
            # follow_now/exited 属于"直接型"，本身不依赖盘中触发条件；
            # 只有 conditional 才在缺条件时按 reason 兜底推断
            if not sm_conditions and signal_type == "conditional":
                sm_conditions = infer_conditions_from_reason(sm.get("reason"))
            source_name = str(sm.get("source_name") or sm.get("source_token") or "").strip()
            mention = StockMention(
                code=sm.get("code", "unknown"),
                name=sm.get("name"),
                source_name=source_name[:120] or None,
                direction=sm.get("direction", "neutral"),
                signal_type=signal_type,
                reason=sm.get("reason"),
                conditions=sm_conditions,
            )
            md = mention.model_dump(exclude_none=True)
            # 透传候选名（LLM 拿不准时给的2-3个候选），供 pipeline 用行情二次消歧
            cands = sm.get("candidates") or []
            if isinstance(cands, list) and cands:
                md["candidates"] = [str(c) for c in cands if c][:3]
            stock_mentions.append(md)
        except Exception as e:
            logger.warning(f"跳过无效的 stock_mention: {sm}, 错误: {e}")

    # 验证并清理 conditions
    conditions = []
    for cond in raw_json.get("conditions", []):
        try:
            clause = ConditionClause(
                condition_type=cond.get("condition_type", "other"),
                operator=cond.get("operator", "eq"),
                value=float(cond.get("value", 0)),
                unit=cond.get("unit"),
                description=cond.get("description", ""),
                plain_description=cond.get("plain_description"),
            )
            conditions.append(clause.model_dump(exclude_none=True))
        except Exception as e:
            logger.warning(f"跳过无效的 condition: {cond}, 错误: {e}")

    # 确定整体方向：取多数 stock_mention 的方向
    direction = raw_json.get("direction", "neutral")
    if not direction or direction not in ("bullish", "bearish", "neutral"):
        # 根据 stock_mentions 推断
        bullish_count = sum(1 for s in stock_mentions if s.get("direction") == "bullish")
        bearish_count = sum(1 for s in stock_mentions if s.get("direction") == "bearish")
        if bullish_count > bearish_count:
            direction = "bullish"
        elif bearish_count > bullish_count:
            direction = "bearish"
        else:
            direction = "neutral"

    return {
        "summary": raw_json.get("summary", ""),
        "market_sentiment": raw_json.get("market_sentiment", "neutral"),
        "direction": direction,
        "time_horizon": raw_json.get("time_horizon", "unknown"),
        "confidence": raw_json.get("confidence", "medium"),
        "stock_mentions": stock_mentions,
        "conditions": conditions,
    }
