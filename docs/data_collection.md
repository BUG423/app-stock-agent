# 数据采集功能说明

## 概述

StockAgent 的数据采集层负责从B站抓取博主的图文和视频内容，并将其转换为文本格式，供后续AI分析使用。

## 支持的内容类型

### 1. 图文专栏
- **抓取方式**: 使用B站图文API (`/x/space/article`)
- **内容来源**: 卡片接口的 `summary` 字段（通常已包含完整内容）
- **特点**: 无需登录，稳定可靠，是主要数据源

### 2. 视频内容
- **抓取方式**: 使用B站动态API或视频列表API
- **内容处理**:
  1. 获取视频标题和描述
  2. 使用本地 SenseVoice INT8 + Silero VAD 将视频音频转为文本
  3. 合并标题、描述和ASR转写内容
- **特点**: 需要 Cookie、本地 sherpa-onnx 依赖和模型文件，不调用付费 ASR

## 配置说明

### 环境变量配置 (.env)

```bash
# B站Cookie（视频抓取需要）
BILIBILI_COOKIES=your_cookie_here

# 可选：修改本地模型目录
LOCAL_ASR_MODEL_DIR=data/models/sensevoice
```

### 应用配置 (config/default.yaml)

```yaml
scrapers:
  bilibili:
    # 视频抓取开关
    fetch_videos: true
    # 视频ASR转写开关
    transcribe_videos: true
    local_asr:
      enabled: true
      model_dir: ${LOCAL_ASR_MODEL_DIR:-data/models/sensevoice}
      num_threads: 1
    # 专栏全文抓取开关
    fetch_article_full_text: false
```

## 使用方法

### 1. 基本使用

```python
import asyncio
from stockagent.core.config import config
from stockagent.scrapers.bilibili.scraper import BilibiliScraper

async def main():
    config.load()
    scraper = BilibiliScraper()
    
    # 抓取用户内容
    contents = await scraper.scrape_user_posts(
        user_id="12345678",  # B站用户ID
        max_items=10
    )
    
    for content in contents:
        print(f"标题: {content.title}")
        print(f"类型: {content.content_type}")
        print(f"内容: {content.text[:200]}...")
        print()
    
    await scraper.close()

asyncio.run(main())
```

### 2. 测试脚本

```bash
# 运行测试脚本
python scripts/test_scraper.py
```

## 内容格式

### 图文内容
```
标题
正文内容（来自summary字段）
```

### 视频内容
```
标题
视频描述

【视频语音内容】
ASR转写文本（来自本地 SenseVoice）
```

## 数据结构

### ScrapedContent

```python
@dataclass
class ScrapedContent:
    platform: str          # 平台标识 (bilibili)
    blogger_id: int        # 博主ID
    content_id: str        # 内容ID
    title: str             # 标题
    text: str              # 文本内容（已处理）
    url: str               # 内容URL
    content_type: str      # 内容类型 (article/video)
    published_at: datetime # 发布时间
    metadata: dict         # 元数据
```

## 依赖工具

### 视频ASR转写需要
1. **yt-dlp**: 下载视频音频
2. **ffmpeg**: 音频格式转换
3. **SenseVoice INT8**: 本地语音转文本；Silero VAD 负责长音频分段

### 安装依赖
```bash
# 安装 Python 依赖和本地模型
pip install sherpa-onnx yt-dlp
python3 scripts/install_local_asr.py

# 安装ffmpeg (Ubuntu/Debian)
sudo apt-get install ffmpeg

# 安装ffmpeg (macOS)
brew install ffmpeg
```

## 注意事项

1. **Cookie配置**: 视频抓取需要有效的B站Cookie，否则可能被风控拦截
2. **请求频率**: 默认每次请求间隔2秒，避免触发反爬机制
3. **ASR资源**: 本地 ASR 不收费，但会短时占用 CPU 和约数百 MB 内存；生产限制为单线程串行执行
4. **内容过滤**: 原始内容需经过AI分析层过滤无用信息

## 故障排除

### 1. 视频抓取失败
- 检查Cookie是否有效
- 确认用户ID是否正确
- 查看日志中的错误信息

### 2. ASR转写失败
- 调用 `/api/v1/health` 检查 `local_asr_available`
- 确认 sherpa-onnx、yt-dlp、ffmpeg 和三个模型文件均已安装
- 检查网络连接

### 3. 内容为空
- 确认用户是否有公开内容
- 检查时间过滤条件
- 查看API返回状态码

## 下一步

1. 集成内容过滤功能（过滤无用内容）
2. 添加关键帧提取（视频重要画面）
3. 优化ASR转写质量
4. 添加内容去重机制
