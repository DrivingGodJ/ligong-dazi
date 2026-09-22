from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.conftest import register_user


async def test_optional_search_location_requires_a_place_only_when_creating(client) -> None:
    _, requester = await register_user(client, "no-place@njust.edu.cn", "尚未定地点")
    candidate = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "candidate-place@njust.edu.cn",
            "student_id": "202600000003",
            "password": "test-password-123",
            "display_name": "可以被邀请的搭子",
            "campus": "南京",
        },
    )
    assert candidate.status_code == 201
    candidate_me = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {candidate.json()['access_token']}"},
    )
    start = datetime.now(UTC) + timedelta(days=2)
    request = {
        "category": "羽毛球",
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(hours=2)).isoformat(),
        "people_needed": 9,
    }
    preview = await client.post("/api/v1/matches/preview", headers=requester, json=request)
    assert preview.status_code == 201, preview.text
    candidate_id = candidate_me.json()["id"]
    assert candidate_id in {
        item["candidate_id"]
        for item in preview.json()["candidates"]
        if item["candidate_type"] == "user"
    }
    confirm_path = f"/api/v1/matches/{preview.json()['match_request_id']}/confirm"
    without_place = await client.post(
        confirm_path, headers=requester, json={"candidate_user_ids": [candidate_id]}
    )
    assert without_place.status_code == 422
    assert "地点" in without_place.json()["detail"]
    with_place = await client.post(
        confirm_path,
        headers=requester,
        json={"candidate_user_ids": [candidate_id], "location": "  北区体育馆  "},
    )
    assert with_place.status_code == 200, with_place.text
    assert with_place.json()["activity"]["location"] == "北区体育馆"
    assert with_place.json()["activity"]["capacity"] == 10

    _, solo = await register_user(client, "solo-place@njust.edu.cn", "想先发布")
    solo_preview = await client.post(
        "/api/v1/matches/preview", headers=solo, json={**request, "location": "   "}
    )
    assert solo_preview.status_code == 201
    solo_path = f"/api/v1/matches/{solo_preview.json()['match_request_id']}/confirm"
    assert (
        await client.post(solo_path, headers=solo, json={"create_solo_activity": True})
    ).status_code == 422
    solo_confirmed = await client.post(
        solo_path,
        headers=solo,
        json={"create_solo_activity": True, "location": "南区图书馆"},
    )
    assert solo_confirmed.status_code == 200
    assert solo_confirmed.json()["activity"]["location"] == "南区图书馆"

    _, over_limit = await register_user(client, "over-nine@njust.edu.cn", "想要十位")
    rejected = await client.post(
        "/api/v1/matches/preview",
        headers=over_limit,
        json={**request, "people_needed": 10},
    )
    assert rejected.status_code == 422
    with_null = await client.post(
        "/api/v1/matches/preview",
        headers=over_limit,
        json={**request, "location": None, "people_needed": 1},
    )
    assert with_null.status_code == 201


async def test_nearby_event_times_are_ranked_and_joined_without_search_location(client) -> None:
    _, owner = await register_user(client, "near-owner@njust.edu.cn", "活动发起人")
    _, requester = await register_user(client, "near-guest@njust.edu.cn", "时间可灵活")
    start = datetime.now(UTC) + timedelta(days=3)
    events = {}
    for title, offset_minutes in (
        ("正好同一时间", 0),
        ("晚一点的活动", 90),
        ("太晚的活动", 181),
    ):
        begins = start + timedelta(minutes=offset_minutes)
        response = await client.post(
            "/api/v1/activities",
            headers=owner,
            json={
                "title": title,
                "category": "羽毛球",
                "starts_at": begins.isoformat(),
                "ends_at": (begins + timedelta(hours=1)).isoformat(),
                "location": "南区体育馆",
                "capacity": 3,
            },
        )
        assert response.status_code == 201, response.text
        events[title] = response.json()

    preview = await client.post(
        "/api/v1/matches/preview",
        headers=requester,
        json={
            "category": "羽毛球",
            "starts_at": start.isoformat(),
            "ends_at": (start + timedelta(hours=1)).isoformat(),
            "location": "",
            "people_needed": 1,
        },
    )
    assert preview.status_code == 201, preview.text
    activities = [
        item for item in preview.json()["candidates"] if item["candidate_type"] == "activity"
    ]
    ids = {item["candidate_id"] for item in activities}
    assert events["正好同一时间"]["id"] in ids
    assert events["晚一点的活动"]["id"] in ids
    assert events["太晚的活动"]["id"] not in ids
    exact = next(
        item for item in activities if item["candidate_id"] == events["正好同一时间"]["id"]
    )
    nearby = next(
        item for item in activities if item["candidate_id"] == events["晚一点的活动"]["id"]
    )
    assert exact["score"] > nearby["score"]
    assert any("晚 90 分钟" in explanation for explanation in nearby["explanation"])
    assert nearby["activity"]["starts_at"] == events["晚一点的活动"]["starts_at"]

    joined = await client.post(
        f"/api/v1/matches/{preview.json()['match_request_id']}/confirm",
        headers=requester,
        json={"existing_activity_id": nearby["candidate_id"]},
    )
    assert joined.status_code == 200, joined.text
    assert joined.json()["status"] == "joined"
    assert joined.json()["activity"]["location"] == "南区体育馆"
    assert joined.json()["activity"]["starts_at"] == nearby["activity"]["starts_at"]


async def test_direct_activity_still_needs_a_real_location(client) -> None:
    _, owner = await register_user(client, "direct-location@njust.edu.cn", "直接创建")
    start = datetime.now(UTC) + timedelta(days=2)
    response = await client.post(
        "/api/v1/activities",
        headers=owner,
        json={
            "title": "不能没有地点",
            "category": "跑步",
            "starts_at": start.isoformat(),
            "ends_at": (start + timedelta(hours=1)).isoformat(),
            "location": "   ",
            "capacity": 3,
        },
    )
    assert response.status_code == 422
