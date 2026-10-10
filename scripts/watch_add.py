"""用户自选盯盘 —— 手动添加股票进盯盘队列(第三信号源: 用户自选)。

用法:
  python3 scripts/watch_add.py 东方锆业            # 默认条件: 回踩5日线企稳(低吸)
  python3 scripts/watch_add.py 002167 ma20        # 指定回踩MA20
  python3 scripts/watch_add.py 东方锆业 none       # 只关注不设买入条件(不会触发买入)

流程: 解析代码 → AI复核当前技术面 → 入库(用户手工关注来源) → 今日起盯盘 → 确认邮件。
"""
import asyncio
import sys

from stockagent.core.config import config
config.load()

from sqlalchemy import select
from stockagent.core.db import get_sessionmaker
from stockagent.core.enums import (AnalysisStatus, ConfidenceLevel, ContentType,
                                   Direction, MonitorStatus, TimeHorizon)
from stockagent.core.models import AnalysisResult, Blogger, MonitoredStock, RawContent, StockReview
from stockagent.market.stock_client import get_stock_client
from stockagent.market.trading_calendar import is_trading_time
from stockagent.utils.time_utils import is_trading_day, next_trading_day, now_cst, today_cst

USER_BLOGGER = ("用户自选", "user", "user_watchlist")


async def get_or_create_user_blogger(s):
    b = (await s.execute(select(Blogger).where(Blogger.platform == "user"))).scalar_one_or_none()
    if b:
        return b
    b = Blogger(name=USER_BLOGGER[0], platform=USER_BLOGGER[1], platform_user_id=USER_BLOGGER[2],
                profile_url="", enabled=True, process_mode="advice",
                notes="用户手动添加的自选盯盘股票")
    s.add(b)
    await s.flush()
    return b


def build_conditions(kind: str, tech: dict):
    if kind == "none":
        return []
    if kind == "ma20":
        n, ma = 20, tech.get("ma20", 0)
    else:
        n, ma = 5, tech.get("ma5", 0)
    return [{
        "condition_type": "support_bounce", "operator": "eq", "value": float(n), "unit": "日线",
        "description": f"回踩{n}日线({ma})分时企稳",
        "plain_description": f"股价回落到{n}日均价({ma})附近且盘中止跌企稳时提醒买入",
    }]


async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    query = sys.argv[1]
    cond_kind = (sys.argv[2] if len(sys.argv) > 2 else "ma5").lower()

    client = get_stock_client()
    # 解析代码
    if query.isdigit() and len(query) == 6:
        code = query
        name = query
    else:
        code = client.resolve_stock_code(query)
        name = query
        if not code:
            print(f"❌ 无法解析股票: {query}")
            return
    quote = client.get_realtime_quote(code) or {}
    if quote.get("stock_name"):
        name = quote["stock_name"]
    px = quote.get("current_price", 0)
    tech = client.get_technical_indicators(code) or {}
    print(f"标的: {name}({code}) 现价{px} MA5={tech.get('ma5')} MA20={tech.get('ma20')}")

    # AI复核当前技术面
    from stockagent.analysis.reviewer import review_stock
    rv = await review_stock(code=code, name=name,
                            blogger_view="用户自选关注，请独立评估当前技术面与短线机会",
                            direction="bullish")
    print(f"AI复核: {rv['score']}/5 {rv['stance']} - {rv.get('verdict','')}")

    conds = build_conditions(cond_kind, tech)
    mon_date = today_cst() if (is_trading_day(today_cst()) and is_trading_time()) else next_trading_day(today_cst())

    maker = await get_sessionmaker()
    async with maker() as s:
        b = await get_or_create_user_blogger(s)
        raw = RawContent(
            blogger_id=b.id, content_id=f"user_{code}_{now_cst().strftime('%Y%m%d%H%M%S')}",
            title=f"用户自选: {name}({code})", text=f"用户手动添加盯盘 {name}({code})，条件: {cond_kind}",
            url="", content_type=ContentType.POST, published_at=now_cst(),
            analysis_status=AnalysisStatus.COMPLETED, scraped_at=now_cst(),
        )
        s.add(raw); await s.flush()
        ar = AnalysisResult(
            raw_content_id=raw.id, blogger_id=b.id,
            summary=f"用户自选盯盘 {name}({code})，AI复核{rv['score']}/5({rv['stance']}): {rv.get('verdict','')}",
            stock_mentions=[{"code": code, "name": name, "direction": "bullish",
                             "signal_type": "conditional", "reason": "用户自选", "conditions": conds}],
            conditions=[], time_horizon=TimeHorizon.NEXT_WEEK, confidence=ConfidenceLevel.MEDIUM,
            market_sentiment="neutral", direction=Direction.BULLISH,
            llm_model="user-watchlist", llm_provider="user",
        )
        s.add(ar); await s.flush()
        ms = MonitoredStock(
            analysis_result_id=ar.id, stock_code=code, stock_name=name,
            direction=Direction.BULLISH, reason=f"用户自选 (AI复核{rv['score']}/5)",
            signal_type="conditional", trigger_conditions=conds,
            monitor_date=mon_date, status=MonitorStatus.PENDING,
            review_score=rv["score"], review_stance=rv["stance"],
            review_note=(rv.get("verdict", "") + (f" | 风险: {rv['risk']}" if rv.get("risk") else ""))[:500],
        )
        s.add(ms); await s.flush()
        s.add(StockReview(
            monitored_stock_id=ms.id, analysis_result_id=ar.id, stock_code=code, stock_name=name,
            blogger_view="用户自选", score=rv["score"], stance=rv["stance"],
            verdict=rv.get("verdict", ""), risk=rv.get("risk"),
            tech_snapshot=rv.get("tech_snapshot"), reasoning=rv.get("reasoning"),
            llm_model=rv.get("llm_model"), llm_provider=rv.get("llm_provider"),
        ))
        await s.commit()
        print(f"✅ 已加入盯盘: {name}({code}) 盯盘日={mon_date} 条件={cond_kind}")

    # 确认邮件
    try:
        from stockagent.notification.email_notifier import EmailNotifier
        await EmailNotifier().send_new_content_alert(
            blogger_name="用户自选", title=f"已开始盯盘: {name}({code})",
            stocks_detail=[{"name": name, "code": code, "direction": "bullish",
                            "signal_type": "conditional", "reason": "用户手动添加",
                            "review": {"score": rv["score"], "stance": rv["stance"],
                                       "verdict": rv.get("verdict", ""), "risk": rv.get("risk", "")}}],
            conditions=conds, watching=1,
            raw_text=f"现价{px} MA5={tech.get('ma5')} MA20={tech.get('ma20')}\nAI复核: {rv.get('verdict','')}",
            url="")
        print("📧 确认邮件已发送")
    except Exception as e:
        print(f"邮件发送失败: {e}")


asyncio.run(main())
