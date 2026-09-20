from __future__ import annotations

import base64
import binascii
import re
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Activity, ActivityPhoto, utcnow

IMAGE_TYPES = {
    "image/jpeg": ("jpg", lambda value: value.startswith(b"\xff\xd8\xff")),
    "image/png": ("png", lambda value: value.startswith(b"\x89PNG\r\n\x1a\n")),
    "image/webp": (
        "webp",
        lambda value: len(value) >= 12 and value[:4] == b"RIFF" and value[8:12] == b"WEBP",
    ),
    "image/gif": ("gif", lambda value: value.startswith((b"GIF87a", b"GIF89a"))),
}


def decode_image_data_url(data_url: str, max_bytes: int) -> tuple[bytes, str, str]:
    match = re.fullmatch(r"data:(image/[a-z0-9.+-]+);base64,([A-Za-z0-9+/=\r\n]+)", data_url)
    if match is None or match.group(1) not in IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="请选择 JPG、PNG、WebP 或 GIF 图片。",
        )
    media_type = match.group(1)
    try:
        content = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="图片内容无法读取，请重新选择。",
        ) from exc
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"单张图片不能超过 {max_bytes // (1024 * 1024)} MB。",
        )
    extension, signature_check = IMAGE_TYPES[media_type]
    if not content or not signature_check(content):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="图片格式与文件内容不一致，请换一张图片。",
        )
    return content, media_type, extension


def remove_photo_file(photo: ActivityPhoto) -> None:
    Path(photo.file_path).unlink(missing_ok=True)


async def cleanup_expired_activity_photos(
    session: AsyncSession,
    now: datetime | None = None,
) -> int:
    current = (now or utcnow()).astimezone(UTC)
    photos = list(
        (
            await session.scalars(
                select(ActivityPhoto)
                .join(Activity, Activity.id == ActivityPhoto.activity_id)
                .where(Activity.ends_at <= current)
            )
        ).all()
    )
    for photo in photos:
        remove_photo_file(photo)
        await session.delete(photo)
    return len(photos)


def _escape_ics(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _fold_ics_line(line: str) -> list[str]:
    chunks: list[str] = []
    current = ""
    limit = 75
    for character in line:
        prefix = "" if not chunks else " "
        if len((prefix + current + character).encode("utf-8")) > limit and current:
            chunks.append(("" if not chunks else " ") + current)
            current = character
        else:
            current += character
    chunks.append(("" if not chunks else " ") + current)
    return chunks


def build_activity_ics(activity: Activity) -> bytes:
    now_text = utcnow().strftime("%Y%m%dT%H%M%SZ")
    starts_text = activity.starts_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    ends_text = activity.ends_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    description = activity.description or f"{activity.category}搭子活动"
    logical_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Ligong Dazi//Activity Calendar//ZH-CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{activity.id}@ligong-dazi.local",
        f"DTSTAMP:{now_text}",
        f"DTSTART:{starts_text}",
        f"DTEND:{ends_text}",
        f"SUMMARY:{_escape_ics(activity.title)}",
        f"LOCATION:{_escape_ics(activity.location)}",
        f"DESCRIPTION:{_escape_ics(description)}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    physical_lines = [part for line in logical_lines for part in _fold_ics_line(line)]
    return ("\r\n".join(physical_lines) + "\r\n").encode("utf-8")
