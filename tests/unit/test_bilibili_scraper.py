"""B站动态结构兼容回归测试。"""

from types import SimpleNamespace

import pytest

from stockagent.core.base_scraper import ScrapedContent
from stockagent.scrapers.bilibili import scraper as scraper_module
from stockagent.scrapers.bilibili.scraper import BilibiliScraper


def _parser_only_scraper() -> BilibiliScraper:
    """解析纯函数测试不需要加载配置或创建 HTTP 客户端。"""
    return BilibiliScraper.__new__(BilibiliScraper)


def test_extract_detail_desc_when_major_is_null():
    """空间 feed 为空壳时，详情里的纯文字 desc 仍应成为图文内容。"""
    item = {
        "id_str": "1225477710032142339",
        "modules": {
            "module_author": {"pub_ts": 1784167164},
            "module_dynamic": {
                "desc": {"text": "7月16日上升回档战法备选：浪潮信息、埃斯顿、风语筑。"},
                "major": None,
            },
        },
    }

    content = _parser_only_scraper()._extract_dynamic_text(item, {})

    assert content is not None
    assert content.content_id == "1225477710032142339"
    assert content.content_type == "article"
    assert "浪潮信息" in content.text
    assert content.url.endswith("/1225477710032142339")


def test_extract_detail_desc_with_list_modules():
    """B站灰度中的 list modules 格式也必须支持。"""
    item = {
        "id_str": "new-format-id",
        "modules": [
            {"module_author": {"pub_ts": 1784165043}},
            {
                "module_dynamic": {
                    "desc": {"text": "今日围绕指数震荡，科技方向不追涨。"},
                    "major": None,
                }
            },
        ],
    }

    content = _parser_only_scraper()._extract_dynamic_text(item, {})

    assert content is not None
    assert "科技方向不追涨" in content.text


@pytest.mark.asyncio
async def test_public_search_fallback_strictly_filters_by_mid():
    scraper = _parser_only_scraper()
    scraper._user_agents = ["test-agent"]
    scraper._referers = ["https://space.bilibili.com"]
    scraper._cookies_str = ""

    async def no_wait():
        return None

    async def fake_enrichment(**kwargs):
        assert kwargs["user_id"] == "3707010912618582"
        assert kwargs["duration_seconds"] == 12
        return "实盘记录\n【视频画面OCR】\n持仓内容", {"has_ocr": True}

    async def fake_public_request(url, params):
        if url.endswith("/card"):
            return {"code": 0, "data": {"card": {"name": "提前炒股早退休"}}}
        assert url.endswith("/search/type")
        assert params["order"] == "pubdate"
        return {
            "code": 0,
            "data": {
                "result": [
                    {"mid": 1, "bvid": "BV-other", "title": "同名搜索噪声"},
                    {
                        "mid": 3707010912618582,
                        "bvid": "BV-target",
                        "title": "<em>10个</em>，实盘记录",
                        "description": "",
                        "duration": "0:12",
                        "pubdate": 1784253600,
                        "pic": "//i0.hdslb.com/cover.jpg",
                    },
                ]
            },
        }

    scraper._rate_limit_wait = no_wait
    scraper._enrich_video_text = fake_enrichment
    scraper._public_json_request = fake_public_request

    rows = await scraper._scrape_videos_via_search("3707010912618582", max_items=2)

    assert len(rows) == 1
    assert isinstance(rows[0], ScrapedContent)
    assert rows[0].content_id == "BV-target"
    assert rows[0].title == "10个，实盘记录"
    assert rows[0].metadata["source"] == "public_search_fallback"
    assert rows[0].metadata["has_ocr"] is True


class _FakeVideoInfoResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeVideoInfoClient:
    def __init__(self):
        self.headers = {}
        self.cookies = SimpleNamespace(set=lambda *_args: None)

    async def get(self, url, params):
        assert url == scraper_module.BILIBILI_VIDEO_INFO
        assert params == {"bvid": "BV-latest"}
        return _FakeVideoInfoResponse(
            {
                "code": 0,
                "data": {
                    "bvid": "BV-latest",
                    "aid": 123,
                    "title": "7/17日",
                    "desc": "",
                    "dynamic": "",
                    "duration": 12,
                    "pubdate": 1784253647,
                    "pic": "http://i0.hdslb.com/cover.jpg",
                    "owner": {"mid": 3707010912618582},
                    "stat": {"view": 100},
                },
            }
        )


@pytest.mark.asyncio
async def test_ytdlp_space_fallback_enriches_video_and_checks_owner(monkeypatch):
    scraper = _parser_only_scraper()
    scraper._bypass_proxy = True
    scraper._client = _FakeVideoInfoClient()
    scraper._user_agents = ["test-agent"]
    scraper._referers = ["https://space.bilibili.com"]
    scraper._cookies_str = ""

    async def no_wait():
        return None

    async def fake_enrichment(**kwargs):
        assert kwargs["user_id"] == "3707010912618582"
        assert kwargs["duration_seconds"] == 12
        return "7/17日\n【视频画面OCR】\n持仓", {"has_ocr": True}

    async def fake_space_bvids(user_id, limit):
        assert user_id == "3707010912618582"
        assert limit == 5
        return ["BV-latest"]

    scraper._rate_limit_wait = no_wait
    scraper._enrich_video_text = fake_enrichment
    scraper._space_bvids = fake_space_bvids

    rows = await scraper._scrape_videos_via_space_playlist("3707010912618582", max_items=5)

    assert len(rows) == 1
    assert rows[0].content_id == "BV-latest"
    assert rows[0].metadata["source"] == "yt_dlp_space_fallback"
    assert rows[0].metadata["has_ocr"] is True
    assert rows[0].metadata["cover"].startswith("https://")
