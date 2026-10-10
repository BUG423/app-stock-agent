"""B站 (bilibili) 爬虫 —— 抓取用户空间视频和图文内容。"""

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timedelta

import httpx

from stockagent.core.base_scraper import AbstractScraper, ScrapedContent
from stockagent.core.config import config
from stockagent.core.exceptions import ScraperError, ScraperRateLimitError
from stockagent.analysis.ocr_text import compact_ocr_text
from stockagent.llm.local_asr import transcribe_video
from stockagent.llm.ocr import ocr_bilibili_video

logger = logging.getLogger(__name__)


def _safe_get_module(it: dict, key: str, default=None):
    """兼容 B站 modules 新旧格式: 旧=dick, 新=list[dick].

    B站 2026-07 起逐步把动态 API 返回的 modules 从 {key: val}
    改为 [{key: val}, ...] 格式。旧代码 .get() 在 list 上抛
    AttributeError 导致整个动态被静默跳过——这是漏帖的根因。
    """
    modules = it.get("modules")
    if modules is None:
        return default
    if isinstance(modules, list):
        for m in modules:
            if isinstance(m, dict) and key in m:
                return m[key]
        return default
    if isinstance(modules, dict):
        return modules.get(key, default)
    return default


# B站 API 端点
BILIBILI_SPACE_VIDEOS = "https://api.bilibili.com/x/space/wbi/arc/search"
BILIBILI_SPACE_ARTICLES = "https://api.bilibili.com/x/space/article"
BILIBILI_VIDEO_INFO = "https://api.bilibili.com/x/web-interface/view"
BILIBILI_DYNAMIC = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
BILIBILI_USER_CARD = "https://api.bilibili.com/x/web-interface/card"
BILIBILI_VIDEO_SEARCH = "https://api.bilibili.com/x/web-interface/search/type"
_PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def _browser_json_request(
    url: str,
    params: dict[str, str],
    *,
    bypass_proxy: bool,
) -> dict:
    """用真实浏览器 TLS 指纹访问公开接口，避开 B 站搜索验证码页。"""
    from curl_cffi import requests as browser_requests

    with browser_requests.Session(
        impersonate="chrome",
        trust_env=not bypass_proxy,
    ) as session:
        response = session.get(
            url,
            params=params,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": "https://search.bilibili.com/",
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict):
        raise ScraperError("B站公开接口返回格式异常")
    return data


def _list_space_bvids(user_id: str, limit: int, *, bypass_proxy: bool) -> list[str]:
    """使用 yt-dlp 的 WBI 空间提取器获取近期 BV 号。"""
    env = os.environ.copy()
    if bypass_proxy:
        for key in _PROXY_ENV_KEYS:
            env.pop(key, None)
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--quiet",
        "--no-warnings",
        "--flat-playlist",
        "--playlist-end",
        str(max(1, limit)),
        "--dump-single-json",
        f"https://space.bilibili.com/{user_id}/video",
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "空间列表为空")[-300:].strip()
        raise ScraperError(f"yt-dlp 空间列表失败: {detail}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ScraperError("yt-dlp 空间列表返回格式异常") from exc
    entries = payload.get("entries") or []
    return list(
        dict.fromkeys(
            str(entry.get("id") or "").strip()
            for entry in entries
            if isinstance(entry, dict) and str(entry.get("id") or "").startswith("BV")
        )
    )


def _strip_html(s: str) -> str:
    """去除 B站标题里的 <em> 高亮标签。"""
    return re.sub(r"<[^>]+>", "", s or "")


def _parse_duration_text(dur_text: str) -> int:
    """把动态接口的时长文本 '02:35' / '1:02:35' 转成秒数(用于判断是否值得ASR)。"""
    if not dur_text or ":" not in dur_text:
        return 0
    try:
        parts = [int(p) for p in dur_text.strip().split(":")]
    except (ValueError, AttributeError):
        return 0
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return 0


BILIBILI_VIDEO_PAGE_LIST = "https://api.bilibili.com/x/player/pagelist"

# 混元 WBI 签名相关密钥（B站 API 风控）
WBI_MIXIN_KEY_ENC_TAB = [
    46,
    47,
    18,
    2,
    53,
    8,
    23,
    32,
    15,
    50,
    10,
    31,
    58,
    3,
    45,
    35,
    27,
    43,
    5,
    49,
    33,
    9,
    42,
    19,
    29,
    28,
    14,
    39,
    12,
    38,
    41,
    13,
    37,
    48,
    7,
    16,
    24,
    55,
    40,
    61,
    26,
    17,
    0,
    1,
    60,
    51,
    30,
    4,
    22,
    25,
    54,
    21,
    56,
    59,
    6,
    63,
    57,
    62,
    11,
    36,
    20,
    52,
    44,
    34,
]


def _get_mixin_key(orig: str) -> str:
    """对 img_key 和 sub_key 进行字符顺序重组获得 mixin_key。"""
    return "".join(orig[i] for i in WBI_MIXIN_KEY_ENC_TAB if i < len(orig))


def _wbi_sign(params: dict[str, str], img_key: str, sub_key: str) -> dict[str, str]:
    """为请求参数添加 WBI 签名（w_rid 和 wts）。"""
    mixin_key = _get_mixin_key(img_key + sub_key)
    params["wts"] = str(int(time.time()))
    # 按 key 排序并拼接
    sorted_params = sorted(params.items())
    query_string = urllib.parse.urlencode(sorted_params)
    w_rid = hashlib.md5((query_string + mixin_key).encode()).hexdigest()
    params["w_rid"] = w_rid
    return params


class BilibiliScraper(AbstractScraper):
    """B站爬虫 —— 抓取用户空间的视频和图文列表。"""

    platform = "bilibili"

    def __init__(self) -> None:
        scraper_cfg = config.get("scrapers", default={})
        bili_cfg = scraper_cfg.get("bilibili", {})
        self._cookies_str: str = bili_cfg.get("cookies", "")
        # UA 轮换池（防封禁：每次请求随机选取）
        self._user_agents: list[str] = scraper_cfg.get(
            "user_agents",
            [
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            ],
        )
        self._request_delay: float = scraper_cfg.get("request_delay_seconds", 2.0)
        self._request_jitter_max: float = scraper_cfg.get("request_jitter_max", 3.0)
        # 防封禁配置
        anti_ban = bili_cfg.get("anti_ban", {})
        self._max_requests_per_minute: int = anti_ban.get("max_requests_per_minute", 15)
        self._backoff_on_412: int = anti_ban.get("backoff_on_412", 60)
        # 速率限制状态
        self._request_times: list[float] = []  # 最近请求时间戳
        # 视频抓取开关
        self._fetch_videos: bool = bili_cfg.get("fetch_videos", False)
        self._fetch_article_full_text: bool = bili_cfg.get("fetch_article_full_text", False)
        self._transcribe_videos: bool = bili_cfg.get("transcribe_videos", True)
        self._bypass_proxy: bool = bili_cfg.get("bypass_proxy", True)
        video_ocr_cfg = bili_cfg.get("video_ocr", {})
        self._video_ocr_enabled: bool = video_ocr_cfg.get("enabled", False)
        self._video_ocr_user_ids: set[str] = {
            str(user_id) for user_id in video_ocr_cfg.get("user_ids", [])
        }
        self._client: httpx.AsyncClient | None = None
        # Referer 轮换池
        self._referers: list[str] = [
            "https://www.bilibili.com",
            "https://space.bilibili.com",
            "https://t.bilibili.com",
        ]
        # WBI 密钥缓存
        self._wbi_img_key: str = ""
        self._wbi_sub_key: str = ""

    def _random_delay(self) -> float:
        """返回基延迟 + 随机抖动（防封禁：使请求间隔不可预测）。"""
        return self._request_delay + random.uniform(0.5, self._request_jitter_max)

    async def _rate_limit_wait(self) -> None:
        """速率限制：每分钟不超过 max_requests_per_minute 次请求。"""
        now = time.time()
        # 清理 60 秒前的记录
        self._request_times = [t for t in self._request_times if now - t < 60]
        if len(self._request_times) >= self._max_requests_per_minute:
            wait = self._request_times[0] + 60 - now + random.uniform(0.5, 2.0)
            if wait > 0:
                logger.debug(f"B站速率限制: 等待 {wait:.1f}s")
                await asyncio.sleep(wait)
                now = time.time()
                self._request_times = [t for t in self._request_times if now - t < 60]
        self._request_times.append(now)

    def _random_ua(self) -> str:
        """随机选取 User-Agent（防封禁）。"""
        return random.choice(self._user_agents)

    def _random_referer(self) -> str:
        """随机选取 Referer（防封禁）。"""
        return random.choice(self._referers)

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                trust_env=not self._bypass_proxy,
            )
        # 每次访问动态更新 headers（UA/Referer 轮换）
        self._client.headers.update(
            {
                "User-Agent": self._random_ua(),
                "Referer": self._random_referer(),
            }
        )
        # 动态更新 cookies
        if self._cookies_str:
            for pair in self._cookies_str.split("; "):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    self._client.cookies.set(k, v)
        return self._client

    async def _fetch_wbi_keys(self) -> None:
        """获取 WBI 签名所需的 img_key 和 sub_key。"""
        if self._wbi_img_key and self._wbi_sub_key:
            return

        await self._rate_limit_wait()
        try:
            resp = await self.client.get("https://api.bilibili.com/x/web-interface/nav")
            data = resp.json()
            wbi_img = data.get("data", {}).get("wbi_img", {})
            img_url = wbi_img.get("img_url", "")
            sub_url = wbi_img.get("sub_url", "")

            if img_url:
                # 从 URL 中提取 key 文件名（去掉扩展名）
                self._wbi_img_key = img_url.rsplit("/", 1)[-1].split(".")[0]
            if sub_url:
                self._wbi_sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0]
        except Exception as e:
            logger.warning(f"获取 WBI 密钥失败: {e}")

    async def scrape_user_posts(
        self,
        user_id: str,
        since: datetime | None = None,
        max_items: int = 50,
    ) -> list[ScrapedContent]:
        """抓取 B站用户的所有动态（视频+图文+转发）。

        使用动态 API 作为主数据源，抓取所有类型的内容。
        """
        results: list[ScrapedContent] = []

        # 1. 通过动态 API 抓取所有动态（主数据源）
        if self._cookies_str:
            try:
                dynamics = await self._scrape_videos_via_dynamic(user_id, max_items, since=since)
                results.extend(dynamics)
            except Exception as e:
                logger.error(f"抓取 B站动态失败 (uid={user_id}): {e}")
            if not results and self._fetch_videos:
                try:
                    results.extend(await self._scrape_videos_via_space_playlist(user_id, max_items))
                except Exception as e:
                    logger.error(f"B站空间列表兜底失败 (uid={user_id}): {e}")
            if not results and self._fetch_videos:
                try:
                    results.extend(await self._scrape_videos_via_search(user_id, max_items))
                except Exception as e:
                    logger.error(f"B站公开搜索兜底失败 (uid={user_id}): {e}")
        else:
            # 无 Cookie 时尝试 WBI 视频列表，再降级到专栏 API。
            logger.warning("无 B站 Cookie，改用 WBI 视频列表和专栏接口")
            if self._fetch_videos:
                try:
                    videos = await self._scrape_videos(user_id, max_items)
                    results.extend(videos)
                except Exception as e:
                    logger.error(f"抓取 B站视频失败 (uid={user_id}): {e}")
                if not results:
                    try:
                        results.extend(
                            await self._scrape_videos_via_space_playlist(user_id, max_items)
                        )
                    except Exception as e:
                        logger.error(f"B站空间列表兜底失败 (uid={user_id}): {e}")
                if not results:
                    try:
                        results.extend(await self._scrape_videos_via_search(user_id, max_items))
                    except Exception as e:
                        logger.error(f"B站公开搜索兜底失败 (uid={user_id}): {e}")
            try:
                articles = await self._scrape_articles(user_id, max_items)
                results.extend(articles)
            except Exception as e:
                logger.error(f"抓取 B站图文失败 (uid={user_id}): {e}")

        # 2. 时间过滤由 pipeline 层通过 content_id 去重处理，此处不过滤
        # 爬虫负责抓取所有内容，pipeline 负责去重

        # 3. 限制数量，按时间倒序
        results.sort(key=lambda r: r.published_at or datetime.min, reverse=True)
        return results[:max_items]

    def _is_recent_for_media_enrichment(self, published_at: datetime | None) -> bool:
        """OCR/ASR 只处理实时窗口内内容，避免首次抓取时下载几十条旧视频。"""
        if published_at is None:
            return True
        cutoff: datetime | None = None
        max_age_days = config.get("scrapers", "max_content_age_days", default=2)
        if max_age_days and max_age_days > 0:
            cutoff = datetime.now() - timedelta(days=float(max_age_days))
        min_time_str = config.get("scrapers", "min_content_time", default=None)
        if min_time_str:
            try:
                min_time = datetime.fromisoformat(str(min_time_str).strip().replace("/", "-"))
                if cutoff is None or min_time > cutoff:
                    cutoff = min_time
            except ValueError:
                pass
        value = published_at.replace(tzinfo=None) if published_at.tzinfo else published_at
        return cutoff is None or value >= cutoff

    async def _enrich_video_text(
        self,
        *,
        user_id: str,
        bvid: str,
        title: str,
        description: str,
        cover_url: str,
        duration_seconds: int,
        published_at: datetime | None,
        base_text: str | None = None,
    ) -> tuple[str, dict]:
        """本地转写近期视频；指定 UID 和无语音视频同时叠加画面 OCR。"""
        text_content = (base_text or f"{title}\n{description}").strip()
        enrichment = {"has_asr": False, "has_ocr": False}
        is_local_ocr_user = bool(
            getattr(self, "_video_ocr_enabled", False)
            and str(user_id) in getattr(self, "_video_ocr_user_ids", set())
        )
        is_recent = self._is_recent_for_media_enrichment(published_at)

        async def append_local_ocr(fallback_reason: str = "") -> None:
            nonlocal text_content
            try:
                result = await ocr_bilibili_video(
                    bvid=bvid,
                    cookie_string=self._cookies_str,
                    cover_url=cover_url,
                )
                ocr_text = compact_ocr_text(result.get("text", ""))
                if result.get("success") and ocr_text.strip():
                    text_content = (
                        f"{text_content}\n\n"
                        "【视频画面OCR｜只认完整可见文字，遮挡内容不得猜测】\n"
                        f"{ocr_text}"
                    ).strip()
                    enrichment.update(
                        {
                            "has_ocr": True,
                            "ocr_stock_codes": result.get("stock_codes", []),
                            "ocr_frame_count": result.get("frame_count", 0),
                            "ocr_cache_hit": result.get("cache_hit", False),
                        }
                    )
                    if fallback_reason:
                        enrichment["ocr_fallback_reason"] = fallback_reason
                    logger.info("视频本地 OCR 成功: %s - %s字", bvid, len(ocr_text))
                else:
                    logger.warning(
                        "视频本地 OCR 未识别到文字 %s: %s", bvid, result.get("error", "")
                    )
            except Exception as e:
                logger.warning(f"视频本地 OCR 失败 {bvid}: {e}")

        if not is_recent:
            return text_content, enrichment
        if is_local_ocr_user:
            await append_local_ocr()

        should_asr = self._transcribe_videos and bvid and is_recent
        if should_asr:
            try:
                asr_result = await transcribe_video(bvid, self._cookies_str)
                asr_text = str(asr_result.get("text", "")).strip()
                if asr_text:
                    text_content = f"{text_content}\n\n【视频语音内容】\n{asr_text}".strip()
                    enrichment.update(
                        {
                            "has_asr": True,
                            "asr_source": asr_result.get("source", "sensevoice_local"),
                            "asr_model": asr_result.get("model_id", ""),
                            "asr_segment_count": asr_result.get("segment_count", 0),
                            "asr_cache_hit": asr_result.get("cache_hit", False),
                            "asr_duration_seconds": asr_result.get("duration_seconds", 0),
                            "asr_elapsed_seconds": asr_result.get("elapsed_seconds", 0),
                        }
                    )
                    logger.info("视频本地 ASR 转写成功: %s - %s字", bvid, len(asr_text))
                elif asr_result.get("error"):
                    enrichment["asr_error"] = str(asr_result["error"])[:300]
                    error_code = str(asr_result.get("error_code", ""))
                    if error_code:
                        enrichment["asr_error_code"] = error_code
                    logger.warning("视频本地 ASR 无结果 %s: %s", bvid, asr_result["error"])
                    if (
                        not is_local_ocr_user
                        and getattr(self, "_video_ocr_enabled", False)
                        and error_code in {"no_audio_stream", "no_speech"}
                    ):
                        await append_local_ocr(error_code)
            except Exception as e:
                logger.warning(f"视频本地 ASR 转写失败 {bvid}: {e}")
        return text_content, enrichment

    async def _scrape_videos_via_space_playlist(
        self, user_id: str, max_items: int
    ) -> list[ScrapedContent]:
        """用 yt-dlp 列空间 BV 号，再由公开详情接口补齐并严格校验作者。"""
        list_limit = min(max(5, max_items), 20)
        bvids = await self._space_bvids(user_id, list_limit)
        results: list[ScrapedContent] = []
        for bvid in bvids:
            await self._rate_limit_wait()
            try:
                response = await self.client.get(BILIBILI_VIDEO_INFO, params={"bvid": bvid})
                payload = response.json()
            except Exception as exc:
                logger.warning("B站视频详情失败 %s: %s", bvid, exc)
                continue
            if payload.get("code") != 0:
                logger.warning("B站视频详情接口错误 %s: %s", bvid, payload.get("message"))
                continue
            video = payload.get("data") or {}
            owner_mid = str((video.get("owner") or {}).get("mid") or "")
            if owner_mid != str(user_id):
                logger.warning("B站空间列表作者不匹配，丢弃 %s: %s", bvid, owner_mid)
                continue
            title = str(video.get("title") or "")
            description = str(video.get("desc") or "")
            dynamic_text = str(video.get("dynamic") or "")
            pub_timestamp = video.get("pubdate") or 0
            try:
                published_at = datetime.fromtimestamp(int(pub_timestamp))
            except (TypeError, ValueError, OSError):
                published_at = None
            cover_url = str(video.get("pic") or "")
            if cover_url.startswith("http://"):
                cover_url = "https://" + cover_url.removeprefix("http://")
            duration_seconds = int(video.get("duration") or 0)
            text_content, enrichment = await self._enrich_video_text(
                user_id=user_id,
                bvid=bvid,
                title=title,
                description=description,
                cover_url=cover_url,
                duration_seconds=duration_seconds,
                published_at=published_at,
                base_text=f"{title}\n{description}\n{dynamic_text}".strip(),
            )
            results.append(
                ScrapedContent(
                    platform=self.platform,
                    blogger_id=0,
                    content_id=bvid,
                    title=title,
                    text=text_content,
                    url=f"https://www.bilibili.com/video/{bvid}",
                    content_type="video",
                    published_at=published_at,
                    metadata={
                        "bvid": bvid,
                        "aid": video.get("aid"),
                        "play_count": (video.get("stat") or {}).get("view"),
                        "duration": duration_seconds,
                        "cover": cover_url,
                        "source": "yt_dlp_space_fallback",
                        **enrichment,
                    },
                )
            )
            if len(results) >= max_items:
                break
        return results

    async def _space_bvids(self, user_id: str, limit: int) -> list[str]:
        """在线程中运行 yt-dlp，避免阻塞调度器事件循环。"""
        return await asyncio.to_thread(
            _list_space_bvids,
            user_id,
            limit,
            bypass_proxy=self._bypass_proxy,
        )

    async def _scrape_videos_via_search(self, user_id: str, max_items: int) -> list[ScrapedContent]:
        """无 Cookie/WBI 受限时，用公开搜索接口抓视频并按 mid 严格验作者。"""
        await self._rate_limit_wait()
        card_data = await self._public_json_request(BILIBILI_USER_CARD, {"mid": user_id})
        if card_data.get("code") != 0:
            raise ScraperError(f"用户卡片接口错误: {card_data.get('message', '未知错误')}")
        user_name = str(card_data.get("data", {}).get("card", {}).get("name") or "").strip()
        if not user_name:
            raise ScraperError("用户卡片未返回昵称")

        results: list[ScrapedContent] = []
        seen_bvids: set[str] = set()
        for page in range(1, 4):
            await self._rate_limit_wait()
            try:
                data = await self._public_json_request(
                    BILIBILI_VIDEO_SEARCH,
                    {
                        "search_type": "video",
                        "keyword": user_name,
                        "order": "pubdate",
                        "page": str(page),
                    },
                )
            except Exception:
                if results:
                    logger.warning("B站搜索第%s页失败，保留前页 %s 条结果", page, len(results))
                    return results
                raise
            if data.get("code") != 0:
                raise ScraperError(f"视频搜索接口错误: {data.get('message', '未知错误')}")
            rows = data.get("data", {}).get("result") or []
            for video in rows:
                if str(video.get("mid") or "") != str(user_id):
                    continue
                bvid = str(video.get("bvid") or "").strip()
                if not bvid or bvid in seen_bvids:
                    continue
                seen_bvids.add(bvid)
                title = _strip_html(video.get("title", ""))
                description = _strip_html(video.get("description", "") or "")
                pub_timestamp = video.get("pubdate") or video.get("senddate") or 0
                try:
                    published_at = datetime.fromtimestamp(int(pub_timestamp))
                except (TypeError, ValueError, OSError):
                    published_at = None
                duration_text = str(video.get("duration") or "")
                cover_url = str(video.get("pic") or "")
                if cover_url.startswith("//"):
                    cover_url = "https:" + cover_url
                text_content, enrichment = await self._enrich_video_text(
                    user_id=user_id,
                    bvid=bvid,
                    title=title,
                    description=description,
                    cover_url=cover_url,
                    duration_seconds=_parse_duration_text(duration_text),
                    published_at=published_at,
                )
                results.append(
                    ScrapedContent(
                        platform=self.platform,
                        blogger_id=0,
                        content_id=bvid,
                        title=title,
                        text=text_content,
                        url=f"https://www.bilibili.com/video/{bvid}",
                        content_type="video",
                        published_at=published_at,
                        metadata={
                            "bvid": bvid,
                            "aid": video.get("aid"),
                            "play_count": video.get("play"),
                            "duration": duration_text,
                            "cover": cover_url,
                            "source": "public_search_fallback",
                            **enrichment,
                        },
                    )
                )
                if len(results) >= max_items:
                    return results
            if not rows:
                break
        return results

    async def _public_json_request(self, url: str, params: dict[str, str]) -> dict:
        """在线程中执行浏览器指纹请求，避免阻塞调度器事件循环。"""
        return await asyncio.to_thread(
            _browser_json_request,
            url,
            params,
            bypass_proxy=self._bypass_proxy,
        )

    async def _scrape_videos_via_dynamic(
        self, user_id: str, max_items: int, since: datetime | None = None
    ) -> list[ScrapedContent]:
        """动态 API 抓视频（带 cookie，不需 wbi 签名，比 wbi 视频列表更稳）。

        走 /x/polymer/web-dynamic/v1/feed/space，翻页直至 max_items。
        妖板选手等账号 wbi 视频列表返回 -401，但动态接口可用。
        """
        results: list[ScrapedContent] = []
        offset = ""
        skipped_summary: dict[str, int] = {}
        while len(results) < max_items:
            await self._rate_limit_wait()
            params = {"host_mid": user_id}
            if offset:
                params["offset"] = offset
            resp = await self.client.get(BILIBILI_DYNAMIC, params=params)
            data = resp.json()
            if data.get("code") != 0:
                logger.warning(
                    f"[B站动态API异常] code={data.get('code')} msg={data.get('message')} "
                    f"(uid={user_id}) 可能Cookie失效或触发风控,会导致漏抓,请检查 BILIBILI_COOKIES"
                )
                break
            d = data.get("data", {})
            items = d.get("items", [])
            if not items:
                break
            for it in items:
                # 空值健壮化: 部分动态(转发/直播/删除)的 module_dynamic 或 major 可能为 None
                _md = _safe_get_module(it, "module_dynamic", {})
                major = _md.get("major") or {}
                mtype = major.get("type")
                # ---- B站 2026-07 新格式: module_content 替代 module_dynamic ----
                # 部分新发图文动态没有 module_dynamic, 直接用 module_content.paragraphs 存文字.
                # 此时 mtype 为 None, 但内容就在 module_content 里——必须抓取.
                if mtype is None:
                    mc = _safe_get_module(it, "module_content", {})
                    if mc.get("paragraphs"):
                        mtype = "MAJOR_TYPE_OPUS"  # 伪装成 OPUS, 走统一的正文提取
                # ----------------------------------------------------------------
                # 图文动态(DRAW/OPUS): 抓取正文文本, 让公开给票的图文进入系统
                pub_ts_check = (_safe_get_module(it, "module_author") or {}).get("pub_ts")
                try:
                    pub_at_check = (
                        datetime.fromtimestamp(int(pub_ts_check)) if pub_ts_check else None
                    )
                except (TypeError, ValueError):
                    pub_at_check = None
                # 不再根据时间停止翻页，让 pipeline 通过 content_id 去重

                if mtype in ("MAJOR_TYPE_DRAW", "MAJOR_TYPE_OPUS"):
                    sc = self._extract_dynamic_text(it, major)
                    if not sc:
                        # 空间 feed 经常只给 DRAW 空壳（major.draw.items=[] 且无 desc），
                        # 正文仅存在 detail.item.module_dynamic.desc，major 甚至为 null。
                        # 旧逻辑只在详情含图片时回填 draw，导致这种纯文字图文永久漏抓。
                        # 必须用完整 detail_item 重新解析，让 desc / draw / opus 任一路径均可生效。
                        try:
                            await self._rate_limit_wait()
                            detail_resp = await self.client.get(
                                "https://api.bilibili.com/x/polymer/web-dynamic/v1/detail",
                                params={"id": it.get("id_str", "")},
                            )
                            detail_data = detail_resp.json()
                            if detail_data.get("code") == 0:
                                detail_item = detail_data.get("data", {}).get("item", {})
                                detail_md = _safe_get_module(detail_item, "module_dynamic", {})
                                detail_major = detail_md.get("major") or {}
                                sc = self._extract_dynamic_text(detail_item, detail_major)
                            else:
                                logger.warning(
                                    f"B站动态详情异常: id={it.get('id_str')} "
                                    f"code={detail_data.get('code')} msg={detail_data.get('message')}"
                                )
                        except Exception as e:
                            logger.warning(f"获取动态详情失败 id={it.get('id_str')}: {e}")
                    if sc:
                        results.append(sc)
                    continue
                # 专栏文章动态(ARTICLE): 博主盘前预告/复盘常发成B站专栏, article.desc
                # 已含完整预告(个股+条件)。此前 mtype 不匹配→整类被丢弃(跳跳虎每晚盘前预告漏抓根因)。
                if mtype == "MAJOR_TYPE_ARTICLE":
                    art = major.get("article") or {}
                    a_id = str(art.get("id") or "")
                    a_title = _strip_html(art.get("title", ""))
                    a_desc = _strip_html(art.get("desc", "") or "")
                    a_text = f"{a_title}\n{a_desc}".strip()
                    if a_text:
                        a_jump = art.get("jump_url") or (
                            f"//www.bilibili.com/read/cv{a_id}/" if a_id else ""
                        )
                        a_url = ("https:" + a_jump) if a_jump.startswith("//") else a_jump
                        results.append(
                            ScrapedContent(
                                platform=self.platform,
                                blogger_id=0,
                                content_id=(f"cv{a_id}" if a_id else (it.get("id_str") or "")),
                                title=a_title or "专栏文章",
                                text=a_text,
                                url=a_url,
                                content_type="article",
                                published_at=pub_at_check,
                                metadata={
                                    "dyn_id": it.get("id_str"),
                                    "cvid": a_id,
                                    "images": [c for c in (art.get("covers") or []) if c],
                                    "dyn_type": mtype,
                                },
                            )
                        )
                        if len(results) >= max_items:
                            break
                    continue
                if mtype != "MAJOR_TYPE_ARCHIVE":
                    # 未被 DRAW/OPUS/ARTICLE/ARCHIVE 任何分支处理的动态。转发尽力抓博主评论,
                    # 其余记入 skipped_summary 并告警——把"静默漏抓"变可见(今天专栏漏抓即因缺此告警)。
                    top_type = it.get("type")
                    if top_type == "DYNAMIC_TYPE_FORWARD":
                        fwd_text = _strip_html((_md.get("desc") or {}).get("text") or "")
                        if fwd_text:
                            fwd_id = it.get("id_str") or ""
                            results.append(
                                ScrapedContent(
                                    platform=self.platform,
                                    blogger_id=0,
                                    content_id=(f"fwd{fwd_id}" if fwd_id else fwd_text[:24]),
                                    title=(fwd_text.split(chr(10), 1)[0][:40] or "转发动态"),
                                    text=fwd_text,
                                    url=(f"https://t.bilibili.com/{fwd_id}" if fwd_id else ""),
                                    content_type="article",
                                    published_at=pub_at_check,
                                    metadata={"dyn_id": fwd_id, "dyn_type": top_type},
                                )
                            )
                            if len(results) >= max_items:
                                break
                            continue
                    key = f"{top_type}|{mtype}"
                    skipped_summary[key] = skipped_summary.get(key, 0) + 1
                    logger.warning(
                        f"[漏抓告警] 未处理的动态类型 top={top_type} major={mtype} "
                        f"pub={pub_at_check} id={it.get('id_str')} 需在爬虫补该类型解析"
                    )
                    continue
                a = major.get("archive", {})
                bvid = a.get("bvid", "")
                if not bvid:
                    continue
                title = _strip_html(a.get("title", ""))
                desc = a.get("desc", "") or ""
                pub_ts = (_safe_get_module(it, "module_author", {}) or {}).get("pub_ts")
                try:
                    pub_at = datetime.fromtimestamp(int(pub_ts)) if pub_ts else None
                except (TypeError, ValueError):
                    pub_at = None
                stat = a.get("stat", {}) or {}

                # 注意：不再用 since 跳过视频——content_id 去重在 pipeline 层处理，
                # since 跳过会导致漏抓(如视频发布后 since 已更新，永久丢失)

                duration_sec = _parse_duration_text(a.get("duration_text", ""))
                cover_url = a.get("cover", "") or ""
                text_content, enrichment = await self._enrich_video_text(
                    user_id=user_id,
                    bvid=bvid,
                    title=title,
                    description=desc,
                    cover_url=cover_url,
                    duration_seconds=duration_sec,
                    published_at=pub_at,
                )

                results.append(
                    ScrapedContent(
                        platform=self.platform,
                        blogger_id=0,
                        content_id=bvid,
                        title=title,
                        text=text_content,
                        url=f"https://www.bilibili.com/video/{bvid}",
                        content_type="video",
                        published_at=pub_at,
                        metadata={
                            "bvid": bvid,
                            "play_count": stat.get("view"),
                            "danmaku": stat.get("danmaku"),
                            "duration": a.get("duration_text"),
                            "cover": cover_url,
                            **enrichment,
                        },
                    )
                )
                if len(results) >= max_items:
                    break
            # time boundary check removed - dedup by content_id instead
            offset = d.get("offset", "")
            if not offset:
                break
            await asyncio.sleep(self._random_delay())
        if skipped_summary:
            logger.warning(
                f"[漏抓告警] 本轮跳过未处理动态类型 (uid={user_id}): {skipped_summary} "
                f"若含博主实际发文类型即为漏抓,需在爬虫补对应解析"
            )
            try:
                from stockagent.core.skip_tracker import record_skips

                record_skips(user_id, skipped_summary)
            except Exception:
                pass
        return results

    def _extract_dynamic_text(self, it: dict, major: dict):
        """从图文动态(DRAW/OPUS)抽取正文文本, 图片URL存入metadata(供未来OCR)。

        纯图片动态(如二维码引流广告)无文本→存占位, 交由内容过滤剔除。
        """
        dyn_id = it.get("id_str") or ""
        if not dyn_id:
            return None
        md = _safe_get_module(it, "module_dynamic", {})
        parts: list[str] = []
        desc = md.get("desc") or {}
        if desc.get("text"):
            parts.append(desc["text"])
        op = major.get("opus") or {}
        if op.get("title"):
            parts.append(op["title"])
        summ = op.get("summary") or {}
        for node in summ.get("rich_text_nodes") or []:
            if node.get("text"):
                parts.append(node["text"])
        draw = major.get("draw") or {}
        pics = [p.get("src") for p in (draw.get("items") or []) if p.get("src")]
        pics += [p.get("url") for p in (op.get("pics") or []) if p.get("url")]

        text = _strip_html("\n".join(parts).strip())

        # ---- B站 2026-07 新格式: module_content.paragraphs ----
        # 新 API 把正文放在 module_content 而非 module_dynamic.major.opus 中。
        # 如果旧路径没取到文字, 尝试从 paragraphs 中提取。
        if not text:
            mc = _safe_get_module(it, "module_content", {})
            paragraphs = mc.get("paragraphs", [])
            para_texts = []
            for para in paragraphs:
                for node in para.get("text", {}).get("nodes") or []:
                    word = node.get("word", {})
                    words = word.get("words", "")
                    if words:
                        para_texts.append(words)
            if para_texts:
                text = "\n".join(para_texts)
                logger.info(f"从 module_content.paragraphs 提取文字: {len(text)} 字符")
        # -------------------------------------------------------

        if not text and not pics:
            return None
        pub_ts = (_safe_get_module(it, "module_author", {}) or {}).get("pub_ts")
        try:
            pub_at = datetime.fromtimestamp(int(pub_ts)) if pub_ts else None
        except (TypeError, ValueError):
            pub_at = None
        title = text.split("\n", 1)[0][:40] if text else "图文动态(仅图片)"
        return ScrapedContent(
            platform=self.platform,
            blogger_id=0,
            content_id=dyn_id,
            title=title,
            text=text or "[图文动态-仅图片, 无文字]",
            url=f"https://t.bilibili.com/{dyn_id}",
            content_type="article",
            published_at=pub_at,
            metadata={"dyn_id": dyn_id, "images": pics, "dyn_type": major.get("type")},
        )

    async def _scrape_videos(self, user_id: str, max_items: int) -> list[ScrapedContent]:
        """抓取用户视频列表。"""
        results: list[ScrapedContent] = []
        await self._fetch_wbi_keys()

        params: dict[str, str] = {
            "mid": user_id,
            "ps": str(min(max_items, 50)),
            "pn": "1",
            "order": "pubdate",
        }

        if self._wbi_img_key and self._wbi_sub_key:
            params = _wbi_sign(params, self._wbi_img_key, self._wbi_sub_key)

        resp = await self.client.get(BILIBILI_SPACE_VIDEOS, params=params)
        data = resp.json()

        if data.get("code") != 0:
            msg = data.get("message", "未知错误")
            if data.get("code") == -412:
                backoff = self._backoff_on_412 + random.uniform(0, 30)
                logger.warning(f"B站风控 -412，退避 {backoff:.0f}s: {msg}")
                await asyncio.sleep(backoff)
                raise ScraperRateLimitError(f"B站请求被限流(已退避{backoff:.0f}s): {msg}")
            raise ScraperError(f"B站API返回错误: {msg}")

        video_list = data.get("data", {}).get("list", {}).get("vlist", [])

        for video in video_list:
            bvid = video.get("bvid", "")
            title = video.get("title", "")
            description = video.get("description", "")
            created = video.get("created", 0)
            pub_time = datetime.fromtimestamp(created) if created else None
            video_url = f"https://www.bilibili.com/video/{bvid}"

            # 获取视频详细信息（简介页面文本）
            full_text = f"{title}\n{description}"
            if bvid:
                try:
                    detail_text = await self._get_video_detail(bvid)
                    full_text = f"{title}\n{detail_text}"
                except Exception as e:
                    logger.debug(f"获取视频详情失败 {bvid}: {e}")

            duration_text = video.get("length", "") or ""
            cover_url = video.get("pic", "") or ""
            full_text, enrichment = await self._enrich_video_text(
                user_id=user_id,
                bvid=bvid,
                title=title,
                description=description,
                cover_url=cover_url,
                duration_seconds=_parse_duration_text(duration_text),
                published_at=pub_time,
                base_text=full_text,
            )

            results.append(
                ScrapedContent(
                    platform=self.platform,
                    blogger_id=0,  # 由调用方填充
                    content_id=bvid or str(created),
                    title=title,
                    text=full_text,
                    url=video_url,
                    content_type="video",
                    published_at=pub_time,
                    metadata={
                        "bvid": bvid,
                        "aid": video.get("aid"),
                        "play_count": video.get("play"),
                        "comment_count": video.get("comment"),
                        "length": duration_text,
                        "cover": cover_url,
                        **enrichment,
                    },
                )
            )

        return results

    async def _get_video_detail(self, bvid: str) -> str:
        """获取视频的详细描述文本。"""
        resp = await self.client.get(BILIBILI_VIDEO_INFO, params={"bvid": bvid})
        data = resp.json()
        if data.get("code") != 0:
            return ""

        video_data = data.get("data", {})
        desc = video_data.get("desc", "")
        dynamic = video_data.get("dynamic", "")
        return f"{desc}\n{dynamic}".strip()

    async def _scrape_articles(self, user_id: str, max_items: int) -> list[ScrapedContent]:
        """抓取用户专栏/图文列表。

        优先使用卡片接口返回的 summary 作为正文 —— B站专栏的 summary 字段
        通常已包含博主完整内容（如盘前分析、投资建议），无需再逐条调
        article/view，更稳定且省请求。如需完整 HTML 正文，可开启
        fetch_article_full_text 配置。
        """
        results: list[ScrapedContent] = []

        await self._rate_limit_wait()
        resp = await self.client.get(
            BILIBILI_SPACE_ARTICLES,
            params={
                "mid": user_id,
                "ps": str(min(max_items, 30)),
                "pn": "1",
            },
        )
        data = resp.json()

        if data.get("code") != 0:
            logger.warning(f"B站图文API返回错误: {data.get('message')}")
            return results

        articles = data.get("data", {}).get("articles", [])

        for article in articles:
            article_id = str(article.get("id", ""))
            title = article.get("title", "")
            summary = article.get("summary", "")  # 卡片摘要，通常即完整内容
            publish_time = article.get("publish_time", 0)
            pub_time = datetime.fromtimestamp(publish_time) if publish_time else None
            article_url = f"https://www.bilibili.com/read/cv{article_id}"

            # 正文默认用 summary（完整、稳定）
            full_text = f"{title}\n{summary}".strip()

            # 可选：获取完整 HTML 正文（默认关闭）
            if article_id and self._fetch_article_full_text:
                try:
                    detail = await self._get_article_content(article_id, title)
                    if detail:
                        full_text = detail
                except Exception as e:
                    logger.debug(f"获取文章全文失败 cv{article_id}: {e}（使用 summary）")

            results.append(
                ScrapedContent(
                    platform=self.platform,
                    blogger_id=0,
                    content_id=f"cv{article_id}",
                    title=title,
                    text=full_text,
                    url=article_url,
                    content_type="article",
                    published_at=pub_time,
                    metadata={
                        "article_id": article_id,
                        "view_count": article.get("stats", {}).get("view", 0),
                        "comment_count": article.get("stats", {}).get("reply", 0),
                        "summary_length": len(summary),
                    },
                )
            )

        return results

    async def _get_article_content(self, article_id: str, title: str) -> str:
        """获取专栏文章完整 HTML 正文并转为纯文本（可选增强，失败时返回空）。"""
        resp = await self.client.get(
            "https://api.bilibili.com/x/article/view",
            params={"id": article_id},
        )
        try:
            data = resp.json()
        except Exception:
            return ""

        if data.get("code") != 0:
            return ""

        article_data = data.get("data", {})
        summary = article_data.get("summary", "")
        # B站专栏内容是 HTML，需要简单处理去除标签
        content_html = article_data.get("content", "")
        # 简单去除 HTML 标签
        import re

        content_text = re.sub(r"<[^>]+>", "", content_html)
        content_text = re.sub(r"\s+", " ", content_text).strip()

        full = f"{title}\n{summary}\n{content_text}"
        return full[:5000]  # 限制长度，避免 token 过多

    async def scrape_single_post(self, url: str) -> ScrapedContent | None:
        """按 URL 抓取单条 B站内容。"""
        import re

        # 匹配视频 URL: https://www.bilibili.com/video/BV...
        video_match = re.match(r".*/video/(BV[\w]+)", url)
        if video_match:
            bvid = video_match.group(1)
            resp = await self.client.get(BILIBILI_VIDEO_INFO, params={"bvid": bvid})
            data = resp.json()
            if data.get("code") != 0:
                raise ScraperError(f"获取视频信息失败: {data.get('message')}")
            video = data.get("data", {})
            return ScrapedContent(
                platform=self.platform,
                blogger_id=0,
                content_id=bvid,
                title=video.get("title", ""),
                text=f"{video.get('title', '')}\n{video.get('desc', '')}",
                url=url,
                content_type="video",
                published_at=datetime.fromtimestamp(video.get("pubdate", 0))
                if video.get("pubdate")
                else None,
                metadata={"bvid": bvid},
            )

        # 匹配专栏 URL: https://www.bilibili.com/read/cv...
        article_match = re.match(r".*/read/cv(\d+)", url)
        if article_match:
            article_id = article_match.group(1)
            resp = await self.client.get(
                "https://api.bilibili.com/x/article/view",
                params={"id": article_id},
            )
            data = resp.json()
            if data.get("code") != 0:
                raise ScraperError(f"获取文章失败: {data.get('message')}")
            article = data.get("data", {})
            return ScrapedContent(
                platform=self.platform,
                blogger_id=0,
                content_id=f"cv{article_id}",
                title=article.get("title", ""),
                text=article.get("summary", "")[:5000],
                url=url,
                content_type="article",
                published_at=datetime.fromtimestamp(article.get("publish_time", 0))
                if article.get("publish_time")
                else None,
                metadata={"article_id": article_id},
            )

        raise ScraperError(f"不支持的 B站 URL 格式: {url}")

    async def validate_credentials(self) -> bool:
        """检查 Cookie 是否有效 —— 尝试访问用户信息接口。"""
        await self._rate_limit_wait()
        try:
            resp = await self.client.get("https://api.bilibili.com/x/web-interface/nav")
            data = resp.json()
            is_login = data.get("data", {}).get("isLogin", False)
            return is_login
        except Exception:
            return False

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aexit__(self, *args):
        await self.close()
