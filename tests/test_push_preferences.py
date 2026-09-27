from __future__ import annotations

from sqlalchemy import select

from app.models import Notification, PushSubscription, User
from app.notifications import dispatch_push
from tests.conftest import register_user


async def test_push_status_and_disable_are_scoped_to_the_account_and_endpoint(client):
    _, owner = await register_user(client, "push-settings-owner@example.com", "通知设置同学")
    _, other = await register_user(client, "push-settings-other@example.com", "其他同学")
    endpoints = [f"https://fcm.googleapis.com/fcm/send/device-{i}" for i in range(2)]
    for endpoint in endpoints:
        saved = await client.post(
            "/api/v1/push/subscriptions",
            headers=owner,
            json={"endpoint": endpoint, "p256dh": "a" * 30, "auth": "b" * 15},
        )
        assert saved.status_code == 201

    query = {"endpoint": endpoints[0]}
    assert (await client.get("/api/v1/push/subscriptions", params=query)).status_code == 401
    status = await client.get("/api/v1/push/subscriptions", params=query, headers=owner)
    assert status.json() == {"subscribed": True}
    assert status.headers["cache-control"] == "no-store"
    status = await client.get("/api/v1/push/subscriptions", params=query, headers=other)
    assert status.json() == {"subscribed": False}
    await client.delete("/api/v1/push/subscriptions", params=query, headers=other)
    assert (await client.get("/api/v1/push/subscriptions", params=query, headers=owner)).json() == {
        "subscribed": True
    }

    deleted = await client.delete("/api/v1/push/subscriptions", params=query, headers=owner)
    assert deleted.status_code == 200
    assert (await client.get("/api/v1/push/subscriptions", params=query, headers=owner)).json() == {
        "subscribed": False
    }
    assert (
        await client.get(
            "/api/v1/push/subscriptions", params={"endpoint": endpoints[1]}, headers=owner
        )
    ).json() == {"subscribed": True}

    saved = await client.post(
        "/api/v1/push/subscriptions",
        headers=owner,
        json={"endpoint": endpoints[0], "p256dh": "a" * 30, "auth": "b" * 15},
    )
    assert saved.status_code == 201
    assert (await client.get("/api/v1/push/subscriptions", params=query, headers=owner)).json() == {
        "subscribed": True
    }


async def test_disabling_push_stops_delivery_but_keeps_in_app_messages(client, monkeypatch):
    _, headers = await register_user(client, "push-off@example.com", "关闭通知同学")
    endpoint = "https://fcm.googleapis.com/fcm/send/device-off"
    await client.post(
        "/api/v1/push/subscriptions",
        headers=headers,
        json={"endpoint": endpoint, "p256dh": "a" * 30, "auth": "b" * 15},
    )
    await client.delete(
        "/api/v1/push/subscriptions", params={"endpoint": endpoint}, headers=headers
    )
    delivered = []
    monkeypatch.setattr("app.notifications.webpush", lambda **kwargs: delivered.append(kwargs))
    app = client._transport.app
    async with app.state.database.session_factory() as session:
        user = await session.scalar(select(User).where(User.display_name == "关闭通知同学"))
        session.add(
            Notification(
                user_id=user.id,
                event_key="push-off:invitation",
                kind="invitation",
                title="你收到新的邀请",
                body="消息仍在站内保留",
                url="/?tab=invitations",
            )
        )
        await session.commit()
        await dispatch_push(session, app.state.settings.push_key_file_path)
        await session.commit()
        assert await session.scalar(select(PushSubscription.id)) is None
    assert delivered == []
    inbox = (await client.get("/api/v1/notifications", headers=headers)).json()
    assert any(item["title"] == "你收到新的邀请" for item in inbox)
