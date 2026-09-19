from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from app.models import Activity as ActivityModel
from app.models import ActivityMember, User
from tests.conftest import register_user


async def update_profile(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    **overrides: object,
) -> dict:
    profile = {
        "campus": "南区",
        "department": "设计艺术与传媒学院",
        "grade_year": 3,
        "bio": "喜欢运动和摄影，守时",
        "interests": ["羽毛球", "摄影"],
        "preferred_locations": ["南区体育馆"],
        "social_style": "quiet",
        "preferred_group_min": 2,
        "preferred_group_max": 4,
    }
    profile.update(overrides)
    response = await client.patch("/api/v1/users/me", headers=headers, json=profile)
    assert response.status_code == 200, response.text
    return response.json()


async def test_health_auth_and_duplicate_registration(client: httpx.AsyncClient) -> None:
    frontend = await client.get("/")
    live = await client.get("/health/live")
    ready = await client.get("/health/ready")
    assert frontend.status_code == 200
    assert "理工搭子局" in frontend.text
    assert live.json() == {"status": "ok"}
    assert ready.json() == {"status": "ready", "agent_mode": "deterministic"}

    token, headers = await register_user(client, "owner@njust.edu.cn", "发起人")
    assert token
    me = await client.get("/api/v1/users/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["display_name"] == "发起人"

    duplicate = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "owner@njust.edu.cn",
            "password": "test-password-123",
            "display_name": "重复用户",
        },
    )
    assert duplicate.status_code == 409

    login = await client.post(
        "/api/v1/auth/token",
        json={"email": "owner@njust.edu.cn", "password": "test-password-123"},
    )
    assert login.status_code == 200


async def test_agent_preview_confirmation_invitation_and_feedback(
    client: httpx.AsyncClient,
) -> None:
    _, owner_headers = await register_user(client, "owner@njust.edu.cn", "小曾")
    _, best_headers = await register_user(client, "best@njust.edu.cn", "小王")
    _, other_headers = await register_user(client, "other@njust.edu.cn", "小李")

    owner = await update_profile(client, owner_headers)
    best = await update_profile(client, best_headers)
    await update_profile(
        client,
        other_headers,
        department="自动化学院",
        bio="喜欢篮球，性格外向",
        interests=["篮球", "游戏"],
        preferred_locations=["北区篮球场"],
        social_style="outgoing",
    )

    starts_at = datetime.now(UTC) + timedelta(days=2)
    ends_at = starts_at + timedelta(hours=2)
    preview = await client.post(
        "/api/v1/matches/preview",
        headers=owner_headers,
        json={
            "category": "羽毛球",
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "location": "南区体育馆",
            "people_needed": 1,
            "title": "南区羽毛球",
            "personal_requirement": "想找同院系、安静、靠谱且也喜欢羽毛球的搭子",
        },
    )
    assert preview.status_code == 201, preview.text
    preview_body = preview.json()
    assert preview_body["agent_mode"] == "deterministic"
    assert preview_body["requires_confirmation"] is True
    assert any("同院系" in item for item in preview_body["personalization"]["applied"])
    assert any("信用分" in item for item in preview_body["personalization"]["applied"])
    user_candidates = [
        item for item in preview_body["candidates"] if item["candidate_type"] == "user"
    ]
    assert user_candidates
    assert user_candidates[0]["candidate_id"] == best["id"]
    assert user_candidates[0]["score"] > user_candidates[-1]["score"]

    before_confirm = await client.get(
        f"/api/v1/agent-runs/{preview_body['agent_run_id']}",
        headers=owner_headers,
    )
    assert before_confirm.status_code == 200
    tool_names = {item.get("tool") for item in before_confirm.json()["trace"] if item.get("tool")}
    assert {"search_activities", "search_users", "calculate_match"}.issubset(tool_names)

    confirm = await client.post(
        f"/api/v1/matches/{preview_body['match_request_id']}/confirm",
        headers=owner_headers,
        json={"candidate_user_ids": [best["id"]]},
    )
    assert confirm.status_code == 200, confirm.text
    confirm_body = confirm.json()
    assert confirm_body["status"] == "confirmed"
    assert confirm_body["activity"]["participant_count"] == 1
    assert len(confirm_body["invitations"]) == 1

    confirm_again = await client.post(
        f"/api/v1/matches/{preview_body['match_request_id']}/confirm",
        headers=owner_headers,
        json={"candidate_user_ids": [best["id"]]},
    )
    assert confirm_again.status_code == 200
    assert confirm_again.json()["activity"]["id"] == confirm_body["activity"]["id"]
    assert len(confirm_again.json()["invitations"]) == 1

    invitation_id = confirm_body["invitations"][0]["id"]
    inbox = await client.get("/api/v1/invitations?status=pending", headers=best_headers)
    assert inbox.status_code == 200
    assert inbox.json()[0]["id"] == invitation_id
    assert inbox.json()[0]["activity"]["title"] == "南区羽毛球"

    accepted = await client.post(
        f"/api/v1/invitations/{invitation_id}/respond",
        headers=best_headers,
        json={"decision": "accepted"},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"

    activities = await client.get("/api/v1/activities", headers=owner_headers)
    activity = next(
        item for item in activities.json() if item["id"] == confirm_body["activity"]["id"]
    )
    assert activity["status"] == "formed"
    assert activity["participant_count"] == 2

    feedback_too_early = await client.post(
        "/api/v1/feedback",
        headers=owner_headers,
        json={
            "activity_id": activity["id"],
            "reviewee_id": best["id"],
            "attendance": "no_show",
            "rating": 1,
            "comment": "没有出现，也没有提前说明",
        },
    )
    assert feedback_too_early.status_code == 409

    app = client._transport.app  # type: ignore[attr-defined]
    async with app.state.database.session_factory() as session:
        stored_activity = await session.get(ActivityModel, activity["id"])
        stored_other = await session.scalar(
            select(User).where(User.email == "other@njust.edu.cn")
        )
        assert stored_activity is not None
        assert stored_other is not None
        stored_activity.starts_at = datetime.now(UTC) - timedelta(hours=2)
        stored_activity.ends_at = datetime.now(UTC) - timedelta(hours=1)
        session.add(
            ActivityMember(
                activity_id=stored_activity.id,
                user_id=stored_other.id,
                role="participant",
                status="confirmed",
            )
        )
        await session.commit()

    mine = await client.get("/api/v1/activities/mine", headers=owner_headers)
    assert mine.status_code == 200
    ended = next(item for item in mine.json() if item["activity"]["id"] == activity["id"])
    assert ended["activity"]["status"] == "completed"
    assert ended["needs_feedback"] is True

    feedback = await client.post(
        "/api/v1/feedback",
        headers=owner_headers,
        json={
            "activity_id": activity["id"],
            "reviewee_id": best["id"],
            "attendance": "no_show",
            "rating": 1,
            "comment": "没有出现，也没有提前说明",
            "personality_tags": ["quiet"],
            "incident_tags": ["no_show"],
        },
    )
    assert feedback.status_code == 201, feedback.text
    assert feedback.json()["requires_peer_review"] is True
    assert feedback.json()["reviewee_credit_delta"] == 0

    tasks = await client.get("/api/v1/feedback/peer-review-tasks", headers=other_headers)
    assert tasks.status_code == 200
    assert tasks.json()[0]["feedback_id"] == feedback.json()["feedback_id"]
    peer_review = await client.post(
        f"/api/v1/feedback/{feedback.json()['feedback_id']}/peer-review",
        headers=other_headers,
        json={
            "verdict": "mostly_true",
            "comment": "我也在现场，确实一直没有见到他。",
            "true_parts": "未到场",
            "false_parts": "",
        },
    )
    assert peer_review.status_code == 201, peer_review.text
    assert peer_review.json()["reviewee_credit_delta"] == -9

    async with app.state.database.session_factory() as session:
        reviewed_user = await session.get(User, best["id"])
        assert reviewed_user is not None
        assert reviewed_user.credit_score == 91
        assert reviewed_user.hidden_profile["finalized_feedback_count"] == 1

    other_user = await client.get("/api/v1/users/me", headers=other_headers)
    disputed = await client.post(
        "/api/v1/feedback",
        headers=owner_headers,
        json={
            "activity_id": activity["id"],
            "reviewee_id": other_user.json()["id"],
            "attendance": "no_show",
            "rating": 1,
            "comment": "他说没来，但这是一条用于检验恶意评价识别的指控。",
            "incident_tags": ["no_show"],
        },
    )
    assert disputed.status_code == 201
    rejected_claim = await client.post(
        f"/api/v1/feedback/{disputed.json()['feedback_id']}/peer-review",
        headers=best_headers,
        json={
            "verdict": "mostly_false",
            "comment": "他全程都在，原评价的主要事实不成立。",
            "true_parts": "",
            "false_parts": "未到场",
        },
    )
    assert rejected_claim.status_code == 201, rejected_claim.text
    assert rejected_claim.json()["reviewee_credit_delta"] == 0
    assert rejected_claim.json()["original_reviewer_credit_delta"] == -3

    async with app.state.database.session_factory() as session:
        original_reviewer = await session.get(User, owner["id"])
        falsely_reviewed = await session.get(User, other_user.json()["id"])
        assert original_reviewer is not None
        assert falsely_reviewed is not None
        assert original_reviewer.hidden_profile["review_integrity"][
            "unsupported_serious_feedback_count"
        ] == 1
        assert falsely_reviewed.hidden_profile["finalized_feedback_count"] == 0

    after_confirm = await client.get(
        f"/api/v1/agent-runs/{preview_body['agent_run_id']}",
        headers=owner_headers,
    )
    confirmation_events = [
        item for item in after_confirm.json()["trace"] if item.get("event") == "user_confirmation"
    ]
    assert confirmation_events
    assert "invite_user" in confirmation_events[0]["approved_tools"]
    assert owner["id"] != best["id"]


async def test_guided_registration_solo_activity_and_timed_leave(
    client: httpx.AsyncClient,
) -> None:
    registration = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "guided@njust.edu.cn",
            "password": "test-password-123",
            "display_name": "画像用户",
            "university": "南京理工大学",
            "campus": "江阴校区",
            "department": "设计艺术与传媒学院",
            "grade_year": 3,
            "bio": "喜欢有计划地参加活动",
            "interests": ["摄影", "羽毛球"],
            "preferred_locations": ["图书馆"],
            "social_style": "balanced",
            "preferred_group_min": 2,
            "preferred_group_max": 5,
        },
    )
    assert registration.status_code == 201, registration.text
    headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
    me = await client.get("/api/v1/users/me", headers=headers)
    assert me.json()["department"] == "设计艺术与传媒学院"
    assert me.json()["interests"] == ["摄影", "羽毛球"]
    assert "hidden_profile" not in me.json()

    starts_at = datetime.now(UTC) + timedelta(hours=12)
    preview = await client.post(
        "/api/v1/matches/preview",
        headers=headers,
        json={
            "category": "摄影",
            "starts_at": starts_at.isoformat(),
            "ends_at": (starts_at + timedelta(hours=2)).isoformat(),
            "location": "图书馆",
            "people_needed": 2,
            "title": "校园夜景拍摄",
        },
    )
    assert preview.status_code == 201, preview.text
    confirmed = await client.post(
        f"/api/v1/matches/{preview.json()['match_request_id']}/confirm",
        headers=headers,
        json={"create_solo_activity": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["invitations"] == []
    assert confirmed.json()["activity"]["participant_count"] == 1

    mine = await client.get("/api/v1/activities/mine", headers=headers)
    mine_item = next(
        item
        for item in mine.json()
        if item["activity"]["id"] == confirmed.json()["activity"]["id"]
    )
    assert mine_item["can_leave"] is True
    assert mine_item["leave_penalty"] == 2

    unconfirmed_leave = await client.post(
        f"/api/v1/activities/{confirmed.json()['activity']['id']}/leave",
        headers=headers,
        json={"confirm_penalty": False},
    )
    assert unconfirmed_leave.status_code == 409
    leave = await client.post(
        f"/api/v1/activities/{confirmed.json()['activity']['id']}/leave",
        headers=headers,
        json={"confirm_penalty": True},
    )
    assert leave.status_code == 200, leave.text
    assert leave.json()["credit_delta"] == -2
    assert leave.json()["credit_score"] == 98


async def test_sensitive_personalization_is_not_used_for_ranking(client: httpx.AsyncClient) -> None:
    _, owner_headers = await register_user(client, "owner@njust.edu.cn", "发起人")
    _, candidate_headers = await register_user(client, "candidate@njust.edu.cn", "候选人")
    await update_profile(client, owner_headers)
    await update_profile(client, candidate_headers)
    starts_at = datetime.now(UTC) + timedelta(days=1)

    response = await client.post(
        "/api/v1/matches/preview",
        headers=owner_headers,
        json={
            "category": "羽毛球",
            "starts_at": starts_at.isoformat(),
            "ends_at": (starts_at + timedelta(hours=1)).isoformat(),
            "location": "南区体育馆",
            "people_needed": 1,
            "personal_requirement": "只要女生，最好靠谱",
        },
    )
    assert response.status_code == 201, response.text
    report = response.json()["personalization"]
    assert any("性别" in item for item in report["ignored_for_safety"])
    assert response.json()["candidates"]


async def test_user_cannot_read_another_users_agent_trace(client: httpx.AsyncClient) -> None:
    _, owner_headers = await register_user(client, "owner@njust.edu.cn", "发起人")
    _, outsider_headers = await register_user(client, "outsider@njust.edu.cn", "其他人")
    await update_profile(client, owner_headers)
    await update_profile(client, outsider_headers)
    starts_at = datetime.now(UTC) + timedelta(days=1)
    preview = await client.post(
        "/api/v1/matches/preview",
        headers=owner_headers,
        json={
            "category": "自习",
            "starts_at": starts_at.isoformat(),
            "ends_at": (starts_at + timedelta(hours=2)).isoformat(),
            "location": "图书馆",
            "people_needed": 1,
        },
    )
    run_id = preview.json()["agent_run_id"]
    forbidden = await client.get(f"/api/v1/agent-runs/{run_id}", headers=outsider_headers)
    assert forbidden.status_code == 404
