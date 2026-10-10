"""重试工具 —— 基于 tenacity 的便捷封装。"""

import logging
from typing import Any, Callable, TypeVar

from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from stockagent.core.exceptions import (
    LLMError,
    LLMTimeoutError,
    MarketDataUnavailableError,
    ScraperError,
)

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def async_retry_scraper(max_retries: int = 3, base_delay: float = 5.0, max_delay: float = 60.0):
    """爬虫专用重试装饰器 —— 指数退避。"""
    return retry(
        stop=stop_after_attempt(max_retries),
        wait=wait_exponential(multiplier=1, min=base_delay, max=max_delay),
        retry=retry_if_exception_type(ScraperError),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )


def async_retry_llm(max_retries: int = 2, base_delay: float = 2.0, max_delay: float = 10.0):
    """LLM 调用重试装饰器。"""
    return retry(
        stop=stop_after_attempt(max_retries),
        wait=wait_exponential(multiplier=1, min=base_delay, max=max_delay),
        retry=retry_if_exception_type((LLMError, LLMTimeoutError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )


def async_retry_market(max_retries: int = 2, base_delay: float = 1.0, max_delay: float = 5.0):
    """行情数据重试装饰器。"""
    return retry(
        stop=stop_after_attempt(max_retries),
        wait=wait_exponential(multiplier=1, min=base_delay, max=max_delay),
        retry=retry_if_exception_type(MarketDataUnavailableError),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
