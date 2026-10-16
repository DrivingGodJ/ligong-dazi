from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models import Activity, ActivityMember, User, UserBlock
from tests.conftest import register_user


async def seed_activity(session, owner_id, title, **overrides):
    start = datetime.now(UTC) + timedelta(days=2)
    values = {
        "owner_id": owner_id,
        "campus": "南京",
        "title": title,
        "category": "跑步",
        "location": "南区体育馆",
        "starts_at": start,
        "ends_at": start + timedelta(hours=2),
        "capacity": 3,
        "status": "open",
    }
    activity = Activity(**(values | overrides))
    session.add(activity)
    await session.flush()
    session.add(ActivityMember(activity_id=activity.id, user_id=owner_id, role="owner"))
    return activity


async def test_square_latest_first_and_stable_pagination(client) -> None:
    _, owner = await register_user(client, "realtime-owner@njust.edu.cn", "发起人")
    _, viewer = await register_user(client, "realtime-viewer@njust.edu.cn", "看活动")
    owner_id = (await client.get("/api/v1/users/me", headers=owner)).json()["id"]
    await client.patch("/api/v1/users/me", headers=viewer, json={"interests": ["跑步"]})
    now = datetime.now(UTC)
    app = client._transport.app
    async with app.state.database.session_factory() as session:
        old = await seed_activity(session, owner_id, "旧活动", created_at=now - timedelta(hours=2))
        tied_a = await seed_activity(
            session, owner_id, "同时发布甲", created_at=now - timedelta(hours=1)
        )
        tied_b = await seed_activity(
            session, owner_id, "同时发布乙", created_at=now - timedelta(hours=1)
        )
        newest = await seed_activity(
            session,
            owner_id,
            "最新活动",
            created_at=now,
            category="吃饭",
            starts_at=now + timedelta(days=4),
            ends_at=now + timedelta(days=4, hours=1),
        )
        await session.commit()
        expected = [newest.id, *sorted([tied_a.id, tied_b.id]), old.id]

    full = (await client.get("/api/v1/activities/square", headers=viewer)).json()
    assert [item["activity"]["id"] for item in full["items"]] == expected
    assert full["items"][0]["recommendation_score"] < full["items"][-1]["recommendation_score"]
    assert full["items"][0]["activity"]["created_at"] == now.isoformat().replace("+00:00", "Z")
    assert full["items"][0]["activity"]["recruitment_status"] == "recruiting"

    paged_ids = []
    for offset in range(len(expected)):
        response = await client.get(
            f"/api/v1/activities/square?limit=1&offset={offset}",
            headers=viewer,
        )
        page = response.json()
        paged_ids.extend(item["activity"]["id"] for item in page["items"])
        assert page["has_more"] is (offset < len(expected) - 1)
        assert page["next_offset"] == (offset + 1 if page["has_more"] else None)
    assert paged_ids == expected


async def test_square_hides_full_past_finished_and_cancelled(client) -> None:
    _, owner = await register_user(client, "hidden-owner@njust.edu.cn", "发起人")
    _, viewer = await register_user(client, "hidden-viewer@njust.edu.cn", "看活动")
    owner_id = (await client.get("/api/v1/users/me", headers=owner)).json()["id"]
    viewer_id = (await client.get("/api/v1/users/me", headers=viewer)).json()["id"]
    now = datetime.now(UTC)
    app = client._transport.app
    async with app.state.database.session_factory() as session:
        visible = await seed_activity(session, owner_id, "招募中")
        for status in ("open", "formed"):
            full = await seed_activity(
                session, owner_id, f"满员-{status}", capacity=2, status=status
            )
            session.add(ActivityMember(activity_id=full.id, user_id=viewer_id))
        for status in ("completed", "cancelled"):
            await seed_activity(session, owner_id, status, status=status, category="隐藏类型")
        await seed_activity(
            session,
            owner_id,
            "已开始",
            starts_at=now - timedelta(minutes=1),
            ends_at=now + timedelta(hours=1),
            category="隐藏类型",
        )
        ended = await seed_activity(
            session,
            owner_id,
            "已结束",
            starts_at=now - timedelta(hours=2),
            ends_at=now - timedelta(hours=1),
            category="隐藏类型",
        )
        await session.commit()

    square = (await client.get("/api/v1/activities/square", headers=viewer)).json()
    assert [item["activity"]["id"] for item in square["items"]] == [visible.id]
    assert square["categories"] == ["跑步"]
    # ActivityPublic keeps the true lifecycle and exposes a separate display state.
    activities = (await client.get("/api/v1/activities?limit=100", headers=owner)).json()
    by_title = {item["title"]: item for item in activities}
    assert by_title["满员-open"]["recruitment_status"] == "full"
    assert by_title["满员-open"]["status"] == "open"
    assert by_title["已结束"]["id"] == ended.id
    assert by_title["已结束"]["recruitment_status"] == "ended"


async def test_square_preserves_visibility_join_permissions_and_filters(client) -> None:
    users = {}
    for name in ("viewer", "owner", "blocked-by-viewer", "blocks-viewer"):
        _, headers = await register_user(client, f"visibility-{name}@njust.edu.cn", name)
        users[name] = (
            (await client.get("/api/v1/users/me", headers=headers)).json()["id"],
            headers,
        )
    viewer_id, viewer_headers = users["viewer"]
    owner_id, owner_headers = users["owner"]
    await client.patch("/api/v1/users/me", headers=owner_headers, json={"gender": "male"})
    await client.patch("/api/v1/users/me", headers=viewer_headers, json={"gender": "female"})
    app = client._transport.app
    async with app.state.database.session_factory() as session:
        restricted = await seed_activity(session, owner_id, "仅限同性", same_gender_only=True)
        approval = await seed_activity(
            session,
            owner_id,
            "申请自习",
            join_policy="approval",
            category="学习",
            location="图书馆",
        )
        await seed_activity(session, owner_id, "其他校区", campus="江阴")
        await seed_activity(session, users["blocked-by-viewer"][0], "我拉黑的发起人")
        await seed_activity(session, users["blocks-viewer"][0], "拉黑我的发起人")
        session.add_all(
            [
                UserBlock(blocker_id=viewer_id, blocked_id=users["blocked-by-viewer"][0]),
                UserBlock(blocker_id=users["blocks-viewer"][0], blocked_id=viewer_id),
            ]
        )
        await session.commit()

    response = await client.get("/api/v1/activities/square", headers=viewer_headers)
    by_id = {item["activity"]["id"]: item for item in response.json()["items"]}
    assert set(by_id) == {restricted.id, approval.id}
    assert by_id[restricted.id]["joinable"] is False
    assert "同性" in by_id[restricted.id]["join_reason"]
    assert by_id[approval.id]["joinable"] is True
    assert by_id[approval.id]["activity"]["join_policy"] == "approval"
    local_date = approval.starts_at.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
    found = await client.get(
        f"/api/v1/activities/square?search=自习&category=学习&date={local_date}",
        headers=viewer_headers,
    )
    assert [item["activity"]["id"] for item in found.json()["items"]] == [approval.id]
    assert (await client.get("/api/v1/activities/square")).status_code == 401
    async with app.state.database.session_factory() as session:
        user = await session.get(User, viewer_id)
        user.campus = None
        await session.commit()
    assert (
        await client.get("/api/v1/activities/square", headers=viewer_headers)
    ).status_code == 403
