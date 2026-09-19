from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import TOOL_CATALOG, run_matching_agent
from app.core import (
    DatabaseRuntime,
    Settings,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.matching import MatchContext
from app.models import (
    Activity,
    ActivityMember,
    AgentRun,
    CreditEvent,
    Feedback,
    Invitation,
    MatchCandidate,
    MatchRequest,
    PeerFeedbackReview,
    Reminder,
    SystemEvent,
    User,
    utcnow,
)
from app.post_activity import (
    apply_moderation_decision,
    calculate_leave_penalty,
    eligible_peer_reviewer_ids,
    finalize_feedback_with_peer_review,
    moderate_feedback,
    process_completed_activities,
    refresh_hidden_profile,
)
from app.schemas import (
    ActivityCreate,
    ActivityLeaveRequest,
    ActivityLeaveResult,
    ActivityPublic,
    AgentRunPublic,
    CandidatePublic,
    FeedbackCreate,
    FeedbackResult,
    InvitationInboxItem,
    InvitationPublic,
    InvitationRespondRequest,
    LoginRequest,
    MatchConfirmRequest,
    MatchConfirmResponse,
    MatchPreviewRequest,
    MatchPreviewResponse,
    MyActivityItem,
    PeerReviewCreate,
    PeerReviewResult,
    PeerReviewTask,
    PersonalizationReport,
    RegisterRequest,
    TokenResponse,
    ToolDefinition,
    UserMe,
    UserProfileUpdate,
    UserPublic,
)

router = APIRouter(prefix="/api/v1")
auth_scheme = HTTPBearer(auto_error=False)


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


def user_public(user: User) -> UserPublic:
    return UserPublic.model_validate(user)


async def activity_public(session: AsyncSession, activity: Activity) -> ActivityPublic:
    count = await session.scalar(
        select(func.count(ActivityMember.id)).where(
            ActivityMember.activity_id == activity.id,
            ActivityMember.status == "confirmed",
        )
    )
    return ActivityPublic(
        id=activity.id,
        owner_id=activity.owner_id,
        title=activity.title,
        category=activity.category,
        starts_at=activity.starts_at,
        ends_at=activity.ends_at,
        location=activity.location,
        capacity=activity.capacity,
        participant_count=int(count or 0),
        description=activity.description,
        personal_requirement=activity.personal_requirement,
        status=activity.status,
    )


@router.post("/auth/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    email = payload.email.lower()
    if await session.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该邮箱已注册")
    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name.strip(),
        university=payload.university.strip(),
        campus=payload.campus.strip() if payload.campus else None,
        department=payload.department.strip() if payload.department else None,
        grade_year=payload.grade_year,
        bio=payload.bio.strip() if payload.bio else None,
        interests=payload.interests,
        preferred_locations=payload.preferred_locations,
        social_style=payload.social_style,
        preferred_group_min=payload.preferred_group_min,
        preferred_group_max=payload.preferred_group_max,
    )
    session.add(user)
    await session.commit()
    token, expires_at = create_access_token(user.id, settings)
    return TokenResponse(access_token=token, expires_at=expires_at)


@router.post("/auth/token", response_model=TokenResponse)
async def login(payload: LoginRequest, session: SessionDep, settings: SettingsDep) -> TokenResponse:
    user = await session.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="用户已停用")
    token, expires_at = create_access_token(user.id, settings)
    return TokenResponse(access_token=token, expires_at=expires_at)


@router.get("/users/me", response_model=UserMe)
async def read_me(current_user: CurrentUser) -> UserMe:
    return UserMe.model_validate(current_user)


@router.patch("/users/me", response_model=UserMe)
async def update_me(
    payload: UserProfileUpdate,
    current_user: CurrentUser,
    session: SessionDep,
) -> UserMe:
    values = payload.model_dump(exclude_unset=True)
    min_group = values.get("preferred_group_min", current_user.preferred_group_min)
    max_group = values.get("preferred_group_max", current_user.preferred_group_max)
    if min_group > max_group:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="preferred_group_min 不能大于 preferred_group_max",
        )
    for field_name, value in values.items():
        setattr(current_user, field_name, value)
    await session.commit()
    await session.refresh(current_user)
    return UserMe.model_validate(current_user)


@router.post("/activities", response_model=ActivityPublic, status_code=status.HTTP_201_CREATED)
async def create_activity(
    payload: ActivityCreate,
    current_user: CurrentUser,
    session: SessionDep,
) -> ActivityPublic:
    activity = Activity(owner_id=current_user.id, **payload.model_dump())
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
    _: CurrentUser,
    category: str | None = Query(default=None, max_length=50),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[ActivityPublic]:
    statement = select(Activity).order_by(Activity.starts_at.asc()).limit(limit)
    if category:
        statement = statement.where(Activity.category == category)
    activities = list((await session.scalars(statement)).all())
    return [await activity_public(session, activity) for activity in activities]


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
        can_leave = membership.status == "confirmed" and utcnow() < activity.ends_at
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
    if utcnow() >= activity.ends_at:
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
            f"已退出活动，信用分扣除 {penalty} 分。"
            if penalty
            else "已退出活动，没有扣信用分。"
        ),
    )


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
    )
    try:
        decision = await run_matching_agent(session, context, settings)
    except Exception as exc:
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
    match_request = await session.scalar(
        select(MatchRequest).where(MatchRequest.id == match_request_id).with_for_update()
    )
    if match_request is None or match_request.requester_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="匹配请求不存在")
    if match_request.status in {"confirmed", "joined"}:
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
        if activity is None or activity.status != "open":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="活动已不可加入")
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
        session.add(
            ActivityMember(
                activity_id=activity.id,
                user_id=current_user.id,
                role="participant",
                status="confirmed",
            )
        )
        match_request.status = "joined"
        match_request.activity_id = activity.id
        invitations: list[Invitation] = []
        write_tools = ["join_activity", "schedule_reminder"]
    else:
        selected_ids = set(payload.candidate_user_ids)
        if not selected_ids.issubset(allowed_users):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="只能邀请本次推荐的用户"
            )
        if len(selected_ids) > match_request.people_needed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="邀请人数不能超过本次需求人数",
            )
        activity = Activity(
            owner_id=current_user.id,
            title=match_request.title or f"{match_request.location}{match_request.category}搭子局",
            category=match_request.category,
            starts_at=match_request.starts_at,
            ends_at=match_request.ends_at,
            location=match_request.location,
            capacity=match_request.people_needed + 1,
            personal_requirement=match_request.personal_requirement,
            status="open",
        )
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
        expires_at = min(match_request.starts_at, utcnow() + timedelta(hours=24))
        invitations = []
        for invitee_id in payload.candidate_user_ids:
            invitation = Invitation(
                activity_id=activity.id,
                match_request_id=match_request.id,
                inviter_id=current_user.id,
                invitee_id=invitee_id,
                message=f"邀请你参加“{activity.title}”",
                expires_at=expires_at,
            )
            session.add(invitation)
            invitations.append(invitation)
        match_request.status = "confirmed"
        match_request.activity_id = activity.id
        write_tools = ["create_activity", "schedule_reminder"]
        if payload.candidate_user_ids:
            write_tools.insert(1, "invite_user")

    reminder_at = match_request.starts_at - timedelta(hours=1)
    if reminder_at > utcnow():
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
    invitation.status = payload.decision
    invitation.responded_at = utcnow()
    await session.commit()
    await session.refresh(invitation)
    return InvitationPublic.model_validate(invitation)


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
