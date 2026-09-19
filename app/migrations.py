from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

SQLITE_COMPATIBILITY_COLUMNS = {
    "users": {
        "hidden_profile": "JSON NOT NULL DEFAULT '{}'",
        "hidden_profile_updated_at": "DATETIME",
    },
    "activities": {
        "post_activity_processed_at": "DATETIME",
    },
    "activity_members": {
        "left_at": "DATETIME",
        "leave_penalty": "INTEGER NOT NULL DEFAULT 0",
    },
    "feedback": {
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
            present = existing.get(table, set())
            for column, definition in columns.items():
                if column not in present:
                    await connection.execute(
                        text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')
                    )
