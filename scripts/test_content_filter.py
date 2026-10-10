"""测试内容过滤功能"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from stockagent.core.config import config
from stockagent.analysis.content_filter import get_content_filter


async def test_content_filter():
    """测试内容过滤器"""
    config.load()

    content_filter = get_content_filter()

    print("=" * 60)
    print("内容过滤功能测试")
    print("=" * 60)
    print()

    # 测试用例1: 有投资价值的内容（明确推荐）
    test_case_1 = {
        "blogger_name": "测试博主",
        "title": "明日操作策略",
        "text": "明天大盘如果低开，可以考虑抄底茅台，目标价1800。另外彩虹股份回踩5日线企稳可以关注。",
        "published_at": "2025-01-01 20:00",
        "content_type": "article",
    }

    # 测试用例2: 无投资价值的内容（吐槽）
    test_case_2 = {
        "blogger_name": "测试博主",
        "title": "今天又亏了",
        "text": "今天大盘跌成狗，我的持仓全绿，心态崩了。这个市场真的没法玩了，垃圾。",
        "published_at": "2025-01-01 20:00",
        "content_type": "article",
    }

    # 测试用例3: 有投资价值的内容（条件预测）
    test_case_3 = {
        "blogger_name": "测试博主",
        "title": "明天关注这几个方向",
        "text": "明天如果科创板50指数站上1000点，可以关注半导体板块。另外如果茅台跌破1600，可以考虑抄底。",
        "published_at": "2025-01-01 20:00",
        "content_type": "video",
    }

    # 测试用例4: 无投资价值的内容（纯娱乐）
    test_case_4 = {
        "blogger_name": "测试博主",
        "title": "周末去哪玩",
        "text": "周末带孩子去了趟游乐园，过山车太刺激了，孩子玩得很开心。推荐大家带孩子去玩。",
        "published_at": "2025-01-01 20:00",
        "content_type": "article",
    }

    test_cases = [
        ("明确推荐", test_case_1),
        ("吐槽抱怨", test_case_2),
        ("条件预测", test_case_3),
        ("纯娱乐", test_case_4),
    ]

    for name, case in test_cases:
        print(f"测试: {name}")
        print(f"标题: {case['title']}")
        print(f"内容: {case['text'][:50]}...")

        result = await content_filter.filter_content(
            blogger_name=case["blogger_name"],
            title=case["title"],
            text=case["text"],
            published_at=case["published_at"],
            content_type=case["content_type"],
        )

        status = "✓ 保留" if result.is_useful else "✗ 过滤"
        print(f"结果: {status}")
        print(f"理由: {result.reason}")
        print(f"分类: {result.category}")
        print("-" * 60)

    print()
    print("=" * 60)
    print("测试完成!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_content_filter())
