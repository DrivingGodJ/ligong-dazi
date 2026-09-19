from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RegisterRequest(ApiModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)
    university: str = Field(default="南京理工大学", min_length=2, max_length=120)


class LoginRequest(ApiModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class TokenResponse(ApiModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime


class UserProfileUpdate(ApiModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    campus: str | None = Field(default=None, max_length=80)
    department: str | None = Field(default=None, max_length=120)
    grade_year: int | None = Field(default=None, ge=1, le=8)
    bio: str | None = Field(default=None, max_length=500)
    interests: list[str] | None = None
    preferred_locations: list[str] | None = None
    social_style: Literal["quiet", "balanced", "outgoing"] | None = None
    preferred_group_min: int | None = Field(default=None, ge=2, le=30)
    preferred_group_max: int | None = Field(default=None, ge=2, le=30)

    @field_validator("interests", "preferred_locations")
    @classmethod
    def clean_string_list(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if len(cleaned) > 30:
            raise ValueError("列表最多包含 30 项")
        return cleaned

    @model_validator(mode="after")
    def validate_group_range(self) -> UserProfileUpdate:
        if (
            self.preferred_group_min is not None
            and self.preferred_group_max is not None
            and self.preferred_group_min > self.preferred_group_max
        ):
            raise ValueError("preferred_group_min 不能大于 preferred_group_max")
        return self


class UserPublic(ApiModel):
    id: str
    display_name: str
    university: str
    campus: str | None
    department: str | None
    grade_year: int | None
    bio: str | None
    interests: list[str]
    preferred_locations: list[str]
    social_style: str
    preferred_group_min: int
    preferred_group_max: int
    credit_score: int


class UserMe(UserPublic):
    email: EmailStr
    is_active: bool
    created_at: datetime


class ActivityCreate(ApiModel):
    title: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=50)
    starts_at: datetime
    ends_at: datetime
    location: str = Field(min_length=1, max_length=160)
    capacity: int = Field(ge=2, le=50)
    description: str | None = Field(default=None, max_length=1000)
    personal_requirement: str | None = Field(default=None, max_length=500)

    @field_validator("starts_at", "ends_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("时间必须包含时区，例如 +08:00")
        return value

    @model_validator(mode="after")
    def validate_times(self) -> ActivityCreate:
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at 必须晚于 starts_at")
        if self.starts_at <= datetime.now(UTC):
            raise ValueError("starts_at 必须晚于当前时间")
        return self


class ActivityPublic(ApiModel):
    id: str
    owner_id: str
    title: str
    category: str
    starts_at: datetime
    ends_at: datetime
    location: str
    capacity: int
    participant_count: int
    description: str | None
    personal_requirement: str | None
    status: str


class MatchPreviewRequest(ApiModel):
    category: str = Field(min_length=1, max_length=50)
    starts_at: datetime
    ends_at: datetime
    location: str = Field(min_length=1, max_length=160)
    people_needed: int = Field(ge=1, le=20)
    title: str | None = Field(default=None, max_length=120)
    personal_requirement: str | None = Field(default=None, max_length=500)

    @field_validator("starts_at", "ends_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("时间必须包含时区，例如 +08:00")
        return value

    @model_validator(mode="after")
    def validate_times(self) -> MatchPreviewRequest:
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at 必须晚于 starts_at")
        if self.starts_at <= datetime.now(UTC):
            raise ValueError("starts_at 必须晚于当前时间")
        return self


class CandidatePublic(ApiModel):
    candidate_type: Literal["user", "activity"]
    candidate_id: str
    rank: int
    score: float
    factors: dict[str, float]
    explanation: list[str]
    user: UserPublic | None = None
    activity: ActivityPublic | None = None


class PersonalizationReport(ApiModel):
    applied: list[str]
    ignored_for_safety: list[str]
    unresolved: list[str]


class MatchPreviewResponse(ApiModel):
    match_request_id: str
    agent_run_id: str
    agent_mode: str
    summary: str
    personalization: PersonalizationReport
    candidates: list[CandidatePublic]
    requires_confirmation: bool = True


class MatchConfirmRequest(ApiModel):
    candidate_user_ids: list[str] = Field(default_factory=list, max_length=20)
    existing_activity_id: str | None = None

    @model_validator(mode="after")
    def validate_selection(self) -> MatchConfirmRequest:
        if not self.candidate_user_ids and not self.existing_activity_id:
            raise ValueError("至少选择一位推荐用户或一个已有活动")
        if self.candidate_user_ids and self.existing_activity_id:
            raise ValueError("不能同时选择推荐用户和已有活动")
        if len(set(self.candidate_user_ids)) != len(self.candidate_user_ids):
            raise ValueError("candidate_user_ids 不能重复")
        return self


class InvitationPublic(ApiModel):
    id: str
    activity_id: str
    inviter_id: str
    invitee_id: str
    status: str
    message: str | None
    expires_at: datetime


class InvitationInboxItem(InvitationPublic):
    inviter: UserPublic
    activity: ActivityPublic


class MatchConfirmResponse(ApiModel):
    match_request_id: str
    status: str
    activity: ActivityPublic
    invitations: list[InvitationPublic]


class InvitationRespondRequest(ApiModel):
    decision: Literal["accepted", "rejected"]


class FeedbackCreate(ApiModel):
    activity_id: str
    reviewee_id: str
    attendance: Literal["attended", "cancelled_early", "late_cancel", "no_show"]
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=500)


class FeedbackResult(ApiModel):
    feedback_id: str
    reviewee_credit_score: int
    reviewee_credit_delta: int
    reviewer_credit_score: int
    reviewer_credit_delta: int


class AgentRunPublic(ApiModel):
    id: str
    match_request_id: str
    mode: str
    model: str | None
    status: str
    trace: list[dict]
    summary: str | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class ToolDefinition(ApiModel):
    name: str
    description: str
    effect: Literal["read", "write"]
    requires_confirmation: bool


class AdminAIConfigUpdate(ApiModel):
    vendor: Literal["rules", "deepseek", "openai", "custom"]
    api_key: SecretStr | None = None
    base_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=120)
    fallback_enabled: bool = True

    @field_validator("base_url", "model")
    @classmethod
    def clean_config_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_custom_provider(self) -> AdminAIConfigUpdate:
        if self.vendor == "custom" and (not self.base_url or not self.model):
            raise ValueError("其他服务需要填写接口地址和模型名称")
        return self


class AdminAIConfigPublic(ApiModel):
    selected_provider: Literal["rules", "deepseek", "openai", "custom"]
    saved_vendor: Literal["deepseek", "openai", "custom"]
    provider_label: str
    mode_label: str
    base_url: str
    model: str
    key_configured: bool
    fallback_enabled: bool
    can_test: bool
    status_message: str


class AdminAIConnectionTestResponse(ApiModel):
    ok: bool
    title: str
    message: str
    provider_label: str
    model: str | None = None
    elapsed_ms: int | None = None


class AdminOverview(ApiModel):
    registered_users: int
    match_requests: int
    completed_runs: int
    pending_invitations: int
    fallback_runs: int
    service_status: Literal["ready", "attention", "rules"]
    service_status_label: str
    provider_label: str
    model: str | None


class AdminLogStep(ApiModel):
    title: str
    detail: str | None = None


class AdminLogItem(ApiModel):
    id: str
    kind: Literal["agent", "system"]
    status: Literal["success", "attention", "running", "failed", "info"]
    status_label: str
    title: str
    message: str
    created_at: datetime
    duration_ms: int | None = None
    context: str | None = None
    steps: list[AdminLogStep] = Field(default_factory=list)
    technical_info: dict[str, str | int | bool | None] = Field(default_factory=dict)
