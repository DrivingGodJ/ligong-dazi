from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.activity_media import (
    build_activity_ics,
    cleanup_expired_activity_photos,
    decode_image_data_url,
    remove_photo_file,
)
from app.agent import TOOL_CATALOG, AgentOutputError, run_matching_agent
from app.core import (
    DatabaseRuntime,
    Settings,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.matching import MatchContext, location_similarity
from app.models import (
    Activity,
    ActivityMember,
    ActivityPhoto,
    ActivityTimeVote,
    ActivityTimeVoteResponse,
    AgentRun,
    CreditEvent,
    Feedback,
    Invitation,
    JoinApplication,
    JoinApproval,
    MatchCandidate,
    MatchRequest,
    Notification,
    PeerFeedbackReview,
    PushSubscription,
    Reminder,
    StudentIdAppeal,
    SystemEvent,
    User,
    UserBlock,
    new_id,
    utcnow,
)
from app.notifications import enqueue_notification
from app.post_activity import (
    apply_moderation_decision,
    calculate_leave_penalty,
    eligible_peer_reviewer_ids,
    finalize_feedback_with_peer_review,
    moderate_feedback,
    process_completed_activities,
    refresh_hidden_profile,
)
from app.profile_summary import generate_profile_summary
from app.schemas import (
    ActivityCreate,
    ActivityJoinResult,
    ActivityLeaveRequest,
    ActivityLeaveResult,
    ActivityPhotoPublic,
    ActivityPhotoUpload,
    ActivityPublic,
    ActivitySquareItem,
    ActivitySquarePage,
    ActivityTimeVoteCreate,
    ActivityTimeVotePublic,
    ActivityTimeVoteRespond,
    AgentRunPublic,
    CandidatePublic,
    FeedbackCreate,
    FeedbackResult,
    InvitationInboxItem,
    InvitationPublic,
    InvitationRespondRequest,
    JoinApplicationDecision,
    JoinApplicationPublic,
    LoginRequest,
    MatchConfirmRequest,
    MatchConfirmResponse,
    MatchPreviewRequest,
    MatchPreviewResponse,
    MyActivityItem,
    NotificationPublic,
    PeerReviewCreate,
    PeerReviewResult,
    PeerReviewTask,
    PersonalizationReport,
    ProfileSummaryResponse,
    PushSubscriptionRequest,
    RegisterRequest,
    StudentIdAppealCreate,
    TokenResponse,
    ToolDefinition,
    UserMe,
    UserProfilePage,
    UserProfileUpdate,
    UserPublic,
    UserSystemProfile,
)

router = APIRouter(prefix="/api/v1")
auth_scheme = HTTPBearer(auto_error=False)
MATCH_COOLDOWN_SECONDS = 60
PROFILE_SUMMARY_COOLDOWN_SECONDS = 5 * 60
PHOTO_UPLOAD_WINDOW = timedelta(minutes=15)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    runtime: DatabaseRuntime = request.app.state.database
    async with runtime.session_factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(auth_scheme)],
    session: SessionDep,
    settings: SettingsDep,
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要登录")
    try:
        user_id = decode_access_token(credentials.credentials, settings)
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录凭证无效或已过期",
        ) from exc
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已停用")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def ensure_campus(user: User) -> str:
    if user.campus not in {"南京", "江阴"}:
        raise HTTPException(status_code=403, detail="请先在我的画像中选择南京或江阴校区")
    return user.campus


def normalized_gender(value: str) -> str:
    return value if value in {"male", "female", "undisclosed"} else "undisclosed"


def user_public(user: User) -> UserPublic:
    values = {field: getattr(user, field) for field in UserPublic.model_fields}
    values["gender"] = normalized_gender(user.gender)
    return UserPublic.model_validate(values)


def user_me(user: User) -> UserMe:
    values = {field: getattr(user, field) for field in UserMe.model_fields}
    values["gender"] = normalized_gender(user.gender)
    if user.email.endswith("@accounts.invalid"):
        values["email"] = None
    return UserMe.model_validate(values)


async def activity_public(session: AsyncSession, activity: Activity) -> ActivityPublic:
    gender_rows = (
        await session.execute(
            select(User.gender, func.count(ActivityMember.id))
            .select_from(ActivityMember)
            .join(User, User.id == ActivityMember.user_id)
            .where(
                ActivityMember.activity_id == activity.id,
                ActivityMember.status == "confirmed",
            )
            .group_by(User.gender)
        )
    ).all()
    gender_counts = {"male": 0, "female": 0, "undisclosed": 0}
    for gender, count in gender_rows:
        key = normalized_gender(gender)
        gender_counts[key] += int(count)
    count = sum(gender_counts.values())
    return ActivityPublic(
        id=activity.id,
        owner_id=activity.owner_id,
        campus=activity.campus,
        join_policy=activity.join_policy,
        title=activity.title,
        category=activity.category,
        starts_at=activity.starts_at,
        ends_at=activity.ends_at,
        location=activity.location,
        capacity=activity.capacity,
        participant_count=count,
        gender_counts=gender_counts,
        description=activity.description,
        personal_requirement=activity.personal_requirement,
        same_gender_only=activity.same_gender_only,
        status=activity.status,
    )


async def activity_join_block_reason(
    session: AsyncSession, activity: Activity, user: User
) -> str | None:
    if activity.campus != ensure_campus(user):
        return "只能加入本校区的活动"
    if not activity.same_gender_only or user.id == activity.owner_id:
        return None
    owner_gender = await session.scalar(select(User.gender).where(User.id == activity.owner_id))
    if user.gender not in {"male", "female"} or user.gender != owner_gender:
        return "这场活动仅限与发起人同性的搭子加入"
    return None


def system_profile_public(user: User) -> UserSystemProfile:
    learned = user.hidden_profile or {}
    review_integrity = learned.get("review_integrity") or {}
    return UserSystemProfile(
        summary=str(learned.get("summary") or "活动记录还不多，系统暂时没有形成稳定印象。"),
        completed_activity_count=int(learned.get("completed_activity_count") or 0),
        finalized_feedback_count=int(learned.get("finalized_feedback_count") or 0),
        average_rating=learned.get("average_rating"),
        activity_signals=learned.get("activity_signals") or [],
        personality_signals=learned.get("personality_signals") or [],
        attendance_signals=learned.get("attendance_signals") or {},
        incident_signals=learned.get("incident_signals") or {},
        review_integrity={
            "supported_feedback_count": int(review_integrity.get("supported_feedback_count") or 0),
            "unsupported_serious_feedback_count": int(
                review_integrity.get("unsupported_serious_feedback_count") or 0
            ),
        },
        skill_marks=user.skill_marks or [],
        updated_at=user.hidden_profile_updated_at,
    )


async def confirmed_membership(
    session: AsyncSession,
    activity_id: str,
    user_id: str,
) -> ActivityMember | None:
    return await session.scalar(
        select(ActivityMember).where(
            ActivityMember.activity_id == activity_id,
            ActivityMember.user_id == user_id,
            ActivityMember.status == "confirmed",
        )
    )


async def activity_member_ids(session: AsyncSession, activity_id: str) -> set[str]:
    return set(
        (
            await session.scalars(
                select(ActivityMember.user_id).where(
                    ActivityMember.activity_id == activity_id, ActivityMember.status == "confirmed"
                )
            )
        ).all()
    )


async def apply_to_activity(
    session: AsyncSession, activity: Activity, user: User
) -> JoinApplication:
    application = await session.scalar(
        select(JoinApplication).where(
            JoinApplication.activity_id == activity.id, JoinApplication.applicant_id == user.id
        )
    )
    if application and application.status == "pending":
        return application
    if application and application.status == "approved":
        raise HTTPException(status_code=409, detail="你已经加入这场活动")
    if application is None:
        application = JoinApplication(activity_id=activity.id, applicant_id=user.id)
        session.add(application)
    else:
        await session.execute(
            delete(JoinApproval).where(JoinApproval.application_id == application.id)
        )
        application.status = "pending"
        application.resolved_at = None
        application.created_at = utcnow()
    await session.flush()
    for member_id in await activity_member_ids(session, activity.id):
        await enqueue_notification(
            session,
            member_id,
            f"join_application:{application.id}:{application.created_at.isoformat()}",
            "join_application",
            "有人想加入你们的活动",
            f"{user.display_name}申请加入“{activity.title}”，查看档案后请表态。",
            "/?tab=activities",
        )
    return application


async def close_pending_applications(session: AsyncSession, activity: Activity) -> None:
    pending = list(
        (
            await session.scalars(
                select(JoinApplication).where(
                    JoinApplication.activity_id == activity.id, JoinApplication.status == "pending"
                )
            )
        ).all()
    )
    for application in pending:
        if application.status != "pending":
            continue
        application.status = "closed"
        application.resolved_at = utcnow()
        await enqueue_notification(
            session,
            application.applicant_id,
            f"join_closed:{application.id}:{application.created_at.isoformat()}",
            "join_closed",
            "活动已经满员",
            f"“{activity.title}”已满员，去广场看看别的活动吧。",
            "/?tab=square",
        )


async def join_application_public(
    session: AsyncSession, application: JoinApplication, current_user_id: str
) -> JoinApplicationPublic:
    applicant = await session.get(User, application.applicant_id)
    assert applicant is not None
    member_ids = await activity_member_ids(session, application.activity_id)
    member_ids.discard(application.applicant_id)
    responses = list(
        (
            await session.scalars(
                select(JoinApproval).where(JoinApproval.application_id == application.id)
            )
        ).all()
    )
    return JoinApplicationPublic(
        id=application.id,
        activity_id=application.activity_id,
        applicant=user_public(applicant),
        status=application.status,
        approvals=sum(r.decision == "approved" and r.member_id in member_ids for r in responses),
        required_approvals=len(member_ids),
        my_decision=next((r.decision for r in responses if r.member_id == current_user_id), None),
        created_at=application.created_at,
    )


def activity_recommendation(user: User, activity: Activity) -> tuple[float, list[str]]:
    score = 20.0
    reasons: list[str] = []
    if activity.category in user.interests:
        score += 35
        reasons.append(f"符合你对{activity.category}的兴趣")
    location_score = max(
        (location_similarity(activity.location, item) for item in user.preferred_locations),
        default=0.0,
    )
    if location_score >= 0.75:
        score += 30
        reasons.append("地点与你常去的位置很接近")
    elif location_score >= 0.4:
        score += 18
        reasons.append("地点大致符合你的活动范围")
    elif user.campus and location_similarity(activity.location, user.campus) >= 0.4:
        score += 10
        reasons.append("活动地点可能在你的常驻校区附近")
    if user.preferred_group_min <= activity.capacity <= user.preferred_group_max:
        score += 10
        reasons.append("活动人数规模符合你的偏好")
    if activity.starts_at <= utcnow() + timedelta(days=3):
        score += 5
        reasons.append("活动就在最近几天")
    if not reasons:
        reasons.append("这是一场仍有名额的新活动")
    return round(min(score, 100.0), 1), reasons[:3]


async def time_vote_public(
    session: AsyncSession,
    vote: ActivityTimeVote,
    current_user_id: str,
) -> ActivityTimeVotePublic:
    proposer = await session.get(User, vote.proposer_id)
    if proposer is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="投票发起人不存在")
    responses = list(
        (
            await session.scalars(
                select(ActivityTimeVoteResponse).where(ActivityTimeVoteResponse.vote_id == vote.id)
            )
        ).all()
    )
    confirmed_count = int(
        await session.scalar(
            select(func.count(ActivityMember.id)).where(
                ActivityMember.activity_id == vote.activity_id,
                ActivityMember.status == "confirmed",
            )
        )
        or 0
    )
    my_response = next(
        (item.decision for item in responses if item.user_id == current_user_id), None
    )
    return ActivityTimeVotePublic(
        id=vote.id,
        activity_id=vote.activity_id,
        proposer=user_public(proposer),
        proposed_starts_at=vote.proposed_starts_at,
        proposed_ends_at=vote.proposed_ends_at,
        status=vote.status,
        approvals=sum(item.decision == "approved" for item in responses),
        required_approvals=max(0, confirmed_count - 1),
        my_decision=my_response,
        created_at=vote.created_at,
        resolved_at=vote.resolved_at,
    )


async def reschedule_activity_reminders(session: AsyncSession, activity: Activity) -> None:
    await session.execute(delete(Reminder).where(Reminder.activity_id == activity.id))
    reminder_at = activity.starts_at - timedelta(hours=1)
    if reminder_at <= utcnow():
        return
    member_ids = list(
        (
            await session.scalars(
                select(ActivityMember.user_id).where(
                    ActivityMember.activity_id == activity.id,
                    ActivityMember.status == "confirmed",
                )
            )
        ).all()
    )
    session.add_all(
        [
            Reminder(activity_id=activity.id, user_id=user_id, remind_at=reminder_at)
            for user_id in member_ids
        ]
    )


@router.post("/auth/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    if payload.campus not in {"南京", "江阴"}:
        raise HTTPException(status_code=422, detail="必须选择南京或江阴校区")
    if await session.scalar(select(User.id).where(User.student_id == payload.student_id)):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "student_id_taken",
                "message": "这个学号已被使用，可以留下联系方式申诉",
            },
        )
    # The existing SQLite email column is NOT NULL. Keep it internally for old accounts,
    # and use a non-deliverable value for new student-ID-only accounts until a full migration.
    email = str(payload.email).lower() if payload.email else f"no-email-{new_id()}@accounts.invalid"
    if payload.email and await session.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status_code=409, detail="该邮箱已注册；旧账号请直接登录")
    user = User(
        email=email,
        student_id=payload.student_id,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name.strip(),
        university=payload.university.strip(),
        campus=payload.campus,
        department=payload.department.strip() if payload.department else None,
        grade_year=payload.grade_year,
        gender=payload.gender,
        bio=payload.bio.strip() if payload.bio else None,
        interests=payload.interests,
        hobby_skills=[item.model_dump() for item in payload.hobby_skills],
        preferred_locations=payload.preferred_locations,
        social_style=payload.social_style,
        preferred_group_min=payload.preferred_group_min,
        preferred_group_max=payload.preferred_group_max,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "student_id_taken",
                "message": "这个学号已被使用，可以留下联系方式申诉",
            },
        ) from exc
    token, expires_at = create_access_token(user.id, settings)
    return TokenResponse(access_token=token, expires_at=expires_at)


@router.post("/auth/student-id-appeals", status_code=201)
async def create_student_id_appeal(
    payload: StudentIdAppealCreate,
    request: Request,
    session: SessionDep,
) -> dict[str, str]:
    if not await session.scalar(select(User.id).where(User.student_id == payload.student_id)):
        raise HTTPException(status_code=409, detail="这个学号目前没有被使用，请返回注册页面重试")
    client_host = request.client.host if request.client else "unknown"
    attempts: dict[str, list[float]] = request.app.state.appeal_attempts
    now = time.monotonic()
    recent = [stamp for stamp in attempts.get(client_host, []) if now - stamp < 3600]
    if len(recent) >= 30:
        raise HTTPException(status_code=429, detail="申诉提交较频繁，请稍后再试")
    recent.append(now)
    attempts[client_host] = recent
    existing = await session.scalar(
        select(StudentIdAppeal.id).where(
            StudentIdAppeal.student_id == payload.student_id,
            StudentIdAppeal.contact == payload.contact,
            StudentIdAppeal.status == "pending",
        )
    )
    if existing is None:
        session.add(
            StudentIdAppeal(
                student_id=payload.student_id,
                contact=payload.contact,
                description=payload.description.strip() if payload.description else None,
            )
        )
        await session.commit()
    return {"message": "申诉已收到，管理员核查后会通过你留下的方式联系。请勿重复提交。"}


@router.post("/auth/token", response_model=TokenResponse)
async def login(payload: LoginRequest, session: SessionDep, settings: SettingsDep) -> TokenResponse:
    account = (payload.account or str(payload.email)).strip()
    user = await session.scalar(
        select(User).where(
            User.email == account.lower() if "@" in account else User.student_id == account.upper()
        )
    )
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="学号、旧邮箱或密码错误"
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="用户已停用")
    token, expires_at = create_access_token(user.id, settings)
    return TokenResponse(access_token=token, expires_at=expires_at)


@router.get("/users/me", response_model=UserMe)
async def read_me(current_user: CurrentUser) -> UserMe:
    return user_me(current_user)


@router.patch("/users/me", response_model=UserMe)
async def update_me(
    payload: UserProfileUpdate,
    current_user: CurrentUser,
    session: SessionDep,
) -> UserMe:
    values = payload.model_dump(exclude_unset=True)
    if "student_id" in values:
        student_id = values["student_id"]
        if student_id is None:
            raise HTTPException(status_code=422, detail="学号不能为空")
        if current_user.student_id and current_user.student_id != student_id:
            raise HTTPException(status_code=409, detail="学号已绑定；如需更正请联系管理员")
        occupied = await session.scalar(
            select(User.id).where(User.student_id == student_id, User.id != current_user.id)
        )
        if occupied:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "student_id_taken",
                    "message": "这个学号已被使用，可以留下联系方式申诉",
                },
            )
    if "campus" in values and values["campus"] not in {"南京", "江阴"}:
        raise HTTPException(status_code=422, detail="必须选择南京或江阴校区")
    min_group = values.get("preferred_group_min", current_user.preferred_group_min)
    max_group = values.get("preferred_group_max", current_user.preferred_group_max)
    if min_group > max_group:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="preferred_group_min 不能大于 preferred_group_max",
        )
    for field_name, value in values.items():
        if field_name == "hobby_skills":
            value = [item.model_dump() if hasattr(item, "model_dump") else item for item in value]
        setattr(current_user, field_name, value)
    if values.get("campus") in {"南京", "江阴"}:
        await session.execute(
            update(Activity)
            .where(Activity.owner_id == current_user.id, Activity.campus.is_(None))
            .values(campus=values["campus"])
        )
    if "hobby_skills" in values:
        await session.flush()
        await refresh_hidden_profile(session, current_user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "student_id_taken",
                "message": "这个学号已被使用，可以留下联系方式申诉",
            },
        ) from exc
    await session.refresh(current_user)
    return user_me(current_user)


@router.get("/users/{user_id}/profile", response_model=UserProfilePage)
async def read_user_profile(
    user_id: str,
    current_user: CurrentUser,
    session: SessionDep,
) -> UserProfilePage:
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    blocked = await session.scalar(
        select(UserBlock.id).where(
            or_(
                (UserBlock.blocker_id == current_user.id) & (UserBlock.blocked_id == user.id),
                (UserBlock.blocker_id == user.id) & (UserBlock.blocked_id == current_user.id),
            )
        )
    )
    if blocked:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    return UserProfilePage(
        user=user_public(user),
        system_profile=system_profile_public(user),
    )


@router.post("/users/me/ai-summary", response_model=ProfileSummaryResponse)
async def summarize_me(
    current_user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> ProfileSummaryResponse:
    now = utcnow()
    if current_user.ai_summary_generated_at is not None:
        next_available = current_user.ai_summary_generated_at + timedelta(
            seconds=PROFILE_SUMMARY_COOLDOWN_SECONDS
        )
        if now < next_available:
            retry_after = max(1, int((next_available - now).total_seconds()))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"刚刚已经总结过啦，请 {retry_after} 秒后再试。",
                headers={"Retry-After": str(retry_after)},
            )
    summary, mode = await generate_profile_summary(current_user, settings)
    generated_at = utcnow()
    current_user.ai_summary = summary
    current_user.ai_summary_generated_at = generated_at
    session.add(
        SystemEvent(
            category="profile_summary",
            status="success",
            title="用户生成了个人综合形象总结",
            message="已根据现有资料和活动记录生成一份可供本人查看的总结。",
            details={"user_id": current_user.id, "mode": mode},
        )
    )
    await session.commit()
    return ProfileSummaryResponse(
        summary=summary,
        generated_at=generated_at,
        next_available_at=generated_at + timedelta(seconds=PROFILE_SUMMARY_COOLDOWN_SECONDS),
        mode=mode,
    )


@router.post("/activities", response_model=ActivityPublic, status_code=status.HTTP_201_CREATED)
async def create_activity(
    payload: ActivityCreate,
    current_user: CurrentUser,
    session: SessionDep,
) -> ActivityPublic:
    campus = ensure_campus(current_user)
    if payload.same_gender_only and current_user.gender not in {"male", "female"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="请先在我的画像中选择男或女，才能创建仅限同性的活动",
        )
    activity = Activity(owner_id=current_user.id, campus=campus, **payload.model_dump())
    session.add(activity)
    await session.flush()
    session.add(
        ActivityMember(
            activity_id=activity.id,
            user_id=current_user.id,
            role="owner",
            status="confirmed",
        )
    )
    await session.commit()
    return await activity_public(session, activity)


@router.get("/activities", response_model=list[ActivityPublic])
async def list_activities(
    session: SessionDep,
    current_user: CurrentUser,
    category: str | None = Query(default=None, max_length=50),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[ActivityPublic]:
    statement = (
        select(Activity)
        .where(Activity.campus == ensure_campus(current_user))
        .order_by(Activity.starts_at.asc())
        .limit(limit)
    )
    if category:
        statement = statement.where(Activity.category == category)
    activities = list((await session.scalars(statement)).all())
    return [await activity_public(session, activity) for activity in activities]


@router.get("/activities/square", response_model=ActivitySquarePage)
async def activity_square(
    current_user: CurrentUser,
    session: SessionDep,
    category: str | None = Query(default=None, max_length=50),
    search: str | None = Query(default=None, max_length=160),
    activity_date: Annotated[date | None, Query(alias="date")] = None,
    location: str | None = Query(default=None, max_length=160),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=12, ge=1, le=30),
) -> ActivitySquarePage:
    campus = ensure_campus(current_user)
    activities = list(
        (
            await session.scalars(
                select(Activity)
                .where(
                    Activity.status.in_(["open", "formed"]),
                    Activity.starts_at > utcnow(),
                    Activity.campus == campus,
                )
                .order_by(Activity.starts_at.asc())
            )
        ).all()
    )
    categories = sorted({activity.category for activity in activities})
    search_term = (search or location or "").strip().casefold()
    items: list[ActivitySquareItem] = []
    for activity in activities:
        if category and activity.category != category:
            continue
        local_activity_date = activity.starts_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
        if activity_date and local_activity_date != activity_date:
            continue
        if search_term and not (
            search_term in activity.title.casefold()
            or search_term in activity.location.casefold()
            or location_similarity(search_term, activity.location) >= 0.3
        ):
            continue
        public_activity = await activity_public(session, activity)
        membership = await session.scalar(
            select(ActivityMember.status).where(
                ActivityMember.activity_id == activity.id,
                ActivityMember.user_id == current_user.id,
            )
        )
        application_status = await session.scalar(
            select(JoinApplication.status).where(
                JoinApplication.activity_id == activity.id,
                JoinApplication.applicant_id == current_user.id,
            )
        )
        score, reasons = activity_recommendation(current_user, activity)
        join_block_reason = await activity_join_block_reason(session, activity, current_user)
        is_full = public_activity.participant_count >= activity.capacity
        items.append(
            ActivitySquareItem(
                activity=public_activity,
                joined=membership == "confirmed",
                joinable=(activity.status == "open" and not is_full and join_block_reason is None),
                join_reason=join_block_reason or ("已经满员" if is_full else None),
                application_status=application_status,
                recommendation_score=score,
                recommendation_reasons=reasons,
            )
        )
    items.sort(
        key=lambda item: (
            not item.joinable and not item.joined,
            -item.recommendation_score,
            item.activity.starts_at,
        )
    )
    page_items = items[offset : offset + limit]
    next_offset = offset + len(page_items)
    has_more = next_offset < len(items)
    return ActivitySquarePage(
        items=page_items,
        categories=categories,
        offset=offset,
        next_offset=next_offset if has_more else None,
        has_more=has_more,
    )


@router.get(
    "/activities/{activity_id}/participants",
    response_model=list[UserPublic],
)
async def list_activity_participants(
    activity_id: str,
    current_user: CurrentUser,
    session: SessionDep,
) -> list[UserPublic]:
    activity = await session.get(Activity, activity_id)
    if activity is None or (
        activity.campus != ensure_campus(current_user)
        and await confirmed_membership(session, activity_id, current_user.id) is None
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="活动不存在")
    if activity.status not in {"open", "formed"}:
        membership = await confirmed_membership(session, activity_id, current_user.id)
        if membership is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="活动不存在")
    users = list(
        (
            await session.scalars(
                select(User)
                .join(ActivityMember, ActivityMember.user_id == User.id)
                .where(
                    ActivityMember.activity_id == activity_id,
                    ActivityMember.status == "confirmed",
                    User.is_active.is_(True),
                )
                .order_by(ActivityMember.role.desc(), ActivityMember.joined_at.asc())
            )
        ).all()
    )
    return [user_public(user) for user in users]


@router.post("/activities/{activity_id}/join", response_model=ActivityJoinResult)
async def join_activity_from_square(
    activity_id: str,
    current_user: CurrentUser,
    session: SessionDep,
) -> ActivityJoinResult:
    ensure_campus(current_user)
    activity = await session.scalar(
        select(Activity).where(Activity.id == activity_id).with_for_update()
    )
    if activity is None or activity.status != "open" or activity.starts_at <= utcnow():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="这场活动已经不能加入了")
    membership = await session.scalar(
        select(ActivityMember).where(
            ActivityMember.activity_id == activity.id,
            ActivityMember.user_id == current_user.id,
        )
    )
    if membership is not None and membership.status == "confirmed":
        return ActivityJoinResult(
            activity=await activity_public(session, activity),
            message="你已经在这场活动里啦。",
        )
    join_block_reason = await activity_join_block_reason(session, activity, current_user)
    if join_block_reason:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=join_block_reason)
    member_count = int(
        await session.scalar(
            select(func.count(ActivityMember.id)).where(
                ActivityMember.activity_id == activity.id,
                ActivityMember.status == "confirmed",
            )
        )
        or 0
    )
    if member_count >= activity.capacity:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="刚刚满员了，看看其他活动吧"
        )
    if activity.join_policy == "approval":
        await apply_to_activity(session, activity, current_user)
        await session.commit()
        return ActivityJoinResult(
            activity=await activity_public(session, activity),
            message="申请已送出，等现有搭子都同意后才会加入。",
        )
    if membership is None:
        membership = ActivityMember(
            activity_id=activity.id,
            user_id=current_user.id,
            role="participant",
            status="confirmed",
            joined_at=utcnow(),
        )
        session.add(membership)
    else:
        membership.status = "confirmed"
        membership.joined_at = utcnow()
        membership.left_at = None
        membership.leave_penalty = 0
    reminder_at = activity.starts_at - timedelta(hours=1)
    if reminder_at > utcnow():
        existing_reminder = await session.scalar(
            select(Reminder.id).where(
                Reminder.activity_id == activity.id,
                Reminder.user_id == current_user.id,
                Reminder.remind_at == reminder_at,
            )
        )
        if existing_reminder is None:
            session.add(
                Reminder(
                    activity_id=activity.id,
                    user_id=current_user.id,
                    remind_at=reminder_at,
                )
            )
    if member_count + 1 >= activity.capacity:
        activity.status = "formed"
        await close_pending_applications(session, activity)
    for member_id in await activity_member_ids(session, activity.id):
        if member_id != current_user.id:
            await enqueue_notification(
                session,
                member_id,
                f"public_join:{activity.id}:{current_user.id}:{membership.joined_at.isoformat()}",
                "public_join",
                "活动迎来新搭子",
                f"{current_user.display_name}加入了“{activity.title}”。",
                "/?tab=activities",
            )
    await session.commit()
    return ActivityJoinResult(
        activity=await activity_public(session, activity),
        message="加入成功，已经放进“我的活动”。",
    )


@router.get("/activities/{activity_id}/applications", response_model=list[JoinApplicationPublic])
async def list_join_applications(
    activity_id: str,
    current_user: CurrentUser,
    session: SessionDep,
) -> list[JoinApplicationPublic]:
    activity = await session.get(Activity, activity_id)
    if (
        activity is None
        or await confirmed_membership(session, activity_id, current_user.id) is None
    ):
        raise HTTPException(status_code=404, detail="只有活动成员能查看加入申请")
    applications = list(
        (
            await session.scalars(
                select(JoinApplication)
                .where(
                    JoinApplication.activity_id == activity_id, JoinApplication.status == "pending"
                )
                .order_by(JoinApplication.created_at)
            )
        ).all()
    )
    return [await join_application_public(session, item, current_user.id) for item in applications]


@router.post(
    "/activities/{activity_id}/applications/{application_id}/respond",
    response_model=JoinApplicationPublic,
)
async def respond_join_application(
    activity_id: str,
    application_id: str,
    payload: JoinApplicationDecision,
    current_user: CurrentUser,
    session: SessionDep,
) -> JoinApplicationPublic:
    activity = await session.scalar(
        select(Activity).where(Activity.id == activity_id).with_for_update()
    )
    application = await session.scalar(
        select(JoinApplication)
        .where(JoinApplication.id == application_id, JoinApplication.activity_id == activity_id)
        .with_for_update()
    )
    if (
        activity is None
        or application is None
        or await confirmed_membership(session, activity_id, current_user.id) is None
    ):
        raise HTTPException(status_code=404, detail="申请不存在或你不是活动成员")
    if application.status != "pending":
        return await join_application_public(session, application, current_user.id)
    if activity.status != "open" or activity.starts_at <= utcnow():
        raise HTTPException(status_code=409, detail="活动已无法接收新成员")
    applicant = await session.get(User, application.applicant_id)
    if applicant is None or await activity_join_block_reason(session, activity, applicant):
        raise HTTPException(status_code=409, detail="申请人已不符合加入条件")
    response = await session.scalar(
        select(JoinApproval).where(
            JoinApproval.application_id == application.id, JoinApproval.member_id == current_user.id
        )
    )
    if response is None:
        response = JoinApproval(
            application_id=application.id, member_id=current_user.id, decision=payload.decision
        )
        session.add(response)
    else:
        response.decision = payload.decision
    await session.flush()
    if payload.decision == "rejected":
        application.status = "rejected"
        application.resolved_at = utcnow()
    else:
        member_ids = await activity_member_ids(session, activity_id)
        approved_ids = set(
            (
                await session.scalars(
                    select(JoinApproval.member_id).where(
                        JoinApproval.application_id == application.id,
                        JoinApproval.decision == "approved",
                    )
                )
            ).all()
        )
        if member_ids.issubset(approved_ids):
            count = len(member_ids)
            if count >= activity.capacity:
                raise HTTPException(status_code=409, detail="活动刚刚满员，请刷新后再试")
            membership = await session.scalar(
                select(ActivityMember).where(
                    ActivityMember.activity_id == activity_id,
                    ActivityMember.user_id == applicant.id,
                )
            )
            if membership is None:
                session.add(ActivityMember(activity_id=activity_id, user_id=applicant.id))
            else:
                membership.status = "confirmed"
                membership.left_at = None
                membership.joined_at = utcnow()
            application.status = "approved"
            application.resolved_at = utcnow()
            if count + 1 >= activity.capacity:
                activity.status = "formed"
                await close_pending_applications(session, activity)
            reminder_at = activity.starts_at - timedelta(hours=1)
            if reminder_at > utcnow():
                session.add(
                    Reminder(activity_id=activity.id, user_id=applicant.id, remind_at=reminder_at)
                )
            await enqueue_notification(
                session,
                applicant.id,
                f"join_approved:{application.id}",
                "join_approved",
                "申请通过啦",
                f"你已经加入“{activity.title}”！",
                "/?tab=activities",
            )
    if application.status == "rejected":
        await enqueue_notification(
            session,
            applicant.id,
            f"join_rejected:{application.id}:{application.created_at.isoformat()}",
            "join_rejected",
            "加入申请未通过",
            f"“{activity.title}”的申请没有通过，可以再看看其他活动。",
            "/?tab=square",
        )
    await session.commit()
    return await join_application_public(session, application, current_user.id)


@router.get("/activities/mine", response_model=list[MyActivityItem])
async def list_my_activities(
    current_user: CurrentUser,
    session: SessionDep,
) -> list[MyActivityItem]:
    completed_count = await process_completed_activities(session)
    if completed_count:
        await session.commit()
    memberships = list(
        (
            await session.scalars(
                select(ActivityMember)
                .where(ActivityMember.user_id == current_user.id)
                .order_by(ActivityMember.joined_at.desc())
            )
        ).all()
    )
    result: list[MyActivityItem] = []
    for membership in memberships:
        activity = await session.get(Activity, membership.activity_id)
        if activity is None:
            continue
        public_activity = await activity_public(session, activity)
        penalty, policy_message = calculate_leave_penalty(activity)
        can_leave = membership.status == "confirmed" and utcnow() < activity.starts_at
        if membership.role == "owner" and public_activity.participant_count > 1:
            can_leave = False
            policy_message = "你是发起人且已有搭子，暂不能直接退出；请先和成员协商。"

        feedback_targets: list[UserPublic] = []
        if activity.ends_at <= utcnow() and membership.status == "confirmed":
            already_reviewed = set(
                (
                    await session.scalars(
                        select(Feedback.reviewee_id).where(
                            Feedback.activity_id == activity.id,
                            Feedback.reviewer_id == current_user.id,
                        )
                    )
                ).all()
            )
            target_ids = list(
                (
                    await session.scalars(
                        select(ActivityMember.user_id).where(
                            ActivityMember.activity_id == activity.id,
                            ActivityMember.status == "confirmed",
                            ActivityMember.user_id != current_user.id,
                        )
                    )
                ).all()
            )
            for user_id in target_ids:
                if user_id in already_reviewed:
                    continue
                target = await session.get(User, user_id)
                if target is not None:
                    feedback_targets.append(user_public(target))

        result.append(
            MyActivityItem(
                activity=public_activity,
                role=membership.role,
                membership_status=membership.status,
                joined_at=membership.joined_at,
                left_at=membership.left_at,
                can_leave=can_leave,
                leave_penalty=penalty if can_leave else 0,
                leave_policy_message=policy_message,
                needs_feedback=bool(feedback_targets),
                feedback_targets=feedback_targets,
            )
        )
    return sorted(result, key=lambda item: item.activity.starts_at, reverse=True)


@router.post("/activities/{activity_id}/leave", response_model=ActivityLeaveResult)
async def leave_activity(
    activity_id: str,
    payload: ActivityLeaveRequest,
    current_user: CurrentUser,
    session: SessionDep,
) -> ActivityLeaveResult:
    activity = await session.scalar(
        select(Activity).where(Activity.id == activity_id).with_for_update()
    )
    membership = await session.scalar(
        select(ActivityMember)
        .where(
            ActivityMember.activity_id == activity_id,
            ActivityMember.user_id == current_user.id,
        )
        .with_for_update()
    )
    if activity is None or membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="活动不存在")
    if membership.status != "confirmed":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="你已经不在这个活动中")
    penalty, policy_message = calculate_leave_penalty(activity)
    if utcnow() >= activity.starts_at:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=policy_message)

    member_count = int(
        await session.scalar(
            select(func.count(ActivityMember.id)).where(
                ActivityMember.activity_id == activity.id,
                ActivityMember.status == "confirmed",
            )
        )
        or 0
    )
    if membership.role == "owner" and member_count > 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="发起人已有搭子时不能直接退出，请先与成员协商或联系管理员。",
        )
    if membership.role == "owner" and member_count == 1:
        photos = list(
            (
                await session.scalars(
                    select(ActivityPhoto).where(ActivityPhoto.activity_id == activity.id)
                )
            ).all()
        )
        await session.execute(
            update(MatchRequest)
            .where(MatchRequest.activity_id == activity.id)
            .values(activity_id=None, status="cancelled")
        )
        await session.execute(delete(Invitation).where(Invitation.activity_id == activity.id))
        await session.execute(delete(Reminder).where(Reminder.activity_id == activity.id))
        await session.delete(activity)
        session.add(
            SystemEvent(
                category="activity_membership",
                status="success",
                title="单人活动已取消",
                message=f"{current_user.display_name}取消了尚未有人加入的“{activity.title}”。",
                details={"activity_id": activity.id, "user_id": current_user.id},
            )
        )
        await session.commit()
        for photo in photos:
            remove_photo_file(photo)
        return ActivityLeaveResult(
            activity_id=activity_id,
            membership_status="deleted",
            credit_delta=0,
            credit_score=current_user.credit_score,
            message="活动已取消，并从活动广场中删除。",
            activity_deleted=True,
        )
    if penalty and not payload.confirm_penalty:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"这次退出会扣 {penalty} 分，请确认后再退出。",
        )

    old_score = current_user.credit_score
    current_user.credit_score = max(0, current_user.credit_score - penalty)
    actual_delta = current_user.credit_score - old_score
    membership.status = "withdrawn"
    membership.left_at = utcnow()
    membership.leave_penalty = -actual_delta
    if membership.role == "owner":
        activity.status = "cancelled"
    elif activity.status == "formed":
        activity.status = "open"

    invitation = await session.scalar(
        select(Invitation).where(
            Invitation.activity_id == activity.id,
            Invitation.invitee_id == current_user.id,
            Invitation.status == "accepted",
        )
    )
    if invitation is not None:
        invitation.status = "withdrawn"
    if actual_delta:
        session.add(
            CreditEvent(
                user_id=current_user.id,
                delta=actual_delta,
                reason="late_activity_withdrawal",
                activity_id=activity.id,
            )
        )
    session.add(
        SystemEvent(
            category="activity_membership",
            status="attention" if penalty else "success",
            title="用户退出了活动",
            message=(
                f"{current_user.display_name}退出“{activity.title}”，信用分扣除 {penalty} 分。"
                if penalty
                else f"{current_user.display_name}提前退出“{activity.title}”，未扣信用分。"
            ),
            details={
                "activity_id": activity.id,
                "user_id": current_user.id,
                "penalty": penalty,
            },
        )
    )
    await session.commit()
    return ActivityLeaveResult(
        activity_id=activity.id,
        membership_status=membership.status,
        credit_delta=actual_delta,
        credit_score=current_user.credit_score,
        message=(
            f"已退出活动，信用分扣除 {penalty} 分。" if penalty else "已退出活动，没有扣信用分。"
        ),
        activity_deleted=False,
    )


@router.get("/activities/{activity_id}/calendar.ics")
async def export_activity_calendar(
    activity_id: str,
    current_user: CurrentUser,
    session: SessionDep,
) -> Response:
    activity = await session.get(Activity, activity_id)
    membership = await confirmed_membership(session, activity_id, current_user.id)
    if activity is None or membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="活动不存在")
    return Response(
        content=build_activity_ics(activity),
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="activity-{activity.id}.ics"'},
    )


@router.get("/activities/{activity_id}/photos", response_model=list[ActivityPhotoPublic])
async def list_activity_photos(
    activity_id: str,
    current_user: CurrentUser,
    session: SessionDep,
) -> list[ActivityPhotoPublic]:
    activity = await session.get(Activity, activity_id)
    membership = await confirmed_membership(session, activity_id, current_user.id)
    if activity is None or membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="活动不存在")
    if activity.ends_at <= utcnow():
        if await cleanup_expired_activity_photos(session):
            await session.commit()
        return []
    photos = list(
        (
            await session.scalars(
                select(ActivityPhoto)
                .where(ActivityPhoto.activity_id == activity_id)
                .order_by(ActivityPhoto.uploaded_at.desc(), ActivityPhoto.id.desc())
                .limit(5)
            )
        ).all()
    )
    result: list[ActivityPhotoPublic] = []
    for photo in photos:
        uploader = await session.get(User, photo.uploader_id)
        if uploader is None:
            continue
        result.append(
            ActivityPhotoPublic(
                id=photo.id,
                activity_id=photo.activity_id,
                uploader=user_public(uploader),
                media_type=photo.media_type,
                uploaded_at=photo.uploaded_at,
                content_url=(f"/api/v1/activities/{activity_id}/photos/{photo.id}/content"),
            )
        )
    return result


@router.post(
    "/activities/{activity_id}/photos",
    response_model=ActivityPhotoPublic,
    status_code=status.HTTP_201_CREATED,
)
async def upload_activity_photo(
    activity_id: str,
    payload: ActivityPhotoUpload,
    current_user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> ActivityPhotoPublic:
    activity = await session.get(Activity, activity_id)
    membership = await confirmed_membership(session, activity_id, current_user.id)
    if activity is None or membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="活动不存在")
    now = utcnow()
    if now < activity.starts_at - PHOTO_UPLOAD_WINDOW:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="活动开始前 15 分钟才能上传集合照片。",
        )
    if now >= activity.ends_at:
        if await cleanup_expired_activity_photos(session):
            await session.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="活动已结束，照片已清理。")

    content, media_type, extension = decode_image_data_url(
        payload.data_url, settings.activity_photo_max_bytes
    )
    photo_id = new_id()
    photo_directory = await asyncio.to_thread(
        lambda: Path(settings.activity_photo_directory).expanduser().resolve()
    )
    await asyncio.to_thread(photo_directory.mkdir, parents=True, exist_ok=True)
    file_path = photo_directory / f"{photo_id}.{extension}"
    await asyncio.to_thread(file_path.write_bytes, content)
    photo = ActivityPhoto(
        id=photo_id,
        activity_id=activity.id,
        uploader_id=current_user.id,
        file_path=str(file_path),
        media_type=media_type,
    )
    session.add(photo)
    try:
        await session.flush()
        photos = list(
            (
                await session.scalars(
                    select(ActivityPhoto)
                    .where(ActivityPhoto.activity_id == activity.id)
                    .order_by(ActivityPhoto.uploaded_at.desc(), ActivityPhoto.id.desc())
                )
            ).all()
        )
        for expired in photos[5:]:
            remove_photo_file(expired)
            await session.delete(expired)
        await session.commit()
    except Exception:
        await asyncio.to_thread(file_path.unlink, missing_ok=True)
        await session.rollback()
        raise
    return ActivityPhotoPublic(
        id=photo.id,
        activity_id=photo.activity_id,
        uploader=user_public(current_user),
        media_type=photo.media_type,
        uploaded_at=photo.uploaded_at,
        content_url=f"/api/v1/activities/{activity_id}/photos/{photo.id}/content",
    )


@router.get("/activities/{activity_id}/photos/{photo_id}/content")
async def read_activity_photo(
    activity_id: str,
    photo_id: str,
    current_user: CurrentUser,
    session: SessionDep,
) -> FileResponse:
    activity = await session.get(Activity, activity_id)
    membership = await confirmed_membership(session, activity_id, current_user.id)
    if activity is None or membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="照片不存在")
    if activity.ends_at <= utcnow():
        if await cleanup_expired_activity_photos(session):
            await session.commit()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="照片已按时清理")
    photo = await session.scalar(
        select(ActivityPhoto).where(
            ActivityPhoto.id == photo_id,
            ActivityPhoto.activity_id == activity_id,
        )
    )
    photo_exists = photo is not None and await asyncio.to_thread(Path(photo.file_path).is_file)
    if photo is None or not photo_exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="照片不存在")
    return FileResponse(photo.file_path, media_type=photo.media_type)


@router.get(
    "/activities/{activity_id}/time-votes",
    response_model=list[ActivityTimeVotePublic],
)
async def list_activity_time_votes(
    activity_id: str,
    current_user: CurrentUser,
    session: SessionDep,
) -> list[ActivityTimeVotePublic]:
    if await confirmed_membership(session, activity_id, current_user.id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="活动不存在")
    votes = list(
        (
            await session.scalars(
                select(ActivityTimeVote)
                .where(ActivityTimeVote.activity_id == activity_id)
                .order_by(ActivityTimeVote.created_at.desc())
                .limit(20)
            )
        ).all()
    )
    return [await time_vote_public(session, vote, current_user.id) for vote in votes]


@router.post(
    "/activities/{activity_id}/time-votes",
    response_model=ActivityTimeVotePublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_activity_time_vote(
    activity_id: str,
    payload: ActivityTimeVoteCreate,
    current_user: CurrentUser,
    session: SessionDep,
) -> ActivityTimeVotePublic:
    activity = await session.scalar(
        select(Activity).where(Activity.id == activity_id).with_for_update()
    )
    membership = await confirmed_membership(session, activity_id, current_user.id)
    if activity is None or membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="活动不存在")
    if activity.starts_at <= utcnow():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="活动开始后不能再发起改期")
    existing = await session.scalar(
        select(ActivityTimeVote.id).where(
            ActivityTimeVote.activity_id == activity_id,
            ActivityTimeVote.proposer_id == current_user.id,
            ActivityTimeVote.status == "pending",
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="你已经有一项改期方案在等待大家表态。",
        )
    vote = ActivityTimeVote(
        activity_id=activity.id,
        proposer_id=current_user.id,
        proposed_starts_at=payload.starts_at,
        proposed_ends_at=payload.ends_at,
    )
    session.add(vote)
    await session.flush()
    for member_id in await activity_member_ids(session, activity.id):
        if member_id != current_user.id:
            await enqueue_notification(
                session,
                member_id,
                f"time_vote:{vote.id}",
                "time_vote",
                "搭子提议改时间",
                f"{current_user.display_name}想调整“{activity.title}”的时间，请看看是否同意。",
                "/?tab=activities",
            )
    member_count = int(
        await session.scalar(
            select(func.count(ActivityMember.id)).where(
                ActivityMember.activity_id == activity.id,
                ActivityMember.status == "confirmed",
            )
        )
        or 0
    )
    if member_count == 1:
        activity.starts_at = vote.proposed_starts_at
        activity.ends_at = vote.proposed_ends_at
        vote.status = "approved"
        vote.resolved_at = utcnow()
        await reschedule_activity_reminders(session, activity)
    await session.commit()
    return await time_vote_public(session, vote, current_user.id)


@router.post(
    "/activities/{activity_id}/time-votes/{vote_id}/respond",
    response_model=ActivityTimeVotePublic,
)
async def respond_activity_time_vote(
    activity_id: str,
    vote_id: str,
    payload: ActivityTimeVoteRespond,
    current_user: CurrentUser,
    session: SessionDep,
) -> ActivityTimeVotePublic:
    activity = await session.scalar(
        select(Activity).where(Activity.id == activity_id).with_for_update()
    )
    vote = await session.scalar(
        select(ActivityTimeVote)
        .where(
            ActivityTimeVote.id == vote_id,
            ActivityTimeVote.activity_id == activity_id,
        )
        .with_for_update()
    )
    if (
        activity is None
        or vote is None
        or await confirmed_membership(session, activity_id, current_user.id) is None
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="改期投票不存在")
    if vote.status != "pending":
        return await time_vote_public(session, vote, current_user.id)
    if vote.proposer_id == current_user.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="发起人已经默认同意该方案")
    response = await session.scalar(
        select(ActivityTimeVoteResponse).where(
            ActivityTimeVoteResponse.vote_id == vote.id,
            ActivityTimeVoteResponse.user_id == current_user.id,
        )
    )
    if response is None:
        response = ActivityTimeVoteResponse(
            vote_id=vote.id,
            user_id=current_user.id,
            decision=payload.decision,
        )
        session.add(response)
    else:
        response.decision = payload.decision
    await session.flush()

    if payload.decision == "rejected":
        vote.status = "rejected"
        vote.resolved_at = utcnow()
    else:
        confirmed_ids = set(
            (
                await session.scalars(
                    select(ActivityMember.user_id).where(
                        ActivityMember.activity_id == activity.id,
                        ActivityMember.status == "confirmed",
                    )
                )
            ).all()
        )
        approvals = set(
            (
                await session.scalars(
                    select(ActivityTimeVoteResponse.user_id).where(
                        ActivityTimeVoteResponse.vote_id == vote.id,
                        ActivityTimeVoteResponse.decision == "approved",
                    )
                )
            ).all()
        )
        required = confirmed_ids - {vote.proposer_id}
        if required.issubset(approvals):
            activity.starts_at = vote.proposed_starts_at
            activity.ends_at = vote.proposed_ends_at
            vote.status = "approved"
            vote.resolved_at = utcnow()
            await session.execute(
                update(ActivityTimeVote)
                .where(
                    ActivityTimeVote.activity_id == activity.id,
                    ActivityTimeVote.status == "pending",
                    ActivityTimeVote.id != vote.id,
                )
                .values(status="superseded", resolved_at=utcnow())
            )
            await reschedule_activity_reminders(session, activity)
    await session.commit()
    return await time_vote_public(session, vote, current_user.id)


@router.post(
    "/matches/preview",
    response_model=MatchPreviewResponse,
    status_code=status.HTTP_201_CREATED,
)
async def preview_match(
    payload: MatchPreviewRequest,
    current_user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> MatchPreviewResponse:
    ensure_campus(current_user)
    if payload.same_gender_only and current_user.gender not in {"male", "female"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="请先在我的画像中选择男或女，才能创建仅限同性的活动",
        )
    latest_request_at = await session.scalar(
        select(func.max(MatchRequest.created_at)).where(
            MatchRequest.requester_id == current_user.id,
            MatchRequest.status != "failed",
        )
    )
    if latest_request_at is not None:
        next_available = latest_request_at + timedelta(seconds=MATCH_COOLDOWN_SECONDS)
        if utcnow() < next_available:
            retry_after = max(1, int((next_available - utcnow()).total_seconds()))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Agent 正在歇口气，请 {retry_after} 秒后再找一次。",
                headers={"Retry-After": str(retry_after)},
            )
    match_request = MatchRequest(requester_id=current_user.id, **payload.model_dump())
    session.add(match_request)
    await session.flush()
    agent_run = AgentRun(
        match_request_id=match_request.id,
        mode="starting",
        model=settings.ai_model if settings.use_llm else None,
    )
    session.add(agent_run)
    await session.flush()

    context = MatchContext(
        requester=current_user,
        category=match_request.category,
        starts_at=match_request.starts_at,
        ends_at=match_request.ends_at,
        location=match_request.location,
        people_needed=match_request.people_needed,
        personal_requirement=match_request.personal_requirement,
        same_gender_only=match_request.same_gender_only,
    )
    try:
        decision = await run_matching_agent(session, context, settings)
    except AgentOutputError as exc:
        match_request.status = "failed"
        agent_run.status = "failed"
        agent_run.error = str(exc)[:500]
        agent_run.finished_at = utcnow()
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "agent_output_error",
                "message": "这次 Agent 没能生成可用结果，刚才填写的内容将被清空，请重新填写。",
            },
        ) from exc
    except Exception as exc:
        match_request.status = "failed"
        agent_run.status = "failed"
        agent_run.error = str(exc)[:500]
        agent_run.finished_at = utcnow()
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="匹配 Agent 执行失败"
        ) from exc

    agent_run.mode = decision.mode
    agent_run.model = decision.model
    agent_run.status = "completed"
    agent_run.trace = decision.trace
    agent_run.summary = decision.summary
    agent_run.error = decision.error
    agent_run.finished_at = utcnow()

    for rank, item in enumerate(decision.candidates, start=1):
        session.add(
            MatchCandidate(
                match_request_id=match_request.id,
                candidate_type=item.candidate_type,
                candidate_id=item.candidate_id,
                rank=rank,
                score=item.score,
                factors=item.factors,
                explanation=item.explanation,
            )
        )
    await session.commit()

    candidates: list[CandidatePublic] = []
    for rank, item in enumerate(decision.candidates, start=1):
        candidate = CandidatePublic(
            candidate_type=item.candidate_type,
            candidate_id=item.candidate_id,
            rank=rank,
            score=item.score,
            factors=item.factors,
            explanation=item.explanation,
        )
        if item.candidate_type == "user" and item.candidate_id in decision.users:
            candidate.user = user_public(decision.users[item.candidate_id])
        elif item.candidate_type == "activity" and item.candidate_id in decision.activities:
            activity, _ = decision.activities[item.candidate_id]
            candidate.activity = await activity_public(session, activity)
        candidates.append(candidate)

    return MatchPreviewResponse(
        match_request_id=match_request.id,
        agent_run_id=agent_run.id,
        agent_mode=decision.mode,
        agent_model=decision.model,
        summary=decision.summary,
        personalization=PersonalizationReport(
            applied=decision.personalization.applied,
            ignored_for_safety=decision.personalization.ignored_for_safety,
            unresolved=decision.personalization.unresolved,
        ),
        candidates=candidates,
    )


async def load_confirmed_result(
    session: AsyncSession,
    match_request: MatchRequest,
) -> MatchConfirmResponse:
    if not match_request.activity_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="匹配请求状态异常")
    activity = await session.get(Activity, match_request.activity_id)
    if activity is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="活动不存在")
    invitations = list(
        (
            await session.scalars(
                select(Invitation).where(Invitation.match_request_id == match_request.id)
            )
        ).all()
    )
    return MatchConfirmResponse(
        match_request_id=match_request.id,
        status=match_request.status,
        activity=await activity_public(session, activity),
        invitations=[InvitationPublic.model_validate(item) for item in invitations],
    )


@router.post("/matches/{match_request_id}/confirm", response_model=MatchConfirmResponse)
async def confirm_match(
    match_request_id: str,
    payload: MatchConfirmRequest,
    current_user: CurrentUser,
    session: SessionDep,
) -> MatchConfirmResponse:
    ensure_campus(current_user)
    match_request = await session.scalar(
        select(MatchRequest).where(MatchRequest.id == match_request_id).with_for_update()
    )
    if match_request is None or match_request.requester_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="匹配请求不存在")
    if match_request.status in {"confirmed", "joined", "applied"}:
        return await load_confirmed_result(session, match_request)
    if match_request.status != "previewed":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="当前状态不可确认")

    candidates = list(
        (
            await session.scalars(
                select(MatchCandidate).where(MatchCandidate.match_request_id == match_request.id)
            )
        ).all()
    )
    allowed_users = {item.candidate_id for item in candidates if item.candidate_type == "user"}
    allowed_activities = {
        item.candidate_id for item in candidates if item.candidate_type == "activity"
    }

    if payload.existing_activity_id:
        if payload.existing_activity_id not in allowed_activities:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="只能加入本次推荐的活动"
            )
        activity = await session.scalar(
            select(Activity).where(Activity.id == payload.existing_activity_id).with_for_update()
        )
        if activity is None or activity.status != "open" or activity.starts_at <= utcnow():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="活动已不可加入")
        join_block_reason = await activity_join_block_reason(session, activity, current_user)
        if join_block_reason:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=join_block_reason)
        member_count = int(
            await session.scalar(
                select(func.count(ActivityMember.id)).where(
                    ActivityMember.activity_id == activity.id,
                    ActivityMember.status == "confirmed",
                )
            )
            or 0
        )
        if member_count >= activity.capacity:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="活动人数已满")
        if activity.join_policy == "approval":
            await apply_to_activity(session, activity, current_user)
            match_request.status = "applied"
        else:
            session.add(
                ActivityMember(
                    activity_id=activity.id,
                    user_id=current_user.id,
                    role="participant",
                    status="confirmed",
                )
            )
            match_request.status = "joined"
            if member_count + 1 >= activity.capacity:
                activity.status = "formed"
                await close_pending_applications(session, activity)
            for member_id in await activity_member_ids(session, activity.id):
                await enqueue_notification(
                    session,
                    member_id,
                    f"public_join:{activity.id}:{current_user.id}",
                    "public_join",
                    "活动迎来新搭子",
                    f"{current_user.display_name}加入了“{activity.title}”。",
                    "/?tab=activities",
                )
        match_request.activity_id = activity.id
        invitations: list[Invitation] = []
        write_tools = ["join_activity", "schedule_reminder"]
    else:
        location = match_request.location.strip() or payload.location or ""
        if not location:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="创建活动前请填写地点；也可以选择加入已有活动。",
            )
        selected_ids = set(payload.candidate_user_ids)
        if match_request.same_gender_only:
            if current_user.gender not in {"male", "female"}:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="请先选择男或女，才能创建仅限同性的活动",
                )
            selected_genders = (
                await session.execute(select(User.id, User.gender).where(User.id.in_(selected_ids)))
            ).all()
            if any(gender != current_user.gender for _, gender in selected_genders):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="仅限同性活动不能邀请其他性别的搭子",
                )
        if not selected_ids.issubset(allowed_users):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="只能邀请本次推荐的用户"
            )
        if selected_ids:
            selected_campuses = (
                await session.execute(
                    select(User.id, User.campus).where(
                        User.id.in_(selected_ids), User.is_active.is_(True)
                    )
                )
            ).all()
            if len(selected_campuses) != len(selected_ids) or any(
                campus != ensure_campus(current_user) for _, campus in selected_campuses
            ):
                raise HTTPException(status_code=409, detail="候选搭子的校区发生变化，请重新匹配")
        if len(selected_ids) > match_request.people_needed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="邀请人数不能超过本次需求人数",
            )
        activity = Activity(
            owner_id=current_user.id,
            campus=ensure_campus(current_user),
            join_policy=payload.join_policy,
            title=match_request.title or f"{location}{match_request.category}搭子局",
            category=match_request.category,
            starts_at=match_request.starts_at,
            ends_at=match_request.ends_at,
            location=location,
            capacity=match_request.people_needed + 1,
            personal_requirement=match_request.personal_requirement,
            same_gender_only=match_request.same_gender_only,
            status="open",
        )
        session.add(activity)
        match_request.location = location
        await session.flush()
        session.add(
            ActivityMember(
                activity_id=activity.id,
                user_id=current_user.id,
                role="owner",
                status="confirmed",
            )
        )
        expires_at = min(match_request.starts_at, utcnow() + timedelta(hours=24))
        invitations = []
        for invitee_id in payload.candidate_user_ids:
            invitation = Invitation(
                id=new_id(),
                activity_id=activity.id,
                match_request_id=match_request.id,
                inviter_id=current_user.id,
                invitee_id=invitee_id,
                message=f"邀请你参加“{activity.title}”",
                expires_at=expires_at,
            )
            session.add(invitation)
            invitations.append(invitation)
            await enqueue_notification(
                session,
                invitee_id,
                f"invitation:{invitation.id}",
                "invitation",
                "收到一份搭子邀请",
                f"{current_user.display_name}邀请你参加“{activity.title}”。",
                "/?tab=invitations",
            )
        match_request.status = "confirmed"
        match_request.activity_id = activity.id
        write_tools = ["create_activity", "schedule_reminder"]
        if payload.candidate_user_ids:
            write_tools.insert(1, "invite_user")

    reminder_at = match_request.starts_at - timedelta(hours=1)
    if reminder_at > utcnow() and match_request.status != "applied":
        session.add(
            Reminder(
                activity_id=activity.id,
                user_id=current_user.id,
                remind_at=reminder_at,
            )
        )

    agent_run = await session.scalar(
        select(AgentRun)
        .where(AgentRun.match_request_id == match_request.id)
        .order_by(AgentRun.started_at.desc())
    )
    if agent_run is not None:
        trace = list(agent_run.trace)
        trace.append(
            {
                "event": "user_confirmation",
                "approved_tools": write_tools,
                "confirmed_at": datetime.now(UTC).isoformat(),
            }
        )
        agent_run.trace = trace
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该操作已经完成") from exc
    for invitation in invitations:
        await session.refresh(invitation)
    return MatchConfirmResponse(
        match_request_id=match_request.id,
        status=match_request.status,
        activity=await activity_public(session, activity),
        invitations=[InvitationPublic.model_validate(item) for item in invitations],
    )


@router.post("/invitations/{invitation_id}/respond", response_model=InvitationPublic)
async def respond_to_invitation(
    invitation_id: str,
    payload: InvitationRespondRequest,
    current_user: CurrentUser,
    session: SessionDep,
) -> InvitationPublic:
    invitation = await session.scalar(
        select(Invitation).where(Invitation.id == invitation_id).with_for_update()
    )
    if invitation is None or invitation.invitee_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="邀请不存在")
    if invitation.status != "pending":
        return InvitationPublic.model_validate(invitation)
    if invitation.expires_at < utcnow():
        invitation.status = "expired"
        invitation.responded_at = utcnow()
        await session.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="邀请已过期")

    activity = await session.scalar(
        select(Activity).where(Activity.id == invitation.activity_id).with_for_update()
    )
    if activity is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="活动不存在")
    if payload.decision == "accepted":
        join_block_reason = await activity_join_block_reason(session, activity, current_user)
        if join_block_reason:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=join_block_reason)
        member_count = int(
            await session.scalar(
                select(func.count(ActivityMember.id)).where(
                    ActivityMember.activity_id == activity.id,
                    ActivityMember.status == "confirmed",
                )
            )
            or 0
        )
        if member_count >= activity.capacity:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="活动人数已满")
        if activity.join_policy == "approval":
            await apply_to_activity(session, activity, current_user)
            invitation.status = "accepted"
            invitation.responded_at = utcnow()
            await session.commit()
            return InvitationPublic.model_validate(invitation)
        session.add(
            ActivityMember(
                activity_id=activity.id,
                user_id=current_user.id,
                role="participant",
                status="confirmed",
            )
        )
        reminder_at = activity.starts_at - timedelta(hours=1)
        if reminder_at > utcnow():
            session.add(
                Reminder(
                    activity_id=activity.id,
                    user_id=current_user.id,
                    remind_at=reminder_at,
                )
            )
        if member_count + 1 >= activity.capacity:
            activity.status = "formed"
            await close_pending_applications(session, activity)
    invitation.status = payload.decision
    invitation.responded_at = utcnow()
    await session.commit()
    await session.refresh(invitation)
    return InvitationPublic.model_validate(invitation)


@router.get("/notifications", response_model=list[NotificationPublic])
async def list_notifications(
    current_user: CurrentUser,
    session: SessionDep,
) -> list[NotificationPublic]:
    rows = list(
        (
            await session.scalars(
                select(Notification)
                .where(Notification.user_id == current_user.id)
                .order_by(Notification.created_at.desc())
                .limit(80)
            )
        ).all()
    )
    return [NotificationPublic.model_validate(item) for item in rows]


@router.post("/notifications/{notification_id}/read", response_model=NotificationPublic)
async def mark_notification_read(
    notification_id: str,
    current_user: CurrentUser,
    session: SessionDep,
) -> NotificationPublic:
    item = await session.scalar(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == current_user.id,
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="消息不存在")
    item.read_at = utcnow()
    await session.commit()
    return NotificationPublic.model_validate(item)


@router.get("/push/public-key")
async def read_push_public_key(request: Request) -> dict[str, str]:
    return {"public_key": request.app.state.vapid_public_key}


@router.post("/push/subscriptions", status_code=201)
async def save_push_subscription(
    payload: PushSubscriptionRequest,
    current_user: CurrentUser,
    session: SessionDep,
) -> dict[str, str]:
    endpoint_host = urlsplit(payload.endpoint).hostname or ""
    allowed_hosts = {
        "web.push.apple.com",
        "fcm.googleapis.com",
        "updates.push.services.mozilla.com",
    }
    if not payload.endpoint.startswith("https://") or (
        endpoint_host not in allowed_hosts and not endpoint_host.endswith(".notify.windows.com")
    ):
        raise HTTPException(status_code=422, detail="推送订阅地址无效")
    subscription = await session.scalar(
        select(PushSubscription).where(PushSubscription.endpoint == payload.endpoint)
    )
    if subscription is None:
        session.add(PushSubscription(user_id=current_user.id, **payload.model_dump()))
    else:
        subscription.user_id = current_user.id
        subscription.p256dh = payload.p256dh
        subscription.auth = payload.auth
    await session.commit()
    return {"status": "subscribed"}


@router.delete("/push/subscriptions")
async def remove_push_subscription(
    current_user: CurrentUser,
    session: SessionDep,
    endpoint: str = Query(max_length=2048),
) -> dict[str, str]:
    await session.execute(
        delete(PushSubscription).where(
            PushSubscription.user_id == current_user.id,
            PushSubscription.endpoint == endpoint,
        )
    )
    await session.commit()
    return {"status": "unsubscribed"}


@router.get("/invitations", response_model=list[InvitationInboxItem])
async def list_invitations(
    current_user: CurrentUser,
    session: SessionDep,
    invitation_status: str | None = Query(default=None, alias="status", max_length=30),
) -> list[InvitationInboxItem]:
    statement = (
        select(Invitation)
        .where(Invitation.invitee_id == current_user.id)
        .order_by(Invitation.created_at.desc())
    )
    if invitation_status:
        statement = statement.where(Invitation.status == invitation_status)
    invitations = list((await session.scalars(statement)).all())
    result: list[InvitationInboxItem] = []
    for invitation in invitations:
        activity = await session.get(Activity, invitation.activity_id)
        inviter = await session.get(User, invitation.inviter_id)
        if activity is None or inviter is None:
            continue
        result.append(
            InvitationInboxItem(
                **InvitationPublic.model_validate(invitation).model_dump(),
                inviter=user_public(inviter),
                activity=await activity_public(session, activity),
            )
        )
    return result


@router.post("/feedback", response_model=FeedbackResult, status_code=status.HTTP_201_CREATED)
async def submit_feedback(
    payload: FeedbackCreate,
    current_user: CurrentUser,
    session: SessionDep,
) -> FeedbackResult:
    if payload.reviewee_id == current_user.id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="不能评价自己")
    activity = await session.get(Activity, payload.activity_id)
    if activity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="活动不存在")
    if activity.ends_at > utcnow():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="活动结束后才能评价，先好好享受这次搭子局吧。",
        )
    await process_completed_activities(session)
    member_ids = set(
        (
            await session.scalars(
                select(ActivityMember.user_id).where(
                    ActivityMember.activity_id == payload.activity_id,
                    ActivityMember.status == "confirmed",
                )
            )
        ).all()
    )
    if current_user.id not in member_ids or payload.reviewee_id not in member_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="只有同一活动参与者可以互评"
        )
    reviewee = await session.get(User, payload.reviewee_id)
    if reviewee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="被评价用户不存在")
    existing = await session.scalar(
        select(Feedback.id).where(
            Feedback.activity_id == payload.activity_id,
            Feedback.reviewer_id == current_user.id,
            Feedback.reviewee_id == payload.reviewee_id,
        )
    )
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="你已经评价过该用户")

    feedback = Feedback(reviewer_id=current_user.id, **payload.model_dump())
    session.add(feedback)
    await session.flush()
    decision = await moderate_feedback(session, feedback, current_user, reviewee)
    await apply_moderation_decision(session, feedback, decision)
    if not decision.requires_peer_review:
        await session.flush()
        await refresh_hidden_profile(session, reviewee)
        await refresh_hidden_profile(session, current_user)
    await session.commit()
    return FeedbackResult(
        feedback_id=feedback.id,
        reviewee_credit_score=reviewee.credit_score,
        reviewee_credit_delta=decision.reviewee_delta,
        reviewer_credit_score=current_user.credit_score,
        reviewer_credit_delta=decision.reviewer_delta,
        moderation_status=decision.status,
        requires_peer_review=decision.requires_peer_review,
        ai_summary=decision.reason,
    )


@router.get("/feedback/peer-review-tasks", response_model=list[PeerReviewTask])
async def list_peer_review_tasks(
    current_user: CurrentUser,
    session: SessionDep,
) -> list[PeerReviewTask]:
    pending_feedback = list(
        (
            await session.scalars(
                select(Feedback)
                .where(Feedback.moderation_status == "needs_peer_review")
                .order_by(Feedback.created_at.asc())
            )
        ).all()
    )
    tasks: list[PeerReviewTask] = []
    for feedback in pending_feedback:
        if current_user.id not in await eligible_peer_reviewer_ids(session, feedback):
            continue
        existing = await session.scalar(
            select(PeerFeedbackReview.id).where(
                PeerFeedbackReview.feedback_id == feedback.id,
                PeerFeedbackReview.reviewer_id == current_user.id,
            )
        )
        if existing:
            continue
        activity = await session.get(Activity, feedback.activity_id)
        author = await session.get(User, feedback.reviewer_id)
        subject = await session.get(User, feedback.reviewee_id)
        if activity is None or author is None or subject is None:
            continue
        tasks.append(
            PeerReviewTask(
                feedback_id=feedback.id,
                activity=await activity_public(session, activity),
                author=user_public(author),
                subject=user_public(subject),
                attendance=feedback.attendance,
                rating=feedback.rating,
                comment=feedback.comment,
                skill_name=feedback.skill_name,
                skill_level=feedback.skill_level,
                personality_tags=feedback.personality_tags,
                incident_tags=feedback.incident_tags,
                ai_summary=feedback.ai_reason or "审核 Agent 希望获得同场参与者的补充信息。",
            )
        )
    return tasks


@router.post(
    "/feedback/{feedback_id}/peer-review",
    response_model=PeerReviewResult,
    status_code=status.HTTP_201_CREATED,
)
async def submit_peer_review(
    feedback_id: str,
    payload: PeerReviewCreate,
    current_user: CurrentUser,
    session: SessionDep,
) -> PeerReviewResult:
    feedback = await session.scalar(
        select(Feedback).where(Feedback.id == feedback_id).with_for_update()
    )
    if feedback is None or feedback.moderation_status != "needs_peer_review":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="这条评价不需要复核，或已经处理完成。",
        )
    if current_user.id not in await eligible_peer_reviewer_ids(session, feedback):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有同场且与原评价无关的第三位参与者可以复核。",
        )
    existing = await session.scalar(
        select(PeerFeedbackReview.id).where(
            PeerFeedbackReview.feedback_id == feedback.id,
            PeerFeedbackReview.reviewer_id == current_user.id,
        )
    )
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="你已经复核过这条评价")
    original_reviewer = await session.get(User, feedback.reviewer_id)
    reviewee = await session.get(User, feedback.reviewee_id)
    if original_reviewer is None or reviewee is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="评价关联用户不存在")

    peer_review = PeerFeedbackReview(
        feedback_id=feedback.id,
        reviewer_id=current_user.id,
        **payload.model_dump(),
    )
    session.add(peer_review)
    await session.flush()
    summary = await finalize_feedback_with_peer_review(
        session,
        feedback,
        peer_review,
        original_reviewer,
        reviewee,
        current_user,
    )
    await session.flush()
    await refresh_hidden_profile(session, reviewee)
    await refresh_hidden_profile(session, original_reviewer)
    await refresh_hidden_profile(session, current_user)
    await session.commit()
    return PeerReviewResult(
        feedback_id=feedback.id,
        moderation_status=feedback.moderation_status,
        reviewee_credit_delta=feedback.reviewee_credit_delta,
        original_reviewer_credit_delta=feedback.reviewer_credit_delta,
        peer_reviewer_credit_delta=peer_review.reviewer_credit_delta,
        ai_summary=summary,
    )


@router.get("/agent-runs/{agent_run_id}", response_model=AgentRunPublic)
async def read_agent_run(
    agent_run_id: str,
    current_user: CurrentUser,
    session: SessionDep,
) -> AgentRunPublic:
    run = await session.get(AgentRun, agent_run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent 运行记录不存在")
    match_request = await session.get(MatchRequest, run.match_request_id)
    if match_request is None or match_request.requester_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent 运行记录不存在")
    return AgentRunPublic.model_validate(run)


@router.get("/agent-tools", response_model=list[ToolDefinition])
async def list_agent_tools(_: CurrentUser) -> list[ToolDefinition]:
    return [ToolDefinition.model_validate(item) for item in TOOL_CATALOG]
