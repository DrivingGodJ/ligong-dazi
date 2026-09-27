from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity_verification import STUDENT_CARD_SERVER_ERROR_MESSAGE
from app.models import StudentCardUploadAttempt, StudentIdAppeal, User, new_id, utcnow

ACTIVE_APPEAL_STATUSES = {
    "agent_review",
    "awaiting_owner",
    "owner_review",
    "manual_review",
    "claimant_retry",
    "owner_retry",
}

RESOLVABLE_APPEAL_STATUSES = {
    "awaiting_owner",
    "owner_review",
    "manual_review",
    "owner_retry",
}


def can_resolve_identity_appeal(status: str, claimant_review: dict) -> bool:
    """Unapproved claimant evidence is read-only, never an ownership decision."""
    return (
        status in RESOLVABLE_APPEAL_STATUSES
        and claimant_review.get("verdict") == "approved"
        and not claimant_review.get("service_error")
    )


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

    # Protect every entry point, including admin, owner relinquishment and timeouts.
    review_status = "awaiting_owner" if appeal.status == "timeout_processing" else appeal.status
    if not can_resolve_identity_appeal(review_status, appeal.claimant_agent_review):
        return None
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
    if not appeal.claimant_user_id:
        # The claimant's daily allowance follows them when their account is created.
        await session.execute(
            update(StudentCardUploadAttempt)
            .where(
                StudentCardUploadAttempt.subject_key == f"claimant:{appeal.student_id}",
            )
            .values(subject_key=f"user:{claimant.id}")
        )
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
                    StudentIdAppeal.owner_card_path.is_(None),
                    StudentIdAppeal.owner_deadline.is_not(None),
                    StudentIdAppeal.owner_deadline <= current,
                )
            )
        ).all()
    )
    transferred: list[StudentIdAppeal] = []
    for appeal in appeals:
        if not can_resolve_identity_appeal(appeal.status, appeal.claimant_agent_review):
            continue
        claimed = await session.execute(
            update(StudentIdAppeal)
            .where(
                StudentIdAppeal.id == appeal.id,
                StudentIdAppeal.status == "awaiting_owner",
                StudentIdAppeal.owner_card_path.is_(None),
                StudentIdAppeal.owner_deadline <= current,
            )
            .values(status="timeout_processing")
        )
        if not claimed.rowcount:
            continue
        claimant = await transfer_student_id(session, appeal, "原账号在 24 小时内未提交材料")
        if claimant is not None:
            transferred.append(appeal)
    return transferred


def apply_owner_card_review(appeal: StudentIdAppeal, review: dict) -> str:
    appeal.owner_agent_review = review
    # Uploading is a response, even when recognition fails. Only silence can time out.
    appeal.owner_deadline = None
    if not review.get("service_error") and review.get("verdict") == "approved":
        appeal.status = "manual_review"
        appeal.resolution_note = "双方学生卡材料均通过 AI 初审，等待人工核验联系方式"
        return "双方材料都通过了初审，请补充联系方式并等待管理员联系。"
    appeal.status = "owner_retry"
    appeal.resolution_note = str(review.get("reason") or "材料未通过识别")[:300]
    if review.get("service_error"):
        return STUDENT_CARD_SERVER_ERROR_MESSAGE + "你已提交照片，不会自动注销账号。"
    return "材料未通过识别，今天的机会已使用，请明天重新上传；账号不会因本次识别失败自动注销。"
