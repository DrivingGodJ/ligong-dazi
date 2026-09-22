"""Durable in-app notifications with optional standards-based Web Push delivery."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from datetime import timedelta
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Activity, ActivityMember, Notification, PushSubscription, User, utcnow

logger = logging.getLogger(__name__)


def ensure_push_key(path: str) -> str:
    """Use one stable private key across restarts, stored on the persistent volume."""
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        key = ec.generate_private_key(ec.SECP256R1())
        content = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as output:
                output.write(content)
    private_key = serialization.load_pem_private_key(target.read_bytes(), password=None)
    assert isinstance(private_key, ec.EllipticCurvePrivateKey)
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return base64.urlsafe_b64encode(public_key).rstrip(b"=").decode("ascii")


async def enqueue_notification(
    session: AsyncSession,
    user_id: str,
    event_key: str,
    kind: str,
    title: str,
    body: str,
    url: str,
) -> None:
    exists = await session.scalar(
        select(Notification.id).where(
            Notification.user_id == user_id, Notification.event_key == event_key
        )
    )
    if exists is None:
        session.add(
            Notification(
                user_id=user_id,
                event_key=event_key,
                kind=kind,
                title=title,
                body=body,
                url=url,
            )
        )


async def enqueue_activity_reminders(session: AsyncSession) -> None:
    now = utcnow()
    rows = (
        await session.execute(
            select(Activity, ActivityMember.user_id)
            .join(ActivityMember, ActivityMember.activity_id == Activity.id)
            .where(
                ActivityMember.status == "confirmed",
                Activity.status.in_(["open", "formed"]),
                Activity.starts_at > now,
                Activity.starts_at <= now + timedelta(minutes=15),
            )
        )
    ).all()
    for activity, user_id in rows:
        await enqueue_notification(
            session,
            user_id,
            f"starts15:{activity.id}:{activity.starts_at.isoformat()}",
            "starts_soon",
            "活动快开始啦",
            f"“{activity.title}”还有不到 15 分钟开始，记得出发！",
            "/?tab=activities",
        )


async def enqueue_legacy_student_id_notices(session: AsyncSession) -> None:
    """Tell old email accounts about the new sign-in option once per account."""
    user_ids = list(
        (
            await session.scalars(
                select(User.id).where(User.student_id.is_(None), User.is_active.is_(True))
            )
        ).all()
    )
    for user_id in user_ids:
        await enqueue_notification(
            session,
            user_id,
            "student_id_migration_20260922",
            "account_update",
            "旧账号请补填学号",
            "绑定前仍可用原邮箱登录。到“我的画像”绑定学号后，旧邮箱会被移除，请改用学号登录。",
            "/?tab=profile",
        )


async def dispatch_push(session: AsyncSession, private_key_path: str) -> None:
    """Outbox processing; push errors do not lose the in-app message."""
    pending = list(
        (
            await session.scalars(
                select(Notification)
                .where(Notification.pushed_at.is_(None), Notification.push_attempts < 5)
                .order_by(Notification.created_at)
                .limit(100)
            )
        ).all()
    )
    for notification in pending:
        subscriptions = list(
            (
                await session.scalars(
                    select(PushSubscription).where(PushSubscription.user_id == notification.user_id)
                )
            ).all()
        )
        if not subscriptions:
            notification.pushed_at = utcnow()
            continue
        failed = False
        for subscription in subscriptions:
            try:
                await asyncio.to_thread(
                    webpush,
                    subscription_info={
                        "endpoint": subscription.endpoint,
                        "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
                    },
                    data=json.dumps(
                        {
                            "title": notification.title,
                            "body": notification.body,
                            "url": notification.url,
                            "tag": notification.event_key,
                        },
                        ensure_ascii=False,
                    ),
                    vapid_private_key=private_key_path,
                    vapid_claims={"sub": "mailto:push@ligong-dazi.zeabur.app"},
                    ttl=900 if notification.kind == "starts_soon" else 86_400,
                    timeout=8,
                )
            except WebPushException as exc:
                if exc.response is not None and exc.response.status_code in (404, 410):
                    await session.delete(subscription)
                else:
                    failed = True
                    logger.warning("推送未送达，将稍后重试：%s", type(exc).__name__)
            except Exception as exc:
                failed = True
                logger.warning("推送暂时失败：%s", type(exc).__name__)
        notification.push_attempts += 1
        if not failed:
            notification.pushed_at = utcnow()
