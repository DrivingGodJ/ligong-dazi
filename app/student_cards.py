from __future__ import annotations

import asyncio
import warnings
from datetime import datetime, time, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import Settings
from app.models import StudentCardUploadAttempt, utcnow

CHINA_TIME = ZoneInfo("Asia/Shanghai")


def upload_day(now: datetime | None = None) -> str:
    return (now or utcnow()).astimezone(CHINA_TIME).date().isoformat()


def next_upload_time(now: datetime | None = None) -> datetime:
    current = (now or utcnow()).astimezone(CHINA_TIME)
    return datetime.combine(current.date() + timedelta(days=1), time(), CHINA_TIME)


async def card_upload_available(session: AsyncSession, subject: str, settings: Settings) -> bool:
    if settings.student_card_uploads_unlimited:
        return True
    return not await session.scalar(
        select(StudentCardUploadAttempt.id).where(
            StudentCardUploadAttempt.subject_key == subject,
            StudentCardUploadAttempt.upload_day == upload_day(),
        )
    )


def daily_upload_error() -> HTTPException:
    retry = next_upload_time()
    return HTTPException(
        status_code=429,
        detail={
            "code": "student_card_daily_limit",
            "message": (
                "今天已上传过学生卡，每人每天仅一次；识别失败也请明天再试（北京时间凌晨重置）。"
            ),
            "next_upload_at": retry.isoformat(),
        },
        headers={"Retry-After": str(max(1, int((retry - utcnow()).total_seconds())))},
    )


async def ensure_card_upload_available(
    session: AsyncSession, subject: str, settings: Settings
) -> None:
    if not await card_upload_available(session, subject, settings):
        raise daily_upload_error()


async def reserve_card_upload(
    session: AsyncSession, subject: str, purpose: str, settings: Settings
) -> str | None:
    """Commit before AI review; a unique key enforces the limit across workers/restarts."""
    if settings.student_card_uploads_unlimited:
        # Still persist dispute evidence before the AI call; preserve existing quota rows.
        await session.commit()
        return None
    attempt = StudentCardUploadAttempt(
        subject_key=subject,
        upload_day=upload_day(),
        purpose=purpose,
    )
    session.add(attempt)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise daily_upload_error() from exc
    return attempt.id


async def release_card_upload(session: AsyncSession, attempt_id: str | None) -> None:
    """Refund only this request's reservation; the caller commits with its review result."""
    if attempt_id is not None:
        await session.execute(
            delete(StudentCardUploadAttempt).where(StudentCardUploadAttempt.id == attempt_id)
        )


def compress_student_card(content: bytes) -> bytes:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as source:
                if source.width * source.height > 24_000_000:
                    raise ValueError("图片尺寸过大")
                image = ImageOps.exif_transpose(source)
                image.thumbnail((1280, 1280))
                rgba = image.convert("RGBA")
                flattened = Image.new("RGB", rgba.size, "white")
                flattened.paste(rgba, mask=rgba.getchannel("A"))
                for quality in (65, 55, 45, 35):
                    output = BytesIO()
                    flattened.save(output, format="JPEG", quality=quality, optimize=True)
                    result = output.getvalue()
                    if len(result) <= 256 * 1024:
                        return result
                raise ValueError("照片内容过复杂，请靠近学生卡重新拍摄")
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise HTTPException(status_code=422, detail="学生卡照片无法处理，请换一张清晰照片") from exc


async def read_student_card(upload: UploadFile, settings: Settings) -> tuple[bytes, str, str]:
    try:
        if upload.content_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise HTTPException(status_code=422, detail="学生卡照片请选择 JPG、PNG 或 WebP 格式")
        content = await upload.read(settings.identity_card_max_bytes + 1)
        if len(content) > settings.identity_card_max_bytes:
            raise HTTPException(status_code=413, detail="学生卡照片过大，请先压缩后上传")
    finally:
        await upload.close()
    return await asyncio.to_thread(compress_student_card, content), "image/jpeg", "jpg"
