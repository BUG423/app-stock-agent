# 小米 MiMo API 配置信息

## API Key
> ⚠️ 真实 Key 只放在 `.env` 的 `MIMO_API_KEY` 中，**切勿写入本文档或提交到仓库**。
```
MIMO_API_KEY=<在 .env 中配置>
```

## 接口地址

### OpenAI 兼容接口
```
https://token-plan-cn.xiaomimimo.com/v1
```

### Anthropic 兼容接口
```
https://token-plan-cn.xiaomimimo.com/anthropic
```

## 可用模型

### 主力模型
- `mimo-v2.5-pro` - 专业版，推荐用于投资分析
- `mimo-v2.5` - 标准版

### 轻量模型
- `mimo-v2.5-lite` - 轻量版，速度快

### 其他模型
- `mimo-v2` - 旧版
- `mimo-v2-pro` - 旧版专业

## 套餐信息
- 套餐类型: Standard 月度套餐
- 每月额度: 20,000,000 Credits
- 已使用: ~53.8%

## 使用场景

### 1. 投资内容分析
使用 `mimo-v2.5-pro` 进行博主内容分析，提取投资建议。

### 2. 文本理解
使用 `mimo-v2.5` 进行常规文本理解和过滤。

## 环境变量配置
在 `.env` 文件中设置:
```
MIMO_API_KEY=your-mimo-api-key
```

## 配置文件位置
- 主配置: `config/default.yaml`
- 环境变量: `.env` / `.env.example`

语音转写已改为 `stockagent/llm/local_asr.py` 的本地 SenseVoice INT8，不使用 MiMo ASR。
