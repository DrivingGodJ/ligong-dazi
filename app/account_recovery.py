from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from datetime import timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from sqlalchemy import or_, select, update

from app import api as identity_api
from app.api import SessionDep, SettingsDep
from app.core import hash_password, verify_password
from app.identity_appeals import apply_owner_card_review
from app.identity_verification import STUDENT_CARD_SERVER_ERROR_MESSAGE
from app.models import PasswordResetGrant, StudentIdAppeal, User, utcnow
from app.notifications import enqueue_notification
from app.schemas import PasswordResetRequest, PasswordResetReview, normalize_student_id
from app.student_cards import (
    ensure_card_upload_available,
    next_upload_time,
    read_student_card,
    release_card_upload,
    reserve_card_upload,
)

router = APIRouter(prefix="/api/v1/auth")


@router.post("/password-reset/card", response_model=PasswordResetReview)
async def verify_password_reset_card(
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    student_id: Annotated[str, Form()],
    student_card: Annotated[UploadFile, File()],
    ai_consent: Annotated[bool, Form()],
) -> PasswordResetReview:
    response.headers["Cache-Control"] = "no-store"
    try:
        student_id = normalize_student_id(student_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not ai_consent:
        raise HTTPException(status_code=422, detail="请先同意学生卡照片用于身份核验")
    host = request.client.host if request.client else "unknown"
    stamps = request.app.state.appeal_attempts
    now_tick = time.monotonic()
    recent = [tick for tick in stamps.get(host, []) if now_tick - tick < 3600]
    if not settings.student_card_uploads_unlimited and len(recent) >= 30:
        raise HTTPException(status_code=429, detail="身份核验提交较频繁，请稍后再试")
    if not settings.student_card_uploads_unlimited:
        stamps[host] = [*recent, now_tick]
    user = await session.scalar(
        select(User).where(
            User.student_id == student_id,
            User.is_active.is_(True),
        )
    )
    subject = f"user:{user.id}" if user else f"reset:{student_id}"
    await ensure_card_upload_available(session, subject, settings)

    # An unauthenticated applicant is not yet the owner. Only a verified card can
    # count as their dispute response; otherwise anyone could cancel the deadline.
    appeal = (
        await session.get(StudentIdAppeal, user.identity_appeal_id)
        if (user and user.identity_frozen and user.identity_appeal_id)
        else None
    )
    old_path = appeal.owner_card_path if appeal else None
    path = None
    if (
        appeal
        and appeal.owner_id == user.id
        and appeal.status
        in {
            "awaiting_owner",
            "owner_retry",
            "owner_review",
        }
    ):
        path, media_type, content = await identity_api.save_identity_card(
            student_card,
            appeal.id,
            "owner",
            settings,
        )
    else:
        if appeal and appeal.status == "timeout_processing":
            raise HTTPException(status_code=409, detail="身份材料正在处理中，请稍后再试")
        content, media_type, _ = await read_student_card(student_card, settings)
        appeal = None
    try:
        attempt_id = await reserve_card_upload(session, subject, "password_reset", settings)
    except Exception:
        if path:
            await asyncio.to_thread(Path(path).unlink, missing_ok=True)
        raise
    try:
        review = await identity_api.safely_review_student_card(
            content, media_type, student_id, settings
        )
    except Exception:
        if path:
            await asyncio.to_thread(Path(path).unlink, missing_ok=True)
        raise
    if review.service_error:
        await release_card_upload(session, attempt_id)
        await session.commit()
        if path:
            await asyncio.to_thread(Path(path).unlink, missing_ok=True)
        return PasswordResetReview(
            status="server_error",
            message=STUDENT_CARD_SERVER_ERROR_MESSAGE + "原密码不会改变。",
            can_upload=True,
            service_error=True,
        )
    if user:
        # Ownership or activity can change while the AI request is running.
        await session.refresh(user)
    approved = (
        user is not None
        and user.is_active
        and user.student_id == student_id
        and review.verdict == "approved"
        and review.confidence >= 0.85
        and review.extracted_student_id == student_id
    )
    if not approved:
        if path:
            await asyncio.to_thread(Path(path).unlink, missing_ok=True)
        message = (
            "未能核验学生卡与账号，请确认学校、照片和学号清晰且正确。"
            "今天仅有一次机会，请明天重试；原密码不会改变。"
        )
        if settings.student_card_uploads_unlimited:
            message = (
                "未能核验学生卡与账号，请确认学校、照片和学号清晰且正确。"
            ) + "本地测试不限次数，可以立即重新上传；原密码不会改变。"
        return PasswordResetReview(
            status="retry" if settings.student_card_uploads_unlimited else "retry_tomorrow",
            message=message,
            can_upload=settings.student_card_uploads_unlimited,
            next_upload_at=None if settings.student_card_uploads_unlimited else next_upload_time(),
        )
    retained_path = False
    if appeal:
        claimed = await session.execute(
            update(StudentIdAppeal)
            .where(
                StudentIdAppeal.id == appeal.id,
                StudentIdAppeal.owner_id == user.id,
                StudentIdAppeal.status.in_(["awaiting_owner", "owner_retry", "owner_review"]),
                or_(
                    StudentIdAppeal.owner_card_path.is_not(None),
                    StudentIdAppeal.owner_deadline > utcnow(),
                ),
            )
            .values(owner_card_path=path, owner_card_media_type=media_type)
        )
        if claimed.rowcount:
            apply_owner_card_review(appeal, review.as_dict())
            retained_path = True
    token = secrets.token_urlsafe(32)
    expires_at = utcnow() + timedelta(minutes=15)
    session.add(
        PasswordResetGrant(
            user_id=user.id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            password_hash_snapshot=user.password_hash,
            student_id=student_id,
            expires_at=expires_at,
        )
    )
    await session.commit()
    if path and not retained_path:
        await asyncio.to_thread(Path(path).unlink, missing_ok=True)
    if retained_path and old_path and old_path != path:
        await asyncio.to_thread(Path(old_path).unlink, missing_ok=True)
    return PasswordResetReview(
        status="approved",
        message="学生卡核验通过，请在 15 分钟内设置新密码，并输入两次确认。",
        reset_token=token,
        expires_at=expires_at,
        next_upload_at=None if settings.student_card_uploads_unlimited else next_upload_time(),
    )


@router.post("/password-reset/complete")
async def complete_password_reset(
    payload: PasswordResetRequest,
    response: Response,
    session: SessionDep,
) -> dict[str, str]:
    response.headers["Cache-Control"] = "no-store"
    digest = hashlib.sha256(payload.reset_token.encode()).hexdigest()
    grant = await session.scalar(
        select(PasswordResetGrant).where(
            PasswordResetGrant.token_hash == digest,
        )
    )
    now = utcnow()
    user = await session.get(User, grant.user_id) if grant else None
    if (
        grant is None
        or grant.used_at
        or grant.expires_at <= now
        or user is None
        or not user.is_active
        or user.student_id != grant.student_id
        or user.password_hash != grant.password_hash_snapshot
    ):
        raise HTTPException(status_code=400, detail="重设凭证已失效，请重新进行学生卡核验")
    if verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=422, detail="新密码不能与原密码相同")
    claimed = await session.execute(
        update(PasswordResetGrant)
        .where(
            PasswordResetGrant.id == grant.id,
            PasswordResetGrant.used_at.is_(None),
            PasswordResetGrant.expires_at > now,
        )
        .values(used_at=now)
    )
    changed = await session.execute(
        update(User)
        .where(
            User.id == user.id,
            User.is_active.is_(True),
            User.student_id == grant.student_id,
            User.password_hash == grant.password_hash_snapshot,
        )
        .values(password_hash=hash_password(payload.password), token_version=User.token_version + 1)
    )
    if not claimed.rowcount or not changed.rowcount:
        await session.rollback()
        raise HTTPException(status_code=400, detail="重设凭证已使用或账号状态已变化")
    await enqueue_notification(
        session,
        user.id,
        f"password_reset:{grant.id}",
        "account_update",
        "你的密码已重设",
        "学生卡核验后密码已更新，其他设备需要重新登录。",
        "/?tab=profile",
    )
    await session.commit()
    return {"message": "密码已更新，请使用新密码登录；其他设备的旧登录凭证已失效。"}
