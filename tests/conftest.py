from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager
from pydantic import SecretStr

from app.core import Settings
from app.main import create_app


@pytest.fixture
async def client(tmp_path) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        environment="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        auto_create_schema=True,
        jwt_secret=SecretStr("test-secret-with-enough-entropy-for-tests"),
        ai_provider="deterministic",
        ai_vendor="custom",
        ai_api_key=None,
        config_file_path=str(tmp_path / ".env"),
        activity_photo_directory=str(tmp_path / "activity-photos"),
        push_key_file_path=str(tmp_path / "vapid.pem"),
        cors_origins=["http://test"],
    )
    app = create_app(settings)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
            yield test_client


async def register_user(
    client: httpx.AsyncClient,
    email: str,
    display_name: str,
    password: str = "test-password-123",
) -> tuple[str, dict[str, str]]:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "display_name": display_name,
            "university": "南京理工大学",
            "campus": "南京",
        },
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    return token, {"Authorization": f"Bearer {token}"}
