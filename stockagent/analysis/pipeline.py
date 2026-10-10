"""分析流水线 —— 编排 抓取 → 内容过滤 → LLM分析 → 存储 的完整流程。"""

import asyncio
import json
import logging
import random
from datetime import datetime, timedelta
from html import escape
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stockagent.analysis.blogger_profiles import (
    BloggerProfile,
    get_blogger_profile,
    verified_direct_mention,
)
from stockagent.analysis.content_filter import get_content_filter
from stockagent.analysis.ocr_stock_resolver import resolve_ocr_holding_stock
from stockagent.analysis.ocr_text import compact_ocr_text
from stockagent.core.config import config
from stockagent.core.db import get_sessionmaker
from stockagent.core.enums import (
    AnalysisStatus,
    ConfidenceLevel,
    ContentCategory,
    Direction,
    FilterStatus,
    MonitorStatus,
    TimeHorizon,
)
from stockagent.core.models import (
    AnalysisResult,
    Blogger,
    MonitoredStock,
    Notification,
    RawContent,
)
from stockagent.llm.client import get_llm_client
from stockagent.llm.ocr import ocr_image_url
from stockagent.llm.parsers import (
    extract_json_from_response,
    infer_conditions_from_reason,
    parse_analysis_result,
)
from stockagent.llm.prompts import INVESTMENT_ANALYSIS_SYSTEM, INVESTMENT_ANALYSIS_USER
from stockagent.market.stock_client import get_stock_client, is_a_share
from stockagent.scrapers.registry import scraper_registry
from stockagent.utils.time_utils import (
    is_trading_day,
    is_trading_time,
    next_trading_day,
    now_cst,
    today_cst,
)

logger = logging.getLogger(__name__)

# 进程级启动水位：不能在每次定时抓取时重置，否则会把本轮抓取开始前几分钟
# 发布的真实新动态误判为旧内容。重启进程时才重新建立水位。
PROCESS_STARTUP_WATERMARK = datetime.now()

_DIR_CN = {"bullish": "看多", "bearish": "看空", "neutral": "观望"}
_STANCE = {
    "support": ("✅支持", "#2b8a3e"),
    "against": ("⚠️反对", "#c92a2a"),
    "neutral": ("◽中性", "#868e96"),
}


def _first_configured_recipient(notifier) -> str:
    """返回首个非空收件人，避免环境变量占位项抢占主收件人。"""
    return next((str(item).strip() for item in notifier._to if str(item).strip()), "")


def _default_blogger_overview() -> list[dict[str, str]]:
    """Return the configured three-blogger shape when no DB snapshot is available."""
    entries = config.get("scrapers", "bilibili", "tracked_bloggers", default=[])
    return [
        {"name": str(entry.get("name") or ""), "holding": "暂无已确认持仓", "watch": "暂无"}
        for entry in entries
        if isinstance(entry, dict) and entry.get("name")
    ]


async def _build_blogger_overview(maker) -> list[dict[str, str]]:
    """Build a compact current holding/watch snapshot for every enabled blogger."""
    active_statuses = {
        MonitorStatus.PENDING,
        MonitorStatus.MONITORING,
        MonitorStatus.TRIGGERED,
    }
    configured = _default_blogger_overview()
    by_name = {row["name"]: row for row in configured}
    today = today_cst()
    try:
        async with maker() as session:
            rows = (
                await session.execute(
                    select(MonitoredStock, AnalysisResult, Blogger)
                    .join(AnalysisResult, MonitoredStock.analysis_result_id == AnalysisResult.id)
                    .join(Blogger, AnalysisResult.blogger_id == Blogger.id)
                    .where(
                        Blogger.enabled.is_(True),
                        Blogger.platform == "bilibili",
                        MonitoredStock.monitor_date >= today,
                    )
                    .order_by(MonitoredStock.monitor_date.asc(), MonitoredStock.created_at.desc())
                )
            ).all()
    except Exception as exc:
        logger.warning("构建博主总览失败，邮件退回空总览: %s", exc)
        return configured

    holdings: dict[str, list[str]] = {name: [] for name in by_name}
    watches: dict[str, list[str]] = {name: [] for name in by_name}
    for monitored, _analysis, blogger in rows:
        row = by_name.setdefault(
            blogger.name,
            {"name": blogger.name, "holding": "暂无已确认持仓", "watch": "暂无"},
        )
        code = str(monitored.stock_code or "").strip()
        label = f"{monitored.stock_name or code}({code})" if code else (monitored.stock_name or "未命名标的")
        signal_type = str(monitored.signal_type or "conditional").lower()
        status = monitored.status.value if hasattr(monitored.status, "value") else str(monitored.status)
        if signal_type == "follow_now":
            holdings.setdefault(blogger.name, []).append(label)
        elif signal_type == "exited":
            # Keep the row visible in the status column without presenting it as a holding.
            holdings.setdefault(blogger.name, []).append(f"已清仓 {label}")
        if status in {item.value for item in active_statuses}:
            watches.setdefault(blogger.name, []).append(label)

    for name, row in by_name.items():
        row["holding"] = "、".join(list(dict.fromkeys(holdings.get(name, [])))[:6]) or "暂无已确认持仓"
        row["watch"] = "、".join(list(dict.fromkeys(watches.get(name, [])))[:8]) or "暂无"
    return list(by_name.values())


def _normalized_stock_alias(value: str) -> str:
    """缩写匹配忽略空白和英文大小写，但保留中文/字母本身。"""
    return "".join(str(value or "").lower().split())


def _stock_alias_hints_for_text(text: str) -> list[dict[str, str]]:
    """返回正文中实际出现、且由配置显式核验过的股票缩写。"""
    configured = config.get("analysis", "stock_aliases", default={})
    if not isinstance(configured, dict):
        return []

    normalized_text = _normalized_stock_alias(text)
    hints: list[dict[str, str]] = []
    for alias, target in configured.items():
        if not isinstance(target, dict):
            continue
        alias_text = str(alias).strip()
        name = str(target.get("name") or "").strip()
        code = str(target.get("code") or "").strip()
        if not alias_text or not name or not (code.isdigit() and len(code) == 6):
            continue
        if _normalized_stock_alias(alias_text) in normalized_text:
            hints.append({"alias": alias_text, "name": name, "code": code})
    return hints


def _format_stock_alias_hints(hints: list[dict[str, str]]) -> str:
    if not hints:
        return "无"
    return "；".join(f"{item['alias']} = {item['name']}({item['code']})" for item in hints)


def _apply_stock_alias_hints(
    mentions: list[dict], hints: list[dict[str, str]]
) -> list[dict]:
    """用原文缩写精确覆盖模型的同音/首字误配结果，并保留审计证据。"""
    aliases = {_normalized_stock_alias(item["alias"]): item for item in hints}
    if not aliases:
        return mentions

    result: list[dict] = []
    for mention in mentions:
        item = dict(mention)
        source = _normalized_stock_alias(item.get("source_name") or "")
        target = aliases.get(source)
        if target is None:
            # 某些模型未返回 source_name，但若已给出完全一致的标准名，仍补齐代码与审计来源。
            name = _normalized_stock_alias(item.get("name") or "")
            target = next(
                (
                    candidate
                    for candidate in aliases.values()
                    if _normalized_stock_alias(candidate["name"]) == name
                ),
                None,
            )
        if target:
            item.update(
                source_name=item.get("source_name") or target["alias"],
                name=target["name"],
                code=target["code"],
                resolution_confidence="high",
                resolution_source="configured_alias",
                resolution_evidence=[f"原文缩写「{target['alias']}」的本地核验映射"],
            )
        result.append(item)
    return result


def _apply_exit_intent_direction(mentions: list[dict]) -> list[dict]:
    """避免把“不能转强就撤”等明确离场条件误做成观望或买入提醒。"""
    exit_words = ("不转强", "考虑撤", "考虑卖", "减仓", "止损", "清仓", "撤了")
    result: list[dict] = []
    for mention in mentions:
        item = dict(mention)
        reason = str(item.get("reason") or "")
        if any(word in reason for word in exit_words):
            item["direction"] = Direction.BEARISH
            item["signal_type"] = "conditional"
        result.append(item)
    return result


_HOLDINGS_ONLY_MARKERS = (
    "暂时不新开",
    "不新开",
    "不开新仓",
    "不新仓",
    "不新增",
    "处理持仓为主",
    "以持仓为主",
    "只处理持仓",
)
_GENERIC_MARKERS = {"科技股", "大科技", "指数", "大盘", "市场", "板块"}
_CLEAR_POSITION_MARKERS = (
    "清仓",
    "空仓",
    "全部卖出",
    "全卖",
    "卖光",
    "没有持仓",
    "不持仓",
    "今日清掉",
    "今天清掉",
)


def _is_holdings_only_update(text: str) -> bool:
    """Detect an explicit no-new-position update before trusting LLM stock extraction."""
    compact = "".join(str(text or "").split())
    return any(marker in compact for marker in _HOLDINGS_ONLY_MARKERS)


def _suppress_holdings_only_mentions(mentions: list[dict]) -> list[dict]:
    """Drop generic/unknown candidates from a holdings-management-only post."""
    kept: list[dict] = []
    for mention in mentions or []:
        name = str(mention.get("name") or "").strip()
        code = str(mention.get("code") or "").strip()
        if code == "unknown" or name in _GENERIC_MARKERS:
            logger.info("持仓管理动态不新增盯盘，丢弃泛化标的: %s(%s)", name, code)
            continue
        kept.append(mention)
    return kept


def _is_explicitly_cleared(text: str) -> bool:
    """Detect an explicit flat/cleared statement from a blogger."""
    compact = "".join(str(text or "").split())
    return any(marker in compact for marker in _CLEAR_POSITION_MARKERS)


def _guard_ocr_stock_mentions(content, mentions: list[dict]) -> list[dict]:
    """OCR 视频只放行画面明确出现的代码或完整名称，防止补全被遮挡股票。"""
    metadata = getattr(content, "metadata", None) or {}
    if content.content_type != "video" or not metadata.get("has_ocr"):
        return mentions

    visible_text = content.text or ""
    visible_codes = {str(code) for code in metadata.get("ocr_stock_codes", [])}
    verified = metadata.get("verified_stock") or {}
    verified_code = str(verified.get("code") or "")
    verified_name = str(verified.get("name") or "").strip()
    verified_is_high = verified.get("confidence") == "high" and verified_code.isdigit()
    requires_exact_evidence = bool(metadata.get("ocr_fallback_reason"))
    guarded: list[dict] = []
    for mention in mentions:
        code = str(mention.get("code") or "")
        name = str(mention.get("name") or "").strip()
        if mention.get("resolution_source") == "configured_alias":
            # 配置别名仅在原文命中时注入，已记录原始缩写作为审计证据。
            guarded.append(mention)
            continue
        if verified_is_high and (code == verified_code or name == verified_name):
            item = dict(mention)
            item.update(
                code=verified_code,
                name=verified_name,
                resolution_confidence="high",
                resolution_evidence=verified.get("evidence") or [],
            )
            guarded.append(item)
            continue
        if code in visible_codes:
            guarded.append(mention)
            continue
        if requires_exact_evidence:
            logger.warning(
                "无语音OCR视频丢弃未核验标的: name=%r code=%r",
                name,
                code,
            )
            continue
        # 三字是最短常见完整证券简称（如万科A）；两字片段通常是遮挡后的残缺文字。
        if len(name) >= 3 and name in visible_text:
            item = dict(mention)
            item["code"] = "unknown"  # 代码未出现在画面，必须按完整可见名称重新解析。
            guarded.append(item)
            continue
        logger.warning(
            "OCR视频丢弃不可核实标的: name=%r code=%r visible_codes=%s",
            name,
            code,
            sorted(visible_codes),
        )
    return guarded


def _apply_blogger_profile(
    profile: BloggerProfile,
    content,
    mentions: list[dict],
) -> list[dict]:
    """把固定画像用于信号分类；只允许高置信度核验结果补充直接型标的。"""
    result = [dict(item) for item in mentions]
    direct = verified_direct_mention(profile, getattr(content, "metadata", None))
    if not direct:
        return result

    for item in result:
        if str(item.get("code") or "") == direct["code"]:
            item.update(direct)
            return result
    result.append(direct)
    return result


def _profile_email_block(
    blogger_name: str,
    blogger_user_id: str = "",
    metadata: dict | None = None,
) -> str:
    profile = get_blogger_profile(blogger_name, blogger_user_id)
    verified = (metadata or {}).get("verified_stock") or {}
    direct = verified_direct_mention(profile, metadata)
    detail = ""
    if direct:
        evidence = [escape(str(item)) for item in verified.get("evidence") or []]
        evidence_html = "；".join(evidence[:4])
        detail = (
            f"<p style='margin:8px 0 0'><b>本次确认标的：</b>"
            f"{escape(direct['name'])}({escape(direct['code'])})</p>"
            + (
                f"<p style='margin:4px 0 0;color:#555;font-size:12px'><b>识别依据：</b>"
                f"{evidence_html}</p>"
                if evidence_html
                else ""
            )
            + "<p style='margin:4px 0 0;color:#9c2f00'><b>本次信号：</b>"
            "直接购入/持有倾向，无前置条件；系统只提醒，不自动下单。</p>"
        )
    elif profile.requires_verified_stock:
        detail = (
            "<p style='margin:8px 0 0;color:#9c2f00'><b>本次处理：</b>"
            "标的未通过名称、持仓数字与行情的多证据核验，只转发原文，"
            "不生成买入提醒。</p>"
        )

    return (
        "<div style='border-left:4px solid #386641;background:#f6faf5;padding:10px 12px;"
        "margin:10px 0;font-size:13px;line-height:1.6'>"
        f"<b>{escape(profile.email_title)}</b>"
        f"<p style='margin:4px 0 0'>{escape(profile.email_rule)}</p>"
        f"{detail}</div>"
    )


def _build_new_content_email(
    blogger_name: str,
    title: str,
    url: str,
    stock_mentions: list,
    analysis_dir: str,
    watch_count: int,
    text: str,
    blogger_user_id: str = "",
    metadata: dict | None = None,
    blogger_overview: list[dict[str, str]] | None = None,
) -> str:
    """新内容提醒邮件 HTML —— 博主动态原文 + 每只标的的 AI 审核复核结论(用户核心诉求)。"""
    td = "padding:6px 8px;border:1px solid #eee;vertical-align:top;word-break:break-word"
    rows = ""
    for s in (stock_mentions or [])[:8]:
        nm = f"{s.get('name', '?')}({s.get('code', '?')})"
        # 方向：归一化大小写并去枚举前缀，缺失/未知一律按"观望"，避免空白列
        _dv = str(s.get("direction") or analysis_dir or "neutral").lower().replace("direction.", "")
        d = _DIR_CN.get(_dv, "观望")
        # 博主条件：优先取精简版 description（如"冲高就先抛后吸"），退回白话版；无则"无"
        _cparts = [
            (c.get("description") or c.get("plain_description") or "").strip()
            for c in (s.get("conditions") or [])
        ]
        _cparts = [p for p in _cparts if p]
        profile = get_blogger_profile(blogger_name, blogger_user_id)
        if _cparts:
            cond_txt = escape("；".join(_cparts)[:90])
        elif profile.default_signal_type == "follow_now":
            cond_txt = "<span style='color:#9c2f00'>无前置条件（直接购入/持有型）</span>"
        else:
            cond_txt = "<span style='color:#9c2f00'>本条未识别到明确条件，不触发买入</span>"
        rv = s.get("review") or {}
        if rv:
            label, color = _STANCE.get(rv.get("stance"), (rv.get("stance") or "-", "#666"))
            verdict = (rv.get("verdict") or "")[:60]
            risk = (
                f"<br><span style='color:#c92a2a'>⚠️风险：{(rv.get('risk') or '')[:50]}</span>"
                if rv.get("risk")
                else ""
            )
            review = (
                f"<span style='color:{color};font-weight:600'>AI复核 {rv.get('score', '?')}/5 "
                f"{label}</span>：{verdict}{risk}"
            )
        else:
            review = "<span style='color:#999'>（未复核/非数字代码）</span>"
        rows += (
            f"<tr><td style='{td};font-weight:600'>{escape(nm)}</td>"
            f"<td style='{td};white-space:nowrap'>{d}</td>"
            f"<td style='{td};color:#555;font-size:12px'>{cond_txt}</td>"
            f"<td style='{td};font-size:12px'>{review}</td></tr>"
        )
    if not rows:
        rows = (
            f"<tr><td colspan='4' style='{td};color:#9c2f00'>"
            "本条未确认可进入盯盘的股票标的。</td></tr>"
        )
    profile_block = _profile_email_block(blogger_name, blogger_user_id, metadata)
    overview_rows = "".join(
        "<tr>"
        f"<td style='{td};font-weight:600'>{escape(str(row.get('name') or ''))}</td>"
        f"<td style='{td}'>{escape(str(row.get('holding') or '暂无已确认持仓'))}</td>"
        f"<td style='{td}'>{escape(str(row.get('watch') or '暂无'))}</td>"
        "</tr>"
        for row in (blogger_overview or _default_blogger_overview())
    )
    if not overview_rows:
        overview_rows = (
            f"<tr><td colspan='3' style='{td};color:#777'>暂无博主持仓/盯盘数据</td></tr>"
        )
    return f"""<div style="font-family:'Microsoft YaHei',sans-serif;max-width:640px;margin:0 auto;padding:16px;color:#222">
<h3 style="margin:0 0 6px">📢 {escape(blogger_name)} 新动态</h3>
<p style="color:#555;margin:0 0 10px">{escape(title)}</p>
{profile_block}
<h4 style="margin:14px 0 6px">三位博主当前总览</h4>
<table style="width:100%;border-collapse:collapse;table-layout:fixed;font-size:12px;border:1px solid #eee">
<tr style="background:#fafafa"><th style='padding:6px 8px;text-align:left;border:1px solid #eee'>博主</th>
<th style='padding:6px 8px;text-align:left;border:1px solid #eee'>当前持仓状态</th>
<th style='padding:6px 8px;text-align:left;border:1px solid #eee'>全局已有盯盘（来自此前动态）</th></tr>
{overview_rows}</table>
<table style="width:100%;border-collapse:collapse;table-layout:fixed;font-size:13px;border:1px solid #eee">
<colgroup><col style="width:22%"><col style="width:13%"><col style="width:30%"><col style="width:35%"></colgroup>
<tr style="background:#fafafa">
<th style='padding:6px 8px;text-align:left;border:1px solid #eee'>标的</th>
<th style='padding:6px 8px;text-align:left;border:1px solid #eee'>博主方向</th>
<th style='padding:6px 8px;text-align:left;border:1px solid #eee'>博主条件</th>
<th style='padding:6px 8px;text-align:left;border:1px solid #eee'>AI 审核复核</th></tr>
{rows}</table>
<p style="color:#888;font-size:12px;margin:8px 0">本条新增盯盘 {len(stock_mentions or [])} 只 · 全局现有盯盘 {watch_count} 只 · <a href="{escape(url, quote=True)}">查看原动态 ↗</a></p>
<div style="background:#f7f7f7;border-radius:6px;padding:10px;white-space:pre-wrap;font-size:13px;line-height:1.6;color:#333">{escape((text or "")[:1500])}</div>
<p style="color:#bbb;font-size:12px;margin-top:10px">博主动态原文 + AI 复核结论；持仓管理动态不新增盯盘，仅供参考，不构成投资建议。</p>
</div>"""


def _build_raw_content_email(
    blogger_name: str,
    title: str,
    url: str,
    text: str,
    blogger_user_id: str = "",
    metadata: dict | None = None,
    blogger_overview: list[dict[str, str]] | None = None,
) -> str:
    """LLM 不可用时仍推送忠实原文，不生成股票或交易判断。"""
    profile = get_blogger_profile(blogger_name, blogger_user_id)
    direct = verified_direct_mention(profile, metadata)
    if direct:
        fallback_note = (
            "本地 OCR 与发布日行情已完成标的交叉核验；语义分析服务暂不可用，"
            "除该博主的固定提醒画像外不补充任何条件。"
        )
    else:
        fallback_note = "结构化分析暂不可用，本提醒只转发博主原始内容，不生成标的或交易判断。"
    profile_block = _profile_email_block(blogger_name, blogger_user_id, metadata)
    td = "padding:6px 8px;border:1px solid #eee;vertical-align:top;word-break:break-word"
    overview_rows = "".join(
        "<tr>"
        f"<td style='{td};font-weight:600'>{escape(str(row.get('name') or ''))}</td>"
        f"<td style='{td}'>{escape(str(row.get('holding') or '暂无已确认持仓'))}</td>"
        f"<td style='{td}'>{escape(str(row.get('watch') or '暂无'))}</td>"
        "</tr>"
        for row in (blogger_overview or _default_blogger_overview())
    )
    return f"""<div style="font-family:'Microsoft YaHei',sans-serif;max-width:640px;margin:0 auto;padding:16px;color:#222">
<h3 style="margin:0 0 6px">📢 {escape(blogger_name)} 新动态</h3>
<p style="color:#555;margin:0 0 10px">{escape(title)}</p>
{profile_block}
<h4 style="margin:14px 0 6px">三位博主当前总览</h4>
<table style="width:100%;border-collapse:collapse;table-layout:fixed;font-size:12px;border:1px solid #eee">
<tr style="background:#fafafa"><th style='padding:6px 8px;text-align:left;border:1px solid #eee'>博主</th>
<th style='padding:6px 8px;text-align:left;border:1px solid #eee'>当前持仓状态</th>
<th style='padding:6px 8px;text-align:left;border:1px solid #eee'>全局已有盯盘（来自此前动态）</th></tr>
{overview_rows}</table>
<p style="background:#fff8e1;border-left:4px solid #f59f00;padding:8px 10px;font-size:13px">
{escape(fallback_note)}</p>
<div style="background:#f7f7f7;border-radius:6px;padding:10px;white-space:pre-wrap;font-size:13px;line-height:1.6;color:#333">{escape((text or "")[:3000])}</div>
<p style="margin-top:10px"><a href="{escape(url, quote=True)}">查看原动态</a></p>
<p style="color:#bbb;font-size:12px">仅供参考，不构成投资建议。</p>
</div>"""


async def _notify_raw_content_fallback(maker, blogger, content, raw_id: str) -> bool | None:
    """分析失败时发送一次原文提醒；返回 None 表示已通知或无收件人。"""
    blogger_user_id = str(getattr(blogger, "platform_user_id", "") or "")
    metadata = getattr(content, "metadata", None) or {}
    from stockagent.notification.email_format import standard_subject

    subject = standard_subject(
        "博主动态",
        now_cst().strftime("%Y-%m-%d %H:%M"),
        blogger.name,
    )
    blogger_overview = await _build_blogger_overview(maker)
    body = _build_raw_content_email(
        blogger_name=blogger.name,
        title=content.title,
        url=content.url,
        text=content.text,
        blogger_user_id=blogger_user_id,
        metadata=metadata,
        blogger_overview=blogger_overview,
    )

    async with maker() as check_session:
        already_notified = await check_session.scalar(
            select(Notification).where(
                Notification.related_entity_type == "raw_content",
                Notification.related_entity_id == raw_id,
            )
        )
        if already_notified:
            # 清库后的首次测试可能已经排入了旧版通用邮件。只要尚未发送，就原地升级
            # 三类博主画像、主题和核验证据，避免重复邮件，也避免队列保留过时模板。
            if hasattr(already_notified, "sent") and not already_notified.sent:
                already_notified.subject = subject
                already_notified.body = body
                await check_session.commit()
            return None

    from stockagent.core.base_notifier import NotificationMessage
    from stockagent.notification.email_notifier import EmailNotifier

    notifier = EmailNotifier()
    recipient = _first_configured_recipient(notifier)
    if not recipient:
        logger.warning("新内容原文提醒无可用收件人: %s", content.title[:40])
        return None
    message = NotificationMessage(
        type="daily_advice",
        recipient=recipient,
        subject=subject,
        body=body,
        metadata={"blogger": blogger.name, "url": content.url, "analysis": "unavailable"},
    )
    return await notifier.queue_and_send(
        message,
        related_type="raw_content",
        related_id=raw_id,
        # 并发分析时先留出一个调度周期给成功结果覆盖，避免同一原文先发降级版又发完整版。
        defer_send=True,
    )


async def _resolve_verified_stock(profile: BloggerProfile, content) -> dict | None:
    """对需要严格验股的 OCR 内容做本地多证据确认。"""
    metadata = dict(getattr(content, "metadata", None) or {})
    existing = metadata.get("verified_stock") or {}
    if existing.get("confidence") == "high":
        return existing
    if not profile.requires_verified_stock or not metadata.get("has_ocr"):
        return None

    try:
        verified = await asyncio.to_thread(
            resolve_ocr_holding_stock,
            content.text or "",
            content.published_at,
            get_stock_client(),
        )
    except Exception as exc:
        logger.warning("OCR 标的多证据核验异常 '%s': %s", content.title[:40], exc)
        return None
    if not verified:
        logger.warning("OCR 标的证据不足，禁止猜测: %s", content.title[:40])
        return None

    metadata["verified_stock"] = verified
    content.metadata = metadata
    logger.info(
        "OCR 标的核验成功: %s(%s), score=%.1f",
        verified["name"],
        verified["code"],
        verified["score"],
    )
    return verified


def _monitor_date_for_content(content) -> Any:
    published_at = getattr(content, "published_at", None)
    if published_at and is_trading_time(published_at) and is_trading_day(published_at.date()):
        return published_at.date()
    return next_trading_day(published_at.date() if published_at else None)


async def _persist_local_verified_direct(ws, raw, blogger, content) -> dict | None:
    """无 LLM 时仅凭高置信度本地证据落直接型提醒，其他情况返回 None。"""
    profile = get_blogger_profile(blogger.name, blogger.platform_user_id)
    mention = verified_direct_mention(profile, getattr(content, "metadata", None))
    if not mention:
        return None

    existing = await ws.scalar(
        select(AnalysisResult).where(AnalysisResult.raw_content_id == raw.id)
    )
    if existing:
        raw.analysis_status = AnalysisStatus.COMPLETED
        return {"mention": mention, "monitor_date": _monitor_date_for_content(content)}

    summary = (
        f"博主实盘持仓画面确认 {mention['name']}({mention['code']})；"
        "按该博主固定画像归类为直接购入/持有倾向"
    )
    analysis = AnalysisResult(
        raw_content_id=raw.id,
        blogger_id=blogger.id,
        summary=summary,
        stock_mentions=[mention],
        conditions=[],
        time_horizon=TimeHorizon.NEXT_DAY,
        confidence=ConfidenceLevel.HIGH,
        market_sentiment="bullish",
        direction=Direction.BULLISH,
        llm_model="ocr-market-resolver",
        llm_provider="local_evidence",
        raw_llm_output=json.dumps(
            (getattr(content, "metadata", None) or {}).get("verified_stock") or {},
            ensure_ascii=False,
        ),
    )
    ws.add(analysis)
    await ws.flush()

    mon_date = _monitor_date_for_content(content)
    ws.add(
        MonitoredStock(
            analysis_result_id=analysis.id,
            stock_code=mention["code"],
            stock_name=mention["name"],
            direction=Direction.BULLISH,
            reason=mention["reason"],
            signal_type="follow_now",
            trigger_conditions=[],
            monitor_date=mon_date,
            status=MonitorStatus.PENDING,
        )
    )
    raw.extra_data = getattr(content, "metadata", None) or {}
    raw.analysis_status = AnalysisStatus.COMPLETED
    return {"mention": mention, "monitor_date": mon_date}


class AnalysisPipeline:
    """晚间抓取 + 分析流水线。"""

    def __init__(self) -> None:
        # 每次进程启动后建立一次水位；启动前发布的动态永远不进入本次流水线，
        # 即使平台接口因分页/缓存再次返回，也不会重复分析或发信。
        self.startup_watermark = PROCESS_STARTUP_WATERMARK

    def _filter_after_startup(self, contents: list, blogger_name: str = "") -> list:
        fresh = []
        dropped = 0
        for content in contents:
            published = getattr(content, "published_at", None)
            if published is None:
                dropped += 1
                continue
            if getattr(published, "tzinfo", None):
                published = published.replace(tzinfo=None)
            if published > self.startup_watermark:
                fresh.append(content)
            else:
                dropped += 1
        if dropped:
            logger.info(
                "博主 %s 跳过 %d 条启动前动态（启动水位 %s）",
                blogger_name, dropped, self.startup_watermark.strftime("%Y-%m-%d %H:%M:%S"),
            )
        return fresh

    def _filter_recent(self, contents: list, blogger_name: str = "") -> list:
        """只保留 published_at 在近 N 天内的动态（默认 3 天，config 可调）。

        太老的内容直接丢弃：不入库、不分析、不发邮件。避免清零重抓或翻页时把
        陈年旧动态当"新信号"灌进库、刷屏邮件。N 取 3 天（今天+前2天，容一个周末缺口）。
        published_at 无值的保守保留（无法判断新旧）。
        """
        max_age_days = config.get("scrapers", "max_content_age_days", default=3)
        # published_at 由 datetime.fromtimestamp() 生成 = naive 本地时间(服务器北京时区)，
        # 与 naive 的 now 直接比较即可。
        cutoff = None
        if max_age_days and max_age_days > 0:
            cutoff = datetime.now() - timedelta(days=max_age_days)
        # 绝对时间下限：清库重建时"只收某时刻之后的动态"(如今日17点后)。与天数窗口取更晚者，
        # 保证种子抓取和后续定时抓取都不会把该时刻之前的旧内容再灌进来。
        min_time_str = config.get("scrapers", "min_content_time", default=None)
        if min_time_str:
            try:
                min_time = datetime.fromisoformat(str(min_time_str).strip().replace("/", "-"))
                if cutoff is None or min_time > cutoff:
                    cutoff = min_time
            except ValueError:
                logger.warning(f"scrapers.min_content_time 解析失败: {min_time_str!r}，忽略")
        if cutoff is None:
            return contents
        fresh, dropped = [], 0
        for c in contents:
            pub = getattr(c, "published_at", None)
            if pub is None:
                fresh.append(c)
                continue
            if pub.tzinfo is not None:  # 兼容个别带 tz 的来源
                pub = pub.replace(tzinfo=None)
            if pub >= cutoff:
                fresh.append(c)
            else:
                dropped += 1
        if dropped:
            logger.info(
                f"博主 {blogger_name} 跳过 {dropped} 条早于 {cutoff:%Y-%m-%d %H:%M} 的旧动态"
                f"（不入库/不发邮件），保留 {len(fresh)} 条"
            )
        return fresh

    async def run(self, blogger_ids: list[str] | None = None) -> dict[str, Any]:
        """
        执行完整的 抓取→分析→存储 流程。

        Args:
            blogger_ids: 指定博主ID列表，None 表示所有启用的博主

        Returns:
            执行结果摘要
        """
        logger.info("========== 分析流水线开始 ==========")
        scraper_registry.discover_plugins()

        maker = await get_sessionmaker()
        stats = {
            "bloggers_processed": 0,
            "contents_scraped": 0,
            "contents_filtered": 0,
            "contents_useful": 0,
            "contents_analyzed": 0,
            "errors": 0,
            "emails_sent": 0,
            "emails_failed": 0,
        }

        async with maker() as session:
            # 1. 加载博主
            bloggers = await self._load_bloggers(session, blogger_ids)
            if not bloggers:
                logger.info("没有需要处理的博主")
                return stats

            llm_client = None
            llm_init_error = ""
            try:
                llm_client = get_llm_client()
            except Exception as exc:
                llm_init_error = str(exc)
                logger.error("LLM 不可用，继续抓取/OCR并降级推送原文: %s", exc)

            for idx, blogger in enumerate(bloggers):
                # 博主间随机间隔（防封禁：避免连续请求同一平台）
                if idx > 0:
                    delay = random.uniform(2.0, 8.0)
                    logger.debug(f"博主间间隔 {delay:.1f}s")
                    await asyncio.sleep(delay)
                try:
                    profile = get_blogger_profile(blogger.name, blogger.platform_user_id)
                    logger.info(
                        f"处理博主: {blogger.name} ({blogger.platform}:{blogger.platform_user_id})"
                    )
                    scraper = scraper_registry.get(blogger.platform)
                    if scraper is None:
                        logger.debug(f"平台 '{blogger.platform}' 无对应爬虫，跳过通用抓取")
                        continue

                    # 2. 抓取内容
                    contents = await scraper.scrape_user_posts(
                        user_id=blogger.platform_user_id,
                        since=blogger.last_scraped_at,
                        max_items=50,
                    )

                    # 抓取成功即记录时间：避免后续 continue(无新内容/无待分析)跳过更新，
                    # 保证增量抓取 since 正确、数据源健康度真实。
                    blogger.last_scraped_at = now_cst()

                    if not contents:
                        logger.info(f"博主 {blogger.name} 没有新内容")
                        continue

                    contents = self._filter_after_startup(contents, blogger.name)
                    if not contents:
                        logger.info(f"博主 {blogger.name} 全部动态早于本次启动水位，跳过")
                        continue

                    # 只保留近 N 天的动态：太老的既不入库也不发邮件。
                    # 用户要求(2026-07-11)：清零重抓/翻页时别把陈年旧动态灌进库或刷屏邮件，
                    # 盯盘只关心最新一两天的信号。历史内容如需回补走 scripts/fetch_*.py。
                    contents = self._filter_recent(contents, blogger.name)
                    if not contents:
                        logger.info(f"博主 {blogger.name} 近期无新动态（旧内容已跳过）")
                        continue

                    stats["contents_scraped"] += len(contents)

                    # 原始内容优先落库：OCR/过滤/LLM 即使超时或额度耗尽，也不能让已抓到的动态丢失。
                    raw_map: dict[str, tuple[RawContent, bool]] = {}
                    for content in contents:
                        raw, is_new = await self._store_raw_content(
                            session, blogger.id, content, None
                        )
                        raw_map[content.content_id] = (raw, is_new)
                    await session.commit()

                    # 2.5 对图片动态进行OCR识别
                    for content in contents:
                        text = content.text or ""
                        if "仅图片" in text or "图文动态" in text or len(text.strip()) < 20:
                            metadata = content.metadata or {}
                            images = metadata.get("images", [])
                            if images:
                                logger.info(f"对图片动态进行OCR识别: {content.title}")
                                ocr_text = ""
                                ocr_codes: list[str] = []
                                for img_url in images[:3]:
                                    result = await ocr_image_url(img_url)
                                    if result.get("success"):
                                        ocr_text += result.get("text", "") + "\n"
                                        ocr_codes.extend(result.get("stock_codes") or [])
                                if ocr_text.strip():
                                    content.text = (
                                        f"{text}\n\n【图片OCR识别】\n{compact_ocr_text(ocr_text)}"
                                    )
                                    metadata["has_ocr"] = True
                                    metadata["ocr_stock_codes"] = list(dict.fromkeys(ocr_codes))
                                    content.metadata = metadata
                                    logger.info(f"OCR识别成功，补充了 {len(ocr_text)} 字符")

                    # 提前早退休的实盘短视频必须先通过本地 OCR + 行情交叉核验。
                    # 此步骤不依赖 LLM，核验结果会随原文元数据持久化供邮件与审计使用。
                    for content in contents:
                        await _resolve_verified_stock(profile, content)

                    # 3. 内容过滤：使用LLM判断内容是否有投资价值
                    content_filter = get_content_filter()
                    filter_contents = []
                    for content in contents:
                        filter_contents.append(
                            {
                                "blogger_name": blogger.name,
                                "title": content.title,
                                "text": content.text,
                                "published_at": str(content.published_at or ""),
                                "content_type": content.content_type,
                            }
                        )

                    logger.info(f"博主 {blogger.name} 正在过滤 {len(filter_contents)} 条内容...")
                    filter_results = await content_filter.batch_filter(filter_contents)

                    # 统计过滤结果
                    useful_contents = []
                    filtered_out = 0
                    for content, filter_result in zip(contents, filter_results):
                        if filter_result.is_useful:
                            useful_contents.append(content)
                            logger.debug(f"✓ 保留: {content.title[:30]} - {filter_result.reason}")
                        else:
                            filtered_out += 1
                            logger.info(
                                f"✗ 过滤: {content.title[:30]} - {filter_result.reason} "
                                f"(类型: {filter_result.category})"
                            )

                    logger.info(
                        f"博主 {blogger.name} 过滤完成: "
                        f"保留 {len(useful_contents)} 条, 过滤 {filtered_out} 条"
                    )
                    stats["contents_filtered"] += filtered_out
                    stats["contents_useful"] += len(useful_contents)

                    # 4. 更新过滤结果并生成待分析队列（原始内容已在耗时处理前落库）
                    pending: list = []  # [(raw_id, content), ...] 待分析

                    # 构建过滤结果映射
                    filter_result_map = {}
                    for content, filter_result in zip(contents, filter_results):
                        filter_result_map[content.content_id] = filter_result

                    for content, filter_result in zip(contents, filter_results):
                        raw, _ = raw_map[content.content_id]
                        raw.text = content.text
                        raw.extra_data = content.metadata
                        raw.filter_status = (
                            FilterStatus.USEFUL
                            if filter_result.is_useful
                            else FilterStatus.FILTERED
                        )
                        raw.filter_reason = filter_result.reason

                    for content in useful_contents:
                        filter_result = filter_result_map.get(content.content_id)
                        raw, is_new = raw_map[content.content_id]
                        if raw.analysis_status in (AnalysisStatus.PENDING, AnalysisStatus.FAILED):
                            raw.analysis_status = AnalysisStatus.PENDING
                            pending.append((raw.id, content, is_new))
                    await session.commit()  # 提交 raw 写入，释放事务锁

                    # 新内容邮件在分析成功后发（带股票+条件），见写库循环

                    if not pending:
                        logger.info(f"博主 {blogger.name} 无待分析内容")
                    else:
                        logger.info(f"博主 {blogger.name} 待分析 {len(pending)} 条，并发分析中...")

                    # 4. 并发 LLM 分析（最慢的一步并发）
                    sem = asyncio.Semaphore(3)

                    async def _analyze_one(content):
                        if llm_client is None:
                            raise RuntimeError(llm_init_error or "LLM 不可用")
                        messages = [
                            {"role": "system", "content": INVESTMENT_ANALYSIS_SYSTEM},
                            {
                                "role": "user",
                                "content": INVESTMENT_ANALYSIS_USER.format(
                                    platform=blogger.platform,
                                    blogger_name=blogger.name,
                                    title=content.title,
                                    published_at=str(content.published_at or "未知"),
                                    blogger_profile=profile.prompt_rule,
                                    verified_stock_hint=json.dumps(
                                        (content.metadata or {}).get("verified_stock") or {},
                                        ensure_ascii=False,
                                    ),
                                    stock_alias_hints=_format_stock_alias_hints(
                                        _stock_alias_hints_for_text(content.text)
                                    ),
                                    text=content.text[:8000],
                                ),
                            },
                        ]
                        async with sem:
                            return await llm_client.chat(
                                messages=messages,
                                response_format={"type": "json_object"},
                            )

                    responses = await asyncio.gather(
                        *[_analyze_one(c) for _, c, _ in pending],
                        return_exceptions=True,
                    )

                    # 5. 每条用独立 session 写库（彻底隔离，单条失败不影响其他）
                    for (raw_id, content, _is_new), response in zip(pending, responses):
                        async with maker() as ws:
                            try:
                                raw = await ws.get(RawContent, raw_id)
                                if not raw:
                                    continue

                                failure = None
                                if isinstance(response, Exception):
                                    failure = f"LLM 调用异常: {response}"
                                elif not response.success:
                                    failure = f"LLM 分析失败: {response.error}"
                                if failure:
                                    logger.error("%s '%s'", failure, content.title[:30])
                                    failure_metadata = dict(raw.extra_data or {})
                                    failure_metadata["analysis_failure"] = {
                                        "error": failure[:500],
                                        "occurred_at": now_cst().isoformat(),
                                        "provider": str(getattr(response, "provider", "") or ""),
                                    }
                                    raw.extra_data = failure_metadata
                                    local_result = await _persist_local_verified_direct(
                                        ws, raw, blogger, content
                                    )
                                    # 本地 OCR 兜底会重写元数据；仍保留本次主分析失败原因供审计。
                                    if local_result:
                                        metadata = dict(raw.extra_data or {})
                                        metadata["analysis_failure"] = failure_metadata[
                                            "analysis_failure"
                                        ]
                                        raw.extra_data = metadata
                                    if local_result:
                                        await ws.commit()
                                        stats["contents_analyzed"] += 1
                                        logger.info(
                                            "本地证据分析完成: %s(%s)",
                                            local_result["mention"]["name"],
                                            local_result["mention"]["code"],
                                        )
                                    else:
                                        raw.analysis_status = AnalysisStatus.FAILED
                                        await ws.commit()
                                        stats["errors"] += 1

                                    sent = await _notify_raw_content_fallback(
                                        maker, blogger, content, raw_id
                                    )
                                    if sent is True:
                                        from stockagent.notification.email_notifier import (
                                            email_policy_enabled,
                                        )

                                        if email_policy_enabled("new_content", True):
                                            stats["emails_sent"] += 1
                                        else:
                                            logger.info("邮件策略已合并降级原文至定时汇总")
                                    elif sent is False:
                                        stats["emails_failed"] += 1
                                    continue

                                parsed = parse_analysis_result(
                                    extract_json_from_response(response.content)
                                )
                                parsed["stock_mentions"] = _apply_stock_alias_hints(
                                    parsed["stock_mentions"],
                                    _stock_alias_hints_for_text(content.text),
                                )
                                parsed["stock_mentions"] = _apply_exit_intent_direction(
                                    parsed["stock_mentions"]
                                )
                                parsed["stock_mentions"] = _guard_ocr_stock_mentions(
                                    content, parsed["stock_mentions"]
                                )
                                parsed["stock_mentions"] = _apply_blogger_profile(
                                    profile, content, parsed["stock_mentions"]
                                )
                                if _is_holdings_only_update(content.text):
                                    parsed["stock_mentions"] = _suppress_holdings_only_mentions(
                                        parsed["stock_mentions"]
                                    )
                                    parsed["summary"] = (
                                        f"{parsed.get('summary', '').strip()}"
                                        " 本条明确为持仓管理，不新增开仓/盯盘标的。"
                                    ).strip()

                                explicitly_cleared = _is_explicitly_cleared(content.text)
                                if explicitly_cleared:
                                    # 清仓/空仓声明优先级最高：本条只做状态结算，不产生新盯盘。
                                    parsed["stock_mentions"] = []
                                    parsed["summary"] = (
                                        f"{parsed.get('summary', '').strip()}"
                                        " 本条包含明确清仓/空仓声明，已清理当前盯盘。"
                                    ).strip()

                                # 兜底：LLM 推不出代码时按名称查；仍不行则逐个试候选名
                                market = get_stock_client()
                                for sm in parsed["stock_mentions"]:
                                    if sm.get("code") != "unknown":
                                        continue
                                    resolved = None
                                    if sm.get("name"):
                                        resolved = await asyncio.to_thread(
                                            market.resolve_stock_code, sm["name"]
                                        )
                                    # 名称查不到 → 试 LLM 给的候选全称，命中即回填名称
                                    if not resolved:
                                        for cand in sm.get("candidates") or []:
                                            r = await asyncio.to_thread(
                                                market.resolve_stock_code, cand
                                            )
                                            if r:
                                                resolved, sm["name"] = r, cand
                                                logger.info(f"  ↳ 候选消歧: '{cand}' → {r}")
                                                break
                                    if resolved:
                                        sm["code"] = resolved
                                        quote = await asyncio.to_thread(
                                            market.get_realtime_quote, resolved
                                        )
                                        canonical_name = str(quote.get("stock_name") or "").strip()
                                        if canonical_name:
                                            sm["name"] = canonical_name.lstrip("*")
                                        logger.info(
                                            f"  ↳ 行情名称兜底: '{sm.get('name')}' → {resolved}"
                                        )

                                analysis = AnalysisResult(
                                    raw_content_id=raw_id,
                                    blogger_id=blogger.id,
                                    summary=parsed["summary"],
                                    stock_mentions=parsed["stock_mentions"],
                                    conditions=parsed["conditions"],
                                    time_horizon=parsed["time_horizon"],
                                    confidence=parsed["confidence"],
                                    market_sentiment=parsed["market_sentiment"],
                                    direction=parsed.get("direction", Direction.NEUTRAL),
                                    llm_model=response.model,
                                    llm_provider=response.provider or llm_client.provider_name(),
                                    raw_llm_output=response.content,
                                    token_usage=response.usage if response.usage else None,
                                )
                                ws.add(analysis)
                                await ws.flush()  # 生成 analysis.id

                                raw.analysis_status = AnalysisStatus.COMPLETED

                                # 监控日判断：盘中(交易时段)发=今日买入信号；盘后/夜间发=次日预估
                                mon_date = _monitor_date_for_content(content)

                                # 博主明确清仓/空仓时，清掉该博主当前及次日的全部活跃盯盘。
                                if explicitly_cleared:
                                    active_rows = (
                                        await ws.execute(
                                            select(MonitoredStock)
                                            .join(
                                                AnalysisResult,
                                                MonitoredStock.analysis_result_id == AnalysisResult.id,
                                            )
                                            .where(
                                                AnalysisResult.blogger_id == blogger.id,
                                                MonitoredStock.status.in_(
                                                    (
                                                        MonitorStatus.PENDING,
                                                        MonitorStatus.MONITORING,
                                                        MonitorStatus.TRIGGERED,
                                                    )
                                                ),
                                                MonitoredStock.monitor_date >= today_cst(),
                                            )
                                        )
                                    ).scalars().all()
                                    for active in active_rows:
                                        active.status = MonitorStatus.EXPIRED
                                    if active_rows:
                                        logger.info(
                                            "博主明确清仓/空仓，已清掉 %s 条当前盯盘: %s",
                                            len(active_rows),
                                            blogger.name,
                                        )

                                # AI 复核官开关（二次筛选）
                                from stockagent.core.config import config as _cfg
                                from stockagent.core.models import StockReview

                                review_enabled = _cfg.get(
                                    "analysis", "ai_review", "enabled", default=True
                                )

                                # 为每个提及的股票创建监控记录（先校验代码）
                                client = get_stock_client()
                                for sm in parsed["stock_mentions"]:
                                    stock_code = str(sm.get("code", "unknown"))
                                    stock_name = sm.get("name", "")
                                    reason = sm.get("reason") or ""
                                    signal_type = sm.get("signal_type", "conditional")

                                    if signal_type == "exited":
                                        # 卖出/清仓是状态结算，不应新建一条可盯盘记录。
                                        existing = (
                                            await ws.execute(
                                                select(MonitoredStock).where(
                                                    MonitoredStock.stock_code == stock_code,
                                                    MonitoredStock.status.in_(
                                                        (
                                                            MonitorStatus.PENDING,
                                                            MonitorStatus.MONITORING,
                                                            MonitorStatus.TRIGGERED,
                                                        )
                                                    ),
                                                )
                                            )
                                        ).scalars().all()
                                        for active in existing:
                                            active.status = MonitorStatus.EXPIRED
                                        logger.info(
                                            "博主明确卖出，已清掉 %s(%s) 的 %s 条活跃盯盘",
                                            stock_name,
                                            stock_code,
                                            len(existing),
                                        )
                                        continue

                                    # 校验/修正代码：解决 LLM 推断错误（如盛剑科技→603421 实为鼎信通讯）
                                    if sm.get("resolution_confidence") != "high":
                                        try:
                                            corrected, note = await asyncio.to_thread(
                                                client.validate_stock_code, stock_name, stock_code
                                            )
                                            if corrected != stock_code:
                                                logger.info(
                                                    f"  代码修正: {stock_name} {stock_code} → {corrected}"
                                                )
                                                stock_code = corrected
                                            # 名称不匹配/有修正时，把校验说明附到理由
                                            if note and "匹配" not in note:
                                                reason = f"{reason} [{note}]".strip(" []")
                                        except Exception as e:
                                            logger.warning(f"代码校验失败 {stock_name}: {e}")

                                    # A股硬门：只盯 A 股（含北交所/B股），美股/港股等一律不入盯盘管道。
                                    if not is_a_share(stock_code):
                                        logger.info(
                                            f"  ⏭ 跳过非A股标的: {stock_name}({stock_code})——盯盘只做A股"
                                        )
                                        continue

                                    # 条件优先级: 股票专属 > 推断 > 空(不触发)
                                    # follow_now/exited 是"直接型"，本就不需要盘中条件触发
                                    stock_conditions = sm.get("conditions") or []
                                    if not stock_conditions and signal_type == "conditional":
                                        stock_conditions = infer_conditions_from_reason(reason)
                                    monitored = MonitoredStock(
                                        analysis_result_id=analysis.id,
                                        stock_code=stock_code,
                                        stock_name=stock_name,
                                        direction=sm.get("direction", Direction.NEUTRAL),
                                        reason=reason,
                                        signal_type=signal_type,
                                        trigger_conditions=stock_conditions,
                                        monitor_date=mon_date,
                                        status=MonitorStatus.PENDING,
                                    )
                                    ws.add(monitored)
                                    await ws.flush()  # 生成 monitored.id

                                    # AI 二次筛选：用真实行情独立复核博主观点是否靠谱
                                    if review_enabled and stock_code.isdigit():
                                        try:
                                            from stockagent.analysis.reviewer import review_stock

                                            rv = await review_stock(
                                                code=stock_code,
                                                name=stock_name,
                                                blogger_view=reason,
                                                direction=sm.get("direction", "bullish"),
                                            )
                                            monitored.review_score = rv["score"]
                                            monitored.review_stance = rv["stance"]
                                            monitored.review_note = (
                                                rv.get("verdict", "")
                                                + (
                                                    f" | 风险: {rv['risk']}"
                                                    if rv.get("risk")
                                                    else ""
                                                )
                                            )[:500]
                                            ws.add(
                                                StockReview(
                                                    monitored_stock_id=monitored.id,
                                                    analysis_result_id=analysis.id,
                                                    stock_code=stock_code,
                                                    stock_name=stock_name,
                                                    blogger_view=reason,
                                                    score=rv["score"],
                                                    stance=rv["stance"],
                                                    verdict=rv.get("verdict", ""),
                                                    risk=rv.get("risk"),
                                                    tech_snapshot=rv.get("tech_snapshot"),
                                                    reasoning=rv.get("reasoning"),
                                                    llm_model=rv.get("llm_model"),
                                                    llm_provider=rv.get("llm_provider"),
                                                )
                                            )
                                            # 附到 sm 供邮件展示
                                            sm["review"] = {
                                                "score": rv["score"],
                                                "stance": rv["stance"],
                                                "verdict": rv.get("verdict", ""),
                                                "risk": rv.get("risk", ""),
                                            }
                                            logger.info(
                                                f"  🔍 复核 {stock_name}({stock_code}): "
                                                f"{rv['score']}/5 {rv['stance']} - {rv.get('verdict', '')[:30]}"
                                            )
                                        except Exception as e:
                                            logger.warning(f"复核 {stock_code} 失败: {e}")

                                await ws.commit()
                                # 无论内容是否首次入库，都让原子队列处理幂等：成功分析可以把尚未
                                # 投递的降级原文升级为完整版，已投递的记录则不会再发第二封。
                                try:
                                    from stockagent.notification.email_format import (
                                        standard_subject,
                                    )
                                    from stockagent.notification.email_notifier import (
                                        EmailNotifier,
                                        email_policy_enabled,
                                    )

                                    wcnt = 0
                                    async with maker() as ws2:
                                        wcnt = len(
                                            (
                                                await ws2.execute(
                                                    select(MonitoredStock).where(
                                                        MonitoredStock.monitor_date == mon_date,
                                                        MonitoredStock.status == MonitorStatus.PENDING,
                                                    )
                                                )
                                            )
                                            .scalars()
                                            .all()
                                        )
                                    notifier = EmailNotifier()
                                    blogger_overview = await _build_blogger_overview(maker)
                                    # 构建新内容提醒邮件（博主动态原文 + AI 复核结果）
                                    subject = standard_subject(
                                        "博主动态",
                                        now_cst().strftime("%Y-%m-%d %H:%M"),
                                        blogger.name,
                                    )
                                    body = _build_new_content_email(
                                        blogger_name=blogger.name,
                                        title=content.title,
                                        url=content.url,
                                        stock_mentions=parsed["stock_mentions"],
                                        analysis_dir=parsed.get("direction", ""),
                                        watch_count=wcnt,
                                        text=content.text,
                                        blogger_user_id=blogger.platform_user_id,
                                        metadata=content.metadata,
                                        blogger_overview=blogger_overview,
                                    )
                                    from stockagent.core.base_notifier import NotificationMessage

                                    msg = NotificationMessage(
                                        type="daily_advice",
                                        recipient=_first_configured_recipient(notifier),
                                        subject=subject,
                                        body=body,
                                        metadata={"blogger": blogger.name, "url": content.url},
                                    )
                                    success = await notifier.queue_and_send(
                                        msg,
                                        related_type="raw_content",
                                        related_id=raw_id,
                                        replace_pending=True,
                                    )
                                    if success and email_policy_enabled("new_content", True):
                                        stats["emails_sent"] = stats.get("emails_sent", 0) + 1
                                        logger.info(f"  📧 新内容提醒已入队: {content.title[:30]}")
                                    elif success:
                                        logger.info("  邮件策略已合并即时动态至定时汇总")
                                    else:
                                        stats["emails_failed"] = stats.get("emails_failed", 0) + 1
                                        logger.error(
                                            "  ❌ 新内容邮件入队失败: blogger=%s, title=%s",
                                            blogger.name,
                                            content.title[:40],
                                        )
                                except Exception as e:
                                    stats["emails_failed"] = stats.get("emails_failed", 0) + 1
                                    logger.error(
                                        "新内容邮件失败(已分析%s条): blogger=%s, title=%s, error=%s",
                                        stats["contents_analyzed"],
                                        blogger.name,
                                        content.title[:40],
                                        e,
                                    )
                                    try:
                                        await EmailNotifier().send_health_alert(
                                            component="新内容提醒邮件",
                                            error=str(e)[:200],
                                            detail=f"博主: {blogger.name}, 标题: {content.title[:50]}",
                                        )
                                    except Exception:
                                        pass
                                stats["contents_analyzed"] += 1
                                logger.info(
                                    f"  ✓ 分析完成: {content.title[:30]} → "
                                    f"{len(parsed['stock_mentions'])} 只股票"
                                )

                            except Exception as e:
                                logger.error(f"分析内容失败 '{content.title[:30]}': {e}")
                                await ws.rollback()
                                stats["errors"] += 1

                    # 6. 更新博主抓取时间
                    blogger.last_scraped_at = now_cst()

                except Exception as e:
                    logger.error(f"处理博主 {blogger.name} 失败: {e}")
                    stats["errors"] += 1

            await session.commit()

        stats["bloggers_processed"] = stats.get("bloggers_processed", 0) or len(bloggers)

        # 每轮汇总：抓取/过滤/分析/邮件
        logger.info("========== 分析流水线完成 ==========")
        logger.info(
            f"  抓取: {stats['contents_scraped']}条 → "
            f"过滤保留: {stats['contents_useful']}条(过滤{stats.get('contents_filtered', 0)}条) → "
            f"AI分析: {stats['contents_analyzed']}条 → "
            f"错误: {stats['errors']}条"
        )
        return stats

    async def _load_bloggers(
        self, session: AsyncSession, blogger_ids: list[str] | None
    ) -> list[Blogger]:
        """加载需要处理的博主。"""
        query = select(Blogger).where(Blogger.enabled)
        if blogger_ids:
            query = query.where(Blogger.id.in_(blogger_ids))
        result = await session.execute(query)
        bloggers = result.scalars().all()
        return list(bloggers)

    async def _store_raw_content(
        self,
        session: AsyncSession,
        blogger_id: str,
        content,
        filter_result=None,
    ) -> RawContent:
        """存储原始抓取内容（如已存在则跳过）。"""
        # 去重检查
        stmt = select(RawContent).where(
            RawContent.blogger_id == blogger_id,
            RawContent.content_id == content.content_id,
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            logger.debug(f"内容已存在，跳过: {content.content_id}")
            return existing, False

        from stockagent.core.enums import ContentType

        # 映射过滤分类到枚举
        filter_category = None
        if filter_result and filter_result.category:
            category_map = {
                "prediction": ContentCategory.PREDICTION,
                "recommendation": ContentCategory.RECOMMENDATION,
                "analysis": ContentCategory.ANALYSIS,
                "non_investment": ContentCategory.NON_INVESTMENT,
                # 兼容旧分类
                "entertainment": ContentCategory.NON_INVESTMENT,
                "advertisement": ContentCategory.NON_INVESTMENT,
                "other": ContentCategory.NON_INVESTMENT,
            }
            filter_category = category_map.get(
                filter_result.category, ContentCategory.NON_INVESTMENT
            )

        raw = RawContent(
            blogger_id=blogger_id,
            content_id=content.content_id,
            title=content.title,
            text=content.text,
            url=content.url,
            content_type=ContentType(content.content_type)
            if content.content_type in ContentType.__members__.values()
            else ContentType.UNKNOWN,
            published_at=content.published_at,
            extra_data=content.metadata,
            analysis_status=AnalysisStatus.PENDING,
            filter_status=FilterStatus.USEFUL
            if filter_result and filter_result.is_useful
            else FilterStatus.PENDING,
            filter_reason=filter_result.reason if filter_result else None,
            filter_category=filter_category,
            scraped_at=now_cst(),
        )
        session.add(raw)
        await session.flush()
        return raw, True


# 便捷函数
async def run_pipeline(blogger_ids: list[str] | None = None) -> dict[str, Any]:
    """执行一次分析流水线。"""
    pipeline = AnalysisPipeline()
    return await pipeline.run(blogger_ids)
