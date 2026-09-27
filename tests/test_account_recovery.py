from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from pydantic import SecretStr
from sqlalchemy import select, update

from app.core import Settings
from app.identity_appeals import process_expired_identity_appeals
from app.identity_verification import StudentCardReview, _parse_review, student_card_service_error
from app.models import PasswordResetGrant, StudentCardUploadAttempt, StudentIdAppeal, User, utcnow
from app.student_cards import compress_student_card, next_upload_time, upload_day
from tests.conftest import STUDENT_CARD_IMAGE, register_user
from tests.test_student_registration import appeal_upload, registration


def card_upload(student_id: str) -> dict:
    return {
        "data": {"student_id": student_id, "ai_consent": "true"},
        "files": {"student_card": ("card.jpg", STUDENT_CARD_IMAGE, "image/jpeg")},
    }


def set_review(monkeypatch, verdict: str = "approved") -> None:
    async def review(_content, _media_type, student_id, _settings):
        return StudentCardReview(verdict, "测试核验结果", 0.98, student_id)

    monkeypatch.setattr("app.api.review_student_card", review)


async def owner_identity(client, email="recovery@example.com") -> tuple[dict, dict]:
    _, headers = await register_user(client, email, "密码找回同学")
    return (await client.get("/api/v1/users/me", headers=headers)).json(), headers


async def next_day(client) -> None:
    app = client._transport.app
    async with app.state.database.session_factory() as session:
        await session.execute(update(StudentCardUploadAttempt).values(upload_day="2000-01-01"))
        await session.commit()


def enable_local_unlimited(client) -> None:
    settings = client._transport.app.state.settings
    settings.environment = "development"
    settings.local_unlimited_student_card_uploads = True


@pytest.mark.parametrize(
    ("environment", "enabled", "unlimited"),
    [
        ("development", False, False),
        ("development", True, True),
        ("test", True, False),
        ("production", True, False),
        ("production", False, False),
    ],
)
def test_unlimited_uploads_are_strictly_local(environment, enabled, unlimited):
    settings = Settings(
        _env_file=None,
        environment=environment,
        jwt_secret=SecretStr("test-jwt-secret-with-enough-entropy-for-production"),
        admin_password=SecretStr("test-admin-password-with-enough-entropy"),
        local_unlimited_student_card_uploads=enabled,
    )
    assert settings.student_card_uploads_unlimited is unlimited


async def test_local_reset_retries_ignore_existing_quota_and_hourly_limit(client, monkeypatch):
    set_review(monkeypatch, "rejected")
    user, headers = await owner_identity(client)
    upload = card_upload(user["student_id"])
    first = await client.post("/api/v1/auth/password-reset/card", **upload)
    assert first.json()["status"] == "retry_tomorrow"
    app = client._transport.app
    enable_local_unlimited(client)
    app.state.appeal_attempts["127.0.0.1"] = [time.monotonic()] * 30
    for _ in range(3):
        retried = await client.post("/api/v1/auth/password-reset/card", **upload)
        assert retried.status_code == 200, retried.text
        assert retried.json()["status"] == "retry"
        assert retried.json()["can_upload"] is True
        assert retried.json()["next_upload_at"] is None
        assert retried.json()["reset_token"] is None
        assert "立即重新上传" in retried.json()["message"]
        assert "明天" not in retried.json()["message"]
    async with app.state.database.session_factory() as session:
        assert len(list((await session.scalars(select(StudentCardUploadAttempt))).all())) == 1
        assert await session.scalar(select(PasswordResetGrant)) is None
    assert (await client.get("/api/v1/users/me", headers=headers)).status_code == 200
    set_review(monkeypatch)
    approved = await client.post("/api/v1/auth/password-reset/card", **upload)
    assert approved.json()["status"] == "approved"
    assert approved.json()["can_upload"] is False
    assert approved.json()["reset_token"]


async def test_local_claimant_retries_without_duplicate_appeals(client, monkeypatch):
    set_review(monkeypatch, "rejected")
    user, _ = await owner_identity(client)
    upload = appeal_upload(user["student_id"])
    first = await client.post("/api/v1/auth/student-id-appeals", **upload)
    assert first.json()["status"] == "claimant_retry"
    enable_local_unlimited(client)
    app = client._transport.app
    app.state.appeal_attempts["127.0.0.1"] = [time.monotonic()] * 30
    for _ in range(3):
        retried = await client.post("/api/v1/auth/student-id-appeals", **upload)
        assert retried.status_code == 201, retried.text
        assert retried.json()["id"] == first.json()["id"]
        assert retried.json()["can_upload"] is True
        assert "立即重新上传" in retried.json()["message"]
    async with app.state.database.session_factory() as session:
        assert len(list((await session.scalars(select(StudentCardUploadAttempt))).all())) == 1
        assert len(list((await session.scalars(select(StudentIdAppeal))).all())) == 1
        owner = await session.get(User, user["id"])
        assert owner.identity_frozen is False
    set_review(monkeypatch)
    approved = await client.post("/api/v1/auth/student-id-appeals", **upload)
    assert approved.json()["status"] == "awaiting_owner"
    assert approved.json()["can_upload"] is False


async def test_local_owner_retries_are_immediately_available(client, monkeypatch):
    set_review(monkeypatch)
    user, headers = await owner_identity(client)
    created = await client.post(
        "/api/v1/auth/student-id-appeals", **appeal_upload(user["student_id"])
    )
    assert created.json()["status"] == "awaiting_owner"
    set_review(monkeypatch, "manual_review")
    upload = card_upload(user["student_id"])
    upload["data"].pop("student_id")
    first = await client.post("/api/v1/auth/identity-review/card", headers=headers, **upload)
    assert first.json()["can_upload"] is False
    enable_local_unlimited(client)
    for _ in range(3):
        retried = await client.post("/api/v1/auth/identity-review/card", headers=headers, **upload)
        assert retried.status_code == 200, retried.text
        assert retried.json()["status"] == "owner_retry"
        assert retried.json()["can_upload"] is True
        assert retried.json()["deadline"] is None
        assert retried.json()["next_upload_at"] is None
        assert "立即重新上传" in retried.json()["message"]
    state = await client.get("/api/v1/auth/identity-review", headers=headers)
    assert state.json()["can_upload"] is True
    assert state.json()["next_upload_at"] is None
    assert "本地测试不限次数" in state.json()["message"]


async def test_production_ignores_test_switch_and_enforces_daily_and_hourly_limits(
    client, monkeypatch
):
    set_review(monkeypatch, "rejected")
    user, _ = await owner_identity(client)
    app = client._transport.app
    app.state.settings.environment = "production"
    app.state.settings.local_unlimited_student_card_uploads = True
    page = await client.get("/")
    assert 'data-student-card-limit="daily"' in page.text
    upload = card_upload(user["student_id"])
    first = await client.post("/api/v1/auth/password-reset/card", **upload)
    assert first.json()["status"] == "retry_tomorrow"
    assert first.json()["can_upload"] is False
    second = await client.post("/api/v1/auth/password-reset/card", **upload)
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "student_card_daily_limit"
    app.state.appeal_attempts["127.0.0.1"] = [time.monotonic()] * 30
    limited = await client.post("/api/v1/auth/password-reset/card", **upload)
    assert limited.status_code == 429
    assert "较频繁" in limited.json()["detail"]


async def test_local_upload_policy_is_reflected_in_page_and_reverts_when_disabled(client):
    app = client._transport.app
    assert 'data-student-card-limit="daily"' in (await client.get("/")).text
    enable_local_unlimited(client)
    local = await client.get("/")
    assert 'data-student-card-limit="unlimited"' in local.text
    assert local.headers["cache-control"] == "no-store"
    app.state.settings.local_unlimited_student_card_uploads = False
    assert 'data-student-card-limit="daily"' in (await client.get("/")).text


async def test_registration_password_confirmation_required_and_matches(client):
    payload = registration("202600007701")
    payload.pop("password_confirmation")
    assert (await client.post("/api/v1/auth/register", json=payload)).status_code == 422
    payload["password_confirmation"] = "different-password"
    mismatch = await client.post("/api/v1/auth/register", json=payload)
    assert mismatch.status_code == 422
    assert "两次输入的密码不一致" in mismatch.text
    payload["password_confirmation"] = payload["password"]
    assert (await client.post("/api/v1/auth/register", json=payload)).status_code == 201


async def test_verified_reset_requires_two_passwords_and_revokes_existing_sessions(
    client,
    monkeypatch,
):
    set_review(monkeypatch)
    user, headers = await owner_identity(client)
    checked = await client.post(
        "/api/v1/auth/password-reset/card", **card_upload(user["student_id"])
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["status"] == "approved"
    token = checked.json()["reset_token"]
    assert checked.headers["cache-control"] == "no-store"
    # Proof alone neither changes the password nor authenticates this request.
    assert (await client.get("/api/v1/users/me", headers=headers)).status_code == 200
    assert (
        await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    ).status_code == 401
    new_password = "new-password-456"
    body = {
        "reset_token": token,
        "password": new_password,
        "password_confirmation": "different-password",
    }
    assert (await client.post("/api/v1/auth/password-reset/complete", json=body)).status_code == 422
    body["password_confirmation"] = new_password
    changed = await client.post("/api/v1/auth/password-reset/complete", json=body)
    assert changed.status_code == 200, changed.text
    assert "access_token" not in changed.json()
    assert (await client.get("/api/v1/users/me", headers=headers)).status_code == 401
    assert (await client.post("/api/v1/auth/password-reset/complete", json=body)).status_code == 400
    assert (
        await client.post(
            "/api/v1/auth/token",
            json={
                "account": user["student_id"],
                "password": "test-password-123",
            },
        )
    ).status_code == 401
    logged_in = await client.post(
        "/api/v1/auth/token",
        json={
            "account": user["student_id"],
            "password": new_password,
        },
    )
    assert logged_in.status_code == 200
    new_headers = {"Authorization": f"Bearer {logged_in.json()['access_token']}"}
    assert (await client.get("/api/v1/users/me", headers=new_headers)).status_code == 200


@pytest.mark.parametrize("verdict", ["rejected", "manual_review"])
async def test_failed_reset_daily_limit_persists_and_next_day_retries(client, monkeypatch, verdict):
    set_review(monkeypatch, verdict)
    user, headers = await owner_identity(client)
    first = await client.post("/api/v1/auth/password-reset/card", **card_upload(user["student_id"]))
    assert first.json()["status"] == "retry_tomorrow"
    assert first.json()["reset_token"] is None
    second = await client.post(
        "/api/v1/auth/password-reset/card", **card_upload(user["student_id"])
    )
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "student_card_daily_limit"
    assert int(second.headers["retry-after"]) > 0
    assert (await client.get("/api/v1/users/me", headers=headers)).status_code == 200
    app = client._transport.app
    async with app.state.database.session_factory() as session:
        assert len(list((await session.scalars(select(StudentCardUploadAttempt))).all())) == 1
    await next_day(client)
    set_review(monkeypatch)
    third = await client.post("/api/v1/auth/password-reset/card", **card_upload(user["student_id"]))
    assert third.json()["status"] == "approved"


async def test_invalid_or_unconsented_card_does_not_use_daily_opportunity(client, monkeypatch):
    set_review(monkeypatch)
    user, _ = await owner_identity(client)
    upload = card_upload(user["student_id"])
    upload["data"]["ai_consent"] = "false"
    assert (await client.post("/api/v1/auth/password-reset/card", **upload)).status_code == 422
    upload["data"]["ai_consent"] = "true"
    upload["files"]["student_card"] = ("broken.jpg", b"\xff\xd8\xffbroken", "image/jpeg")
    assert (await client.post("/api/v1/auth/password-reset/card", **upload)).status_code == 422
    good = await client.post("/api/v1/auth/password-reset/card", **card_upload(user["student_id"]))
    assert good.json()["status"] == "approved"


@pytest.mark.parametrize("expired", [True, False])
async def test_reset_proof_expires_and_is_bound_to_current_account(client, monkeypatch, expired):
    set_review(monkeypatch)
    user, _ = await owner_identity(client)
    checked = await client.post(
        "/api/v1/auth/password-reset/card", **card_upload(user["student_id"])
    )
    token = checked.json()["reset_token"]
    app = client._transport.app
    async with app.state.database.session_factory() as session:
        grant = await session.scalar(select(PasswordResetGrant))
        assert grant.token_hash != token
        if expired:
            grant.expires_at = utcnow() - timedelta(seconds=1)
        else:
            owner = await session.get(User, user["id"])
            owner.student_id = None
        await session.commit()
    result = await client.post(
        "/api/v1/auth/password-reset/complete",
        json={
            "reset_token": token,
            "password": "new-password-456",
            "password_confirmation": "new-password-456",
        },
    )
    assert result.status_code == 400


async def test_simultaneous_uploads_review_only_once(client, monkeypatch):
    calls = []

    async def review(_content, _media_type, student_id, _settings):
        calls.append(student_id)
        await asyncio.sleep(0.02)
        return StudentCardReview("approved", "通过", 0.98, student_id)

    monkeypatch.setattr("app.api.review_student_card", review)
    user, _ = await owner_identity(client)
    results = await asyncio.gather(
        *[
            client.post("/api/v1/auth/password-reset/card", **card_upload(user["student_id"]))
            for _ in range(2)
        ]
    )
    assert sorted(r.status_code for r in results) == [200, 429]
    assert len(calls) == 1


async def test_same_password_does_not_consume_reset_proof(client, monkeypatch):
    set_review(monkeypatch)
    user, _ = await owner_identity(client)
    checked = await client.post(
        "/api/v1/auth/password-reset/card", **card_upload(user["student_id"])
    )
    body = {
        "reset_token": checked.json()["reset_token"],
        "password": "test-password-123",
        "password_confirmation": "test-password-123",
    }
    assert (await client.post("/api/v1/auth/password-reset/complete", json=body)).status_code == 422
    body.update(password="new-password-456", password_confirmation="new-password-456")
    assert (await client.post("/api/v1/auth/password-reset/complete", json=body)).status_code == 200


async def test_simultaneous_completions_use_proof_only_once(client, monkeypatch):
    set_review(monkeypatch)
    user, _ = await owner_identity(client)
    checked = await client.post(
        "/api/v1/auth/password-reset/card", **card_upload(user["student_id"])
    )
    body = {
        "reset_token": checked.json()["reset_token"],
        "password": "new-password-456",
        "password_confirmation": "new-password-456",
    }
    responses = await asyncio.gather(
        *[client.post("/api/v1/auth/password-reset/complete", json=body) for _ in range(2)]
    )
    assert sorted(response.status_code for response in responses) == [200, 400]


async def test_unavailable_ai_clearly_reports_service_failure_without_approving(
    client, monkeypatch
):
    async def unavailable(_content, _media_type, _student_id, _settings):
        return student_card_service_error("图片审核服务暂时不可用：HTTPStatusError")

    monkeypatch.setattr("app.api.review_student_card", unavailable)
    user, headers = await owner_identity(client)
    result = await client.post(
        "/api/v1/auth/password-reset/card", **card_upload(user["student_id"])
    )
    assert result.json()["status"] == "server_error"
    assert result.json()["reset_token"] is None
    assert "服务暂时不可用" in result.json()["message"]
    assert "服务器错误" in result.json()["message"]
    assert result.json()["can_upload"] is True
    assert result.json()["next_upload_at"] is None
    assert (await client.get("/api/v1/users/me", headers=headers)).status_code == 200


@pytest.mark.parametrize("verdict", ["rejected", "manual_review"])
async def test_owner_uploaded_bad_card_never_auto_transfers_and_can_retry(
    client,
    monkeypatch,
    verdict,
):
    set_review(monkeypatch)
    user, headers = await owner_identity(client)
    created = await client.post(
        "/api/v1/auth/student-id-appeals", **appeal_upload(user["student_id"])
    )
    assert created.json()["status"] == "awaiting_owner"
    set_review(monkeypatch, verdict)
    upload = card_upload(user["student_id"])
    upload["data"].pop("student_id")
    first = await client.post("/api/v1/auth/identity-review/card", headers=headers, **upload)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "owner_retry"
    assert first.json()["deadline"] is None
    second = await client.post("/api/v1/auth/identity-review/card", headers=headers, **upload)
    assert second.status_code == 429
    cross = await client.post("/api/v1/auth/password-reset/card", **card_upload(user["student_id"]))
    assert cross.status_code == 429
    app = client._transport.app
    async with app.state.database.session_factory() as session:
        appeal = await session.get(StudentIdAppeal, created.json()["id"])
        # Even a leftover deadline must not override evidence already received.
        appeal.owner_deadline = utcnow() - timedelta(days=2)
        await session.commit()
        assert await process_expired_identity_appeals(session) == []
        owner = await session.get(User, user["id"])
        assert owner.is_active
    await next_day(client)
    set_review(monkeypatch)
    retry = await client.post("/api/v1/auth/identity-review/card", headers=headers, **upload)
    assert retry.json()["status"] == "manual_review"


async def test_forgot_password_card_counts_as_frozen_owner_response(client, monkeypatch):
    set_review(monkeypatch)
    user, _ = await owner_identity(client)
    created = await client.post(
        "/api/v1/auth/student-id-appeals", **appeal_upload(user["student_id"])
    )
    checked = await client.post(
        "/api/v1/auth/password-reset/card", **card_upload(user["student_id"])
    )
    assert checked.json()["status"] == "approved"
    app = client._transport.app
    async with app.state.database.session_factory() as session:
        appeal = await session.get(StudentIdAppeal, created.json()["id"])
        assert appeal.owner_card_path
        assert appeal.status == "manual_review"
        assert await process_expired_identity_appeals(session, utcnow() + timedelta(days=3)) == []


async def test_unverified_reset_cannot_impersonate_disputed_owner_response(client, monkeypatch):
    set_review(monkeypatch)
    user, _ = await owner_identity(client)
    created = await client.post(
        "/api/v1/auth/student-id-appeals", **appeal_upload(user["student_id"])
    )
    set_review(monkeypatch, "rejected")
    result = await client.post(
        "/api/v1/auth/password-reset/card", **card_upload(user["student_id"])
    )
    assert result.json()["status"] == "retry_tomorrow"
    app = client._transport.app
    async with app.state.database.session_factory() as session:
        appeal = await session.get(StudentIdAppeal, created.json()["id"])
        assert appeal.status == "awaiting_owner"
        assert appeal.owner_card_path is None
        assert appeal.owner_deadline is not None
        paths = await asyncio.to_thread(
            lambda: list(Path(app.state.settings.identity_appeal_directory).rglob("*.jpg"))
        )
        assert len(paths) == 1  # Claimant evidence only; failed unauthenticated proof is discarded.
        expired = await process_expired_identity_appeals(session, utcnow() + timedelta(days=2))
        assert len(expired) == 1


async def test_claimant_daily_limit_follows_new_account_after_transfer(client, monkeypatch):
    set_review(monkeypatch)
    user, headers = await owner_identity(client)
    await client.post("/api/v1/auth/student-id-appeals", **appeal_upload(user["student_id"]))
    released = await client.post("/api/v1/auth/identity-review/relinquish", headers=headers)
    assert released.json()["status"] == "transferred"
    # A newly created claimant account cannot obtain a second allowance on the same day.
    repeated = await client.post(
        "/api/v1/auth/password-reset/card", **card_upload(user["student_id"])
    )
    assert repeated.status_code == 429


async def test_interrupted_claimant_review_can_retry_next_day(client, monkeypatch):
    set_review(monkeypatch, "rejected")
    user, _ = await owner_identity(client)
    first = await client.post(
        "/api/v1/auth/student-id-appeals", **appeal_upload(user["student_id"])
    )
    app = client._transport.app
    async with app.state.database.session_factory() as session:
        appeal = await session.get(StudentIdAppeal, first.json()["id"])
        appeal.status = "agent_review"
        # An earlier client may not have provided the registration form yet.
        appeal.claimant_profile = {}
        appeal.claimant_password_hash = None
        await session.commit()
    await next_day(client)
    set_review(monkeypatch)
    retried = await client.post(
        "/api/v1/auth/student-id-appeals", **appeal_upload(user["student_id"])
    )
    assert retried.json()["id"] == first.json()["id"]
    assert retried.json()["status"] == "awaiting_owner"


@pytest.mark.parametrize("confidence,extracted", [(0.84, None), (0.99, "WRONG12345")])
async def test_reset_fails_closed_for_low_confidence_or_wrong_id(
    client,
    monkeypatch,
    confidence,
    extracted,
):
    async def review(_content, _media_type, student_id, _settings):
        return StudentCardReview("approved", "测试结果", confidence, extracted or student_id)

    monkeypatch.setattr("app.api.review_student_card", review)
    user, _ = await owner_identity(client)
    result = await client.post(
        "/api/v1/auth/password-reset/card", **card_upload(user["student_id"])
    )
    assert result.json()["status"] == "retry_tomorrow"
    assert result.json()["reset_token"] is None


async def test_claimant_failed_review_retries_next_day_without_duplicate_appeal(
    client, monkeypatch
):
    set_review(monkeypatch, "rejected")
    user, owner_headers = await owner_identity(client)
    first = await client.post(
        "/api/v1/auth/student-id-appeals", **appeal_upload(user["student_id"])
    )
    assert first.json()["status"] == "claimant_retry"
    assert first.json()["can_upload"] is False
    assert (await client.get("/api/v1/users/me", headers=owner_headers)).status_code == 200
    listing = (await client.get("/api/v1/admin/student-id-appeals")).json()
    assert listing[0]["can_resolve"] is False
    blocked = await client.post(
        f"/api/v1/admin/student-id-appeals/{first.json()['id']}/resolve",
        json={"decision": "transfer_to_claimant"},
    )
    assert blocked.status_code == 403
    assert (
        await client.post("/api/v1/auth/student-id-appeals", **appeal_upload(user["student_id"]))
    ).status_code == 429
    await next_day(client)
    set_review(monkeypatch)
    second = await client.post(
        "/api/v1/auth/student-id-appeals", **appeal_upload(user["student_id"])
    )
    assert second.json()["status"] == "awaiting_owner"
    assert second.json()["id"] == first.json()["id"]
    assert (await client.get("/api/v1/users/me", headers=owner_headers)).status_code == 423
    assert (await client.get("/api/v1/admin/student-id-appeals")).json()[0]["can_resolve"] is True
    app = client._transport.app
    async with app.state.database.session_factory() as session:
        appeal = await session.get(StudentIdAppeal, first.json()["id"])
        assert "password_confirmation" not in appeal.claimant_profile
        assert "password" not in appeal.claimant_profile


def test_daily_reset_uses_beijing_midnight_not_rolling_24_hours():
    now = datetime(2026, 9, 27, 15, 59, tzinfo=UTC)
    assert upload_day(now) == "2026-09-27"
    assert upload_day(now + timedelta(minutes=2)) == "2026-09-28"
    assert next_upload_time(now).astimezone(UTC) == datetime(2026, 9, 27, 16, tzinfo=UTC)


def test_compression_bounds_size_dimensions_and_strips_metadata():
    raw = BytesIO()
    image = Image.new("RGB", (3000, 2000), "purple")
    exif = Image.Exif()
    exif[270] = "private metadata"
    image.save(raw, format="JPEG", quality=100, exif=exif)
    compressed = compress_student_card(raw.getvalue())
    assert len(compressed) <= 256 * 1024
    with Image.open(BytesIO(compressed)) as result:
        assert max(result.size) <= 1280
        assert not result.getexif()


@pytest.mark.parametrize(
    "content", [None, [], "[]", '{"verdict":"approved","confidence":NaN}', "not-json"]
)
def test_unreliable_ai_output_does_not_approve(content):
    assert _parse_review(content, "202600007701").verdict != "approved"
