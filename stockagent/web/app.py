"""FastAPI Web 应用工厂。"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from stockagent.core.config import config
from stockagent.web.routers import (
    actions,
    analysis,
    bloggers,
    health,
    kline,
    notifications,
    signals,
    triggers,
)


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例。"""
    app = FastAPI(
        title="StockAgent",
        description="A股投资信号聚合系统",
        version="0.1.0",
    )

    # Session 中间件 (用于 basic auth)
    web_cfg = config.get("web", default={})
    session_secret = web_cfg.get(
        "session_secret", config.get("app", "secret_key", default="dev-secret")
    )
    app.add_middleware(SessionMiddleware, secret_key=session_secret)

    # 静态文件
    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # 注册 API 路由
    app.include_router(bloggers.router, prefix="/api/v1/bloggers", tags=["博主管理"])
    app.include_router(analysis.router, prefix="/api/v1/analysis", tags=["分析结果"])
    app.include_router(signals.router, prefix="/api/v1/signals", tags=["交易信号"])
    app.include_router(kline.router, prefix="/api/v1/kline", tags=["K线数据"])
    app.include_router(actions.router, prefix="/api/v1/actions", tags=["用户操作"])
    app.include_router(notifications.router, prefix="/api/v1/notifications", tags=["通知"])
    app.include_router(triggers.router, prefix="/api/v1/triggers", tags=["手动触发"])
    app.include_router(health.router, prefix="/api/v1/health", tags=["系统健康"])

    # 注册页面路由
    # from stockagent.web.routers import pages
    # app.include_router(pages.router, tags=["页面"])

    @app.on_event("startup")
    async def startup():
        import logging

        logger = logging.getLogger(__name__)
        try:
            from stockagent.core.db import init_db

            await init_db()
            logger.info("数据库连接成功")
        except Exception as e:
            logger.warning(f"数据库不可用，Web 面板将以只读模式运行: {e}")

    @app.on_event("shutdown")
    async def shutdown():
        try:
            from stockagent.core.db import close_db

            await close_db()
        except Exception:
            pass

    # 前端静态文件服务
    frontend_dir = Path(__file__).parent / "static" / "frontend"
    if frontend_dir.exists():
        app.mount(
            "/assets", StaticFiles(directory=str(frontend_dir / "assets")), name="frontend-assets"
        )

        from fastapi.responses import FileResponse

        _frontend_root = frontend_dir.resolve()
        _index = _frontend_root / "index.html"

        @app.get("/{full_path:path}")
        async def serve_frontend(full_path: str):
            if full_path.startswith("api/"):
                return None
            # 防路径遍历/绝对路径注入：解析后的真实路径必须仍在前端目录内，
            # 否则一律回退到 SPA 首页（绝对路径如 /etc/passwd 经 resolve 后越界会被拦截）。
            try:
                candidate = (frontend_dir / full_path).resolve()
                candidate.relative_to(_frontend_root)
            except (ValueError, OSError):
                return FileResponse(str(_index))
            if candidate.is_file():
                return FileResponse(str(candidate))
            return FileResponse(str(_index))

    return app
