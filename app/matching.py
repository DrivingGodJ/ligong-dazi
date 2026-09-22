from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Activity, ActivityMember, User, UserBlock, utcnow

ACTIVITY_START_TOLERANCE = timedelta(hours=2)

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
    same_gender_only: bool = False


@dataclass(slots=True)
class Personalization:
    min_credit: int = 0
    same_department: bool = False
    same_grade: bool = False
    preferred_style: str | None = None
    wants_mentor: bool = False
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


def normalize_location(value: str | None) -> str:
    """Normalize common campus address variants without requiring one exact format."""

    normalized = normalize(value)
    normalized = re.sub(r"[，,。.;；:：·\-_/（）()]+", "", normalized)
    for prefix in ("南京理工大学", "南理工大学", "南理工"):
        normalized = normalized.replace(prefix, "")
    replacements = {
        "南区体育馆": "南体",
        "北区体育馆": "北体",
        "江阴校区体育馆": "江阴体",
        "大学生活动中心": "大活",
        "学生活动中心": "大活",
        "图书馆": "图书馆",
        "体育馆": "体",
        "教学楼": "教",
        "校区": "",
    }
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    normalized = re.sub(r"(第?[一二三四五六七八九十0-9]+)(层|楼)", r"\1楼", normalized)
    return normalized


def location_similarity(left: str | None, right: str | None) -> float:
    """Return a forgiving 0..1 similarity for differently formatted campus addresses."""

    a = normalize_location(left)
    b = normalize_location(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.9

    sequence_score = SequenceMatcher(None, a, b).ratio()
    a_pairs = {a[index : index + 2] for index in range(max(1, len(a) - 1))}
    b_pairs = {b[index : index + 2] for index in range(max(1, len(b) - 1))}
    pair_score = len(a_pairs & b_pairs) / max(1, len(a_pairs | b_pairs))
    landmark_bonus = 0.0
    for landmark in ("南体", "北体", "江阴体", "图书馆", "大活"):
        if landmark in a and landmark in b:
            landmark_bonus = 0.2
            break
    return round(min(1.0, max(sequence_score, pair_score) + landmark_bonus), 3)


def location_match_score(requested: str | None, candidate: str | None) -> float:
    similarity = location_similarity(requested, candidate)
    if similarity >= 0.95:
        return 100.0
    if similarity >= 0.75:
        return 90.0
    if similarity >= 0.5:
        return 78.0
    if similarity >= 0.3:
        return 65.0
    return 50.0


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

    if any(
        term in normalized
        for term in ("大佬带", "带带我", "求带", "带新手", "新手求带", "我是小白")
    ):
        result.wants_mentor = True
        result.applied.append("优先匹配该项目熟练度较高的搭子")

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
    # A request to create a same-gender-only activity must not join an existing
    # activity whose membership rules the requester cannot control.
    if context.same_gender_only:
        return []
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
            Activity.id.not_in(
                select(ActivityMember.activity_id).where(
                    ActivityMember.user_id == context.requester.id,
                    ActivityMember.status == "confirmed",
                )
            ),
            Activity.status == "open",
            Activity.category == context.category,
            Activity.starts_at > utcnow(),
            Activity.starts_at >= context.starts_at - ACTIVITY_START_TOLERANCE,
            Activity.starts_at <= context.starts_at + ACTIVITY_START_TOLERANCE,
            (Activity.capacity - func.coalesce(member_count.c.member_count, 0)) >= 1,
            or_(
                Activity.same_gender_only.is_(False),
                Activity.owner_id.in_(
                    select(User.id).where(User.gender == context.requester.gender)
                ),
            ),
        )
    )
    rows = [(row[0], int(row[1])) for row in (await session.execute(statement)).all()]
    rows.sort(
        key=lambda row: (
            abs((row[0].starts_at - context.starts_at).total_seconds()),
            row[0].starts_at,
        )
    )
    return rows[: max(1, min(limit, 50))]


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
    if context.same_gender_only:
        conditions.append(User.gender == context.requester.gender)

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
    learned_profile = candidate.hidden_profile or {}
    learned_categories = {
        normalize(str(item.get("category", "")))
        for item in learned_profile.get("activity_signals", [])
        if isinstance(item, dict)
    }
    candidate_skill = next(
        (
            item
            for item in (candidate.hobby_skills or [])
            if isinstance(item, dict)
            and normalize(str(item.get("name", ""))) == normalize(context.category)
        ),
        None,
    )
    candidate_skill_level = int(candidate_skill.get("level", 0)) if candidate_skill else 0

    activity_score = 100.0 if normalize(context.category) in candidate_interests else 75.0
    if context.category in candidate.interests:
        activity_score = 100.0
    learned_activity_match = normalize(context.category) in learned_categories
    if learned_activity_match:
        activity_score = min(100.0, activity_score + 10.0)

    preferred_location_scores = [
        location_match_score(context.location, item) for item in candidate.preferred_locations
    ]
    location_score = max(preferred_location_scores, default=50.0)
    if not context.location:
        location_score = 65.0
    if candidate.campus and normalize(candidate.campus) in normalize(context.location):
        location_score = max(location_score, 75.0)

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
    learned_traits = learned_profile.get("personality_signals", [])
    learned_style = next(
        (
            str(item.get("key"))
            for item in learned_traits
            if isinstance(item, dict) and item.get("key") in {"quiet", "balanced", "outgoing"}
        ),
        None,
    )
    effective_style = learned_style or candidate.social_style
    if effective_style == target_style:
        social_score = 100.0
    elif "balanced" in (effective_style, target_style):
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
    if personalization.wants_mentor:
        if candidate_skill_level >= 4:
            score += 8
        elif candidate_skill_level == 3:
            score += 3
        elif candidate_skill_level:
            score -= 4

    explanation = [
        "活动时间无冲突",
        f"活动偏好 {int(activity_score)} 分",
        f"地点偏好 {int(location_score)} 分" if context.location else "尚未指定活动地点",
        f"信用分 {candidate.credit_score}",
    ]
    if personalization.same_department and candidate.department == context.requester.department:
        explanation.append("符合你提出的同院系偏好")
    if personalization.same_grade and candidate.grade_year == context.requester.grade_year:
        explanation.append("符合你提出的同年级偏好")
    if (
        personalization.preferred_style
        and effective_style == personalization.preferred_style
    ):
        explanation.append("社交方式符合个性化需求")
    if learned_activity_match:
        explanation.append("过往活动习惯与本次需求相符")
    if location_score >= 78:
        explanation.append("地点名称虽可能写法不同，但位置高度相近")
    if personalization.wants_mentor and candidate_skill_level:
        skill_label = {1: "小白", 2: "入门", 3: "熟练", 4: "擅长", 5: "精通"}.get(
            candidate_skill_level, "已填写"
        )
        explanation.append(f"{context.category}自评为{skill_label}，已用于带新手偏好")

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
    start_shift = (activity.starts_at - context.starts_at).total_seconds()
    start_shift_minutes = round(abs(start_shift) / 60)
    if overlap_seconds:
        time_score = max(
            0.0,
            40.0 + 60.0 * overlap_seconds / request_seconds
            - min(20.0, abs(start_shift) / 360),
        )
    else:
        gap_seconds = max(
            (activity.starts_at - context.ends_at).total_seconds(),
            (context.starts_at - activity.ends_at).total_seconds(),
            0.0,
        )
        time_score = max(10.0, 40.0 - gap_seconds / 240)
    time_score = min(100.0, time_score)
    location_score = (
        location_match_score(context.location, activity.location)
        if context.location else 65.0
    )
    available_slots = activity.capacity - member_count
    capacity_score = 100.0 if available_slots >= context.people_needed else 70.0
    score = time_score * 0.35 + 100.0 * 0.30 + location_score * 0.20 + capacity_score * 0.15
    factors = {
        "time": round(time_score, 1),
        "activity": 100.0,
        "location": round(location_score, 1),
        "capacity": capacity_score,
    }
    if start_shift_minutes:
        direction = "早" if start_shift < 0 else "晚"
        time_explanation = (
            f"开始时间比你填写的{direction} {start_shift_minutes} 分钟，"
            "请核对实际时间"
        )
    elif activity.ends_at != context.ends_at:
        time_explanation = "结束时间与你填写的不同，请核对实际时间"
    else:
        time_explanation = "活动时间与你填写的一致"
    return ScoredCandidate(
        candidate_type="activity",
        candidate_id=activity.id,
        score=round(score, 1),
        factors=factors,
        explanation=[
            "已有同类活动可直接加入",
            time_explanation,
            f"地点相近度 {int(location_score)} 分" if context.location else "可参考现有活动地点",
            f"当前还有 {available_slots} 个名额",
        ],
    )
