from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import ensure_sqlite_compatibility
from app.models import User
from app.notifications import enqueue_legacy_student_id_notices
from tests.conftest import register_user


def registration(student_id: str, *, email: str | None = None) -> dict:
    data = {
        "student_id": student_id,
        "password": "test-password-123",
        "display_name": "新同学",
        "campus": "南京",
    }
    if email:
        data["email"] = email
    return data


async def test_student_id_registration_login_and_private_profile(client) -> None:
    created = await client.post("/api/v1/auth/register", json=registration("202600009999"))
    assert created.status_code == 201, created.text
    headers = {"Authorization": f"Bearer {created.json()['access_token']}"}
    me = (await client.get("/api/v1/users/me", headers=headers)).json()
    assert me["student_id"] == "202600009999"
    assert me["email"] is None
    public = (await client.get(f"/api/v1/users/{me['id']}/profile", headers=headers)).json()
    assert "student_id" not in public["user"]
    assert "email" not in public["user"]
    login = await client.post(
        "/api/v1/auth/token",
        json={"account": "202600009999", "password": "test-password-123"},
    )
    assert login.status_code == 200
    assert (
        await client.post("/api/v1/auth/register", json=registration("12 34"))
    ).status_code == 422
    assert (
        await client.post(
            "/api/v1/auth/register",
            json={
                "email": "new@example.com",
                "password": "test-password-123",
                "display_name": "未填学号",
                "campus": "南京",
            },
        )
    ).status_code == 422


async def test_duplicate_id_appeal_is_admin_only_and_can_be_handled(client) -> None:
    _, owner = await register_user(client, "appeal-owner@example.com", "已有用户")
    sid = (await client.get("/api/v1/users/me", headers=owner)).json()["student_id"]
    duplicate = await client.post("/api/v1/auth/register", json=registration(sid))
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "student_id_taken"
    missing = await client.post(
        "/api/v1/auth/student-id-appeals",
        json={"student_id": "202699999999", "contact": "QQ 12345678"},
    )
    assert missing.status_code == 409
    appeal = await client.post(
        "/api/v1/auth/student-id-appeals",
        json={"student_id": sid, "contact": "QQ 12345678", "description": "这是我的学号"},
    )
    assert appeal.status_code == 201, appeal.text
    again = await client.post(
        "/api/v1/auth/student-id-appeals",
        json={"student_id": sid, "contact": "QQ 12345678"},
    )
    assert again.status_code == 201
    listing = await client.get("/api/v1/admin/student-id-appeals")
    assert listing.status_code == 200
    assert len(listing.json()) == 1
    item = listing.json()[0]
    assert item["student_id"] == sid
    assert item["contact"] == "QQ 12345678"
    assert "contact" not in (await client.get("/api/v1/users/me", headers=owner)).text
    app = client._transport.app  # type: ignore[attr-defined]
    app.state.settings.environment = "production"
    try:
        assert (await client.get("/api/v1/admin/student-id-appeals")).status_code == 401
    finally:
        app.state.settings.environment = "test"
    handled = await client.post(f"/api/v1/admin/student-id-appeals/{item['id']}/handled")
    assert handled.status_code == 200
    assert handled.json()["status"] == "handled"
    assert (await client.get("/api/v1/admin/student-id-appeals")).json() == []
    assert len((await client.get("/api/v1/admin/student-id-appeals?status=handled")).json()) == 1


async def test_old_email_account_can_claim_unique_student_id(client) -> None:
    _, old_headers = await register_user(client, "old-email@example.com", "旧用户")
    app = client._transport.app  # type: ignore[attr-defined]
    async with app.state.database.session_factory() as session:
        old = await session.scalar(select(User).where(User.email == "old-email@example.com"))
        old.student_id = None
        await session.commit()
        await enqueue_legacy_student_id_notices(session)
        await session.commit()
        await enqueue_legacy_student_id_notices(session)
        await session.commit()
    notices = (await client.get("/api/v1/notifications", headers=old_headers)).json()
    assert len([item for item in notices if item["kind"] == "account_update"]) == 1
    logged_in = await client.post(
        "/api/v1/auth/token",
        json={"account": "old-email@example.com", "password": "test-password-123"},
    )
    assert logged_in.status_code == 200
    assert (await client.get("/api/v1/users/me", headers=old_headers)).json()["student_id"] is None
    _, other_headers = await register_user(client, "taken-id@example.com", "另一位同学")
    taken = (await client.get("/api/v1/users/me", headers=other_headers)).json()["student_id"]
    conflict = await client.patch(
        "/api/v1/users/me", headers=old_headers, json={"student_id": taken}
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "student_id_taken"
    claimed = await client.patch(
        "/api/v1/users/me", headers=old_headers, json={"student_id": "202600001234"}
    )
    assert claimed.status_code == 200
    assert claimed.json()["student_id"] == "202600001234"
    assert (
        await client.post(
            "/api/v1/auth/token", json={"account": "202600001234", "password": "test-password-123"}
        )
    ).status_code == 200
    assert (
        await client.patch(
            "/api/v1/users/me", headers=old_headers, json={"student_id": "202600001235"}
        )
    ).status_code == 409


async def test_existing_sqlite_users_table_gets_unique_student_id(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE TABLE users ("
                "id VARCHAR(36) PRIMARY KEY, email VARCHAR(320) NOT NULL UNIQUE)"
            )
        )
        await connection.execute(
            text("INSERT INTO users (id, email) VALUES ('old-user', 'old@example.com')")
        )
    await ensure_sqlite_compatibility(engine)
    async with engine.begin() as connection:
        columns = (await connection.execute(text("PRAGMA table_info(users)"))).all()
        indexes = (await connection.execute(text("PRAGMA index_list(users)"))).all()
        old_user = (
            await connection.execute(
                text("SELECT email, student_id FROM users WHERE id = 'old-user'")
            )
        ).one()
    assert "student_id" in {item[1] for item in columns}
    assert any(item[1] == "ix_users_student_id" and item[2] for item in indexes)
    assert old_user == ("old@example.com", None)
    await engine.dispose()
