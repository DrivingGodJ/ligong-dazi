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

from app.campus import require_campus
from app.colleges import match_college


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


Gender = Literal["male", "female", "undisclosed"]
Campus = Literal["南京", "江阴"]


def normalize_student_id(value: str) -> str:
    value = value.strip().upper()
    if not 6 <= len(value) <= 24 or not value.isascii() or not value.isalnum():
        raise ValueError("学号应为 6–24 位英文字母或数字")
    return value


class HobbySkill(ApiModel):
    name: str = Field(min_length=1, max_length=40)
    level: int = Field(ge=1, le=5)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return value.strip()


class SkillMark(ApiModel):
    name: str
    flag_type: Literal["level_disputed", "possible_smurfing"]
    label: str
    feedback_count: int
    average_level: float


def clean_hobby_skills(value: list[HobbySkill]) -> list[HobbySkill]:
    cleaned: list[HobbySkill] = []
    seen: set[str] = set()
    for item in value:
        name = item.name.strip()
        key = name.casefold()
        if key in seen:
            raise ValueError(f"爱好或特长“{name}”重复了")
        seen.add(key)
        cleaned.append(HobbySkill(name=name, level=item.level))
    return cleaned


class RegisterRequest(ApiModel):
    student_id: str
    # Kept for older API clients; the current signup form does not ask for email.
    email: EmailStr | None = None
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)
    university: str = Field(default="南京理工大学", min_length=2, max_length=120)
    campus: Campus
    department: str | None = Field(default=None, max_length=120)
    grade_year: int | None = Field(default=None, ge=1, le=8)
    gender: Gender = "undisclosed"
    bio: str | None = Field(default=None, max_length=500)
    interests: list[str] = Field(default_factory=list, max_length=30)
    hobby_skills: list[HobbySkill] = Field(default_factory=list, max_length=20)
    preferred_locations: list[str] = Field(default_factory=list, max_length=30)
    social_style: Literal["quiet", "balanced", "outgoing"] = "balanced"
    preferred_group_min: int = Field(default=2, ge=2, le=30)
    preferred_group_max: int = Field(default=6, ge=2, le=30)

    @field_validator("interests", "preferred_locations")
    @classmethod
    def clean_registration_lists(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @field_validator("department")
    @classmethod
    def select_department(cls, value: str | None) -> str | None:
        if value is None:
            return None
        college = match_college(value)
        if college is None:
            raise ValueError("请选择列表中的学院")
        return college

    @field_validator("student_id")
    @classmethod
    def clean_student_id(cls, value: str) -> str:
        return normalize_student_id(value)

    @field_validator("campus", mode="before")
    @classmethod
    def clean_campus(cls, value: str | None) -> str | None:
        return require_campus(value)

    @field_validator("hobby_skills")
    @classmethod
    def clean_registration_hobby_skills(cls, value: list[HobbySkill]) -> list[HobbySkill]:
        return clean_hobby_skills(value)

    @model_validator(mode="after")
    def validate_registration_group_range(self) -> RegisterRequest:
        if self.preferred_group_min > self.preferred_group_max:
            raise ValueError("preferred_group_min 不能大于 preferred_group_max")
        return self


class LoginRequest(ApiModel):
    account: str | None = Field(default=None, max_length=320)
    email: EmailStr | None = None  # Keeps older clients and existing email accounts usable.
    password: str = Field(min_length=8, max_length=128)

    @field_validator("account")
    @classmethod
    def clean_account(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def require_account(self) -> LoginRequest:
        if not (self.account or self.email):
            raise ValueError("请填写学号；旧用户也可以填写原注册邮箱")
        return self


class StudentIdAppealCreate(ApiModel):
    student_id: str
    contact: str = Field(min_length=5, max_length=160)
    description: str | None = Field(default=None, max_length=500)

    @field_validator("student_id")
    @classmethod
    def clean_student_id(cls, value: str) -> str:
        return normalize_student_id(value)

    @field_validator("contact")
    @classmethod
    def clean_contact(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 5:
            raise ValueError("请填写能联系到你的微信、QQ、手机或邮箱")
        return value


class StudentIdAppealPublic(ApiModel):
    id: str
    student_id: str
    contact: str
    description: str | None
    status: str
    created_at: datetime
    resolved_at: datetime | None


class TokenResponse(ApiModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime


class UserProfileUpdate(ApiModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    student_id: str | None = None
    campus: Campus | None = None
    department: str | None = Field(default=None, max_length=120)
    grade_year: int | None = Field(default=None, ge=1, le=8)
    gender: Gender | None = None
    bio: str | None = Field(default=None, max_length=500)
    interests: list[str] | None = None
    hobby_skills: list[HobbySkill] | None = None
    preferred_locations: list[str] | None = None
    social_style: Literal["quiet", "balanced", "outgoing"] | None = None
    preferred_group_min: int | None = Field(default=None, ge=2, le=30)
    preferred_group_max: int | None = Field(default=None, ge=2, le=30)

    @field_validator("campus", mode="before")
    @classmethod
    def clean_campus(cls, value: str | None) -> str | None:
        return require_campus(value)

    @field_validator("student_id")
    @classmethod
    def clean_student_id(cls, value: str | None) -> str | None:
        return normalize_student_id(value) if value is not None else None

    @field_validator("interests", "preferred_locations")
    @classmethod
    def clean_string_list(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if len(cleaned) > 30:
            raise ValueError("列表最多包含 30 项")
        return cleaned

    @field_validator("hobby_skills")
    @classmethod
    def clean_profile_hobby_skills(cls, value: list[HobbySkill] | None) -> list[HobbySkill] | None:
        if value is None:
            return None
        if len(value) > 20:
            raise ValueError("爱好和特长最多填写 20 项")
        return clean_hobby_skills(value)

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
    gender: Gender
    bio: str | None
    interests: list[str]
    hobby_skills: list[HobbySkill]
    skill_marks: list[SkillMark]
    preferred_locations: list[str]
    social_style: str
    preferred_group_min: int
    preferred_group_max: int
    credit_score: int


class UserMe(UserPublic):
    email: EmailStr | None
    student_id: str | None
    is_active: bool
    created_at: datetime
    ai_summary: str | None
    ai_summary_generated_at: datetime | None


class ProfileSummaryResponse(ApiModel):
    summary: str
    generated_at: datetime
    next_available_at: datetime
    mode: Literal["ai", "rules"]


class UserSystemProfile(ApiModel):
    summary: str
    completed_activity_count: int
    finalized_feedback_count: int
    average_rating: float | None
    activity_signals: list[dict]
    personality_signals: list[dict]
    attendance_signals: dict[str, int]
    incident_signals: dict[str, int]
    review_integrity: dict[str, int]
    skill_marks: list[SkillMark]
    updated_at: datetime | None


class UserProfilePage(ApiModel):
    user: UserPublic
    system_profile: UserSystemProfile


class ActivityCreate(ApiModel):
    title: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=50)
    starts_at: datetime
    ends_at: datetime
    location: str = Field(min_length=1, max_length=160)
    capacity: int = Field(ge=2, le=50)
    description: str | None = Field(default=None, max_length=1000)
    personal_requirement: str | None = Field(default=None, max_length=500)
    same_gender_only: bool = False
    join_policy: Literal["open", "approval"] = "open"

    @field_validator("location")
    @classmethod
    def require_location(cls, value: str) -> str:
        location = value.strip()
        if not location:
            raise ValueError("创建活动前请填写地点")
        return location

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
    campus: str | None
    join_policy: Literal["open", "approval"]
    title: str
    category: str
    starts_at: datetime
    ends_at: datetime
    location: str
    capacity: int
    participant_count: int
    gender_counts: dict[Gender, int]
    description: str | None
    personal_requirement: str | None
    same_gender_only: bool
    status: str


class MatchPreviewRequest(ApiModel):
    category: str = Field(min_length=1, max_length=50)
    starts_at: datetime
    ends_at: datetime
    location: str = Field(default="", max_length=160)
    people_needed: int = Field(ge=1, le=9)
    title: str | None = Field(default=None, max_length=120)
    personal_requirement: str | None = Field(default=None, max_length=500)
    same_gender_only: bool = False

    @field_validator("location", mode="before")
    @classmethod
    def clean_location(cls, value: str | None) -> str:
        return value.strip() if value is not None else ""

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
    agent_model: str | None = None
    summary: str
    personalization: PersonalizationReport
    candidates: list[CandidatePublic]
    requires_confirmation: bool = True


class MatchConfirmRequest(ApiModel):
    candidate_user_ids: list[str] = Field(default_factory=list, max_length=20)
    existing_activity_id: str | None = None
    create_solo_activity: bool = False
    location: str | None = Field(default=None, max_length=160)
    join_policy: Literal["open", "approval"] = "open"

    @field_validator("location")
    @classmethod
    def clean_location(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def validate_selection(self) -> MatchConfirmRequest:
        selected_modes = sum(
            [
                bool(self.candidate_user_ids),
                bool(self.existing_activity_id),
                self.create_solo_activity,
            ]
        )
        if selected_modes != 1:
            raise ValueError("请选择邀请搭子、加入已有活动或创建单人活动中的一种")
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


class MyActivityItem(ApiModel):
    activity: ActivityPublic
    role: Literal["owner", "participant"]
    membership_status: str
    joined_at: datetime
    left_at: datetime | None
    can_leave: bool
    leave_penalty: int
    leave_policy_message: str
    needs_feedback: bool
    feedback_targets: list[UserPublic] = Field(default_factory=list)


class ActivityLeaveRequest(ApiModel):
    confirm_penalty: bool = False


class ActivityLeaveResult(ApiModel):
    activity_id: str
    membership_status: str
    credit_delta: int
    credit_score: int
    message: str
    activity_deleted: bool = False


class ActivitySquareItem(ApiModel):
    activity: ActivityPublic
    joined: bool
    joinable: bool
    join_reason: str | None = None
    application_status: str | None = None
    recommendation_score: float
    recommendation_reasons: list[str] = Field(default_factory=list)


class ActivitySquarePage(ApiModel):
    items: list[ActivitySquareItem]
    categories: list[str] = Field(default_factory=list)
    offset: int
    next_offset: int | None
    has_more: bool


class ActivityJoinResult(ApiModel):
    activity: ActivityPublic
    message: str


class JoinApplicationPublic(ApiModel):
    id: str
    activity_id: str
    applicant: UserPublic
    status: str
    approvals: int
    required_approvals: int
    my_decision: str | None
    created_at: datetime


class JoinApplicationDecision(ApiModel):
    decision: Literal["approved", "rejected"]


class PushSubscriptionRequest(ApiModel):
    endpoint: str = Field(max_length=2048)
    p256dh: str = Field(min_length=20, max_length=512)
    auth: str = Field(min_length=8, max_length=512)


class NotificationPublic(ApiModel):
    id: str
    kind: str
    title: str
    body: str
    url: str
    created_at: datetime
    read_at: datetime | None


class ActivityPhotoUpload(ApiModel):
    data_url: str = Field(min_length=32, max_length=8_000_000)


class ActivityPhotoPublic(ApiModel):
    id: str
    activity_id: str
    uploader: UserPublic
    media_type: str
    uploaded_at: datetime
    content_url: str


class ActivityTimeVoteCreate(ApiModel):
    starts_at: datetime
    ends_at: datetime

    @field_validator("starts_at", "ends_at")
    @classmethod
    def require_vote_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("时间必须包含时区，例如 +08:00")
        return value

    @model_validator(mode="after")
    def validate_vote_times(self) -> ActivityTimeVoteCreate:
        if self.ends_at <= self.starts_at:
            raise ValueError("结束时间必须晚于开始时间")
        if self.starts_at <= datetime.now(UTC):
            raise ValueError("新的开始时间必须晚于当前时间")
        return self


class ActivityTimeVoteRespond(ApiModel):
    decision: Literal["approved", "rejected"]


class ActivityTimeVotePublic(ApiModel):
    id: str
    activity_id: str
    proposer: UserPublic
    proposed_starts_at: datetime
    proposed_ends_at: datetime
    status: str
    approvals: int
    required_approvals: int
    my_decision: str | None
    created_at: datetime
    resolved_at: datetime | None


PersonalityTag = Literal[
    "quiet",
    "balanced",
    "outgoing",
    "patient",
    "talkative",
    "focused",
    "easygoing",
    "organized",
]

IncidentTag = Literal[
    "punctual",
    "helpful",
    "clear_communication",
    "late",
    "no_show",
    "unsafe_behavior",
]


class FeedbackCreate(ApiModel):
    activity_id: str
    reviewee_id: str
    attendance: Literal["attended", "no_show"]
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=500)
    skill_name: str | None = Field(default=None, max_length=50)
    skill_level: int | None = Field(default=None, ge=1, le=5)
    personality_tags: list[PersonalityTag] = Field(default_factory=list, max_length=4)
    incident_tags: list[IncidentTag] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def validate_feedback_evidence(self) -> FeedbackCreate:
        if self.skill_name is not None:
            self.skill_name = self.skill_name.strip() or None
        if (self.skill_name is None) != (self.skill_level is None):
            raise ValueError("评价爱好水平时，请同时填写项目名称和水平")
        if self.attendance == "no_show" and "no_show" not in self.incident_tags:
            self.incident_tags.append("no_show")
        if "no_show" in self.incident_tags and self.attendance != "no_show":
            raise ValueError("记录“没有出现”时，到场情况也应选择“没有出现”")
        if {"punctual", "late"} & set(self.incident_tags) and self.attendance == "no_show":
            raise ValueError("“准时到场”或“有迟到”不能和“没有出现”同时选择")
        return self


class FeedbackResult(ApiModel):
    feedback_id: str
    reviewee_credit_score: int
    reviewee_credit_delta: int
    reviewer_credit_score: int
    reviewer_credit_delta: int
    moderation_status: str
    requires_peer_review: bool
    ai_summary: str


class PeerReviewTask(ApiModel):
    feedback_id: str
    activity: ActivityPublic
    author: UserPublic
    subject: UserPublic
    attendance: str
    rating: int
    comment: str | None
    skill_name: str | None
    skill_level: int | None
    personality_tags: list[str]
    incident_tags: list[str]
    ai_summary: str


class PeerReviewCreate(ApiModel):
    verdict: Literal["mostly_true", "partly_true", "not_sure", "mostly_false"]
    comment: str | None = Field(default=None, max_length=500)
    true_parts: str | None = Field(default=None, max_length=500)
    false_parts: str | None = Field(default=None, max_length=500)


class PeerReviewResult(ApiModel):
    feedback_id: str
    moderation_status: str
    reviewee_credit_delta: int
    original_reviewer_credit_delta: int
    peer_reviewer_credit_delta: int
    ai_summary: str


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
