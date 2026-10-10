"""条件匹配引擎 —— 盘中监控股票，匹配博主预设条件。"""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stockagent.core.db import get_sessionmaker
from stockagent.core.enums import Direction, MonitorStatus, SignalStatus, TradeAction
from stockagent.core.models import MonitoredStock, TradeSignal
from stockagent.engine.rules import AbstractRule
from stockagent.market.stock_client import get_stock_client
from stockagent.market.trading_calendar import is_trading_time
from stockagent.utils.time_utils import now_cst, today_cst

logger = logging.getLogger(__name__)


class ConditionMatchingEngine:
    """盘中条件匹配引擎 —— 每5分钟检查一次。"""

    def __init__(self) -> None:
        self.market_client = get_stock_client()

    async def run(self) -> dict:
        """执行一次条件匹配检查。

        Returns:
            检查结果统计
        """
        if not is_trading_time():
            logger.info("当前非交易时间，跳过条件匹配")
            return {"checked": 0, "triggered": 0, "skipped": 0, "message": "非交易时间"}

        today = today_cst()
        maker = await get_sessionmaker()
        stats = {"checked": 0, "triggered": 0, "skipped": 0}

        async with maker() as session:
            # 加载今日待监控的股票
            stmt = select(MonitoredStock).where(
                MonitoredStock.monitor_date == today,
                MonitoredStock.status == MonitorStatus.PENDING,
            )
            result = await session.execute(stmt)
            monitored_list = result.scalars().all()

            if not monitored_list:
                logger.info(f"今日 ({today}) 无待监控股票")
                return stats

            logger.info(f"开始检查 {len(monitored_list)} 只待监控股票...")
            stats["checked"] = len(monitored_list)

            for monitored in monitored_list:
                try:
                    triggered = await self._check_single(session, monitored)
                    if triggered:
                        stats["triggered"] += 1
                except Exception as e:
                    logger.error(f"检查股票 {monitored.stock_code} 失败: {e}")
                    stats["skipped"] += 1

            await session.commit()

        logger.info(f"条件匹配完成: {stats}")
        return stats

    async def _check_single(
        self,
        session: AsyncSession,
        monitored: MonitoredStock,
    ) -> bool:
        """检查单只股票的条件是否触发。"""
        # 获取实时行情（同步调用，放到线程池）
        quote = await asyncio.to_thread(self.market_client.get_realtime_quote, monitored.stock_code)
        if not quote:
            logger.debug(f"无法获取 {monitored.stock_code} 行情，跳过")
            return False

        # 获取技术指标（均线、支撑位、压力位、企稳判断）
        tech = await asyncio.to_thread(
            self.market_client.get_technical_indicators, monitored.stock_code
        )

        # 合并行情 + 技术指标为 market_data
        market_data = {
            "stock_code": monitored.stock_code,
            "current_price": quote.get("current_price", 0),
            "prev_close": quote.get("prev_close", 0),
            "open": quote.get("open", 0),
            "high": quote.get("high", 0),
            "low": quote.get("low", 0),
            "volume": quote.get("volume", 0),
            "change_pct": quote.get("change_pct", 0),
            "avg_volume_20d": 0,
        }
        # 注入技术指标
        if tech:
            market_data.update(
                {
                    "ma5": tech.get("ma5", 0),
                    "ma10": tech.get("ma10", 0),
                    "ma20": tech.get("ma20", 0),
                    "ma60": tech.get("ma60", 0),
                    "support": tech.get("support", 0),
                    "resistance": tech.get("resistance", 0),
                    "is_stable": tech.get("is_stable", False),
                    "volume_surge": tech.get("volume_surge", False),
                }
            )
        else:
            # fallback: 用旧方式获取均量
            try:
                avg_vol = await asyncio.to_thread(
                    self.market_client.get_avg_volume, monitored.stock_code, 20
                )
                market_data["avg_volume_20d"] = avg_vol
            except Exception:
                pass

        # 解析并评估所有规则
        conditions = monitored.trigger_conditions or []
        if not conditions:
            logger.debug(f"{monitored.stock_code} 无触发条件")
            return False

        all_triggered = True
        triggered_conditions = []

        for cond_dict in conditions:
            try:
                rule = AbstractRule.from_dict(cond_dict)
                if await rule.evaluate(market_data):
                    triggered_conditions.append(cond_dict)
                else:
                    all_triggered = False
            except Exception as e:
                logger.warning(f"评估规则失败: {e}")
                all_triggered = False

        # 所有条件都满足 → 生成提醒信号
        if all_triggered and triggered_conditions:
            logger.info(f"🎯 条件触发! {monitored.stock_code} {monitored.stock_name}")

            action = self._determine_action(monitored.direction)
            current_price = quote.get("current_price", 0)

            # 计算建议买入区间：VWAP ~ 触发价+0.5%
            vwap = tech.get("vwap", 0) if tech else 0
            if vwap > 0 and current_price > 0:
                low = round(min(vwap, current_price) * 0.998, 2)
                high = round(max(vwap, current_price) * 1.005, 2)
            else:
                low = round(current_price * 0.995, 2) if current_price > 0 else None
                high = round(current_price * 1.005, 2) if current_price > 0 else None

            # 创建交易信号
            signal = TradeSignal(
                monitored_stock_id=monitored.id,
                action=action,
                stock_code=monitored.stock_code,
                stock_name=monitored.stock_name,
                suggested_price_min=low,
                suggested_price_max=high,
                current_price=current_price,
                conditions_met=triggered_conditions,
                status=SignalStatus.PENDING,
                triggered_at=now_cst(),
            )
            session.add(signal)
            monitored.status = MonitorStatus.TRIGGERED
            monitored.triggered_at = now_cst()
            await session.flush()

            # 发送通知
            try:
                from stockagent.notification.email_notifier import EmailNotifier

                notifier = EmailNotifier()
                await notifier.send_signal_alert(signal, monitored)
            except Exception as e:
                logger.error(f"发送信号通知失败: {e}")

            return True

        return False

    def _determine_action(self, direction: Direction) -> TradeAction:
        """根据方向确定交易动作。"""
        if direction == Direction.BULLISH:
            return TradeAction.BUY
        elif direction == Direction.BEARISH:
            return TradeAction.SELL
        else:
            return TradeAction.HOLD
