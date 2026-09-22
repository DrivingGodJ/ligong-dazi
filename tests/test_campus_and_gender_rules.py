from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import (
    ensure_sqlite_compatibility,
    migrate_activity_campuses,
    migrate_user_campuses,
)
from app.models import Activity, User
from tests.conftest import register_user


async def test_existing_sqlite_tables_gain_same_gender_rule_columns(tmp_path) -> None:
    database = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
    try:
        async with database.begin() as connection:
            await connection.execute(text("CREATE TABLE activities (id VARCHAR PRIMARY KEY)"))
            await connection.execute(text("CREATE TABLE match_requests (id VARCHAR PRIMARY KEY)"))
        await ensure_sqlite_compatibility(database)
        async with database.connect() as connection:
            columns = await connection.run_sync(
                lambda sync_connection: {
                    table: {item["name"] for item in inspect(sync_connection).get_columns(table)}
                    for table in ("activities", "match_requests")
                }
            )
        assert "same_gender_only" in columns["activities"]
        assert "same_gender_only" in columns["match_requests"]
        await ensure_sqlite_compatibility(database)
    finally:
        await database.dispose()


async def test_legacy_campus_migration_defaults_ambiguous_values_to_jiangyin(client) -> None:
    _, south_headers = await register_user(client, "south@njust.edu.cn", "南区同学")
    _, north_headers = await register_user(client, "north@njust.edu.cn", "北区同学")
    _, river_headers = await register_user(client, "river@njust.edu.cn", "江阴同学")
    _, unclear_headers = await register_user(client, "unclear@njust.edu.cn", "待确认同学")
    _, blank_headers = await register_user(client, "blank@njust.edu.cn", "未填校区同学")
    start = datetime.now(UTC) + timedelta(days=1)
    old_activity = await client.post(
        "/api/v1/activities",
        headers=unclear_headers,
        json={
            "title": "旧校区活动",
            "category": "跑步",
            "starts_at": start.isoformat(),
            "ends_at": (start + timedelta(hours=1)).isoformat(),
            "location": "操场",
            "capacity": 3,
        },
    )
    assert old_activity.status_code == 201
    app = client._transport.app  # type: ignore[attr-defined]
    async with app.state.database.session_factory() as session:
        users = list((await session.scalars(select(User))).all())
        by_name = {user.display_name: user for user in users}
        by_name["南区同学"].campus = "南区"
        by_name["北区同学"].campus = "北区"
        by_name["江阴同学"].campus = None
        by_name["江阴同学"].preferred_locations = ["江阴校区体育馆"]
        by_name["待确认同学"].campus = "宿舍"
        by_name["未填校区同学"].campus = None
        by_name["未填校区同学"].preferred_locations = ["南京图书馆"]
        activity = await session.get(Activity, old_activity.json()["id"])
        assert activity is not None
        activity.campus = "宿舍"
        await session.commit()
        assert await migrate_user_campuses(session) == 5
        assert await migrate_activity_campuses(session) == 1
        await session.commit()
        assert await migrate_user_campuses(session) == 0
        assert (await session.get(Activity, old_activity.json()["id"])).campus == "江阴"

    for headers, expected in (
        (south_headers, "南京"),
        (north_headers, "南京"),
        (river_headers, "江阴"),
        (unclear_headers, "江阴"),
        (blank_headers, "江阴"),
    ):
        response = await client.get("/api/v1/users/me", headers=headers)
        assert response.json()["campus"] == expected

    updated = await client.patch(
        "/api/v1/users/me", headers=unclear_headers, json={"campus": "江阴"}
    )
    assert updated.json()["campus"] == "江阴"
    invalid = await client.patch(
        "/api/v1/users/me", headers=south_headers, json={"campus": "其他校区"}
    )
    assert invalid.status_code == 422


async def test_same_gender_rule_blocks_all_join_paths(client) -> None:
    _, owner = await register_user(client, "rule-owner@njust.edu.cn", "男发起人")
    _, man = await register_user(client, "rule-man@njust.edu.cn", "男搭子")
    _, woman = await register_user(client, "rule-woman@njust.edu.cn", "女搭子")
    _, undisclosed = await register_user(client, "rule-hidden@njust.edu.cn", "不公开搭子")
    await client.patch("/api/v1/users/me", headers=owner, json={"gender": "male"})
    await client.patch("/api/v1/users/me", headers=man, json={"gender": "male"})
    await client.patch("/api/v1/users/me", headers=woman, json={"gender": "female"})
    start = datetime.now(UTC) + timedelta(days=2)
    payload = {
        "title": "星光夜跑",
        "category": "跑步",
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(hours=2)).isoformat(),
        "location": "南区体育馆",
        "capacity": 4,
        "same_gender_only": True,
    }
    cannot_create = await client.post("/api/v1/activities", headers=undisclosed, json=payload)
    assert cannot_create.status_code == 422
    created = await client.post("/api/v1/activities", headers=owner, json=payload)
    assert created.status_code == 201, created.text
    activity_id = created.json()["id"]
    assert created.json()["same_gender_only"] is True

    public_event = await client.post(
        "/api/v1/activities",
        headers=owner,
        json={**payload, "title": "所有人都能参加", "same_gender_only": False},
    )
    assert public_event.status_code == 201

    square = await client.get("/api/v1/activities/square", headers=woman)
    item = next(item for item in square.json()["items"] if item["activity"]["id"] == activity_id)
    assert item["joinable"] is False
    assert "同性" in item["join_reason"]
    assert square.json()["items"][0]["activity"]["id"] == public_event.json()["id"]
    for headers in (woman, undisclosed):
        denied = await client.post(f"/api/v1/activities/{activity_id}/join", headers=headers)
        assert denied.status_code == 403
    unknown_preview = await client.post(
        "/api/v1/matches/preview",
        headers=undisclosed,
        json={
            "category": "跑步",
            "starts_at": start.isoformat(),
            "ends_at": (start + timedelta(hours=2)).isoformat(),
            "people_needed": 2,
        },
    )
    assert unknown_preview.status_code == 201
    assert activity_id not in {
        item["candidate_id"] for item in unknown_preview.json()["candidates"]
    }
    joined = await client.post(f"/api/v1/activities/{activity_id}/join", headers=man)
    assert joined.status_code == 200
    await client.patch("/api/v1/users/me", headers=man, json={"gender": "female"})
    already_joined = await client.post(f"/api/v1/activities/{activity_id}/join", headers=man)
    assert already_joined.status_code == 200
    assert "已经" in already_joined.json()["message"]


async def test_matching_only_offers_same_gender_and_checks_invite_acceptance(client) -> None:
    _, owner_headers = await register_user(client, "match-owner@njust.edu.cn", "发起人")
    _, man_headers = await register_user(client, "match-man@njust.edu.cn", "男候选")
    _, woman_headers = await register_user(client, "match-woman@njust.edu.cn", "女候选")
    _, unknown_headers = await register_user(client, "match-unknown@njust.edu.cn", "未定性别")
    await client.patch("/api/v1/users/me", headers=owner_headers, json={"gender": "male"})
    man = await client.patch("/api/v1/users/me", headers=man_headers, json={"gender": "male"})
    woman = await client.patch("/api/v1/users/me", headers=woman_headers, json={"gender": "female"})
    start = datetime.now(UTC) + timedelta(days=2)
    existing = await client.post(
        "/api/v1/activities",
        headers=woman_headers,
        json={
            "title": "已有的公开羽毛球活动",
            "category": "羽毛球",
            "starts_at": start.isoformat(),
            "ends_at": (start + timedelta(hours=2)).isoformat(),
            "location": "南区体育馆",
            "capacity": 4,
        },
    )
    assert existing.status_code == 201
    preview = await client.post(
        "/api/v1/matches/preview",
        headers=owner_headers,
        json={
            "category": "羽毛球",
            "starts_at": start.isoformat(),
            "ends_at": (start + timedelta(hours=2)).isoformat(),
            "location": "南区体育馆",
            "people_needed": 2,
            "same_gender_only": True,
        },
    )
    assert preview.status_code == 201, preview.text
    candidates = {
        item["candidate_id"]
        for item in preview.json()["candidates"]
        if item["candidate_type"] == "user"
    }
    assert man.json()["id"] in candidates
    assert woman.json()["id"] not in candidates
    unknown = (await client.get("/api/v1/users/me", headers=unknown_headers)).json()
    assert unknown["id"] not in candidates
    assert all(item["candidate_type"] == "user" for item in preview.json()["candidates"])
    await client.patch("/api/v1/users/me", headers=man_headers, json={"gender": "undisclosed"})
    cannot_invite = await client.post(
        f"/api/v1/matches/{preview.json()['match_request_id']}/confirm",
        headers=owner_headers,
        json={"candidate_user_ids": [man.json()["id"]]},
    )
    assert cannot_invite.status_code == 422
    await client.patch("/api/v1/users/me", headers=man_headers, json={"gender": "male"})
    confirmed = await client.post(
        f"/api/v1/matches/{preview.json()['match_request_id']}/confirm",
        headers=owner_headers,
        json={"candidate_user_ids": [man.json()["id"]]},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["activity"]["same_gender_only"] is True
    invitation_id = confirmed.json()["invitations"][0]["id"]

    await client.patch("/api/v1/users/me", headers=man_headers, json={"gender": "female"})
    denied = await client.post(
        f"/api/v1/invitations/{invitation_id}/respond",
        headers=man_headers,
        json={"decision": "accepted"},
    )
    assert denied.status_code == 403
    await client.patch("/api/v1/users/me", headers=man_headers, json={"gender": "male"})
    accepted = await client.post(
        f"/api/v1/invitations/{invitation_id}/respond",
        headers=man_headers,
        json={"decision": "accepted"},
    )
    assert accepted.status_code == 200


async def test_square_search_dates_categories_and_pages(client) -> None:
    _, owner_headers = await register_user(client, "filter-owner@njust.edu.cn", "活动发起人")
    _, viewer_headers = await register_user(client, "filter-viewer@njust.edu.cn", "逛广场")
    start = datetime.now(UTC) + timedelta(days=2)
    for title, category, location, offset in (
        ("星光夜跑", "跑步", "南区体育馆", 0),
        ("周末晚餐", "吃饭", "江阴食堂", 24),
    ):
        event_start = start + timedelta(hours=offset)
        response = await client.post(
            "/api/v1/activities",
            headers=owner_headers,
            json={
                "title": title,
                "category": category,
                "starts_at": event_start.isoformat(),
                "ends_at": (event_start + timedelta(hours=2)).isoformat(),
                "location": location,
                "capacity": 3,
            },
        )
        assert response.status_code == 201, response.text
    first_page = await client.get("/api/v1/activities/square?limit=1", headers=viewer_headers)
    assert first_page.status_code == 200
    assert set(first_page.json()["categories"]) == {"跑步", "吃饭"}
    assert first_page.json()["has_more"] is True
    second_page = await client.get(
        "/api/v1/activities/square?limit=1&offset=1", headers=viewer_headers
    )
    assert second_page.json()["items"]
    for query in ("search=星光", "search=南体", "category=跑步"):
        found = await client.get(f"/api/v1/activities/square?{query}", headers=viewer_headers)
        assert [item["activity"]["title"] for item in found.json()["items"]] == ["星光夜跑"]
    local_date = start.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
    by_date = await client.get(
        f"/api/v1/activities/square?date={local_date}", headers=viewer_headers
    )
    assert [item["activity"]["title"] for item in by_date.json()["items"]] == ["星光夜跑"]
