from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Store UTC consistently and return timezone-aware values on every database."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("datetime 必须包含时区")
        value = value.astimezone(UTC)
        if dialect.name == "sqlite":
            return value.replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, _: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=utcnow,
        onupdate=utcnow,
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(80))
    university: Mapped[str] = mapped_column(String(120), default="南京理工大学", index=True)
    campus: Mapped[str | None] = mapped_column(String(80), nullable=True)
    department: Mapped[str | None] = mapped_column(String(120), nullable=True)
    grade_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gender: Mapped[str] = mapped_column(String(20), default="undisclosed", index=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    interests: Mapped[list[str]] = mapped_column(JSON, default=list)
    hobby_skills: Mapped[list[dict]] = mapped_column(JSON, default=list)
    skill_marks: Mapped[list[dict]] = mapped_column(JSON, default=list)
    preferred_locations: Mapped[list[str]] = mapped_column(JSON, default=list)
    social_style: Mapped[str] = mapped_column(String(30), default="balanced")
    preferred_group_min: Mapped[int] = mapped_column(Integer, default=2)
    preferred_group_max: Mapped[int] = mapped_column(Integer, default=6)
    credit_score: Mapped[int] = mapped_column(Integer, default=100, index=True)
    hidden_profile: Mapped[dict] = mapped_column(JSON, default=dict)
    hidden_profile_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_summary_generated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class UserBlock(Base):
    __tablename__ = "user_blocks"
    __table_args__ = (UniqueConstraint("blocker_id", "blocked_id", name="uq_user_block"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    blocker_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    blocked_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Activity(Base, TimestampMixin):
    __tablename__ = "activities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    campus: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    join_policy: Mapped[str] = mapped_column(String(20), default="open")
    title: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(50), index=True)
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    location: Mapped[str] = mapped_column(String(160), index=True)
    capacity: Mapped[int] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    personal_requirement: Mapped[str | None] = mapped_column(Text, nullable=True)
    same_gender_only: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default="open", index=True)
    post_activity_processed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )


class ActivityMember(Base):
    __tablename__ = "activity_members"
    __table_args__ = (UniqueConstraint("activity_id", "user_id", name="uq_activity_member"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    activity_id: Mapped[str] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(30), default="participant")
    status: Mapped[str] = mapped_column(String(30), default="confirmed")
    joined_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    left_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    leave_penalty: Mapped[int] = mapped_column(Integer, default=0)


class JoinApplication(Base):
    __tablename__ = "join_applications"
    __table_args__ = (UniqueConstraint("activity_id", "applicant_id", name="uq_join_applicant"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    activity_id: Mapped[str] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"), index=True
    )
    applicant_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class JoinApproval(Base):
    __tablename__ = "join_approvals"
    __table_args__ = (UniqueConstraint("application_id", "member_id", name="uq_join_approval"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(
        ForeignKey("join_applications.id", ondelete="CASCADE"), index=True
    )
    member_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    decision: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("user_id", "event_key", name="uq_notification_event"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    event_key: Mapped[str] = mapped_column(String(180))
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(String(255), default="/")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    pushed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    push_attempts: Mapped[int] = mapped_column(Integer, default=0)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(Text)
    auth: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ActivityPhoto(Base):
    __tablename__ = "activity_photos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    activity_id: Mapped[str] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"), index=True
    )
    uploader_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    file_path: Mapped[str] = mapped_column(String(500))
    media_type: Mapped[str] = mapped_column(String(60))
    uploaded_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class ActivityTimeVote(Base):
    __tablename__ = "activity_time_votes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    activity_id: Mapped[str] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"), index=True
    )
    proposer_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    proposed_starts_at: Mapped[datetime] = mapped_column(UTCDateTime())
    proposed_ends_at: Mapped[datetime] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class ActivityTimeVoteResponse(Base):
    __tablename__ = "activity_time_vote_responses"
    __table_args__ = (
        UniqueConstraint("vote_id", "user_id", name="uq_activity_time_vote_response"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    vote_id: Mapped[str] = mapped_column(
        ForeignKey("activity_time_votes.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    decision: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class MatchRequest(Base, TimestampMixin):
    __tablename__ = "match_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    requester_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    category: Mapped[str] = mapped_column(String(50), index=True)
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime())
    location: Mapped[str] = mapped_column(String(160), index=True)
    people_needed: Mapped[int] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    personal_requirement: Mapped[str | None] = mapped_column(Text, nullable=True)
    same_gender_only: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default="previewed", index=True)
    activity_id: Mapped[str | None] = mapped_column(ForeignKey("activities.id"), nullable=True)


class MatchCandidate(Base):
    __tablename__ = "match_candidates"
    __table_args__ = (
        UniqueConstraint(
            "match_request_id", "candidate_type", "candidate_id", name="uq_match_candidate"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    match_request_id: Mapped[str] = mapped_column(
        ForeignKey("match_requests.id", ondelete="CASCADE"), index=True
    )
    candidate_type: Mapped[str] = mapped_column(String(20))
    candidate_id: Mapped[str] = mapped_column(String(36), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)
    factors: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    explanation: Mapped[list[str]] = mapped_column(JSON, default=list)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    match_request_id: Mapped[str] = mapped_column(
        ForeignKey("match_requests.id", ondelete="CASCADE"), index=True
    )
    mode: Mapped[str] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="running")
    trace: Mapped[list[dict]] = mapped_column(JSON, default=list)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class SystemEvent(Base):
    __tablename__ = "system_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    category: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    title: Mapped[str] = mapped_column(String(160))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class Invitation(Base, TimestampMixin):
    __tablename__ = "invitations"
    __table_args__ = (UniqueConstraint("activity_id", "invitee_id", name="uq_activity_invitee"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id"), index=True)
    match_request_id: Mapped[str | None] = mapped_column(
        ForeignKey("match_requests.id"), nullable=True, index=True
    )
    inviter_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    invitee_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    responded_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (
        UniqueConstraint("activity_id", "user_id", "remind_at", name="uq_activity_reminder"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    remind_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    status: Mapped[str] = mapped_column(String(30), default="scheduled", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Feedback(Base):
    __tablename__ = "feedback"
    __table_args__ = (
        UniqueConstraint("activity_id", "reviewer_id", "reviewee_id", name="uq_feedback_pair"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id"), index=True)
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    reviewee_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    attendance: Mapped[str] = mapped_column(String(30))
    rating: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    skill_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    skill_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    personality_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    incident_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    moderation_status: Mapped[str] = mapped_column(String(40), default="reviewing", index=True)
    ai_authenticity: Mapped[float] = mapped_column(Float, default=0.5)
    ai_malicious_risk: Mapped[float] = mapped_column(Float, default=0.0)
    ai_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    requires_peer_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    reviewee_credit_delta: Mapped[int] = mapped_column(Integer, default=0)
    reviewer_credit_delta: Mapped[int] = mapped_column(Integer, default=0)
    finalized_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class PeerFeedbackReview(Base):
    __tablename__ = "peer_feedback_reviews"
    __table_args__ = (
        UniqueConstraint("feedback_id", "reviewer_id", name="uq_peer_feedback_reviewer"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    feedback_id: Mapped[str] = mapped_column(
        ForeignKey("feedback.id", ondelete="CASCADE"), index=True
    )
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    verdict: Mapped[str] = mapped_column(String(30))
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    true_parts: Mapped[str | None] = mapped_column(Text, nullable=True)
    false_parts: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_credit_delta: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class CreditEvent(Base):
    __tablename__ = "credit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    delta: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(80))
    activity_id: Mapped[str | None] = mapped_column(
        ForeignKey("activities.id"), nullable=True, index=True
    )
    source_feedback_id: Mapped[str | None] = mapped_column(
        ForeignKey("feedback.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
