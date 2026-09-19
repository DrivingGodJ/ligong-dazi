from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Activity, ActivityMember, User, UserBlock

MATCH_WEIGHTS = {
    "time": 0.30,
    "activity": 0.25,
    "location": 0.15,
    "interest": 0.15,
    "group_size": 0.05,
    "social_style": 0.05,
    "credit": 0.05,
}


@dataclass(slots=True)
class MatchContext:
    requester: User
    category: str
    starts_at: datetime
    ends_at: datetime
    location: str
    people_needed: int
    personal_requirement: str | None


@dataclass(slots=True)
class Personalization:
    min_credit: int = 0
    same_department: bool = False
    same_grade: bool = False
    preferred_style: str | None = None
    preferred_interests: list[str] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)
    ignored_for_safety: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ScoredCandidate:
    candidate_type: str
    candidate_id: str
    score: float
    factors: dict[str, float]
    explanation: list[str]


def normalize(value: str | None) -> str:
    return re.sub(r"\s+", "", (value or "").lower())


def interpret_personal_requirement(text: str | None, known_interests: set[str]) -> Personalization:
    result = Personalization()
    if not text or not text.strip():
        return result

    normalized = normalize(text)
    sensitive_terms = {
        "性别": ("男生", "女生", "只要男", "只要女"),
        "民族或籍贯": ("民族", "哪里人", "本地人", "外地人"),
        "身体与健康": ("残疾", "病史", "健康状况"),
    }
    for label, terms in sensitive_terms.items():
        if any(term in normalized for term in terms):
            result.ignored_for_safety.append(f"{label}条件未用于自动排序")

    if any(term in normalized for term in ("信用高", "靠谱", "不鸽", "别迟到", "不迟到", "守时")):
        result.min_credit = 90
        result.applied.append("优先信用分 90 以上的用户")
    if any(term in normalized for term in ("同院系", "同专业", "一个学院", "本专业")):
        result.same_department = True
        result.applied.append("优先同院系用户")
    if "同年级" in normalized:
        result.same_grade = True
        result.applied.append("优先同年级用户")
    if any(term in normalized for term in ("安静", "社恐", "少说话", "专注")):
        result.preferred_style = "quiet"
        result.applied.append("偏好安静型搭子")
    elif any(term in normalized for term in ("外向", "健谈", "活泼", "话多")):
        result.preferred_style = "outgoing"
        result.applied.append("偏好外向型搭子")

    for interest in sorted(known_interests, key=len, reverse=True):
        if interest and normalize(interest) in normalized:
            result.preferred_interests.append(interest)
    if result.preferred_interests:
        joined = "、".join(result.preferred_interests[:5])
        result.applied.append(f"偏好兴趣包含：{joined}")

    if not result.applied and not result.ignored_for_safety:
        result.unresolved.append(text.strip())
    return result


async def search_open_activities(
    session: AsyncSession,
    context: MatchContext,
    limit: int = 10,
) -> list[tuple[Activity, int]]:
    member_count = (
        select(
            ActivityMember.activity_id.label("activity_id"),
            func.count(ActivityMember.id).label("member_count"),
        )
        .where(ActivityMember.status == "confirmed")
        .group_by(ActivityMember.activity_id)
        .subquery()
    )
    statement = (
        select(Activity, func.coalesce(member_count.c.member_count, 0))
        .outerjoin(member_count, member_count.c.activity_id == Activity.id)
        .where(
            Activity.owner_id != context.requester.id,
            Activity.status == "open",
            Activity.category == context.category,
            Activity.starts_at < context.ends_at,
            Activity.ends_at > context.starts_at,
            (Activity.capacity - func.coalesce(member_count.c.member_count, 0)) >= 1,
        )
        .order_by(Activity.starts_at.asc())
        .limit(max(1, min(limit, 50)))
    )
    return [(row[0], int(row[1])) for row in (await session.execute(statement)).all()]


async def search_available_users(
    session: AsyncSession,
    context: MatchContext,
    personalization: Personalization,
    limit: int = 50,
) -> list[User]:
    block_rows = await session.execute(
        select(UserBlock.blocker_id, UserBlock.blocked_id).where(
            or_(
                UserBlock.blocker_id == context.requester.id,
                UserBlock.blocked_id == context.requester.id,
            )
        )
    )
    blocked_ids = {
        blocked_id if blocker_id == context.requester.id else blocker_id
        for blocker_id, blocked_id in block_rows.all()
    }

    conflict_rows = await session.execute(
        select(ActivityMember.user_id)
        .join(Activity, Activity.id == ActivityMember.activity_id)
        .where(
            ActivityMember.status == "confirmed",
            Activity.status.in_(["open", "formed"]),
            Activity.starts_at < context.ends_at,
            Activity.ends_at > context.starts_at,
        )
    )
    conflict_ids = set(conflict_rows.scalars().all())

    conditions = [
        User.id != context.requester.id,
        User.is_active.is_(True),
        User.university == context.requester.university,
        User.credit_score >= personalization.min_credit,
    ]
    if blocked_ids:
        conditions.append(User.id.not_in(blocked_ids))
    if conflict_ids:
        conditions.append(User.id.not_in(conflict_ids))

    statement = (
        select(User)
        .where(and_(*conditions))
        .order_by(User.credit_score.desc(), User.created_at.asc())
        .limit(max(1, min(limit, 100)))
    )
    return list((await session.scalars(statement)).all())


def score_user_candidate(
    context: MatchContext,
    candidate: User,
    personalization: Personalization,
) -> ScoredCandidate:
    requester_interests = {normalize(item) for item in context.requester.interests}
    candidate_interests = {normalize(item) for item in candidate.interests}

    activity_score = 100.0 if normalize(context.category) in candidate_interests else 75.0
    if context.category in candidate.interests:
        activity_score = 100.0

    location = normalize(context.location)
    preferred_locations = [normalize(item) for item in candidate.preferred_locations]
    if any(location == item for item in preferred_locations):
        location_score = 100.0
    elif any(location in item or item in location for item in preferred_locations if item):
        location_score = 85.0
    elif candidate.campus and normalize(candidate.campus) in location:
        location_score = 75.0
    else:
        location_score = 55.0

    union = requester_interests | candidate_interests
    interest_score = (
        70.0
        if not union
        else 55.0 + 45.0 * len(requester_interests & candidate_interests) / len(union)
    )
    if personalization.preferred_interests:
        requested = {normalize(item) for item in personalization.preferred_interests}
        interest_score = min(100.0, interest_score + 15.0 * bool(requested & candidate_interests))

    expected_group_size = context.people_needed + 1
    if candidate.preferred_group_min <= expected_group_size <= candidate.preferred_group_max:
        group_score = 100.0
    else:
        distance = min(
            abs(expected_group_size - candidate.preferred_group_min),
            abs(expected_group_size - candidate.preferred_group_max),
        )
        group_score = max(40.0, 100.0 - distance * 15.0)

    target_style = personalization.preferred_style or context.requester.social_style
    if candidate.social_style == target_style:
        social_score = 100.0
    elif "balanced" in (candidate.social_style, target_style):
        social_score = 85.0
    else:
        social_score = 65.0

    factors = {
        "time": 100.0,
        "activity": round(activity_score, 1),
        "location": round(location_score, 1),
        "interest": round(interest_score, 1),
        "group_size": round(group_score, 1),
        "social_style": round(social_score, 1),
        "credit": float(max(0, min(candidate.credit_score, 100))),
    }
    score = sum(factors[name] * weight for name, weight in MATCH_WEIGHTS.items())

    if personalization.same_department:
        if context.requester.department and candidate.department == context.requester.department:
            score += 4
        else:
            score -= 8
    if personalization.same_grade:
        if context.requester.grade_year and candidate.grade_year == context.requester.grade_year:
            score += 3
        else:
            score -= 6

    explanation = [
        "活动时间无冲突",
        f"活动偏好 {int(activity_score)} 分",
        f"地点偏好 {int(location_score)} 分",
        f"信用分 {candidate.credit_score}",
    ]
    if personalization.same_department and candidate.department == context.requester.department:
        explanation.append("符合你提出的同院系偏好")
    if personalization.same_grade and candidate.grade_year == context.requester.grade_year:
        explanation.append("符合你提出的同年级偏好")
    if (
        personalization.preferred_style
        and candidate.social_style == personalization.preferred_style
    ):
        explanation.append("社交方式符合个性化需求")

    return ScoredCandidate(
        candidate_type="user",
        candidate_id=candidate.id,
        score=round(max(0.0, min(score, 100.0)), 1),
        factors=factors,
        explanation=explanation,
    )


def score_activity_candidate(
    context: MatchContext,
    activity: Activity,
    member_count: int,
) -> ScoredCandidate:
    overlap_seconds = max(
        0.0,
        (
            min(context.ends_at, activity.ends_at) - max(context.starts_at, activity.starts_at)
        ).total_seconds(),
    )
    request_seconds = max(1.0, (context.ends_at - context.starts_at).total_seconds())
    time_score = min(100.0, overlap_seconds / request_seconds * 100.0)
    requested_location = normalize(context.location)
    activity_location = normalize(activity.location)
    if requested_location == activity_location:
        location_score = 100.0
    elif requested_location in activity_location or activity_location in requested_location:
        location_score = 85.0
    else:
        location_score = 50.0
    available_slots = activity.capacity - member_count
    capacity_score = 100.0 if available_slots >= context.people_needed else 70.0
    score = time_score * 0.35 + 100.0 * 0.30 + location_score * 0.20 + capacity_score * 0.15
    factors = {
        "time": round(time_score, 1),
        "activity": 100.0,
        "location": round(location_score, 1),
        "capacity": capacity_score,
    }
    return ScoredCandidate(
        candidate_type="activity",
        candidate_id=activity.id,
        score=round(score, 1),
        factors=factors,
        explanation=[
            "已有同类活动可直接加入",
            f"时间重合度 {int(time_score)} 分",
            f"当前还有 {available_slots} 个名额",
        ],
    )
