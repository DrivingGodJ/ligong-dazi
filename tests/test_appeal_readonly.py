from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from app.identity_appeals import process_expired_identity_appeals, transfer_student_id
from app.identity_verification import StudentCardReview
from app.models import StudentIdAppeal, User, utcnow
from tests.conftest import register_user
from tests.test_student_registration import appeal_upload


@pytest.mark.parametrize(
    ("appeal_status", "verdict", "service_error"),
    [
        ("claimant_retry", "rejected", False),
        ("claimant_retry", "manual_review", False),
        ("claimant_retry", "manual_review", True),
        ("claimant_retry", "approved", False),
        ("agent_review", "approved", False),
        ("awaiting_owner", "rejected", False),
        ("owner_review", "manual_review", True),
        ("manual_review", "rejected", False),
        ("manual_review", "approved", True),
        ("owner_retry", "manual_review", False),
    ],
)
async def test_unapproved_appeals_cannot_be_mutated_by_admin_or_transfer_helper(
    client, monkeypatch, appeal_status, verdict, service_error
):
    async def reject_card(_content, _media_type, sid, _settings):
        return StudentCardReview("rejected", "学生卡与申请学号不符", 0.98, sid)

    monkeypatch.setattr("app.api.review_student_card", reject_card)
    _, owner_headers = await register_user(client, "readonly-owner@example.com", "原账号")
    owner = (await client.get("/api/v1/users/me", headers=owner_headers)).json()
    created = await client.post(
        "/api/v1/auth/student-id-appeals", **appeal_upload(owner["student_id"])
    )
    assert created.status_code == 201
    appeal_id = created.json()["id"]
    app = client._transport.app
    async with app.state.database.session_factory() as session:
        appeal = await session.get(StudentIdAppeal, appeal_id)
        appeal.status = appeal_status
        appeal.owner_deadline = utcnow() - timedelta(seconds=1)
        appeal.claimant_agent_review = StudentCardReview(
            verdict, "测试初审结果", 0.98, owner["student_id"], service_error
        ).as_dict()
        original_profile = dict(appeal.claimant_profile)
        original_hash = appeal.claimant_password_hash
        original_note = appeal.resolution_note
        card_path = Path(appeal.claimant_card_path)
        original_card = await asyncio.to_thread(card_path.read_bytes)
        await session.commit()

    item = (await client.get("/api/v1/admin/student-id-appeals")).json()[0]
    assert item["can_resolve"] is False
    for action, payload in (
        ("handled", None),
        ("resolve", {"decision": "keep_owner"}),
        ("resolve", {"decision": "transfer_to_claimant"}),
    ):
        response = await client.post(
            f"/api/v1/admin/student-id-appeals/{appeal_id}/{action}", json=payload
        )
        assert response.status_code == 403, response.text
        assert "仅可查看" in response.json()["detail"]

    card = await client.get(f"/api/v1/admin/student-id-appeals/{appeal_id}/card/claimant")
    assert card.status_code == 200
    assert card.content == original_card
    async with app.state.database.session_factory() as session:
        appeal = await session.get(StudentIdAppeal, appeal_id)
        assert await transfer_student_id(session, appeal, "尝试绕过后台限制") is None
        assert await process_expired_identity_appeals(session, utcnow() + timedelta(days=2)) == []
        await session.commit()
        assert appeal.status == appeal_status
        assert appeal.claimant_profile == original_profile
        assert appeal.claimant_password_hash == original_hash
        assert appeal.resolution_note == original_note
        assert appeal.resolved_at is None
        stored_owner = await session.get(User, owner["id"])
        assert stored_owner.student_id == owner["student_id"]
        assert stored_owner.is_active is True
        assert stored_owner.identity_frozen is False
        assert stored_owner.identity_appeal_id is None
    assert await asyncio.to_thread(card_path.read_bytes) == original_card
    assert (await client.get("/api/v1/users/me", headers=owner_headers)).status_code == 200


async def test_approved_appeal_can_still_be_closed_by_admin(client, monkeypatch):
    async def approve_card(_content, _media_type, sid, _settings):
        return StudentCardReview("approved", "学生卡清晰且学号一致", 0.98, sid)

    monkeypatch.setattr("app.api.review_student_card", approve_card)
    _, headers = await register_user(client, "approved-keep-owner@example.com", "原账号")
    owner = (await client.get("/api/v1/users/me", headers=headers)).json()
    created = await client.post(
        "/api/v1/auth/student-id-appeals", **appeal_upload(owner["student_id"])
    )
    appeal_id = created.json()["id"]
    assert (await client.get("/api/v1/users/me", headers=headers)).status_code == 423
    response = await client.post(f"/api/v1/admin/student-id-appeals/{appeal_id}/handled")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "owner_confirmed"
    assert response.json()["can_resolve"] is False
    assert (await client.get("/api/v1/users/me", headers=headers)).status_code == 200
