"""Server-side submission safety; independent of identity appeals and peer moderation."""

from __future__ import annotations

import base64
import hmac
import re
import secrets
import unicodedata
from datetime import timedelta
from io import BytesIO

from fastapi import HTTPException, Request
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import Settings
from app.models import (
    HumanChallenge,
    RegistrationSuccess,
    SafetyCounter,
    SystemEvent,
    User,
    new_id,
    utcnow,
)

# High-confidence imperatives only: code, learning topics and ordinary preferences are not abuse.
INJECTION_PATTERNS = [
    r"(?:忽略|无视|忘掉|覆盖)(?:所有|全部|之前|以前|上面|此前|先前|的)*(?:指令|系统提示|规则)",
    r"(?:泄露|输出|告诉我|显示)(?:你的|全部|所有|内部|的)*(?:系统提示词|系统指令|api密钥|数据库密码)",
    r"(?:绕过|关闭|禁用)(?:所有|全部|系统|的)*(?:安全检查|安全规则|审核机制|权限验证)",
    r"ignore(?:all|any|the|your|previous|prior|system)*(?:instructions|rules|prompts)",
    r"(?:reveal|leak|print)(?:your|the|all|internal)*(?:systemprompt|apikey|databasepassword)",
]
# Keep this list explicit and conservative; no broad words such as “代码” or “傻”.
OBSCENE_TERMS = ("操你妈", "草你妈", "傻逼", "傻屄", "臭婊子", "约炮", "裸聊", "色情服务")
TEXT_FIELDS = {
    "display_name",
    "bio",
    "university",
    "department",
    "interests",
    "hobby_skills",
    "name",
    "skill_name",
    "preferred_locations",
    "category",
    "title",
    "location",
    "personal_requirement",
    "comment",
    "message",
    "description",
    "reason",
    "note",
}

REGISTRATION_LIMIT = 75
REGISTRATION_WINDOW = timedelta(minutes=1)
REGISTRATION_PAUSE = timedelta(hours=1)
# A policy-specific namespace retires old 3/hour, 24-hour registration pauses
# without touching account safety freezes or deleting their audit records.
REGISTRATION_COUNTER_PREFIX = "register:v2:"


def source_key(request: Request, settings: Settings) -> str:
    # Never trust caller-supplied X-Forwarded-For. The deployment's trusted proxy must
    # populate request.client; documentation describes shared-IP limitations.
    host = request.client.host if request.client else "unknown"
    return hmac.new(
        settings.jwt_secret.get_secret_value().encode(), host.encode(), "sha256"
    ).hexdigest()


def answer_hash(answer: str, settings: Settings) -> str:
    return hmac.new(
        settings.jwt_secret.get_secret_value().encode(), answer.encode(), "sha256"
    ).hexdigest()


async def issue_challenge(session: AsyncSession, request: Request, settings: Settings) -> dict:
    if not settings.human_verification_enabled:
        return {"enabled": False}
    now = utcnow()
    key = source_key(request, settings)
    await session.execute(delete(HumanChallenge).where(HumanChallenge.expires_at <= now))
    # Bound storage without invalidating another student on the same campus network.
    older = (
        select(HumanChallenge.id)
        .where(HumanChallenge.source_key == key)
        .order_by(HumanChallenge.expires_at.desc())
        .offset(19)
    )
    await session.execute(delete(HumanChallenge).where(HumanChallenge.id.in_(older)))
    answer = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
    challenge_id = new_id()
    expires = now + timedelta(minutes=5)
    session.add(
        HumanChallenge(
            id=challenge_id,
            source_key=key,
            answer_hash=answer_hash(answer, settings),
            expires_at=expires,
        )
    )
    canvas = Image.new("RGB", (240, 72), "#f4edff")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=34)
    for i, char in enumerate(answer):
        draw.text((16 + i * 35, 13 + secrets.randbelow(12)), char, font=font, fill="#542b8d")
    for _ in range(7):
        draw.line(
            [
                (secrets.randbelow(240), secrets.randbelow(72)),
                (secrets.randbelow(240), secrets.randbelow(72)),
            ],
            fill="#aa89d6",
            width=1,
        )
    buffer = BytesIO()
    canvas.save(buffer, format="PNG")
    await session.commit()
    return {
        "enabled": True,
        "challenge_id": challenge_id,
        "expires_at": expires.isoformat(),
        "image": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(),
    }


async def verify_challenge(
    session: AsyncSession,
    request: Request,
    settings: Settings,
    challenge_id: str | None,
    answer: str | None,
) -> None:
    if not settings.human_verification_enabled:
        return
    challenge = await session.scalar(
        delete(HumanChallenge)
        .where(
            HumanChallenge.id == challenge_id,
            HumanChallenge.source_key == source_key(request, settings),
        )
        .returning(HumanChallenge)
    )
    # Consume exactly once, including wrong answers and subsequent credential failures.
    await session.commit()
    if (
        challenge is None
        or challenge.expires_at <= utcnow()
        or not hmac.compare_digest(
            challenge.answer_hash, answer_hash((answer or "").strip().upper(), settings)
        )
    ):
        raise HTTPException(
            422,
            detail={
                "code": "human_verification_failed",
                "message": "验证码不正确或已失效，请换一张后重试。",
            },
        )


async def increment_counter(session: AsyncSession, key: str, window: timedelta) -> SafetyCounter:
    now = utcnow()
    table = SafetyCounter.__table__
    insert = sqlite_insert if session.bind.dialect.name == "sqlite" else pg_insert
    stmt = insert(table).values(key=key, count=1, window_started_at=now)
    stale = table.c.window_started_at <= now - window
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[table.c.key],
            set_={
                "count": case((stale, 1), else_=table.c.count + 1),
                "window_started_at": case((stale, now), else_=table.c.window_started_at),
            },
        )
    )
    return await session.scalar(
        select(SafetyCounter)
        .where(SafetyCounter.key == key)
        .execution_options(populate_existing=True)
    )


async def ensure_not_blocked(session: AsyncSession, key: str) -> None:
    counter = await session.get(SafetyCounter, key)
    if counter and counter.blocked_until and counter.blocked_until > utcnow():
        raise HTTPException(
            423,
            detail={
                "code": "safety_frozen",
                "message": "因违反安全规则，暂时无法操作，请在冻结到期后重试。",
                "blocked_until": counter.blocked_until.isoformat(),
            },
        )


async def reserve_registration(session: AsyncSession, request: Request, settings: Settings) -> None:
    if not settings.registration_guard_enabled:
        return
    source = source_key(request, settings)
    key = REGISTRATION_COUNTER_PREFIX + source
    # Lock first, then read the pause and receipts. Concurrent requests must see
    # the pause committed by the 75th successful registration, not a stale read.
    counter = await increment_counter(session, key, REGISTRATION_WINDOW)
    now = utcnow()
    if counter.blocked_until and counter.blocked_until > now:
        raise HTTPException(
            429,
            detail={
                "code": "registration_frozen",
                "message": "当前网络注册过于频繁，注册暂停中；已有账号仍可登录。",
                "blocked_until": counter.blocked_until.isoformat(),
            },
        )
    # A rolling minute prevents bursts across fixed clock-minute boundaries.
    cutoff = now - REGISTRATION_WINDOW
    recent_count = await session.scalar(
        select(func.count())
        .select_from(RegistrationSuccess)
        .where(
            RegistrationSuccess.source_key == source,
            RegistrationSuccess.created_at > cutoff,
        )
    )
    if recent_count + 1 >= REGISTRATION_LIMIT:
        counter.blocked_until = now + REGISTRATION_PAUSE
        session.add(
            SystemEvent(
                category="submission_safety",
                status="attention",
                title="频繁注册来源已冻结",
                message="同来源一分钟内已成功注册75个账号，后续注册暂停1小时。",
                details={
                    "source_key": key,
                    "blocked_until": counter.blocked_until.isoformat(),
                    "window_seconds": 60,
                    "successful_registration_limit": REGISTRATION_LIMIT,
                },
            )
        )
    if recent_count >= REGISTRATION_LIMIT:
        # Defensive fallback for pre-existing receipts with no matching counter.
        await session.commit()
        raise HTTPException(
            429,
            detail={
                "code": "registration_frozen",
                "message": "当前网络注册过于频繁，注册已暂停 1 小时；已有账号仍可登录。",
                "blocked_until": counter.blocked_until.isoformat(),
            },
        )
    # The 75th account succeeds and commits its receipt, pause and event together.
    # Duplicate/failed registrations roll back all of them and consume nothing.
    await session.execute(
        delete(RegistrationSuccess).where(RegistrationSuccess.created_at <= cutoff)
    )
    session.add(RegistrationSuccess(source_key=source, created_at=now))


def normalized_text(text: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFKC", text).casefold()
        if not c.isspace() and unicodedata.category(c) != "Cf"
    )


def inspect_text(text: str) -> tuple[bool, str]:
    normalized = normalized_text(text)
    # Only an immediately negated imperative is exempt. Appending “学习/讨论” to
    # a real attack must not turn the whole submission into an allowed one.
    malicious = any(
        not re.search(r"(?:不要|不能|不应|不得|禁止|donot|mustnot)$", normalized[: match.start()])
        for pattern in INJECTION_PATTERNS
        for match in re.finditer(pattern, normalized)
    )
    if not any(term in normalized for term in OBSCENE_TERMS):
        return malicious, text
    # Normalization is for detection only: preserve the student's punctuation
    # and all unrelated characters when removing offensive words.
    original_indexes = []
    for index, char in enumerate(text):
        for _normalized_char in normalized_text(char):
            original_indexes.append(index)
    removed_indexes = set()
    for term in OBSCENE_TERMS:
        for match in re.finditer(re.escape(term), normalized):
            removed_indexes.update(
                range(original_indexes[match.start()], original_indexes[match.end() - 1] + 1)
            )
    cleaned = "".join(char for index, char in enumerate(text) if index not in removed_indexes)
    return malicious, cleaned


async def check_submission(session: AsyncSession, fields: dict, user: User | None = None) -> None:
    malicious = False
    sanitized = {}

    def visit(value):
        nonlocal malicious
        if isinstance(value, str):
            bad, cleaned = inspect_text(value)
            malicious |= bad
            return cleaned
        if isinstance(value, list):
            return [visit(item) for item in value]
        if isinstance(value, dict):
            return {key: visit(item) if key in TEXT_FIELDS else item for key, item in value.items()}
        return value

    for key, value in fields.items():
        if key not in TEXT_FIELDS:
            continue
        cleaned = visit(value)
        if cleaned != value:
            sanitized[key] = cleaned
    if not malicious and not sanitized:
        return
    until = None
    count = 0
    if malicious and user:
        counter = await increment_counter(session, "user:" + user.id, timedelta(hours=24))
        count = counter.count
        if count >= 2:
            until = utcnow() + timedelta(hours=24 if count == 2 else 24 * 7)
            # Never shorten a restriction created by another concurrent request.
            await session.execute(
                update(SafetyCounter)
                .where(SafetyCounter.key == counter.key)
                .values(
                    blocked_until=case(
                        (SafetyCounter.blocked_until > until, SafetyCounter.blocked_until),
                        else_=until,
                    )
                )
            )
    session.add(
        SystemEvent(
            category="submission_safety",
            status="attention",
            title="恶意指令已拦截" if malicious else "低俗内容已拦截",
            message="本次未保存或生成；日志不保存原始违规文本。",
            details={
                "user_id": user.id if user else None,
                "violation_count": count,
                "fields": list(sanitized),
                "blocked_until": until.isoformat() if until else None,
            },
        )
    )
    await session.commit()
    raise HTTPException(
        423 if until else 422,
        detail={
            "code": "malicious_instruction" if malicious else "unsafe_content",
            "message": (
                "检测到干扰系统的恶意指令，本次未生成。"
                + (
                    "账号已冻结 24 小时。"
                    if count == 2
                    else "账号已冻结 7 天。"
                    if count >= 3
                    else "请勿再次提交。"
                )
            )
            if malicious
            else "已清理本次提交中的低俗词句，本次未保存或生成，请检查后重试。",
            "sanitized_fields": sanitized,
            "blocked_until": until.isoformat() if until else None,
        },
    )
