"""自定义异常层次结构。"""


class StockAgentError(Exception):
    """StockAgent 基础异常。"""

    pass


class ConfigError(StockAgentError):
    """配置相关错误。"""

    pass


class DatabaseError(StockAgentError):
    """数据库相关错误。"""

    pass


class ScraperError(StockAgentError):
    """爬虫基础错误。"""

    pass


class ScraperAuthError(ScraperError):
    """爬虫认证失败（Cookie 过期等）。"""

    pass


class ScraperRateLimitError(ScraperError):
    """爬虫被限流。"""

    pass


class ScraperParseError(ScraperError):
    """爬虫解析 HTML/JSON 失败。"""

    pass


class LLMError(StockAgentError):
    """LLM 调用基础错误。"""

    pass


class LLMTimeoutError(LLMError):
    """LLM 调用超时。"""

    pass


class LLMAuthError(LLMError):
    """LLM API Key 无效。"""

    pass


class LLMParseError(LLMError):
    """LLM 响应解析失败。"""

    pass


class MarketDataError(StockAgentError):
    """行情数据错误。"""

    pass


class MarketDataUnavailableError(MarketDataError):
    """行情数据不可用（非交易时间等）。"""

    pass


class EngineError(StockAgentError):
    """引擎错误。"""

    pass


class ConditionParseError(EngineError):
    """条件解析失败。"""

    pass


class NotificationError(StockAgentError):
    """通知发送错误。"""

    pass
