from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.agent import AgentOutputError
from app.colleges import COLLEGES, match_college
from app.migrations import migrate_user_colleges
from app.models import Activity as ActivityModel
from app.models import ActivityMember, Feedback, User
from app.post_activity import refresh_hidden_profile
from tests.conftest import register_user


async def update_profile(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    **overrides: object,
) -> dict:
    profile = {
        "campus": "南区",
        "department": "设计科学与艺术学院",
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
    assert ready.json() == {
        "status": "ready",
        "agent_mode": "deterministic",
        "agent_model": None,
    }

    token, headers = await register_user(client, "owner@njust.edu.cn", "发起人")
    assert token
    me = await client.get("/api/v1/users/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["display_name"] == "发起人"

    duplicate = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "owner@njust.edu.cn",
            "student_id": me.json()["student_id"],
            "password": "test-password-123",
            "display_name": "重复用户",
            "campus": "南京",
        },
    )
    assert duplicate.status_code == 409

    login = await client.post(
        "/api/v1/auth/token",
        json={"email": "owner@njust.edu.cn", "password": "test-password-123"},
    )
    assert login.status_code == 200


async def test_college_choices_and_unknown_legacy_department_uses_default(
    client: httpx.AsyncClient,
) -> None:
    assert len(COLLEGES) == 23
    assert len(set(COLLEGES)) == len(COLLEGES)
    assert match_college("  设计艺术与传媒学院  ") == "设计科学与艺术学院"
    assert match_college("微电子学院（集成电路学院）") == "集成电路学院（微电子学院）"
    assert match_college("不确定的学院") is None
    page = (await client.get("/")).text
    script = (await client.get("/static/app.js")).text
    assert page.count('name="department" data-college-select') == 2
    assert page.count('<option value="江阴" selected>江阴</option>') == 2
    assert '请选择南京或江阴' not in page
    assert all(f'"{college}"' in script for college in COLLEGES)

    invalid = await client.post(
        "/api/v1/auth/register",
        json={
            "student_id": "202600001234",
            "password": "test-password-123",
            "display_name": "学院校验",
            "campus": "南京",
            "department": "随手填的学院",
        },
    )
    assert invalid.status_code == 422

    _, headers = await register_user(client, "college@njust.edu.cn", "学院校验")
    selected = await client.patch(
        "/api/v1/users/me", headers=headers, json={"department": "智能科学与技术学院"}
    )
    assert selected.status_code == 200
    assert selected.json()["department"] == "智能科学与技术学院"
    rejected = await client.patch(
        "/api/v1/users/me", headers=headers, json={"department": "随手填的学院"}
    )
    assert rejected.status_code == 422

    app = client._transport.app  # type: ignore[attr-defined]
    async with app.state.database.session_factory() as session:
        user = await session.scalar(
            select(User).where(User.student_id == selected.json()["student_id"])
        )
        assert user is not None
        user.department = "历史填写的旧学院"
        await session.commit()
    rejected_legacy = await client.patch(
        "/api/v1/users/me",
        headers=headers,
        json={"department": "历史填写的旧学院", "bio": "资料仍可自动保存"},
    )
    assert rejected_legacy.status_code == 422
    async with app.state.database.session_factory() as session:
        assert await migrate_user_colleges(session) == 1
        await session.commit()
    defaulted = await client.get("/api/v1/users/me", headers=headers)
    assert defaulted.json()["department"] is None
    saved = await client.patch(
        "/api/v1/users/me", headers=headers, json={"bio": "资料仍可自动保存"}
    )
    assert saved.status_code == 200
    assert saved.json()["bio"] == "资料仍可自动保存"

    async with app.state.database.session_factory() as session:
        user = await session.scalar(
            select(User).where(User.student_id == selected.json()["student_id"])
        )
        assert user is not None
        user.department = "设计艺术与传媒学院"
        await session.commit()
        assert await migrate_user_colleges(session) == 1
        await session.commit()
    matched = await client.get("/api/v1/users/me", headers=headers)
    assert matched.json()["department"] == "设计科学与艺术学院"


async def test_separate_native_app_download_and_version(client: httpx.AsyncClient) -> None:
    version = await client.get("/api/v1/app/native-version")
    assert version.status_code == 200
    assert version.json()["version_code"] == 2
    assert version.json()["version_name"] == "1.1-beta"
    assert version.json()["download_url"] == "/downloads/ligong-dazi-native.apk"
    apk = await client.get("/downloads/ligong-dazi-native.apk")
    assert apk.status_code == 200
    assert apk.content.startswith(b"PK")
    assert apk.headers["content-type"] == "application/vnd.android.package-archive"


async def test_agent_output_error_has_recoverable_code_and_no_cooldown(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_with_invalid_output(*_: object, **__: object) -> None:
        raise AgentOutputError("Agent 没有返回可用的匹配结果")

    monkeypatch.setattr("app.api.run_matching_agent", fail_with_invalid_output)
    _, headers = await register_user(client, "agent-error@njust.edu.cn", "输出异常测试")
    starts_at = datetime.now(UTC) + timedelta(days=1)
    payload = {
        "category": "羽毛球",
        "starts_at": starts_at.isoformat(),
        "ends_at": (starts_at + timedelta(hours=2)).isoformat(),
        "location": "南区体育馆",
        "people_needed": 2,
        "personal_requirement": "测试无法生成结构化结果时的恢复路径",
    }

    first = await client.post("/api/v1/matches/preview", headers=headers, json=payload)
    second = await client.post("/api/v1/matches/preview", headers=headers, json=payload)

    assert first.status_code == 502
    assert first.json()["detail"]["code"] == "agent_output_error"
    assert "清空" in first.json()["detail"]["message"]
    assert second.status_code == 502


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
    assert preview_body["agent_model"] is None
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
            "student_id": "202600000001",
            "password": "test-password-123",
            "display_name": "画像用户",
            "university": "南京理工大学",
            "campus": "江阴校区",
            "department": "设计科学与艺术学院",
            "grade_year": 3,
            "gender": "male",
            "bio": "喜欢有计划地参加活动",
            "interests": ["摄影", "羽毛球"],
            "hobby_skills": [
                {"name": "摄影", "level": 4},
                {"name": "羽毛球", "level": 2},
            ],
            "preferred_locations": ["图书馆"],
            "social_style": "balanced",
            "preferred_group_min": 2,
            "preferred_group_max": 5,
        },
    )
    assert registration.status_code == 201, registration.text
    headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
    me = await client.get("/api/v1/users/me", headers=headers)
    assert me.json()["department"] == "设计科学与艺术学院"
    assert me.json()["gender"] == "male"
    assert me.json()["interests"] == ["摄影", "羽毛球"]
    assert me.json()["hobby_skills"] == [
        {"name": "摄影", "level": 4},
        {"name": "羽毛球", "level": 2},
    ]
    assert me.json()["skill_marks"] == []
    assert "hidden_profile" not in me.json()

    invalid_gender = await client.patch(
        "/api/v1/users/me",
        headers=headers,
        json={"gender": "other"},
    )
    assert invalid_gender.status_code == 422

    invalid_registration = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "invalid-gender@njust.edu.cn",
            "student_id": "202600000002",
            "password": "test-password-123",
            "display_name": "无效性别",
            "gender": "other",
        },
    )
    assert invalid_registration.status_code == 422

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
    assert mine_item["leave_penalty"] == 0

    leave = await client.post(
        f"/api/v1/activities/{confirmed.json()['activity']['id']}/leave",
        headers=headers,
        json={"confirm_penalty": False},
    )
    assert leave.status_code == 200, leave.text
    assert leave.json()["credit_delta"] == 0
    assert leave.json()["credit_score"] == 100
    assert leave.json()["activity_deleted"] is True
    mine_after = await client.get("/api/v1/activities/mine", headers=headers)
    assert all(
        item["activity"]["id"] != confirmed.json()["activity"]["id"]
        for item in mine_after.json()
    )


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


async def test_mentor_preference_and_accumulated_skill_marks(
    client: httpx.AsyncClient,
) -> None:
    _, owner_headers = await register_user(client, "learner@njust.edu.cn", "新手")
    _, low_headers = await register_user(client, "low@njust.edu.cn", "入门搭子")
    _, expert_headers = await register_user(client, "expert@njust.edu.cn", "高手搭子")
    _, reviewer_one_headers = await register_user(client, "r1@njust.edu.cn", "评价者一")
    _, reviewer_two_headers = await register_user(client, "r2@njust.edu.cn", "评价者二")
    await update_profile(
        client,
        owner_headers,
        hobby_skills=[{"name": "羽毛球", "level": 1}],
    )
    await update_profile(
        client,
        low_headers,
        hobby_skills=[{"name": "羽毛球", "level": 1}],
    )
    expert = await update_profile(
        client,
        expert_headers,
        hobby_skills=[{"name": "羽毛球", "level": 5}],
    )
    starts_at = datetime.now(UTC) + timedelta(days=1)
    preview = await client.post(
        "/api/v1/matches/preview",
        headers=owner_headers,
        json={
            "category": "羽毛球",
            "starts_at": starts_at.isoformat(),
            "ends_at": (starts_at + timedelta(hours=2)).isoformat(),
            "location": "南区体育馆",
            "people_needed": 1,
            "personal_requirement": "我是小白，希望有大佬带带我",
        },
    )
    assert preview.status_code == 201, preview.text
    assert any(
        "熟练度较高" in item for item in preview.json()["personalization"]["applied"]
    )
    candidates = [
        item for item in preview.json()["candidates"] if item["candidate_type"] == "user"
    ]
    assert candidates[0]["candidate_id"] == expert["id"]
    assert any("精通" in reason for reason in candidates[0]["explanation"])

    reviewer_one = (await client.get("/api/v1/users/me", headers=reviewer_one_headers)).json()
    reviewer_two = (await client.get("/api/v1/users/me", headers=reviewer_two_headers)).json()
    owner = (await client.get("/api/v1/users/me", headers=owner_headers)).json()
    app = client._transport.app  # type: ignore[attr-defined]
    async with app.state.database.session_factory() as session:
        reviewed_user = await session.get(User, owner["id"])
        assert reviewed_user is not None
        activity = ActivityModel(
            owner_id=reviewed_user.id,
            title="水平反馈测试",
            category="羽毛球",
            starts_at=datetime.now(UTC) - timedelta(hours=2),
            ends_at=datetime.now(UTC) - timedelta(hours=1),
            location="南区体育馆",
            capacity=4,
            status="completed",
        )
        session.add(activity)
        await session.flush()
        reviewer_ids = [reviewer_one["id"], reviewer_two["id"], expert["id"]]
        for index, reviewer_id in enumerate(reviewer_ids):
            session.add(
                Feedback(
                    activity_id=activity.id,
                    reviewer_id=reviewer_id,
                    reviewee_id=reviewed_user.id,
                    attendance="attended",
                    rating=4,
                    skill_name="羽毛球",
                    skill_level=[4, 5, 4][index],
                    moderation_status="finalized",
                    finalized_at=datetime.now(UTC),
                )
            )
            await session.flush()
            await refresh_hidden_profile(session, reviewed_user)
            if index < 2:
                assert reviewed_user.skill_marks == []

        assert reviewed_user.credit_score == 100
        assert reviewed_user.skill_marks[0]["flag_type"] == "possible_smurfing"
        assert reviewed_user.skill_marks[0]["feedback_count"] == 3
        await session.commit()

    refreshed = await client.get("/api/v1/users/me", headers=owner_headers)
    assert refreshed.status_code == 200
    assert refreshed.json()["skill_marks"][0]["name"] == "羽毛球"


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
