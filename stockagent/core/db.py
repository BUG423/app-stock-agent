"""数据库引擎和会话管理。

支持两种后端：
- PostgreSQL（生产，通过 docker-compose 启动）
- SQLite（开发/演示，零安装，本地文件）

当 DB_PASS 为空 或 USE_SQLITE=1 时自动使用 SQLite。
"""

import logging
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from stockagent.core.config import config

logger = logging.getLogger(__name__)

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_backend: str = ""  # "postgresql" or "sqlite"


class Base(DeclarativeBase):
    """SQLAlchemy ORM 基类。"""

    pass


def _detect_backend() -> str:
    """检测使用哪个数据库后端。"""
    if os.environ.get("USE_SQLITE", "").lower() in ("1", "true", "yes"):
        return "sqlite"
    # 没有 PostgreSQL 密码 → 用 SQLite
    if not os.environ.get("DB_PASS"):
        return "sqlite"
    return "postgresql"


def _build_url() -> str:
    """从配置/环境构建数据库连接 URL。"""
    global _backend
    _backend = _detect_backend()

    if _backend == "sqlite":
        db_dir = Path(os.environ.get("SQLITE_DIR", "./data"))
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = db_dir / "stockagent.db"
        logger.info(f"使用 SQLite 数据库: {db_path}")
        return f"sqlite+aiosqlite:///{db_path}"

    # PostgreSQL
    if config._loaded:
        return config.get("database", "url", default="")

    db_user = os.environ.get("DB_USER", "stockagent")
    db_pass = os.environ.get("DB_PASS", "")
    db_host = os.environ.get("DB_HOST", "localhost")
    db_port = os.environ.get("DB_PORT", "5432")
    db_name = os.environ.get("DB_NAME", "stockagent")
    return f"postgresql+asyncpg://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"


async def get_engine():
    """获取或创建异步数据库引擎。"""
    global _engine
    if _engine is None:
        url = _build_url()
        kwargs = {"echo": False}
        if _backend != "sqlite":
            kwargs["pool_size"] = 10
            kwargs["max_overflow"] = 20
        else:
            # SQLite并发修复: 撞锁时等30秒(而非立即"database is locked"失败)
            kwargs["connect_args"] = {"timeout": 30}
        _engine = create_async_engine(url, **kwargs)
        if _backend == "sqlite":
            # WAL模式(读不阻塞写) + busy_timeout, 解决多定时任务并发写撞锁丢单
            from sqlalchemy import event

            @event.listens_for(_engine.sync_engine, "connect")
            def _sqlite_pragma(dbapi_conn, _rec):
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.close()

    return _engine


async def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """获取或创建异步 session factory。"""
    global _sessionmaker
    if _sessionmaker is None:
        engine = await get_engine()
        _sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return _sessionmaker


async def get_session() -> AsyncSession:
    """获取一个新的异步数据库会话（用于 FastAPI 依赖注入）。"""
    maker = await get_sessionmaker()
    async with maker() as session:
        yield session


async def init_db() -> None:
    """初始化数据库 —— 创建所有表。"""
    # 导入 models 确保所有表定义注册到 Base.metadata（否则 create_all 建空库）
    import stockagent.core.models  # noqa: F401

    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if _backend == "sqlite":
            await conn.run_sync(_migrate_sqlite)
        else:
            await conn.run_sync(_migrate_postgresql)
    await sync_configured_bloggers()
    logger.info(f"数据库已初始化 (backend={_backend})")


async def sync_configured_bloggers() -> int:
    """把配置中的真人博主幂等写入数据库，已有记录保持用户当前设置。"""
    if not config._loaded:
        return 0

    entries = config.get("scrapers", "bilibili", "tracked_bloggers", default=[])
    if not isinstance(entries, list) or not entries:
        return 0

    from stockagent.core.models import Blogger

    maker = await get_sessionmaker()
    async with maker() as session:
        rows = await session.execute(select(Blogger.platform, Blogger.platform_user_id))
        existing = {(platform, user_id) for platform, user_id in rows.all()}
        added = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            platform = str(entry.get("platform") or "bilibili").strip()
            user_id = str(entry.get("platform_user_id") or "").strip()
            name = str(entry.get("name") or "").strip()
            if not user_id or not name or (platform, user_id) in existing:
                continue
            session.add(
                Blogger(
                    name=name,
                    platform=platform,
                    platform_user_id=user_id,
                    profile_url=str(entry.get("profile_url") or "").strip()
                    or f"https://space.bilibili.com/{user_id}/dynamic",
                    enabled=bool(entry.get("enabled", True)),
                    process_mode=str(entry.get("process_mode") or "advice"),
                    scrape_frequency=str(entry.get("scrape_frequency") or "daily"),
                    notes=entry.get("notes"),
                )
            )
            existing.add((platform, user_id))
            added += 1
        if added:
            await session.commit()
            logger.info("已从配置加入 %s 位真人博主", added)
        return added


def _migrate_sqlite(conn) -> None:
    """轻量幂等迁移，保障无 Alembic 的现有 SQLite 部署可升级。"""
    columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(notifications)")}
    additions = {
        "attempt_count": "INTEGER NOT NULL DEFAULT 0",
        "next_retry_at": "DATETIME",
        "dead_letter": "BOOLEAN NOT NULL DEFAULT 0",
        "dedupe_key": "VARCHAR(160)",
        "delivery_lock_until": "DATETIME",
    }
    for name, ddl in additions.items():
        if name not in columns:
            conn.exec_driver_sql(f"ALTER TABLE notifications ADD COLUMN {name} {ddl}")
    # SQLite 对 NULL 的唯一索引允许多条记录，正好让非内容通知继续重复发送；
    # raw_content 通知会写入 dedupe_key，因此可由数据库原子地去重。
    conn.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_notifications_dedupe_key "
        "ON notifications (dedupe_key)"
    )


def _migrate_postgresql(conn) -> None:
    """为非 Alembic 部署补齐通知去重字段和索引。"""
    conn.exec_driver_sql(
        "ALTER TABLE notifications ADD COLUMN IF NOT EXISTS dedupe_key VARCHAR(160)"
    )
    conn.exec_driver_sql(
        "ALTER TABLE notifications ADD COLUMN IF NOT EXISTS delivery_lock_until TIMESTAMPTZ"
    )
    conn.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_notifications_dedupe_key "
        "ON notifications (dedupe_key)"
    )


async def close_db() -> None:
    """关闭数据库连接。"""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
    _sessionmaker = None


def get_backend() -> str:
    """返回当前使用的数据库后端。"""
    return _backend


async def retry_on_locked(func, *args, max_retries=3, **kwargs):
    """SQLite 锁冲突重试包装器。"""
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            if "database is locked" in str(e) and attempt < max_retries - 1:
                logger.warning(f"SQLite 锁冲突，{attempt + 1}/{max_retries} 重试...")
                import asyncio

                await asyncio.sleep(2)
            else:
                raise
