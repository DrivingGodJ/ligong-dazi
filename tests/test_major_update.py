from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import Activity, Notification, User
from app.notifications import dispatch_push, enqueue_activity_reminders
from app.post_activity import process_completed_activities
from tests.conftest import register_user


def activity_payload(start: datetime, *, policy: str = "open") -> dict:
    return {
        "title": "一起去运动",
        "category": "跑步",
        "location": "体育馆",
        "capacity": 4,
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(hours=2)).isoformat(),
        "join_policy": policy,
    }


async def test_campus_required_and_cross_campus_isolation(client) -> None:
    _, nj = await register_user(client, "isolate-nj@example.com", "南京同学")
    _, jy = await register_user(client, "isolate-jy@example.com", "江阴同学")
    changed = await client.patch("/api/v1/users/me", headers=jy, json={"campus": "江阴"})
    assert changed.status_code == 200
    assert (
        await client.patch("/api/v1/users/me", headers=jy, json={"campus": None})
    ).status_code == 422
    start = datetime.now(UTC) + timedelta(days=1)
    event = (
        await client.post("/api/v1/activities", headers=nj, json=activity_payload(start))
    ).json()
    assert event["campus"] == "南京"
    assert (await client.get("/api/v1/activities/square", headers=jy)).json()["items"] == []
    assert (
        await client.post(f"/api/v1/activities/{event['id']}/join", headers=jy)
    ).status_code == 403
    assert (
        await client.get(f"/api/v1/activities/{event['id']}/participants", headers=jy)
    ).status_code == 404
    preview = await client.post(
        "/api/v1/matches/preview",
        headers=jy,
        json={
            "category": "跑步",
            "starts_at": start.isoformat(),
            "ends_at": (start + timedelta(hours=2)).isoformat(),
            "people_needed": 2,
        },
    )
    assert preview.status_code == 201, preview.text
    assert all(item["candidate_type"] != "activity" for item in preview.json()["candidates"])

    app = client._transport.app  # type: ignore[attr-defined]
    async with app.state.database.session_factory() as session:
        old = await session.scalar(select(User).where(User.email == "isolate-jy@example.com"))
        old.campus = None
        await session.commit()
    assert (await client.get("/api/v1/activities/square", headers=jy)).status_code == 403
    assert (
        await client.post(
            "/api/v1/matches/preview",
            headers=jy,
            json={
                "category": "跑步",
                "starts_at": start.isoformat(),
                "ends_at": (start + timedelta(hours=2)).isoformat(),
                "people_needed": 2,
            },
        )
    ).status_code == 403


async def test_unanimous_approval_profile_and_notifications(client) -> None:
    _, owner = await register_user(client, "approve-owner@example.com", "发起人")
    _, member = await register_user(client, "approve-member@example.com", "已有成员")
    _, applicant = await register_user(client, "approve-new@example.com", "申请人")
    start = datetime.now(UTC) + timedelta(days=1)
    event = (
        await client.post(
            "/api/v1/activities", headers=owner, json=activity_payload(start, policy="approval")
        )
    ).json()
    event_id = event["id"]
    first = await client.post(f"/api/v1/activities/{event_id}/join", headers=member)
    assert "申请" in first.json()["message"]
    assert (await client.get("/api/v1/activities/mine", headers=member)).json() == []
    pending = (
        await client.get(f"/api/v1/activities/{event_id}/applications", headers=owner)
    ).json()
    assert len(pending) == 1
    profile = await client.get(
        f"/api/v1/users/{pending[0]['applicant']['id']}/profile", headers=owner
    )
    assert profile.status_code == 200
    assert "password_hash" not in profile.text
    accepted = await client.post(
        f"/api/v1/activities/{event_id}/applications/{pending[0]['id']}/respond",
        headers=owner,
        json={"decision": "approved"},
    )
    assert accepted.json()["status"] == "approved"
    second = await client.post(f"/api/v1/activities/{event_id}/join", headers=applicant)
    assert second.status_code == 200
    request = (
        await client.get(f"/api/v1/activities/{event_id}/applications", headers=owner)
    ).json()[0]
    owner_response = await client.post(
        f"/api/v1/activities/{event_id}/applications/{request['id']}/respond",
        headers=owner,
        json={"decision": "approved"},
    )
    assert owner_response.json()["status"] == "pending"
    assert owner_response.json()["required_approvals"] == 2
    denied = await client.get(f"/api/v1/activities/{event_id}/applications", headers=applicant)
    assert denied.status_code == 404
    final = await client.post(
        f"/api/v1/activities/{event_id}/applications/{request['id']}/respond",
        headers=member,
        json={"decision": "approved"},
    )
    assert final.json()["status"] == "approved"
    assert final.json()["approvals"] == final.json()["required_approvals"] == 2
    assert len((await client.get("/api/v1/activities/mine", headers=applicant)).json()) == 1
    messages = (await client.get("/api/v1/notifications", headers=member)).json()
    assert any(item["kind"] == "join_application" for item in messages)
    read = await client.post(f"/api/v1/notifications/{messages[0]['id']}/read", headers=member)
    assert read.json()["read_at"] is not None


async def test_reminder_fifteen_minutes_idempotent(client) -> None:
    _, owner = await register_user(client, "soon-owner@example.com", "提醒测试")
    start = datetime.now(UTC) + timedelta(minutes=14)
    created = await client.post("/api/v1/activities", headers=owner, json=activity_payload(start))
    assert created.status_code == 201
    app = client._transport.app  # type: ignore[attr-defined]
    async with app.state.database.session_factory() as session:
        await enqueue_activity_reminders(session)
        await session.commit()
        await enqueue_activity_reminders(session)
        await session.commit()
        notices = list(
            (
                await session.scalars(
                    select(Notification).where(Notification.kind == "starts_soon")
                )
            ).all()
        )
        assert len(notices) == 1
        assert "15 分钟" in notices[0].body
    assert (await client.get("/manifest.webmanifest")).status_code == 200
    assert (await client.get("/service-worker.js")).status_code == 200
    assert (await client.get("/api/v1/push/public-key")).json()["public_key"]


async def test_completed_activity_notifies_each_companion_once_but_not_solo(client) -> None:
    _, owner = await register_user(client, "review-owner@example.com", "活动发起人")
    _, partner = await register_user(client, "review-partner@example.com", "活动搭子")
    start = datetime.now(UTC) + timedelta(days=1)
    paired = (
        await client.post("/api/v1/activities", headers=owner, json=activity_payload(start))
    ).json()
    solo = (
        await client.post(
            "/api/v1/activities", headers=owner, json=activity_payload(start + timedelta(days=1))
        )
    ).json()
    assert (
        await client.post(f"/api/v1/activities/{paired['id']}/join", headers=partner)
    ).status_code == 200
    assert not any(
        item["kind"] == "feedback_due"
        for item in (await client.get("/api/v1/notifications", headers=partner)).json()
    )

    app = client._transport.app  # type: ignore[attr-defined]
    async with app.state.database.session_factory() as session:
        for activity_id in (paired["id"], solo["id"]):
            activity = await session.get(Activity, activity_id)
            assert activity is not None
            activity.starts_at = datetime.now(UTC) - timedelta(hours=2)
            activity.ends_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()
        assert await process_completed_activities(session) == 2
        await session.commit()
        assert await process_completed_activities(session) == 0
        await session.commit()
        notices = list(
            (await session.scalars(select(Notification).where(Notification.kind == "feedback_due")))
            .all()
        )
        assert len(notices) == 2
        assert {item.event_key for item in notices} == {f"feedback_due:{paired['id']}"}
        assert all(item.url == "/?tab=activities" for item in notices)
    mine = (await client.get("/api/v1/activities/mine", headers=partner)).json()
    assert next(item for item in mine if item["activity"]["id"] == paired["id"])[
        "needs_feedback"
    ]


async def test_invite_public_join_and_time_vote_each_create_messages(client) -> None:
    _, owner = await register_user(client, "notify-owner@example.com", "发起人")
    _, partner = await register_user(client, "notify-partner@example.com", "候选搭子")
    start = datetime.now(UTC) + timedelta(days=3)
    preview = await client.post(
        "/api/v1/matches/preview",
        headers=owner,
        json={
            "category": "跑步",
            "starts_at": start.isoformat(),
            "ends_at": (start + timedelta(hours=2)).isoformat(),
            "location": "体育馆",
            "people_needed": 2,
        },
    )
    assert preview.status_code == 201, preview.text
    candidate = next(
        item["candidate_id"]
        for item in preview.json()["candidates"]
        if item["candidate_type"] == "user"
    )
    confirmed = await client.post(
        f"/api/v1/matches/{preview.json()['match_request_id']}/confirm",
        headers=owner,
        json={"candidate_user_ids": [candidate]},
    )
    assert confirmed.status_code == 200, confirmed.text
    invitations = (await client.get("/api/v1/notifications", headers=partner)).json()
    assert any(item["kind"] == "invitation" for item in invitations)

    second = (
        await client.post(
            "/api/v1/activities", headers=owner, json=activity_payload(start + timedelta(days=2))
        )
    ).json()
    joined = await client.post(f"/api/v1/activities/{second['id']}/join", headers=partner)
    assert joined.status_code == 200, joined.text
    assert any(
        item["kind"] == "public_join"
        for item in (await client.get("/api/v1/notifications", headers=owner)).json()
    )

    rescheduled = start + timedelta(days=3)
    vote = await client.post(
        f"/api/v1/activities/{second['id']}/time-votes",
        headers=partner,
        json={
            "starts_at": rescheduled.isoformat(),
            "ends_at": (rescheduled + timedelta(hours=2)).isoformat(),
        },
    )
    assert vote.status_code == 201, vote.text
    assert any(
        item["kind"] == "time_vote"
        for item in (await client.get("/api/v1/notifications", headers=owner)).json()
    )


async def test_push_outbox_sends_once_and_rejects_unknown_endpoints(client, monkeypatch) -> None:
    _, headers = await register_user(client, "push-owner@example.com", "推送测试")
    invalid = await client.post(
        "/api/v1/push/subscriptions",
        headers=headers,
        json={
            "endpoint": "https://example.com/internal",
            "p256dh": "a" * 30,
            "auth": "b" * 15,
        },
    )
    assert invalid.status_code == 422
    subscribed = await client.post(
        "/api/v1/push/subscriptions",
        headers=headers,
        json={
            "endpoint": "https://fcm.googleapis.com/fcm/send/test",
            "p256dh": "a" * 30,
            "auth": "b" * 15,
        },
    )
    assert subscribed.status_code == 201
    app = client._transport.app  # type: ignore[attr-defined]
    delivered = []

    def fake_webpush(**kwargs):
        delivered.append(kwargs)

    monkeypatch.setattr("app.notifications.webpush", fake_webpush)
    async with app.state.database.session_factory() as session:
        user = await session.scalar(select(User).where(User.email == "push-owner@example.com"))
        session.add(
            Notification(
                user_id=user.id,
                event_key="test:event",
                kind="invitation",
                title="收到邀请",
                body="朋友在等你",
                url="/?tab=invitations",
            )
        )
        await session.commit()
        await dispatch_push(session, app.state.settings.push_key_file_path)
        await session.commit()
        await dispatch_push(session, app.state.settings.push_key_file_path)
        assert len(delivered) == 1
        assert json.loads(delivered[0]["data"])["title"] == "收到邀请"
        assert delivered[0]["ttl"] == 86_400
