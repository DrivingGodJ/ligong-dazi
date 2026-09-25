from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import StudentIdAppeal, User, new_id, utcnow

ACTIVE_APPEAL_STATUSES = {
    "agent_review",
    "awaiting_owner",
    "owner_review",
    "manual_review",
}


def redact_claimant_registration(appeal: StudentIdAppeal) -> None:
    appeal.claimant_profile = {}
    appeal.claimant_password_hash = None


def remove_appeal_files(appeal: StudentIdAppeal) -> None:
    for path in (appeal.claimant_card_path, appeal.owner_card_path):
        if path:
            Path(path).unlink(missing_ok=True)


async def transfer_student_id(
    session: AsyncSession,
    appeal: StudentIdAppeal,
    reason: str,
) -> User | None:
    """Move only the student identity to a fresh account; never transfer old history."""

    if not appeal.claimant_user_id and (
        not appeal.claimant_profile or not appeal.claimant_password_hash
    ):
        appeal.status = "manual_review"
        appeal.resolution_note = "申诉人注册资料不完整，无法自动交接，需人工处理"
        return None
    owner = await session.get(User, appeal.owner_id)
    if owner is None:
        appeal.status = "manual_review"
        appeal.resolution_note = "原账号不存在，需人工核查"
        return None
    claimant = await session.get(User, appeal.claimant_user_id) if appeal.claimant_user_id else None
    if appeal.claimant_user_id and (
        claimant is None or not claimant.is_active or claimant.student_id is not None
    ):
        appeal.status = "manual_review"
        appeal.resolution_note = "申诉人的现有账号状态已变化，需人工核查"
        return None
    profile = appeal.claimant_profile
    existing = await session.scalar(
        select(User.id).where(User.student_id == appeal.student_id, User.id != owner.id)
    )
    if existing:
        appeal.status = "manual_review"
        appeal.resolution_note = "学号归属在处理期间再次变化，需人工核查"
        return None

    owner.student_id = None
    owner.identity_frozen = False
    owner.identity_appeal_id = None
    owner.is_active = False
    await session.flush()
    if claimant is None:
        claimant = User(
            id=new_id(),
            email=f"no-email-{new_id()}@accounts.invalid",
            student_id=appeal.student_id,
            password_hash=appeal.claimant_password_hash,
            display_name=str(profile["display_name"]),
            university="南京理工大学",
            campus=str(profile["campus"]),
            department=profile.get("department"),
            grade_year=profile.get("grade_year"),
            gender=str(profile.get("gender") or "undisclosed"),
            bio=profile.get("bio"),
            interests=list(profile.get("interests") or []),
            hobby_skills=list(profile.get("hobby_skills") or []),
            preferred_locations=list(profile.get("preferred_locations") or []),
            social_style=str(profile.get("social_style") or "balanced"),
            preferred_group_min=int(profile.get("preferred_group_min") or 2),
            preferred_group_max=int(profile.get("preferred_group_max") or 6),
        )
        session.add(claimant)
    else:
        claimant.student_id = appeal.student_id
        if not claimant.email.endswith("@accounts.invalid"):
            claimant.email = f"no-email-{new_id()}@accounts.invalid"
    appeal.status = "transferred"
    appeal.resolution_note = reason[:300]
    appeal.resolved_at = utcnow()
    redact_claimant_registration(appeal)
    return claimant


async def process_expired_identity_appeals(
    session: AsyncSession,
    now: datetime | None = None,
) -> list[StudentIdAppeal]:
    current = now or utcnow()
    appeals = list(
        (
            await session.scalars(
                select(StudentIdAppeal).where(
                    StudentIdAppeal.status == "awaiting_owner",
                    StudentIdAppeal.owner_deadline.is_not(None),
                    StudentIdAppeal.owner_deadline <= current,
                )
            )
        ).all()
    )
    transferred: list[StudentIdAppeal] = []
    for appeal in appeals:
        claimant = await transfer_student_id(session, appeal, "原账号在 24 小时内未提交材料")
        if claimant is not None:
            transferred.append(appeal)
    return transferred
