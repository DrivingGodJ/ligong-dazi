from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal

import jwt
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="DAZI_",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "理工搭子局 API"
    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite+aiosqlite:///./data/ligong_dazi.db"
    auto_create_schema: bool = True
    jwt_secret: SecretStr = SecretStr("dev-only-change-before-production")
    access_token_minutes: int = Field(default=60 * 24 * 7, ge=5, le=60 * 24 * 30)
    cors_origins: list[str] = ["http://localhost:3000"]

    ai_provider: Literal["auto", "deterministic", "openai_compatible"] = "auto"
    ai_vendor: Literal["deepseek", "openai", "custom"] = "custom"
    ai_api_key: SecretStr | None = None
    ai_base_url: str = "https://api.openai.com/v1"
    ai_model: str = "gpt-4.1-mini"
    ai_timeout_seconds: float = Field(default=30, gt=0, le=120)
    ai_max_tool_rounds: int = Field(default=6, ge=1, le=12)
    ai_fallback_enabled: bool = True
    config_file_path: str = ".env"

    @model_validator(mode="after")
    def validate_production_secrets(self) -> Settings:
        host = self.ai_base_url.lower()
        if self.ai_vendor == "custom":
            if "api.deepseek.com" in host:
                self.ai_vendor = "deepseek"
            elif "api.openai.com" in host:
                self.ai_vendor = "openai"
        if (
            self.environment == "production"
            and self.jwt_secret.get_secret_value() == "dev-only-change-before-production"
        ):
            raise ValueError("production 环境必须配置 DAZI_JWT_SECRET")
        return self

    @property
    def use_llm(self) -> bool:
        if self.ai_provider == "deterministic":
            return False
        if self.ai_provider == "openai_compatible":
            return True
        return bool(self.ai_api_key and self.ai_api_key.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()


class DatabaseRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._prepare_sqlite_directory(settings.database_url)
        self.engine: AsyncEngine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
        )
        if settings.database_url.startswith("sqlite"):
            self._enable_sqlite_foreign_keys()
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    @staticmethod
    def _prepare_sqlite_directory(database_url: str) -> None:
        marker = "sqlite+aiosqlite:///"
        if not database_url.startswith(marker):
            return
        path = database_url.removeprefix(marker)
        if path == ":memory:" or path.startswith("file:"):
            return
        Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    def _enable_sqlite_foreign_keys(self) -> None:
        @event.listens_for(self.engine.sync_engine, "connect")
        def set_sqlite_pragma(dbapi_connection: object, _: object) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    async def dispose(self) -> None:
        await self.engine.dispose()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=64,
    )
    return (
        "scrypt$16384$8$1$"
        + base64.urlsafe_b64encode(salt).decode()
        + "$"
        + base64.urlsafe_b64encode(derived).decode()
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_text, digest_text = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode())
        expected = base64.urlsafe_b64decode(digest_text.encode())
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def create_access_token(user_id: str, settings: Settings) -> tuple[str, datetime]:
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.access_token_minutes)
    payload = {"sub": user_id, "exp": expires_at, "iat": datetime.now(UTC), "typ": "access"}
    token = jwt.encode(
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    return token, expires_at


def decode_access_token(token: str, settings: Settings) -> str:
    payload = jwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=["HS256"],
        options={"require": ["sub", "exp", "iat"]},
    )
    if payload.get("typ") != "access":
        raise jwt.InvalidTokenError("token 类型不正确")
    return str(payload["sub"])
