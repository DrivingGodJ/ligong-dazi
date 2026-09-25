from __future__ import annotations

from sqlalchemy import inspect, or_, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.campus import CAMPUSES, infer_campus
from app.colleges import match_college
from app.models import User, new_id

SQLITE_COMPATIBILITY_COLUMNS = {
    "users": {
        "student_id": "VARCHAR(24)",
        "gender": "VARCHAR(20) NOT NULL DEFAULT 'undisclosed'",
        "hidden_profile": "JSON NOT NULL DEFAULT '{}'",
        "hobby_skills": "JSON NOT NULL DEFAULT '[]'",
        "skill_marks": "JSON NOT NULL DEFAULT '[]'",
        "hidden_profile_updated_at": "DATETIME",
        "ai_summary": "TEXT",
        "ai_summary_generated_at": "DATETIME",
        "allow_invitations": "BOOLEAN NOT NULL DEFAULT 1",
        "identity_frozen": "BOOLEAN NOT NULL DEFAULT 0",
        "identity_appeal_id": "VARCHAR(36)",
    },
    "student_id_appeals": {
        "owner_id": "VARCHAR(36)",
        "claimant_user_id": "VARCHAR(36)",
        "claimant_profile": "JSON NOT NULL DEFAULT '{}'",
        "claimant_password_hash": "VARCHAR(255)",
        "claimant_card_path": "VARCHAR(500)",
        "claimant_card_media_type": "VARCHAR(60)",
        "claimant_agent_review": "JSON NOT NULL DEFAULT '{}'",
        "owner_card_path": "VARCHAR(500)",
        "owner_card_media_type": "VARCHAR(60)",
        "owner_agent_review": "JSON NOT NULL DEFAULT '{}'",
        "owner_deadline": "DATETIME",
        "owner_contact": "VARCHAR(160)",
        "resolution_note": "VARCHAR(500)",
    },
    "activities": {
        "post_activity_processed_at": "DATETIME",
        "same_gender_only": "BOOLEAN NOT NULL DEFAULT 0",
        "campus": "VARCHAR(80)",
        "join_policy": "VARCHAR(20) NOT NULL DEFAULT 'open'",
    },
    "match_requests": {
        "same_gender_only": "BOOLEAN NOT NULL DEFAULT 0",
    },
    "activity_members": {
        "left_at": "DATETIME",
        "leave_penalty": "INTEGER NOT NULL DEFAULT 0",
    },
    "feedback": {
        "skill_name": "VARCHAR(50)",
        "skill_level": "INTEGER",
        "personality_tags": "JSON NOT NULL DEFAULT '[]'",
        "incident_tags": "JSON NOT NULL DEFAULT '[]'",
        "moderation_status": "VARCHAR(40) NOT NULL DEFAULT 'finalized_legacy'",
        "ai_authenticity": "FLOAT NOT NULL DEFAULT 0.5",
        "ai_malicious_risk": "FLOAT NOT NULL DEFAULT 0",
        "ai_reason": "TEXT",
        "requires_peer_review": "BOOLEAN NOT NULL DEFAULT 0",
        "reviewee_credit_delta": "INTEGER NOT NULL DEFAULT 0",
        "reviewer_credit_delta": "INTEGER NOT NULL DEFAULT 0",
        "finalized_at": "DATETIME",
    },
}


async def ensure_sqlite_compatibility(engine: AsyncEngine) -> None:
    """Keep local MVP databases usable until a formal migration tool is introduced."""

    if engine.dialect.name != "sqlite":
        return
    async with engine.begin() as connection:
        existing = await connection.run_sync(
            lambda sync_connection: {
                table: {column["name"] for column in inspect(sync_connection).get_columns(table)}
                for table in SQLITE_COMPATIBILITY_COLUMNS
                if inspect(sync_connection).has_table(table)
            }
        )
        for table, columns in SQLITE_COMPATIBILITY_COLUMNS.items():
            if table not in existing:
                continue
            present = existing.get(table, set())
            for column, definition in columns.items():
                if column not in present:
                    await connection.execute(
                        text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')
                    )
        if "users" in existing:
            await connection.execute(
                text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_student_id ON users (student_id)")
            )
            await connection.execute(
                text(
                    "UPDATE users SET gender = 'undisclosed' "
                    "WHERE gender IS NULL OR gender NOT IN ('male', 'female', 'undisclosed')"
                )
            )


async def migrate_user_campuses(session: AsyncSession) -> int:
    """Normalize legacy values; default missing or unclear campuses to Jiangyin."""

    users = list((await session.scalars(select(User))).all())
    migrated = 0
    for user in users:
        if user.campus in CAMPUSES:
            continue
        user.campus = infer_campus(user.campus) or "江阴"
        migrated += 1
    return migrated


async def migrate_user_colleges(session: AsyncSession) -> int:
    """Map known college names and reset unrecognized legacy text to the empty default."""
    users = list((await session.scalars(select(User).where(User.department.is_not(None)))).all())
    migrated = 0
    for user in users:
        college = match_college(user.department)
        if college != user.department:
            user.department = college
            migrated += 1
    return migrated


async def remove_emails_from_bound_accounts(session: AsyncSession) -> int:
    """Remove legacy login emails once a student ID is bound (email column is NOT NULL)."""
    users = list(
        (
            await session.scalars(
                select(User).where(
                    User.student_id.is_not(None),
                    User.email.not_like("%@accounts.invalid"),
                )
            )
        ).all()
    )
    for user in users:
        user.email = f"no-email-{new_id()}@accounts.invalid"
    return len(users)


async def migrate_activity_campuses(session: AsyncSession) -> int:
    """Keep legacy activities in their creator's normalized campus."""
    from app.models import Activity

    await session.flush()
    rows = (
        await session.execute(
            select(Activity, User.campus)
            .join(User, User.id == Activity.owner_id)
            .where(or_(Activity.campus.is_(None), Activity.campus.not_in(CAMPUSES)))
        )
    ).all()
    for activity, campus in rows:
        if campus in CAMPUSES:
            activity.campus = campus
    return sum(campus in CAMPUSES for _, campus in rows)
