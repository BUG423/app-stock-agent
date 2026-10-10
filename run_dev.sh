#!/usr/bin/env bash
# StockAgent 本地开发一键启动脚本
# 用法: ./run_dev.sh [web|scheduler|pipeline|init-db]
#
# 默认使用 SQLite（零安装），数据存在 ./data/stockagent.db
# 如需用 PostgreSQL：设置 DB_PASS 环境变量后会自动切换

set -e

cd "$(dirname "$0")"

# --- 默认配置（开发用，首次运行会自动生成 .env）---
if [ ! -f .env ]; then
    cp .env.example .env
    echo "✅ 已从 .env.example 生成 .env，请按需修改其中的 API Key 和 SMTP 配置"
fi

# 加载 .env
set -a
. ./.env
set +a

# --- SQLite 模式（不设置 DB_PASS 即自动启用）---
export SQLITE_DIR="${SQLITE_DIR:-./data}"
mkdir -p "$SQLITE_DIR"

# --- 切换数据库后端 ---
if [ -z "$DB_PASS" ]; then
    echo "📦 使用 SQLite 数据库: $SQLITE_DIR/stockagent.db"
else
    echo "🐘 使用 PostgreSQL 数据库: $DB_HOST:$DB_PORT/$DB_NAME"
fi

# --- 子命令 ---
CMD="${1:-web}"

case "$CMD" in
    web)
        echo "🚀 启动 Web 面板 → http://localhost:8000"
        exec python3 -m stockagent web --host 0.0.0.0 --port "${WEB_PORT:-8000}"
        ;;
    scheduler)
        echo "⏰ 启动定时任务调度器"
        exec python3 -m stockagent scheduler
        ;;
    pipeline)
        echo "🔄 手动执行一次抓取+分析流水线"
        exec python3 -m stockagent pipeline
        ;;
    monitor)
        echo "👁️ 手动执行一次条件匹配"
        exec python3 -m stockagent monitor
        ;;
    init-db)
        echo "🗄️ 初始化数据库表"
        exec python3 -m stockagent init-db
        ;;
    *)
        echo "用法: $0 {web|scheduler|pipeline|monitor|init-db}"
        exit 1
        ;;
esac
