"""盘中离场提醒：基于已触发信号观察破位，不维护纸上持仓或盈亏账本。"""

import asyncio
import logging
from datetime import date, timedelta

from sqlalchemy import select

from stockagent.core.config import config
from stockagent.core.db import get_sessionmaker
from stockagent.core.enums import MonitorStatus, SignalStatus
from stockagent.core.models import MonitoredStock
from stockagent.market.stock_client import get_stock_client
from stockagent.market.trading_calendar import is_trading_time
from stockagent.utils.time_utils import is_trading_day, today_cst

logger = logging.getLogger(__name__)


def yaoban_t1_reason(held: int, is_limit_up: bool, ret_pct: float) -> str | None:
    """妖板信号次日兑现纪律（仅生成提醒，不执行交易）。"""
    if held < 2 or is_limit_up:
        return None
    return f"妖板次日兑现: 已过T+1可卖日，尽早了结避免持有塌方({ret_pct:+.1f}%)"


def _trading_days_held(entry: date, today: date) -> int:
    if entry > today:
        return 0
    count, day = 0, entry
    while day <= today:
        if is_trading_day(day):
            count += 1
        day += timedelta(days=1)
    return count


class ExitMonitorEngine:
    """检查已触发的关注信号，满足离场条件时只发提醒并结束监控。"""

    def __init__(self) -> None:
        self.client = get_stock_client()
        cfg = config.get("engine", "exit", default={}) or {}
        self.take_profit = float(cfg.get("take_profit_pct", 0.05))
        self.stop_loss = float(cfg.get("stop_loss_pct", 0.08))
        self.max_hold_days = int(cfg.get("max_hold_days", 2))

    async def run(self) -> dict:
        if not is_trading_time():
            return {"checked": 0, "alerted": 0, "message": "非交易时间"}
        maker = await get_sessionmaker()
        stats = {"checked": 0, "alerted": 0}
        async with maker() as session:
            items = (
                (
                    await session.execute(
                        select(MonitoredStock).where(
                            MonitoredStock.status == MonitorStatus.TRIGGERED
                        )
                    )
                )
                .scalars()
                .all()
            )
            stats["checked"] = len(items)
            for item in items:
                if await self._check_one(session, item, today_cst()):
                    stats["alerted"] += 1
            await session.commit()
        return stats

    async def _check_one(self, session, item: MonitoredStock, today: date) -> bool:
        signal = item.trade_signal
        action = signal.user_action if signal else None
        if not signal or not action or not action.user_confirmed:
            return False
        entry_time = signal.confirmed_at or action.created_at
        if not entry_time:
            return False
        held = _trading_days_held(entry_time.date(), today)
        # A股T+1：确认买入当天不能卖，止盈/止损/破位也不得发送不可执行的卖出提醒。
        if held < 2:
            return False
        quote = await asyncio.to_thread(self.client.get_realtime_quote, item.stock_code)
        price = float((quote or {}).get("current_price") or 0)
        if price <= 0:
            return False
        entry = float(action.actual_price or signal.current_price or 0)
        if entry <= 0:
            return False
        ret_pct = (price / entry - 1) * 100
        reason = None
        if ret_pct >= self.take_profit * 100:
            reason = f"止盈 +{ret_pct:.1f}%"
        elif ret_pct <= -self.stop_loss * 100:
            reason = f"止损 {ret_pct:.1f}%"
        elif held >= self.max_hold_days:
            reason = f"信号已观察{held}个交易日，短线到期"
        else:
            tech = await asyncio.to_thread(self.client.get_technical_indicators, item.stock_code)
            ma5 = float((tech or {}).get("ma5") or 0)
            if ma5 and price < ma5 * 0.995:
                reason = f"跌破5日线({ma5:.2f})，破位先撤"
        if not reason:
            return False

        item.status = MonitorStatus.EXPIRED
        signal.status = SignalStatus.EXPIRED
        from stockagent.notification.email_notifier import EmailNotifier

        await EmailNotifier().send_sell_alert(
            stock_code=item.stock_code,
            stock_name=item.stock_name,
            entry_price=entry,
            current_price=price,
            quantity=0,
            pnl_pct=ret_pct,
            realized_pnl=0,
            reason=reason,
            held_days=held,
        )
        logger.info(f"离场提醒: {item.stock_code} {reason}")
        return True


async def run_exit_monitor() -> dict:
    return await ExitMonitorEngine().run()
