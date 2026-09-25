from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.models import Activity, ActivityPhoto
from tests.conftest import register_user

ONE_PIXEL_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
ONE_PIXEL_PNG_BYTES = base64.b64decode(ONE_PIXEL_PNG.split(",", 1)[1])


async def create_activity(client, headers, *, starts_at, capacity=3, title="南体夜跑") -> dict:
    response = await client.post(
        "/api/v1/activities",
        headers=headers,
        json={
            "title": title,
            "category": "跑步",
            "starts_at": starts_at.isoformat(),
            "ends_at": (starts_at + timedelta(hours=2)).isoformat(),
            "location": "南京理工大学南区体育馆",
            "capacity": capacity,
            "description": "轻松跑两圈",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_square_fuzzy_location_join_time_vote_and_calendar(client) -> None:
    _, owner_headers = await register_user(client, "square-owner@njust.edu.cn", "发起人")
    _, member_headers = await register_user(client, "square-member@njust.edu.cn", "搭子甲")
    _, third_headers = await register_user(client, "square-third@njust.edu.cn", "搭子乙")
    await client.patch(
        "/api/v1/users/me",
        headers=owner_headers,
        json={"gender": "male"},
    )
    await client.patch(
        "/api/v1/users/me",
        headers=member_headers,
        json={
            "gender": "female",
            "interests": ["跑步"],
            "preferred_locations": ["南体"],
        },
    )
    await client.patch(
        "/api/v1/users/me",
        headers=third_headers,
        json={"gender": "male"},
    )
    starts_at = datetime.now(UTC) + timedelta(days=2)
    activity = await create_activity(
        client, owner_headers, starts_at=starts_at, capacity=3
    )

    square = await client.get(
        "/api/v1/activities/square?location=南体&sort=recommended",
        headers=member_headers,
    )
    assert square.status_code == 200, square.text
    square_item = next(
        item for item in square.json()["items"] if item["activity"]["id"] == activity["id"]
    )
    assert square_item["recommendation_score"] >= 80
    assert square_item["activity"]["gender_counts"] == {
        "male": 1,
        "female": 0,
        "undisclosed": 0,
    }
    assert any("地点" in reason for reason in square_item["recommendation_reasons"])

    joined = await client.post(
        f"/api/v1/activities/{activity['id']}/join", headers=member_headers
    )
    assert joined.status_code == 200, joined.text
    joined_third = await client.post(
        f"/api/v1/activities/{activity['id']}/join", headers=third_headers
    )
    assert joined_third.status_code == 200, joined_third.text
    assert joined_third.json()["activity"]["status"] == "formed"
    assert joined_third.json()["activity"]["gender_counts"]["male"] == 2
    assert joined_third.json()["activity"]["gender_counts"]["female"] == 1

    participants = await client.get(
        f"/api/v1/activities/{activity['id']}/participants",
        headers=member_headers,
    )
    assert participants.status_code == 200, participants.text
    assert {item["gender"] for item in participants.json()} == {"male", "female"}
    owner_id = next(item["id"] for item in participants.json() if item["display_name"] == "发起人")
    profile = await client.get(
        f"/api/v1/users/{owner_id}/profile",
        headers=member_headers,
    )
    assert profile.status_code == 200, profile.text
    assert profile.json()["user"]["gender"] == "male"
    assert profile.json()["system_profile"]["completed_activity_count"] == 0
    assert "email" not in profile.json()["user"]
    assert "hidden_profile" not in profile.json()["user"]
    assert "allow_invitations" not in profile.json()["user"]

    proposed_start = starts_at + timedelta(days=1, hours=1)
    vote = await client.post(
        f"/api/v1/activities/{activity['id']}/time-votes",
        headers=owner_headers,
        json={
            "starts_at": proposed_start.isoformat(),
            "ends_at": (proposed_start + timedelta(hours=2)).isoformat(),
        },
    )
    assert vote.status_code == 201, vote.text
    assert vote.json()["required_approvals"] == 2
    first_approval = await client.post(
        f"/api/v1/activities/{activity['id']}/time-votes/{vote.json()['id']}/respond",
        headers=member_headers,
        json={"decision": "approved"},
    )
    assert first_approval.json()["status"] == "pending"
    final_approval = await client.post(
        f"/api/v1/activities/{activity['id']}/time-votes/{vote.json()['id']}/respond",
        headers=third_headers,
        json={"decision": "approved"},
    )
    assert final_approval.json()["status"] == "approved"

    calendar = await client.get(
        f"/api/v1/activities/{activity['id']}/calendar.ics", headers=member_headers
    )
    assert calendar.status_code == 200
    assert calendar.headers["content-type"].startswith("text/calendar")
    assert b"BEGIN:VEVENT" in calendar.content
    assert proposed_start.strftime("%Y%m%dT%H%M%SZ").encode() in calendar.content


async def test_photo_window_permissions_limit_and_end_cleanup(client) -> None:
    _, owner_headers = await register_user(client, "photo-owner@njust.edu.cn", "拍照人")
    _, member_headers = await register_user(client, "photo-member@njust.edu.cn", "看照片的人")
    _, outsider_headers = await register_user(client, "photo-out@njust.edu.cn", "路人")
    starts_at = datetime.now(UTC) + timedelta(minutes=10)
    activity = await create_activity(
        client, owner_headers, starts_at=starts_at, capacity=2, title="马上集合"
    )
    joined = await client.post(
        f"/api/v1/activities/{activity['id']}/join", headers=member_headers
    )
    assert joined.status_code == 200
    outsider_upload = await client.post(
        f"/api/v1/activities/{activity['id']}/photos/file",
        headers=outsider_headers,
        files={"file": ("meeting.png", ONE_PIXEL_PNG_BYTES, "image/png")},
    )
    assert outsider_upload.status_code == 404

    for index in range(6):
        if index % 2:
            uploaded = await client.post(
                f"/api/v1/activities/{activity['id']}/photos/file",
                headers=owner_headers,
                files={"file": ("meeting.png", ONE_PIXEL_PNG_BYTES, "image/png")},
            )
        else:
            uploaded = await client.post(
                f"/api/v1/activities/{activity['id']}/photos",
                headers=owner_headers,
                json={"data_url": ONE_PIXEL_PNG},
            )
        assert uploaded.status_code == 201, uploaded.text

    too_large = await client.post(
        f"/api/v1/activities/{activity['id']}/photos/file",
        headers=owner_headers,
        files={"file": ("oversize.png", b"x" * (5 * 1024 * 1024 + 1), "image/png")},
    )
    assert too_large.status_code == 413
    bad_type = await client.post(
        f"/api/v1/activities/{activity['id']}/photos/file",
        headers=owner_headers,
        files={"file": ("meeting.png", ONE_PIXEL_PNG_BYTES, "text/plain")},
    )
    assert bad_type.status_code == 422

    photos = await client.get(
        f"/api/v1/activities/{activity['id']}/photos", headers=member_headers
    )
    assert photos.status_code == 200
    assert len(photos.json()) == 5
    outsider = await client.get(
        f"/api/v1/activities/{activity['id']}/photos", headers=outsider_headers
    )
    assert outsider.status_code == 404
    photo_content = await client.get(photos.json()[0]["content_url"], headers=member_headers)
    assert photo_content.status_code == 200
    assert photo_content.headers["content-type"] == "image/png"
    assert photo_content.headers["cache-control"] == "private, no-store"

    app = client._transport.app  # type: ignore[attr-defined]
    async with app.state.database.session_factory() as session:
        stored = await session.get(Activity, activity["id"])
        assert stored is not None
        stored.starts_at = datetime.now(UTC) - timedelta(hours=2)
        stored.ends_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()

    expired = await client.get(
        f"/api/v1/activities/{activity['id']}/photos", headers=member_headers
    )
    assert expired.status_code == 200
    assert expired.json() == []
    deleted_content = await client.get(
        photos.json()[0]["content_url"], headers=member_headers
    )
    assert deleted_content.status_code == 404
    async with app.state.database.session_factory() as session:
        remaining = await session.scalar(
            select(func.count(ActivityPhoto.id)).where(ActivityPhoto.activity_id == activity["id"])
        )
        assert remaining == 0


async def test_cooldowns_cancel_policy_and_ai_summary(client) -> None:
    _, owner_headers = await register_user(client, "cooldown@njust.edu.cn", "冷却测试")
    _, member_headers = await register_user(client, "late-member@njust.edu.cn", "临近取消者")
    now = datetime.now(UTC)

    preview_payload = {
        "category": "自习",
        "starts_at": (now + timedelta(days=1)).isoformat(),
        "ends_at": (now + timedelta(days=1, hours=2)).isoformat(),
        "location": "图书馆三楼",
        "people_needed": 1,
    }
    first_match = await client.post(
        "/api/v1/matches/preview", headers=owner_headers, json=preview_payload
    )
    assert first_match.status_code == 201, first_match.text
    second_match = await client.post(
        "/api/v1/matches/preview", headers=owner_headers, json=preview_payload
    )
    assert second_match.status_code == 429
    assert "秒后" in second_match.json()["detail"]

    summary = await client.post("/api/v1/users/me/ai-summary", headers=owner_headers)
    assert summary.status_code == 200, summary.text
    assert summary.json()["mode"] == "rules"
    assert "hidden" not in summary.text
    repeated_summary = await client.post("/api/v1/users/me/ai-summary", headers=owner_headers)
    assert repeated_summary.status_code == 429

    activity = await create_activity(
        client,
        owner_headers,
        starts_at=now + timedelta(minutes=30),
        capacity=2,
        title="半小时后见",
    )
    await client.post(
        f"/api/v1/activities/{activity['id']}/join", headers=member_headers
    )
    warning = await client.post(
        f"/api/v1/activities/{activity['id']}/leave",
        headers=member_headers,
        json={"confirm_penalty": False},
    )
    assert warning.status_code == 409
    leave = await client.post(
        f"/api/v1/activities/{activity['id']}/leave",
        headers=member_headers,
        json={"confirm_penalty": True},
    )
    assert leave.status_code == 200, leave.text
    assert leave.json()["credit_delta"] == -10

    for invalid_attendance in ("cancelled_early", "late_cancel"):
        invalid_feedback = await client.post(
            "/api/v1/feedback",
            headers=owner_headers,
            json={
                "activity_id": activity["id"],
                "reviewee_id": "nobody",
                "attendance": invalid_attendance,
                "rating": 3,
            },
        )
        assert invalid_feedback.status_code == 422

    invalid_skill_mark = await client.post(
        "/api/v1/feedback",
        headers=owner_headers,
        json={
            "activity_id": activity["id"],
            "reviewee_id": "nobody",
            "attendance": "attended",
            "rating": 3,
            "incident_tags": ["suspected_smurfing"],
        },
    )
    assert invalid_skill_mark.status_code == 422

    contradictory_attendance = await client.post(
        "/api/v1/feedback",
        headers=owner_headers,
        json={
            "activity_id": activity["id"],
            "reviewee_id": "nobody",
            "attendance": "no_show",
            "rating": 3,
            "incident_tags": ["late"],
        },
    )
    assert contradictory_attendance.status_code == 422
