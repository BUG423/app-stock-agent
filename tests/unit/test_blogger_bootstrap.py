"""配置内真人博主自动写库测试。"""

import pytest

from stockagent.core import db
from stockagent.core.config import config


class _Rows:
    def all(self):
        return []


class _Session:
    def __init__(self):
        self.added = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, _query):
        return _Rows()

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_configured_real_bloggers_include_all_three_uids(monkeypatch):
    if not config._loaded:
        config.load()
    session = _Session()

    async def fake_sessionmaker():
        return lambda: session

    monkeypatch.setattr(db, "get_sessionmaker", fake_sessionmaker)

    added = await db.sync_configured_bloggers()

    assert added == 2
    assert session.committed is True
    assert {blogger.platform_user_id for blogger in session.added} == {
        "21119247",
        "1856426471",
    }
    target = next(
        blogger for blogger in session.added if blogger.platform_user_id == "1856426471"
    )
    assert target.name == "小火怪神"
    assert target.profile_url == "https://space.bilibili.com/1856426471/dynamic"
    assert target.enabled is True
