from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Activity,
    ActivityMember,
    CreditEvent,
    Feedback,
    PeerFeedbackReview,
    SystemEvent,
    User,
    utcnow,
)

PERSONALITY_LABELS = {
    "quiet": "偏安静",
    "balanced": "收放自如",
    "outgoing": "偏外向",
    "patient": "有耐心",
    "talkative": "健谈",
    "focused": "专注",
    "easygoing": "好相处",
    "organized": "有条理",
}

INCIDENT_LABELS = {
    "punctual": "守时",
    "helpful": "乐于帮忙",
    "clear_communication": "沟通清楚",
    "late": "迟到",
    "cancelled": "临时取消",
    "no_show": "未到场",
    "unsafe_behavior": "存在安全风险",
}


@dataclass(slots=True)
class ModerationDecision:
    status: str
    requires_peer_review: bool
    authenticity: float
    malicious_risk: float
    reason: str
    reviewee_delta: int = 0
    reviewer_delta: int = 0


def calculate_leave_penalty(activity: Activity, now: datetime | None = None) -> tuple[int, str]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if current >= activity.ends_at:
        return 0, "活动已经结束，不能再退出；请完成活动评价。"
    remaining = activity.starts_at - current
    if remaining > timedelta(hours=24):
        return 0, "距离开始超过 24 小时，现在退出不会扣信用分。"
    if remaining > timedelta(hours=6):
        return 2, "距离开始不足 24 小时，退出将扣 2 分。"
    if remaining > timedelta(hours=1):
        return 5, "距离开始不足 6 小时，退出将扣 5 分。"
    if remaining.total_seconds() > 0:
        return 10, "距离开始不足 1 小时，退出将扣 10 分。"
    return 15, "活动已经开始，退出将扣 15 分。"


def feedback_base_delta(feedback: Feedback) -> int:
    attendance_delta = {
        "attended": 0,
        "cancelled_early": -1,
        "late_cancel": -3,
        "no_show": -7,
    }[feedback.attendance]
    rating_delta = {1: -2, 2: -1, 3: 0, 4: 1, 5: 2}[feedback.rating]
    positive_signal = 1 if {"punctual", "helpful"} & set(feedback.incident_tags) else 0
    return max(-10, min(3, attendance_delta + rating_delta + positive_signal))


async def _apply_credit(
    session: AsyncSession,
    user: User,
    requested_delta: int,
    reason: str,
    activity_id: str,
    feedback_id: str | None = None,
) -> int:
    old_score = user.credit_score
    user.credit_score = max(0, min(100, user.credit_score + requested_delta))
    actual_delta = user.credit_score - old_score
    if actual_delta:
        session.add(
            CreditEvent(
                user_id=user.id,
                delta=actual_delta,
                reason=reason,
                activity_id=activity_id,
                source_feedback_id=feedback_id,
            )
        )
    return actual_delta


async def eligible_peer_reviewer_ids(
    session: AsyncSession,
    feedback: Feedback,
) -> list[str]:
    statement = select(ActivityMember.user_id).where(
        ActivityMember.activity_id == feedback.activity_id,
        ActivityMember.status == "confirmed",
        ActivityMember.user_id.not_in([feedback.reviewer_id, feedback.reviewee_id]),
    )
    return list((await session.scalars(statement)).all())


async def moderate_feedback(
    session: AsyncSession,
    feedback: Feedback,
    reviewer: User,
    reviewee: User,
) -> ModerationDecision:
    comment = (feedback.comment or "").strip()
    severe = feedback.attendance == "no_show" or bool(
        {"no_show", "unsafe_behavior"} & set(feedback.incident_tags)
    )
    extreme_without_detail = feedback.rating <= 2 and len(comment) < 8

    prior_feedback = list(
        (
            await session.scalars(
                select(Feedback).where(
                    Feedback.activity_id == feedback.activity_id,
                    Feedback.reviewee_id == feedback.reviewee_id,
                    Feedback.id != feedback.id,
                )
            )
        ).all()
    )
    conflict = any(
        abs(item.rating - feedback.rating) >= 3
        or {item.attendance, feedback.attendance} == {"attended", "no_show"}
        for item in prior_feedback
    )

    authenticity = 0.48
    authenticity += 0.14 if len(comment) >= 8 else 0
    authenticity += 0.1 if feedback.personality_tags or feedback.incident_tags else 0
    authenticity += 0.08 if reviewer.credit_score >= 90 else 0
    authenticity -= 0.22 if conflict else 0
    authenticity = round(max(0.05, min(0.95, authenticity)), 2)

    abusive_terms = ("垃圾", "恶心", "傻", "滚", "报复", "去死")
    malicious_risk = 0.05
    malicious_risk += 0.3 if any(term in comment for term in abusive_terms) else 0
    malicious_risk += 0.24 if conflict else 0
    malicious_risk += 0.18 if extreme_without_detail else 0
    malicious_risk = round(min(0.95, malicious_risk), 2)

    peer_ids = await eligible_peer_reviewer_ids(session, feedback)
    needs_peer_review = bool(peer_ids) and (
        severe
        or conflict
        or extreme_without_detail
        or malicious_risk >= 0.35
        or authenticity < 0.5
    )
    if needs_peer_review:
        reasons = []
        if severe:
            reasons.append("评价涉及爽约或安全等高影响情况")
        if conflict:
            reasons.append("同场评价出现明显矛盾")
        if extreme_without_detail:
            reasons.append("评分较极端但说明较少")
        return ModerationDecision(
            status="needs_peer_review",
            requires_peer_review=True,
            authenticity=authenticity,
            malicious_risk=malicious_risk,
            reason="；".join(reasons) + "，已请同场第三位参与者帮助核实。",
        )

    requested_reviewee_delta = feedback_base_delta(feedback)
    status = "finalized"
    reason = "评价内容与结构化选项基本一致，审核 Agent 已完成结算。"
    reviewer_delta = 1
    if severe and not peer_ids:
        requested_reviewee_delta = max(-1, min(1, requested_reviewee_delta))
        reviewer_delta = 0
        status = "finalized_limited_evidence"
        reason = "评价影响较大，但本场没有合适的第三位参与者；Agent 已按证据不足保守处理。"
    elif malicious_risk >= 0.35:
        requested_reviewee_delta = max(-1, min(1, requested_reviewee_delta))
        reviewer_delta = 0
        status = "finalized_cautious"
        reason = "评价可能带有情绪或证据不足，Agent 仅作保守调整。"

    reviewee_delta = await _apply_credit(
        session,
        reviewee,
        requested_reviewee_delta,
        f"feedback_{feedback.attendance}",
        feedback.activity_id,
        feedback.id,
    )
    actual_reviewer_delta = await _apply_credit(
        session,
        reviewer,
        reviewer_delta,
        "submitted_verified_feedback",
        feedback.activity_id,
        feedback.id,
    )
    return ModerationDecision(
        status=status,
        requires_peer_review=False,
        authenticity=authenticity,
        malicious_risk=malicious_risk,
        reason=reason,
        reviewee_delta=reviewee_delta,
        reviewer_delta=actual_reviewer_delta,
    )


async def apply_moderation_decision(
    session: AsyncSession,
    feedback: Feedback,
    decision: ModerationDecision,
) -> None:
    feedback.moderation_status = decision.status
    feedback.requires_peer_review = decision.requires_peer_review
    feedback.ai_authenticity = decision.authenticity
    feedback.ai_malicious_risk = decision.malicious_risk
    feedback.ai_reason = decision.reason
    feedback.reviewee_credit_delta = decision.reviewee_delta
    feedback.reviewer_credit_delta = decision.reviewer_delta
    if not decision.requires_peer_review:
        feedback.finalized_at = utcnow()
    session.add(
        SystemEvent(
            category="feedback_review",
            status="attention" if decision.requires_peer_review else "success",
            title="评价审核 Agent 已完成初审",
            message=decision.reason,
            details={
                "activity_id": feedback.activity_id,
                "feedback_id": feedback.id,
                "moderation_status": decision.status,
                "requires_peer_review": decision.requires_peer_review,
            },
        )
    )


async def finalize_feedback_with_peer_review(
    session: AsyncSession,
    feedback: Feedback,
    peer_review: PeerFeedbackReview,
    original_reviewer: User,
    reviewee: User,
    peer_reviewer: User,
) -> str:
    multiplier = {
        "mostly_true": 1.0,
        "partly_true": 0.5,
        "not_sure": 0.2,
        "mostly_false": 0.0,
    }[peer_review.verdict]
    requested = round(feedback_base_delta(feedback) * multiplier)
    reviewee_delta = await _apply_credit(
        session,
        reviewee,
        requested,
        f"peer_verified_{feedback.attendance}",
        feedback.activity_id,
        feedback.id,
    )
    if peer_review.verdict == "mostly_false" and feedback_base_delta(feedback) <= -3:
        original_delta = await _apply_credit(
            session,
            original_reviewer,
            -3,
            "unsupported_serious_feedback",
            feedback.activity_id,
            feedback.id,
        )
        summary = "第三方认为主要指控不真实；被评价人未被扣分，原评价人因不实严重评价被扣分。"
    elif peer_review.verdict == "not_sure":
        original_delta = 0
        summary = "第三方也无法确认，Agent 仅按低置信度做轻微或不调整。"
    else:
        original_delta = await _apply_credit(
            session,
            original_reviewer,
            1,
            "peer_supported_feedback",
            feedback.activity_id,
            feedback.id,
        )
        summary = "第三方复核支持了全部或部分内容，Agent 已按可信程度完成结算。"

    peer_delta = await _apply_credit(
        session,
        peer_reviewer,
        1,
        "completed_peer_review",
        feedback.activity_id,
        feedback.id,
    )
    peer_review.reviewer_credit_delta = peer_delta
    feedback.moderation_status = "finalized_after_peer_review"
    feedback.reviewee_credit_delta = reviewee_delta
    feedback.reviewer_credit_delta = original_delta
    feedback.ai_reason = summary
    feedback.finalized_at = utcnow()
    session.add(
        SystemEvent(
            category="feedback_review",
            status="success",
            title="第三方复核已完成",
            message=summary,
            details={
                "activity_id": feedback.activity_id,
                "feedback_id": feedback.id,
                "verdict": peer_review.verdict,
            },
        )
    )
    return summary


async def refresh_hidden_profile(session: AsyncSession, user: User) -> None:
    feedback_rows = list(
        (
            await session.scalars(
                select(Feedback).where(
                    Feedback.reviewee_id == user.id,
                    Feedback.moderation_status.like("finalized%"),
                )
            )
        ).all()
    )
    rejected_feedback_ids = set(
        (
            await session.scalars(
                select(PeerFeedbackReview.feedback_id).where(
                    PeerFeedbackReview.verdict == "mostly_false"
                )
            )
        ).all()
    )
    feedback_rows = [item for item in feedback_rows if item.id not in rejected_feedback_ids]
    activity_rows = list(
        (
            await session.scalars(
                select(Activity)
                .join(ActivityMember, ActivityMember.activity_id == Activity.id)
                .where(
                    ActivityMember.user_id == user.id,
                    ActivityMember.status == "confirmed",
                    Activity.ends_at <= utcnow(),
                )
            )
        ).all()
    )
    personality_counts: Counter[str] = Counter()
    incident_counts: Counter[str] = Counter()
    attendance_counts: Counter[str] = Counter()
    category_counts = Counter(activity.category for activity in activity_rows)
    for item in feedback_rows:
        personality_counts.update(item.personality_tags)
        incident_counts.update(item.incident_tags)
        attendance_counts.update([item.attendance])

    ratings = [item.rating for item in feedback_rows]
    average_rating = round(sum(ratings) / len(ratings), 2) if ratings else None
    leading_traits = [
        {"key": key, "label": PERSONALITY_LABELS.get(key, key), "count": count}
        for key, count in personality_counts.most_common(4)
    ]
    leading_categories = [
        {"category": key, "count": count} for key, count in category_counts.most_common(5)
    ]
    positive_count = sum(incident_counts[key] for key in ("punctual", "helpful"))
    risk_count = sum(incident_counts[key] for key in ("late", "cancelled", "no_show"))
    review_integrity_events = list(
        (
            await session.scalars(
                select(CreditEvent.reason).where(
                    CreditEvent.user_id == user.id,
                    CreditEvent.reason.in_(
                        ["unsupported_serious_feedback", "peer_supported_feedback"]
                    ),
                )
            )
        ).all()
    )
    review_integrity = Counter(review_integrity_events)
    summary_parts = []
    if leading_categories:
        summary_parts.append(f"常参与{leading_categories[0]['category']}类活动")
    if leading_traits:
        summary_parts.append(f"多人印象偏{leading_traits[0]['label']}")
    if positive_count > risk_count:
        summary_parts.append("履约表现整体稳定")
    elif risk_count:
        summary_parts.append("履约稳定性仍需观察")

    user.hidden_profile = {
        "version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "completed_activity_count": len(activity_rows),
        "finalized_feedback_count": len(feedback_rows),
        "average_rating": average_rating,
        "activity_signals": leading_categories,
        "personality_signals": leading_traits,
        "attendance_signals": dict(attendance_counts),
        "incident_signals": dict(incident_counts),
        "review_integrity": {
            "supported_feedback_count": review_integrity["peer_supported_feedback"],
            "unsupported_serious_feedback_count": review_integrity[
                "unsupported_serious_feedback"
            ],
        },
        "summary": "；".join(summary_parts) or "数据还不够，Agent 会在更多活动后继续学习。",
    }
    user.hidden_profile_updated_at = utcnow()


async def process_completed_activities(
    session: AsyncSession,
    now: datetime | None = None,
) -> int:
    current = (now or utcnow()).astimezone(UTC)
    activities = list(
        (
            await session.scalars(
                select(Activity).where(
                    Activity.ends_at <= current,
                    Activity.status.in_(["open", "formed"]),
                )
            )
        ).all()
    )
    for activity in activities:
        activity.status = "completed"
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
        for user_id in member_ids:
            user = await session.get(User, user_id)
            if user is not None:
                await refresh_hidden_profile(session, user)
        activity.post_activity_processed_at = current
        session.add(
            SystemEvent(
                category="post_activity_agent",
                status="success",
                title="活动结束，画像 Agent 已完成复盘",
                message=f"“{activity.title}”已结束，系统已更新参与者的隐藏画像并开放互评。",
                details={"activity_id": activity.id, "member_count": len(member_ids)},
            )
        )
    return len(activities)
