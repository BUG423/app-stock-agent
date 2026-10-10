"""测试B站爬虫功能 - 图文和视频抓取"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from stockagent.core.config import config
from stockagent.scrapers.bilibili.scraper import BilibiliScraper


async def test_scraper():
    """测试爬虫功能"""
    # 加载配置
    config.load()
    
    # 创建爬虫实例
    scraper = BilibiliScraper()
    
    print("=" * 60)
    print("B站爬虫功能测试")
    print("=" * 60)
    print(f"视频抓取开关: {scraper._fetch_videos}")
    print(f"ASR转写开关: {scraper._transcribe_videos}")
    print(f"Cookie配置: {'已配置' if scraper._cookies_str else '未配置'}")
    print()
    
    # 测试用户ID（需要替换为实际的B站用户ID）
    test_user_id = "12345678"  # 示例ID，需要替换
    
    print(f"测试用户ID: {test_user_id}")
    print("开始抓取...")
    print()
    
    try:
        # 抓取用户内容
        contents = await scraper.scrape_user_posts(
            user_id=test_user_id,
            max_items=5
        )
        
        print(f"抓取到 {len(contents)} 条内容")
        print()
        
        # 分类统计
        articles = [c for c in contents if c.content_type == "article"]
        videos = [c for c in contents if c.content_type == "video"]
        
        print(f"图文内容: {len(articles)} 条")
        print(f"视频内容: {len(videos)} 条")
        print()
        
        # 显示图文内容示例
        if articles:
            print("=" * 40)
            print("图文内容示例:")
            print("=" * 40)
            for i, article in enumerate(articles[:2], 1):
                print(f"\n{i}. {article.title}")
                print(f"   URL: {article.url}")
                print(f"   发布时间: {article.published_at}")
                print(f"   内容预览: {article.text[:200]}...")
        
        # 显示视频内容示例
        if videos:
            print("\n" + "=" * 40)
            print("视频内容示例:")
            print("=" * 40)
            for i, video in enumerate(videos[:2], 1):
                print(f"\n{i}. {video.title}")
                print(f"   URL: {video.url}")
                print(f"   发布时间: {video.published_at}")
                print(f"   ASR转写: {'已完成' if video.metadata.get('has_asr') else '未完成'}")
                print(f"   内容预览: {video.text[:200]}...")
        
        print("\n" + "=" * 60)
        print("测试完成!")
        print("=" * 60)
        
    except Exception as e:
        print(f"测试失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await scraper.close()


if __name__ == "__main__":
    asyncio.run(test_scraper())
