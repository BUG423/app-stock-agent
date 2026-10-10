FROM python:3.12-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 复制应用代码
COPY . .

# 安装生产依赖
RUN pip install --no-cache-dir .

# 暴露 Web 端口
EXPOSE 8000

# 默认启动 Web 服务
CMD ["python", "-m", "stockagent", "web", "--host", "0.0.0.0", "--port", "8000"]
