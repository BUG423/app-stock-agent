"""Pytest 配置和共享 fixtures。"""

import asyncio
import os

import pytest


@pytest.fixture(scope="session")
def event_loop():
    """创建事件循环供异步测试使用。"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def setup_env():
    """设置测试环境变量。"""
    os.environ.setdefault("APP_SECRET_KEY", "test-secret")
    os.environ.setdefault("DB_HOST", "localhost")
    os.environ.setdefault("DB_PORT", "5432")
    os.environ.setdefault("DB_USER", "stockagent_test")
    os.environ.setdefault("DB_PASS", "test")
    os.environ.setdefault("DB_NAME", "stockagent_test")
    os.environ.setdefault("QWEN_API_KEY", "test-key")
    os.environ.setdefault("SMTP_SERVER", "")
    os.environ.setdefault("SMTP_PORT", "465")
    os.environ.setdefault("SMTP_USERNAME", "test@example.com")
    os.environ.setdefault("SMTP_PASSWORD", "test")
    os.environ.setdefault("USER_EMAIL", "test@example.com")
    os.environ.setdefault("WEB_USERNAME", "admin")
    os.environ.setdefault("WEB_PASSWORD", "admin")
    os.environ.setdefault("SESSION_SECRET", "test-session-secret")
