from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.identity_appeals import process_expired_identity_appeals
from app.identity_verification import StudentCardReview
from app.migrations import ensure_sqlite_compatibility, remove_emails_from_bound_accounts
from app.models import StudentIdAppeal, User, utcnow
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


def appeal_upload(student_id: str, contact: str = "QQ 12345678") -> dict:
    return {
        "data": {
            "student_id": student_id,
            "contact": contact,
            "description": "这是我的学号",
            "ai_consent": "true",
            "registration_json": json.dumps(registration(student_id), ensure_ascii=False),
        },
        "files": {"student_card": ("student-card.jpg", b"\xff\xd8\xffstudent-card", "image/jpeg")},
    }


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


async def test_duplicate_id_appeal_requires_card_and_can_enter_manual_review(client) -> None:
    _, owner = await register_user(client, "appeal-owner@example.com", "已有用户")
    sid = (await client.get("/api/v1/users/me", headers=owner)).json()["student_id"]
    duplicate = await client.post("/api/v1/auth/register", json=registration(sid))
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "student_id_taken"
    missing = await client.post(
        "/api/v1/auth/student-id-appeals",
        **appeal_upload("202699999999"),
    )
    assert missing.status_code == 409
    no_card = await client.post(
        "/api/v1/auth/student-id-appeals", data={"student_id": sid, "contact": "QQ 12345678"}
    )
    assert no_card.status_code == 422
    appeal = await client.post("/api/v1/auth/student-id-appeals", **appeal_upload(sid))
    assert appeal.status_code == 201, appeal.text
    assert appeal.json()["status"] == "manual_review"
    assert (await client.get("/api/v1/users/me", headers=owner)).status_code == 200
    again = await client.post("/api/v1/auth/student-id-appeals", **appeal_upload(sid))
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
    assert handled.json()["status"] == "owner_confirmed"
    assert (await client.get("/api/v1/admin/student-id-appeals")).json() == []
    assert len((await client.get("/api/v1/admin/student-id-appeals?status=resolved")).json()) == 1


async def test_approved_identity_appeal_freezes_then_transfers_only_login_identity(
    client, monkeypatch
) -> None:
    async def approve_card(_content, _media_type, expected_student_id, _settings):
        return StudentCardReview(
            verdict="approved",
            reason="学生卡清晰且学号一致",
            confidence=0.98,
            extracted_student_id=expected_student_id,
        )

    monkeypatch.setattr("app.api.review_student_card", approve_card)
    _, owner_headers = await register_user(client, "appeal-frozen-owner@example.com", "原账号")
    owner = (await client.get("/api/v1/users/me", headers=owner_headers)).json()
    sid = owner["student_id"]
    appeal = await client.post("/api/v1/auth/student-id-appeals", **appeal_upload(sid))
    assert appeal.status_code == 201, appeal.text
    assert appeal.json()["status"] == "awaiting_owner"

    frozen_login = await client.post(
        "/api/v1/auth/token",
        json={"account": sid, "password": "test-password-123"},
    )
    assert frozen_login.status_code == 200
    assert frozen_login.json()["account_state"] == "identity_frozen"
    assert (await client.get("/api/v1/users/me", headers=owner_headers)).status_code == 423

    _, searcher_headers = await register_user(client, "appeal-searcher@example.com", "找搭子的人")
    start = utcnow() + timedelta(days=2)
    preview = await client.post(
        "/api/v1/matches/preview",
        headers=searcher_headers,
        json={
            "category": "羽毛球",
            "starts_at": start.isoformat(),
            "ends_at": (start + timedelta(hours=2)).isoformat(),
            "location": "南区体育馆",
            "people_needed": 1,
        },
    )
    assert preview.status_code == 201, preview.text
    assert owner["id"] not in {
        item["candidate_id"]
        for item in preview.json()["candidates"]
        if item["candidate_type"] == "user"
    }

    owner_card = await client.post(
        "/api/v1/auth/identity-review/card",
        headers=owner_headers,
        data={"ai_consent": "true"},
        files={"student_card": ("owner-card.jpg", b"\xff\xd8\xffowner-card", "image/jpeg")},
    )
    assert owner_card.status_code == 200, owner_card.text
    assert owner_card.json()["status"] == "manual_review"
    contact = await client.post(
        "/api/v1/auth/identity-review/contact",
        headers=owner_headers,
        json={"contact": "QQ 87654321"},
    )
    assert contact.status_code == 200

    item = (await client.get("/api/v1/admin/student-id-appeals")).json()[0]
    assert item["contact"] == "QQ 12345678"
    assert item["owner_contact"] == "QQ 87654321"
    assert item["claimant_agent_review"]["verdict"] == "approved"
    assert item["owner_agent_review"]["verdict"] == "approved"
    resolved = await client.post(
        f"/api/v1/admin/student-id-appeals/{item['id']}/resolve",
        json={"decision": "transfer_to_claimant", "note": "人工核验后确认申诉人身份"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "transferred"
    app = client._transport.app  # type: ignore[attr-defined]
    async with app.state.database.session_factory() as session:
        stored_appeal = await session.get(StudentIdAppeal, item["id"])
        assert stored_appeal is not None
        assert stored_appeal.claimant_profile == {}
        assert stored_appeal.claimant_password_hash is None

    claimant_login = await client.post(
        "/api/v1/auth/token",
        json={"account": sid, "password": "test-password-123"},
    )
    assert claimant_login.status_code == 200
    claimant_headers = {"Authorization": f"Bearer {claimant_login.json()['access_token']}"}
    claimant = (await client.get("/api/v1/users/me", headers=claimant_headers)).json()
    assert claimant["id"] != owner["id"]
    assert claimant["display_name"] == "新同学"
    assert (await client.get("/api/v1/users/me", headers=owner_headers)).status_code == 401


async def test_identity_appeal_transfers_after_owner_deadline(client, monkeypatch) -> None:
    async def approve_card(_content, _media_type, expected_student_id, _settings):
        return StudentCardReview(
            verdict="approved",
            reason="学生卡清晰且学号一致",
            confidence=0.99,
            extracted_student_id=expected_student_id,
        )

    monkeypatch.setattr("app.api.review_student_card", approve_card)
    _, owner_headers = await register_user(client, "appeal-timeout-owner@example.com", "超时账号")
    owner = (await client.get("/api/v1/users/me", headers=owner_headers)).json()
    sid = owner["student_id"]
    created = await client.post("/api/v1/auth/student-id-appeals", **appeal_upload(sid))
    assert created.json()["status"] == "awaiting_owner"

    app = client._transport.app  # type: ignore[attr-defined]
    async with app.state.database.session_factory() as session:
        appeal = await session.get(StudentIdAppeal, created.json()["id"])
        assert appeal is not None
        appeal.owner_deadline = utcnow() - timedelta(seconds=1)
        await session.commit()
    async with app.state.database.session_factory() as session:
        transferred = await process_expired_identity_appeals(session)
        await session.commit()
        assert [item.id for item in transferred] == [created.json()["id"]]

    login = await client.post(
        "/api/v1/auth/token",
        json={"account": sid, "password": "test-password-123"},
    )
    assert login.status_code == 200
    assert login.json()["account_state"] == "active"
    assert (await client.get("/api/v1/users/me", headers=owner_headers)).status_code == 401


async def test_existing_email_user_can_receive_appealed_student_id(client, monkeypatch) -> None:
    async def approve_card(_content, _media_type, expected_student_id, _settings):
        return StudentCardReview(
            verdict="approved",
            reason="学生卡清晰且学号一致",
            confidence=0.99,
            extracted_student_id=expected_student_id,
        )

    monkeypatch.setattr("app.api.review_student_card", approve_card)
    _, owner_headers = await register_user(client, "appeal-existing-owner@example.com", "原账号")
    owner = (await client.get("/api/v1/users/me", headers=owner_headers)).json()
    _, claimant_headers = await register_user(
        client, "appeal-existing-claimant@example.com", "旧邮箱申诉人"
    )
    app = client._transport.app  # type: ignore[attr-defined]
    async with app.state.database.session_factory() as session:
        claimant = await session.scalar(select(User).where(User.display_name == "旧邮箱申诉人"))
        assert claimant is not None
        claimant_id = claimant.id
        claimant.student_id = None
        await session.commit()

    upload = appeal_upload(owner["student_id"])
    upload["data"].pop("registration_json")
    created = await client.post(
        "/api/v1/auth/student-id-appeals", headers=claimant_headers, **upload
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "awaiting_owner"
    relinquished = await client.post(
        "/api/v1/auth/identity-review/relinquish", headers=owner_headers
    )
    assert relinquished.status_code == 200, relinquished.text
    claimant = (await client.get("/api/v1/users/me", headers=claimant_headers)).json()
    assert claimant["id"] == claimant_id
    assert claimant["student_id"] == owner["student_id"]
    assert claimant["display_name"] == "旧邮箱申诉人"


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
    assert claimed.json()["email"] is None
    async with app.state.database.session_factory() as session:
        old = await session.get(User, claimed.json()["id"])
        assert old is not None
        assert old.email.endswith("@accounts.invalid")
    assert (
        await client.post(
            "/api/v1/auth/token",
            json={"account": "old-email@example.com", "password": "test-password-123"},
        )
    ).status_code == 401
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


async def test_previously_bound_accounts_have_legacy_emails_removed(client) -> None:
    _, headers = await register_user(client, "already-bound@example.com", "早已绑定")
    app = client._transport.app  # type: ignore[attr-defined]
    async with app.state.database.session_factory() as session:
        assert await remove_emails_from_bound_accounts(session) == 1
        await session.commit()
        assert await remove_emails_from_bound_accounts(session) == 0
    me = (await client.get("/api/v1/users/me", headers=headers)).json()
    assert me["email"] is None


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
