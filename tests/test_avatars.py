from __future__ import annotations

import asyncio
from io import BytesIO

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import func, select

from app.avatar_media import AVATAR_INPUT_MAX_BYTES, AVATAR_STORED_MAX_BYTES, normalize_avatar
from app.models import User, UserAvatar, UserBlock
from tests.conftest import register_user


def avatar_image(color="purple", *, image_format="PNG", size=(900, 500)) -> bytes:
    buffer = BytesIO()
    image = Image.new("RGB", size, color)
    exif = Image.Exif()
    exif[270] = "private metadata"
    exif[274] = 6
    image.save(buffer, format=image_format, exif=exif)
    return buffer.getvalue()


async def upload(client, headers, content=None, media_type="image/png"):
    return await client.post(
        "/api/v1/users/me/avatar",
        headers=headers,
        files={"file": ("../unsafe.png", content or avatar_image(), media_type)},
    )


async def test_avatar_persists_in_profile_is_private_and_can_be_restored(client):
    _, owner = await register_user(client, "avatar-owner@njust.edu.cn", "头像主人")
    _, viewer = await register_user(client, "avatar-viewer@njust.edu.cn", "搭子")
    assert (await client.get("/api/v1/users/me", headers=owner)).json()["avatar_url"] is None
    response = await upload(client, owner)
    assert response.status_code == 200, response.text
    url = response.json()["avatar_url"]
    me = (await client.get("/api/v1/users/me", headers=owner)).json()
    assert me["avatar_url"] == url
    public = await client.get(f"/api/v1/users/{me['id']}/profile", headers=viewer)
    assert public.json()["user"]["avatar_url"] == url
    assert "content" not in public.json()["user"]
    assert (await client.get(url)).status_code == 401
    image_response = await client.get(url, headers=viewer)
    assert image_response.status_code == 200
    assert image_response.headers["content-type"] == "image/jpeg"
    assert image_response.headers["cache-control"] == "private, no-store"
    assert image_response.headers["x-content-type-options"] == "nosniff"
    with Image.open(BytesIO(image_response.content)) as image:
        assert image.size == (512, 512)
        assert not image.getexif()
        assert image.mode == "RGB"
    assert len(image_response.content) <= AVATAR_STORED_MAX_BYTES
    assert (await client.delete("/api/v1/users/me/avatar", headers=owner)).json() == {
        "avatar_url": None
    }
    assert (await client.get(url, headers=viewer)).status_code == 404
    assert (await client.get("/api/v1/users/me", headers=owner)).json()["avatar_url"] is None


@pytest.mark.parametrize(
    "content,media_type,expected",
    [
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml", 422),
        (b"\xff\xd8\xffbroken jpeg", "image/jpeg", 422),
        (b"\x89PNG\r\n\x1a\nbroken png", "image/png", 422),
        (avatar_image(), "image/jpeg", 422),
        (b"x" * (AVATAR_INPUT_MAX_BYTES + 1), "image/png", 413),
    ],
    ids=["svg", "broken-jpeg", "broken-png", "type-mismatch", "oversized"],
)
async def test_invalid_avatar_does_not_replace_previous_picture(
    client, content, media_type, expected
):
    _, headers = await register_user(client, "invalid-avatar@njust.edu.cn", "头像")
    old_url = (await upload(client, headers)).json()["avatar_url"]
    response = await upload(client, headers, content, media_type)
    assert response.status_code == expected, response.text
    assert (await client.get("/api/v1/users/me", headers=headers)).json()["avatar_url"] == old_url
    assert (await client.get(old_url, headers=headers)).status_code == 200


async def test_avatar_replace_and_concurrent_upload_leave_one_current_row(client):
    _, headers = await register_user(client, "avatar-race@njust.edu.cn", "头像")
    old_url = (await upload(client, headers)).json()["avatar_url"]
    responses = await asyncio.gather(
        upload(client, headers, avatar_image("red")), upload(client, headers, avatar_image("blue"))
    )
    assert all(response.status_code == 200 for response in responses)
    url = (await client.get("/api/v1/users/me", headers=headers)).json()["avatar_url"]
    assert url in [response.json()["avatar_url"] for response in responses]
    assert (await client.get(old_url, headers=headers)).status_code == 404
    runtime = client._transport.app.state.database
    async with runtime.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(UserAvatar)) == 1
        user = await session.scalar(select(User))
        assert "content" not in user.avatar.__dict__  # Matching never loads image bytes.


@pytest.mark.parametrize("reverse_block", [False, True])
async def test_avatar_respects_both_directions_of_blocking_and_account_freeze(
    client, reverse_block
):
    _, owner = await register_user(client, "blocked-avatar@njust.edu.cn", "头像")
    _, viewer = await register_user(client, "blocked-viewer@njust.edu.cn", "搭子")
    me = (await client.get("/api/v1/users/me", headers=owner)).json()
    other = (await client.get("/api/v1/users/me", headers=viewer)).json()
    url = (await upload(client, owner)).json()["avatar_url"]
    runtime = client._transport.app.state.database
    async with runtime.session_factory() as session:
        blocker, blocked = (other, me) if reverse_block else (me, other)
        session.add(UserBlock(blocker_id=blocker["id"], blocked_id=blocked["id"]))
        await session.commit()
    assert (await client.get(url, headers=viewer)).status_code == 404
    assert (await client.get(url, headers=owner)).status_code == 200
    async with runtime.session_factory() as session:
        user = await session.get(User, me["id"])
        user.identity_frozen = True
        await session.commit()
    assert (await upload(client, owner)).status_code == 423
    assert (await client.delete("/api/v1/users/me/avatar", headers=owner)).status_code == 423


async def test_inactive_users_avatar_is_hidden_and_deletion_removes_image(client):
    _, owner = await register_user(client, "inactive-avatar@njust.edu.cn", "头像")
    _, viewer = await register_user(client, "inactive-viewer@njust.edu.cn", "搭子")
    me = (await client.get("/api/v1/users/me", headers=owner)).json()
    url = (await upload(client, owner)).json()["avatar_url"]
    runtime = client._transport.app.state.database
    async with runtime.session_factory() as session:
        user = await session.get(User, me["id"])
        user.is_active = False
        await session.commit()
    assert (await client.get(url, headers=viewer)).status_code == 404
    async with runtime.session_factory() as session:
        await session.delete(await session.get(User, me["id"]))
        await session.commit()
        assert await session.get(UserAvatar, me["id"]) is None


@pytest.mark.parametrize("limit", ["application", "decoder"])
def test_avatar_rejects_oversized_pixel_count(monkeypatch, limit):
    if limit == "application":
        monkeypatch.setattr("app.avatar_media.AVATAR_MAX_PIXELS", 8)
    else:
        monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1)
    with pytest.raises(HTTPException) as exc:
        normalize_avatar(avatar_image(size=(4, 4)), "image/png")
    assert exc.value.status_code == 422


async def test_upload_only_changes_current_account_and_delete_is_idempotent(client):
    _, a = await register_user(client, "avatar-a@njust.edu.cn", "甲")
    _, b = await register_user(client, "avatar-b@njust.edu.cn", "乙")
    b_user = (await client.get("/api/v1/users/me", headers=b)).json()
    response = await client.post(
        "/api/v1/users/me/avatar",
        headers=a,
        data={"user_id": b_user["id"]},
        files={"file": ("avatar.png", avatar_image(), "image/png")},
    )
    assert response.status_code == 200
    assert (await client.get("/api/v1/users/me", headers=b)).json()["avatar_url"] is None
    assert (
        await client.post(
            "/api/v1/users/me/avatar", files={"file": ("avatar.png", avatar_image(), "image/png")}
        )
    ).status_code == 401
    assert (await client.delete("/api/v1/users/me/avatar")).status_code == 401
    assert (await client.delete("/api/v1/users/me/avatar", headers=b)).status_code == 200


@pytest.mark.parametrize(
    "image_format,media_type",
    [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp"), ("GIF", "image/gif")],
)
def test_avatar_supported_raster_formats_are_reencoded(image_format, media_type):
    content = normalize_avatar(avatar_image(image_format=image_format), media_type)
    with Image.open(BytesIO(content)) as image:
        assert image.format == "JPEG"
        assert image.size == (512, 512)
        assert not image.getexif()
