# 内容过滤功能说明

## 概述

内容过滤器是 StockAgent 数据处理流程中的关键环节，用于在AI分析之前过滤掉无投资价值的内容，提高后续处理效率并减少噪音。

## 过滤逻辑

### 有投资价值的内容（保留）

| 类型 | 说明 | 示例 |
|------|------|------|
| **recommendation** | 博主明确推荐买入/卖出 | "明天可以抄底茅台" |
| **prediction** | 博主给出条件预测 | "如果跌破1600就买入" |
| **analysis** | 市场/板块/个股分析 | "大盘目前处于震荡区间" |
| **other** | 其他有投资价值的内容 | 选股逻辑、操作策略等 |

### 无投资价值的内容（过滤）

| 类型 | 说明 | 示例 |
|------|------|------|
| **entertainment** | 吐槽、娱乐、日常分享 | "今天又亏了，心态崩了" |
| **advertisement** | 广告推广 | "推荐我的课程/社群" |
| **other** | 无意义内容 | 空洞内容、纯情绪宣泄 |

## 处理流程

```
原始内容
    ↓
LLM 内容过滤 (ContentFilter)
    ↓
┌─────────────────┬─────────────────┐
↓                 ↓                 ↓
有投资价值        无投资价值        过滤失败
(is_useful=True)  (is_useful=False) (异常)
    ↓                 ↓                 ↓
存储+分析          仅存储            默认保留
    ↓                 ↓                 ↓
提取投资建议      标记为已过滤      继续处理
```

## 数据库字段

`raw_contents` 表新增字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `filter_status` | Enum | 过滤状态：pending/useful/filtered |
| `filter_reason` | Text | 过滤理由（LLM返回） |
| `filter_category` | Enum | 内容分类：recommendation/prediction/analysis/entertainment/advertisement/other |

## 使用方法

### 1. 单条内容过滤

```python
from stockagent.core.config import config
from stockagent.analysis.content_filter import get_content_filter

config.load()
content_filter = get_content_filter()

result = await content_filter.filter_content(
    blogger_name="测试博主",
    title="明日操作策略",
    text="明天大盘如果低开，可以考虑抄底茅台",
    published_at="2025-01-01 20:00",
    content_type="article",
)

print(f"是否有价值: {result.is_useful}")
print(f"理由: {result.reason}")
print(f"分类: {result.category}")
```

### 2. 批量内容过滤

```python
contents = [
    {
        "blogger_name": "博主A",
        "title": "标题1",
        "text": "内容1",
        "published_at": "2025-01-01",
        "content_type": "article",
    },
    # ... 更多内容
]

results = await content_filter.batch_filter(contents, max_concurrent=3)

for content, result in zip(contents, results):
    print(f"{content['title']}: {'保留' if result.is_useful else '过滤'}")
```

### 3. 在分析流水线中使用

内容过滤已集成到 `AnalysisPipeline` 中，无需额外配置：

```python
from stockagent.analysis.pipeline import run_pipeline

# 自动执行：抓取 → 内容过滤 → LLM分析 → 存储
stats = await run_pipeline()
print(stats)
# 输出: {'contents_scraped': 10, 'contents_filtered': 3, 'contents_useful': 7, ...}
```

## 测试

运行测试脚本验证过滤功能：

```bash
python scripts/test_content_filter.py
```

## 配置

内容过滤使用默认的 LLM 提供商（配置在 `config/default.yaml` 的 `llm.providers` 中）。

### 过滤策略配置

可在 `config/default.yaml` 中调整过滤行为：

```yaml
analysis:
  content_filter:
    enabled: true
    # 过滤阈值（预留，当前使用LLM判断）
    confidence_threshold: 0.5
```

## 注意事项

1. **LLM调用成本**：每次过滤会调用一次LLM，批量过滤会并发调用
2. **过滤延迟**：单条过滤约1-2秒，批量过滤取决于并发数
3. **默认保留**：过滤异常时默认保留内容，避免遗漏有价值信息
4. **分类存储**：过滤结果会存储到数据库，便于后续统计分析

## 统计分析

可通过数据库查询过滤统计：

```sql
-- 过滤状态统计
SELECT filter_status, COUNT(*) 
FROM raw_contents 
GROUP BY filter_status;

-- 内容分类统计
SELECT filter_category, COUNT(*) 
FROM raw_contents 
WHERE filter_status = 'useful'
GROUP BY filter_category;
```
