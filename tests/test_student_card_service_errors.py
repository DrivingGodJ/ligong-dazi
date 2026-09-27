from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.core import Settings
from app.identity_verification import (
    StudentCardReview,
    _parse_review,
    review_student_card,
    student_card_service_error,
)
from app.models import PasswordResetGrant, StudentCardUploadAttempt, StudentIdAppeal
from app.student_cards import upload_day
from tests.conftest import STUDENT_CARD_IMAGE
from tests.test_account_recovery import card_upload, owner_identity, set_review
from tests.test_student_registration import appeal_upload


@pytest.mark.parametrize("failure", ["401", "429", "503", "timeout", "empty", "json", "content"])
async def test_provider_failures_have_explicit_service_error(failure):
    def respond(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("test timeout", request=request)
        if failure in {"401", "429", "503"}:
            return httpx.Response(int(failure), json={"error": {"message": "test failure"}})
        if failure == "empty":
            return httpx.Response(200, json={"choices": []})
        if failure == "json":
            return httpx.Response(200, text="invalid json")
        return httpx.Response(200, json={"choices": [{"message": {"content": None}}]})

    settings = Settings(
        _env_file=None,
        ai_provider="openai_compatible",
        ai_api_key=SecretStr("fake-test-key"),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        result = await review_student_card(
            STUDENT_CARD_IMAGE, "image/jpeg", "TEST0001", settings, client=http
        )
    assert result.service_error is True
    assert result.verdict != "approved"
    assert result.confidence == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"verdict": []},
        {"confidence": "not-a-number"},
        {"confidence": float("nan")},
        {"confidence": float("inf")},
        {"confidence": True},
        {"confidence": 1.5},
        {"reason": []},
        {"reason": ""},
        {"student_id": []},
        {"student_id": None},
    ],
)
def test_malformed_model_decisions_are_service_errors(changes):
    data = {"verdict": "approved", "confidence": 0.98, "student_id": "TEST0001", "reason": "通过"}
    data.update(changes)
    result = _parse_review(json.dumps(data), "TEST0001")
    assert result.service_error is True
    assert result.verdict != "approved"


@pytest.mark.parametrize("verdict", ["approved", "rejected", "manual_review"])
def test_model_cannot_refund_itself_by_setting_service_error(verdict):
    content = json.dumps(
        {
            "verdict": verdict,
            "confidence": 0.95,
            "student_id": "TEST0001",
            "reason": "正常核验结果",
            "service_error": True,
        }
    )
    assert _parse_review(content, "TEST0001").service_error is False


@pytest.mark.parametrize("kind", ["reset", "claimant", "owner"])
@pytest.mark.parametrize("raised", [False, True])
async def test_all_card_entries_refund_only_server_errors(client, monkeypatch, kind, raised):
    set_review(monkeypatch)
    user, headers = await owner_identity(client)
    if kind == "owner":
        created = await client.post(
            "/api/v1/auth/student-id-appeals", **appeal_upload(user["student_id"])
        )
        assert created.json()["status"] == "awaiting_owner"
    app = client._transport.app
    app.state.settings.environment = "production"
    app.state.settings.local_unlimited_student_card_uploads = True
    async with app.state.database.session_factory() as session:
        session.add(
            StudentCardUploadAttempt(
                subject_key="user:unrelated-test-user",
                upload_day=upload_day(),
                purpose="owner_appeal",
            )
        )
        await session.commit()
        original_records = set((await session.scalars(select(StudentCardUploadAttempt.id))).all())

    async def failure(_content, _media_type, _student_id, _settings):
        if raised:
            raise RuntimeError("test provider error")
        return student_card_service_error("图片审核服务暂时不可用：HTTPStatusError")

    monkeypatch.setattr("app.api.review_student_card", failure)
    if kind == "reset":
        path, kwargs = "/api/v1/auth/password-reset/card", card_upload(user["student_id"])
    elif kind == "claimant":
        path, kwargs = "/api/v1/auth/student-id-appeals", appeal_upload(user["student_id"])
    else:
        path, kwargs = "/api/v1/auth/identity-review/card", card_upload(user["student_id"])
        kwargs["data"].pop("student_id")
        kwargs["headers"] = headers
    for _ in range(2):
        failed = await client.post(path, **kwargs)
        assert failed.status_code in {200, 201}, failed.text
        result = failed.json()
        assert result["service_error"] is True
        assert result["can_upload"] is True
        assert result.get("next_upload_at") is None
        assert "服务器错误" in result["message"]
        assert "未占用" in result["message"]
        assert "明天" not in result["message"]
        async with app.state.database.session_factory() as session:
            records = set((await session.scalars(select(StudentCardUploadAttempt.id))).all())
            assert records == original_records
            assert await session.scalar(select(PasswordResetGrant)) is None
    if kind == "owner":
        state = (await client.get("/api/v1/auth/identity-review", headers=headers)).json()
        assert state["service_error"] is True
        assert state["can_upload"] is True
        assert state["deadline"] is None
        assert state["next_upload_at"] is None
        assert "服务器错误" in state["message"]
    # A valid model rejection consumes the allowance, even if its explanation looks like an error.
    async def rejection(_content, _media_type, _student_id, _settings):
        return StudentCardReview("manual_review", "服务器错误（照片上的文字）", 0.9)

    monkeypatch.setattr("app.api.review_student_card", rejection)
    reviewed = (await client.post(path, **kwargs)).json()
    assert reviewed["service_error"] is False
    assert reviewed["can_upload"] is False
    assert (await client.post(path, **kwargs)).status_code == 429
    async with app.state.database.session_factory() as session:
        records = set((await session.scalars(select(StudentCardUploadAttempt.id))).all())
        assert original_records.issubset(records)
        assert len(records) == len(original_records) + 1


async def test_service_error_refund_preserves_concurrent_reservation_guard(client, monkeypatch):
    user, _ = await owner_identity(client)
    started, finish = asyncio.Event(), asyncio.Event()

    async def failure(_content, _media_type, _student_id, _settings):
        started.set()
        await finish.wait()
        return student_card_service_error("图片审核服务超时")

    monkeypatch.setattr("app.api.review_student_card", failure)
    upload = card_upload(user["student_id"])
    first = asyncio.create_task(client.post("/api/v1/auth/password-reset/card", **upload))
    await asyncio.wait_for(started.wait(), 5)
    try:
        second = await client.post("/api/v1/auth/password-reset/card", **upload)
        assert second.status_code == 429
    finally:
        finish.set()
    result = await first
    assert result.json()["status"] == "server_error"
    set_review(monkeypatch, "rejected")
    retried = await client.post("/api/v1/auth/password-reset/card", **upload)
    assert retried.json()["status"] == "retry_tomorrow"
    assert (await client.post("/api/v1/auth/password-reset/card", **upload)).status_code == 429


async def test_unverified_service_failure_does_not_cancel_owner_deadline(client, monkeypatch):
    set_review(monkeypatch)
    user, _ = await owner_identity(client)
    created = await client.post(
        "/api/v1/auth/student-id-appeals", **appeal_upload(user["student_id"])
    )
    async def failure(_content, _media_type, _student_id, _settings):
        return student_card_service_error("图片审核服务超时")

    monkeypatch.setattr("app.api.review_student_card", failure)
    result = await client.post(
        "/api/v1/auth/password-reset/card", **card_upload(user["student_id"])
    )
    assert result.json()["status"] == "server_error"
    app = client._transport.app
    async with app.state.database.session_factory() as session:
        appeal = await session.get(StudentIdAppeal, created.json()["id"])
        assert appeal.owner_card_path is None
        assert appeal.owner_deadline is not None
        assert appeal.status == "awaiting_owner"
        photos = await asyncio.to_thread(
            lambda: list(Path(app.state.settings.identity_appeal_directory).rglob("*.jpg"))
        )
        assert len(photos) == 1
