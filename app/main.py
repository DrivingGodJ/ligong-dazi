from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.activity_media import cleanup_expired_activity_photos
from app.admin import load_persisted_ai_config
from app.admin import router as admin_router
from app.api import router as api_router
from app.core import DatabaseRuntime, Settings, get_settings
from app.migrations import (
    ensure_sqlite_compatibility,
    migrate_activity_campuses,
    migrate_user_campuses,
)
from app.models import Base
from app.notifications import (
    dispatch_push,
    enqueue_activity_reminders,
    enqueue_legacy_student_id_notices,
    ensure_push_key,
)
from app.post_activity import process_completed_activities

logger = logging.getLogger(__name__)


async def post_activity_maintenance(database: DatabaseRuntime, push_key_path: str) -> None:
    while True:
        try:
            async with database.session_factory() as session:
                await process_completed_activities(session)
                await cleanup_expired_activity_photos(session)
                await enqueue_activity_reminders(session)
                await session.commit()
                await dispatch_push(session, push_key_path)
                await session.commit()
        except Exception:
            logger.exception("活动结束后的画像分析任务执行失败")
        await asyncio.sleep(60)


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    web_directory = Path(__file__).resolve().parent.parent / "web"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app_settings.environment == "production":
            if (
                os.environ.get("RAILWAY_SERVICE_ID")
                and os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") != "/app/data"
            ):
                raise RuntimeError("Railway 上必须先挂载 /app/data 持久化存储")
            if os.environ.get("ZEABUR_ENVIRONMENT_ID") and not await asyncio.to_thread(
                os.path.ismount, "/app/data"
            ):
                raise RuntimeError("Zeabur 上必须先挂载 /app/data 持久化存储")
            await asyncio.to_thread(load_persisted_ai_config, app_settings)
        database = DatabaseRuntime(app_settings)
        app.state.database = database
        app.state.vapid_public_key = await asyncio.to_thread(
            ensure_push_key, app_settings.push_key_file_path
        )
        photo_directory = await asyncio.to_thread(
            lambda: Path(app_settings.activity_photo_directory).expanduser().resolve()
        )
        await asyncio.to_thread(photo_directory.mkdir, parents=True, exist_ok=True)
        if app_settings.auto_create_schema:
            async with database.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            await ensure_sqlite_compatibility(database.engine)
        async with database.session_factory() as session:
            migrated_campuses = await migrate_user_campuses(session)
            if migrated_campuses:
                logger.info("已将 %s 位用户的旧校区资料归一为南京或江阴", migrated_campuses)
            await migrate_activity_campuses(session)
            await enqueue_legacy_student_id_notices(session)
            await process_completed_activities(session)
            await cleanup_expired_activity_photos(session)
            await enqueue_activity_reminders(session)
            await session.commit()
        maintenance_task = asyncio.create_task(
            post_activity_maintenance(database, app_settings.push_key_file_path)
        )
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
    app.state.admin_login_attempts = {}
    app.state.appeal_attempts = {}
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
        return FileResponse(web_directory / "admin.html")

    @app.get("/manifest.webmanifest", include_in_schema=False)
    async def manifest() -> FileResponse:
        return FileResponse(
            web_directory / "manifest.webmanifest", media_type="application/manifest+json"
        )

    @app.get("/service-worker.js", include_in_schema=False)
    async def service_worker() -> FileResponse:
        return FileResponse(
            web_directory / "service-worker.js",
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
        )

    @app.get("/.well-known/assetlinks.json", include_in_schema=False)
    async def android_asset_links() -> FileResponse:
        return FileResponse(web_directory / "assetlinks.json", media_type="application/json")

    @app.get("/api/v1/app/version")
    async def app_version() -> dict:
        release_path = web_directory / "android-release.json"
        apk_path = web_directory / "downloads" / "ligong-dazi.apk"
        if not release_path.is_file() or not apk_path.is_file():
            return {"available": False}
        release = json.loads(release_path.read_text(encoding="utf-8"))
        return {
            "available": True,
            "version_code": release["version_code"],
            "version_name": release["version_name"],
            "download_url": "/downloads/ligong-dazi.apk",
            "sha256": hashlib.sha256(apk_path.read_bytes()).hexdigest(),
        }

    @app.get("/api/v1/app/native-version")
    async def native_app_version() -> dict:
        release_path = web_directory / "android-native-release.json"
        apk_path = web_directory / "downloads" / "ligong-dazi-native.apk"
        if not release_path.is_file() or not apk_path.is_file():
            return {"available": False}
        release = json.loads(release_path.read_text(encoding="utf-8"))
        return {
            "available": True,
            "version_code": release["version_code"],
            "version_name": release["version_name"],
            "download_url": "/downloads/ligong-dazi-native.apk",
            "sha256": hashlib.sha256(apk_path.read_bytes()).hexdigest(),
        }

    @app.get("/downloads/ligong-dazi.apk", include_in_schema=False)
    async def android_download() -> FileResponse:
        apk_path = web_directory / "downloads" / "ligong-dazi.apk"
        if not apk_path.is_file():
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="安卓安装包尚未发布")
        return FileResponse(
            apk_path,
            media_type="application/vnd.android.package-archive",
            filename="ligong-dazi.apk",
        )

    @app.get("/downloads/ligong-dazi-native.apk", include_in_schema=False)
    async def native_android_download() -> FileResponse:
        apk_path = web_directory / "downloads" / "ligong-dazi-native.apk"
        if not apk_path.is_file():
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="安卓应用版尚未发布")
        return FileResponse(
            apk_path,
            media_type="application/vnd.android.package-archive",
            filename="ligong-dazi-native.apk",
        )

    @app.get("/health/live")
    async def health_live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def health_ready(request: Request) -> dict[str, str | None]:
        database: DatabaseRuntime = request.app.state.database
        async with database.session_factory() as session:
            await session.execute(text("SELECT 1"))
        return {
            "status": "ready",
            "agent_mode": "llm" if app_settings.use_llm else "deterministic",
            "agent_model": app_settings.ai_model if app_settings.use_llm else None,
        }

    return app


app = create_app()
