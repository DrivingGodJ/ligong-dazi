from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.activity_media import cleanup_expired_activity_photos
from app.admin import router as admin_router
from app.api import router as api_router
from app.core import DatabaseRuntime, Settings, get_settings
from app.migrations import ensure_sqlite_compatibility
from app.models import Base
from app.post_activity import process_completed_activities

logger = logging.getLogger(__name__)


async def post_activity_maintenance(database: DatabaseRuntime) -> None:
    while True:
        try:
            async with database.session_factory() as session:
                await process_completed_activities(session)
                await cleanup_expired_activity_photos(session)
                await session.commit()
        except Exception:
            logger.exception("活动结束后的画像分析任务执行失败")
        await asyncio.sleep(60)


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    web_directory = Path(__file__).resolve().parent.parent / "web"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = DatabaseRuntime(app_settings)
        app.state.database = database
        photo_directory = await asyncio.to_thread(
            lambda: Path(app_settings.activity_photo_directory).expanduser().resolve()
        )
        await asyncio.to_thread(photo_directory.mkdir, parents=True, exist_ok=True)
        if app_settings.auto_create_schema:
            async with database.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            await ensure_sqlite_compatibility(database.engine)
        async with database.session_factory() as session:
            await process_completed_activities(session)
            await cleanup_expired_activity_photos(session)
            await session.commit()
        maintenance_task = asyncio.create_task(post_activity_maintenance(database))
        yield
        maintenance_task.cancel()
        with suppress(asyncio.CancelledError):
            await maintenance_task
        await database.dispose()

    app = FastAPI(
        title=app_settings.app_name,
        version="0.1.0",
        description="理工搭子局 AI 搭子执行 Agent 后端 API",
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.admin_config_lock = asyncio.Lock()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)
    app.include_router(admin_router)
    app.mount("/static", StaticFiles(directory=web_directory), name="static")

    @app.get("/", include_in_schema=False)
    async def frontend() -> FileResponse:
        return FileResponse(web_directory / "index.html")

    @app.get("/admin", include_in_schema=False)
    async def admin_frontend() -> FileResponse:
        if app_settings.environment == "production":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="页面不存在")
        return FileResponse(web_directory / "admin.html")

    @app.get("/health/live")
    async def health_live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def health_ready(request: Request) -> dict[str, str]:
        database: DatabaseRuntime = request.app.state.database
        async with database.session_factory() as session:
            await session.execute(text("SELECT 1"))
        return {
            "status": "ready",
            "agent_mode": "llm" if app_settings.use_llm else "deterministic",
        }

    return app


app = create_app()
