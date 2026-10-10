.PHONY: help dev web scheduler install test lint clean docker-up docker-down db-init db-migrate

help: ## 显示帮助信息
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## 安装依赖
	pip install -e ".[dev]"

dev: ## 启动开发服务器 (web + scheduler 同时运行)
	python -m stockagent web --reload --port 8000

web: ## 仅启动 Web 面板
	python -m stockagent web --host 0.0.0.0 --port 8000

scheduler: ## 仅启动定时任务调度器
	python -m stockagent scheduler

pipeline: ## 手动执行一次抓取+分析流水线
	python -m stockagent pipeline

monitor: ## 手动执行一次条件匹配
	python -m stockagent monitor

db-init: ## 初始化数据库表
	python -m stockagent init-db

db-migrate: ## 运行 Alembic 数据库迁移
	alembic upgrade head

test: ## 运行测试
	pytest -v

lint: ## 代码检查
	ruff check stockagent/

clean: ## 清理缓存
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete

docker-up: ## 启动 Docker Compose 环境
	docker compose up -d

docker-down: ## 停止 Docker Compose 环境
	docker compose down

docker-build: ## 构建 Docker 镜像
	docker compose build
