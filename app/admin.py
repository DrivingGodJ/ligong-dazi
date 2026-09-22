from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import tempfile
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import DatabaseRuntime, Settings
from app.models import (
    AgentRun,
    Invitation,
    MatchRequest,
    StudentIdAppeal,
    SystemEvent,
    User,
    utcnow,
)
from app.schemas import (
    AdminAIConfigPublic,
    AdminAIConfigUpdate,
    AdminAIConnectionTestResponse,
    AdminLogItem,
    AdminLogStep,
    AdminOverview,
    StudentIdAppealPublic,
)

router = APIRouter(prefix="/api/v1/admin", tags=["管理后台"])
ADMIN_COOKIE = "dazi_admin_session"
ADMIN_SESSION_SECONDS = 2 * 60 * 60
CONFIG_KEYS = {
    "DAZI_AI_PROVIDER",
    "DAZI_AI_VENDOR",
    "DAZI_AI_API_KEY",
    "DAZI_AI_BASE_URL",
    "DAZI_AI_MODEL",
    "DAZI_AI_FALLBACK_ENABLED",
}


class AdminLogin(BaseModel):
    password: SecretStr


PROVIDER_PRESETS = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-flash",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4.1-mini",
    },
    "custom": {
        "label": "其他兼容服务",
        "base_url": "",
        "default_model": "",
    },
}


@dataclass(frozen=True)
class ResolvedAIConfig:
    vendor: str
    provider_label: str
    base_url: str
    model: str
    api_key: str
    fallback_enabled: bool


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    runtime: DatabaseRuntime = request.app.state.database
    async with runtime.session_factory() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _check_same_origin(request: Request) -> None:
    origin = request.headers.get("origin", "")
    parsed = urlparse(origin)
    if parsed.scheme != "https" or parsed.netloc != request.headers.get("host", ""):
        raise HTTPException(status_code=403, detail="请从管理后台页面操作，不要从外部页面提交")


def require_admin(request: Request, settings: SettingsDep) -> None:
    if settings.environment == "production":
        if request.method not in {"GET", "HEAD"}:
            _check_same_origin(request)
        token = request.cookies.get(ADMIN_COOKIE, "")
        try:
            payload = jwt.decode(
                token,
                settings.jwt_secret.get_secret_value(),
                algorithms=["HS256"],
                options={"require": ["sub", "exp", "iat"]},
            )
        except jwt.PyJWTError as error:
            raise HTTPException(status_code=401, detail="请先登录管理后台") from error
        if payload.get("typ") != "admin" or payload.get("sub") != "admin":
            raise HTTPException(status_code=401, detail="请先登录管理后台")
        return
    client_host = request.client.host if request.client else ""
    if client_host not in {"127.0.0.1", "::1", "localhost", "test", "testserver"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="管理后台只允许从运行服务的这台电脑访问",
        )


@router.get("/session", dependencies=[Depends(require_admin)])
async def read_admin_session(settings: SettingsDep) -> dict[str, bool]:
    return {"authenticated": True, "requires_login": settings.environment == "production"}


@router.post("/session")
async def create_admin_session(
    payload: AdminLogin,
    request: Request,
    response: Response,
    settings: SettingsDep,
) -> dict[str, bool]:
    if settings.environment != "production":
        raise HTTPException(status_code=404, detail="本地管理后台无需登录")
    _check_same_origin(request)
    client_host = request.client.host if request.client else "unknown"
    attempts: dict[str, list[float]] = request.app.state.admin_login_attempts
    now = time.monotonic()
    recent = [stamp for stamp in attempts.get(client_host, []) if now - stamp < 600]
    if len(recent) >= 10:
        raise HTTPException(status_code=429, detail="尝试次数较多，请 10 分钟后再试")
    submitted = hashlib.sha256(payload.password.get_secret_value().encode()).digest()
    expected = hashlib.sha256(settings.admin_password.get_secret_value().encode()).digest()
    if not hmac.compare_digest(submitted, expected):
        recent.append(now)
        attempts[client_host] = recent
        raise HTTPException(status_code=401, detail="管理员口令不正确")
    attempts.pop(client_host, None)
    issued = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "admin",
            "typ": "admin",
            "iat": issued,
            "exp": issued + timedelta(seconds=ADMIN_SESSION_SECONDS),
        },
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    response.set_cookie(
        ADMIN_COOKIE,
        token,
        max_age=ADMIN_SESSION_SECONDS,
        path="/api/v1/admin",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return {"authenticated": True}


@router.delete("/session", dependencies=[Depends(require_admin)])
async def delete_admin_session(response: Response) -> dict[str, bool]:
    response.delete_cookie(ADMIN_COOKIE, path="/api/v1/admin")
    return {"authenticated": False}


@router.get(
    "/student-id-appeals",
    response_model=list[StudentIdAppealPublic],
    dependencies=[Depends(require_admin)],
)
async def list_student_id_appeals(
    session: SessionDep,
    appeal_status: Annotated[
        Literal["pending", "handled", "all"], Query(alias="status")
    ] = "pending",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> list[StudentIdAppealPublic]:
    statement = select(StudentIdAppeal).order_by(StudentIdAppeal.created_at.desc())
    if appeal_status != "all":
        statement = statement.where(StudentIdAppeal.status == appeal_status)
    rows = list((await session.scalars(statement.offset(offset).limit(limit))).all())
    return [StudentIdAppealPublic.model_validate(row) for row in rows]


@router.post(
    "/student-id-appeals/{appeal_id}/handled",
    response_model=StudentIdAppealPublic,
    dependencies=[Depends(require_admin)],
)
async def mark_student_id_appeal_handled(
    appeal_id: str,
    session: SessionDep,
) -> StudentIdAppealPublic:
    appeal = await session.get(StudentIdAppeal, appeal_id)
    if appeal is None:
        raise HTTPException(status_code=404, detail="申诉记录不存在")
    if appeal.status != "handled":
        appeal.status = "handled"
        appeal.resolved_at = utcnow()
        await session.commit()
    return StudentIdAppealPublic.model_validate(appeal)


def load_persisted_ai_config(settings: Settings) -> None:
    path = Path(settings.config_file_path).expanduser().resolve()
    if not path.exists():
        return
    saved = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key.strip() in CONFIG_KEYS:
            saved[key.strip()] = value
    provider = saved.get("DAZI_AI_PROVIDER")
    vendor = saved.get("DAZI_AI_VENDOR")
    if provider is not None:
        if provider not in {"auto", "deterministic", "openai_compatible"}:
            raise ValueError("已保存的 AI 服务模式无效")
        settings.ai_provider = provider
    if vendor is not None:
        if vendor not in PROVIDER_PRESETS or vendor == "rules":
            raise ValueError("已保存的 AI 服务商无效")
        settings.ai_vendor = vendor
    if "DAZI_AI_API_KEY" in saved:
        key = saved["DAZI_AI_API_KEY"]
        settings.ai_api_key = SecretStr(key) if key else None
    if "DAZI_AI_BASE_URL" in saved:
        settings.ai_base_url = saved["DAZI_AI_BASE_URL"]
    if "DAZI_AI_MODEL" in saved:
        settings.ai_model = saved["DAZI_AI_MODEL"]
    if "DAZI_AI_FALLBACK_ENABLED" in saved:
        value = saved["DAZI_AI_FALLBACK_ENABLED"].lower()
        if value not in {"true", "false"}:
            raise ValueError("已保存的 AI 备用规则设置无效")
        settings.ai_fallback_enabled = value == "true"


def _selected_provider(settings: Settings) -> str:
    if settings.ai_provider == "deterministic":
        return "rules"
    return settings.ai_vendor


def _provider_label(provider: str) -> str:
    if provider == "rules":
        return "纯规则模式"
    return str(PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS["custom"])["label"])


def _has_key(settings: Settings) -> bool:
    return bool(settings.ai_api_key and settings.ai_api_key.get_secret_value().strip())


def present_config(settings: Settings) -> AdminAIConfigPublic:
    selected = _selected_provider(settings)
    has_key = _has_key(settings)
    if selected == "rules":
        status_message = "当前不调用外部 AI，系统会使用本地可解释规则完成匹配。"
        mode_label = "不使用 API"
    elif has_key:
        status_message = "密钥已安全保存，新的匹配请求会使用该 AI 服务。"
        mode_label = "AI Agent 已启用"
    else:
        status_message = "还没有可用密钥，请填写后保存。"
        mode_label = "等待填写密钥"
    return AdminAIConfigPublic(
        selected_provider=selected,
        saved_vendor=settings.ai_vendor,
        provider_label=_provider_label(selected),
        mode_label=mode_label,
        base_url=settings.ai_base_url,
        model=settings.ai_model,
        key_configured=has_key,
        fallback_enabled=settings.ai_fallback_enabled,
        can_test=selected == "rules" or has_key,
        status_message=status_message,
    )


def _normalized_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="接口地址需要以 http:// 或 https:// 开头",
        )
    if parsed.username or parsed.password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="接口地址中不要包含账号或密码",
        )
    return value.rstrip("/")


def _submitted_key(payload: AdminAIConfigUpdate) -> str:
    if payload.api_key is None:
        return ""
    value = payload.api_key.get_secret_value().strip()
    if "\n" in value or "\r" in value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="API Key 格式不正确",
        )
    return value


def resolve_ai_config(payload: AdminAIConfigUpdate, settings: Settings) -> ResolvedAIConfig:
    if payload.vendor == "rules":
        return ResolvedAIConfig(
            vendor=settings.ai_vendor,
            provider_label="纯规则模式",
            base_url=settings.ai_base_url,
            model=settings.ai_model,
            api_key=settings.ai_api_key.get_secret_value() if settings.ai_api_key else "",
            fallback_enabled=payload.fallback_enabled,
        )

    preset = PROVIDER_PRESETS[payload.vendor]
    base_url = str(preset["base_url"]) if payload.vendor != "custom" else payload.base_url
    model = payload.model or str(preset["default_model"])
    if not model:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="请填写模型名称",
        )
    base_url = _normalized_url(base_url)

    api_key = _submitted_key(payload)
    if not api_key and payload.vendor == settings.ai_vendor and settings.ai_api_key:
        api_key = settings.ai_api_key.get_secret_value().strip()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="请选择服务商并填写对应的 API Key",
        )
    return ResolvedAIConfig(
        vendor=payload.vendor,
        provider_label=str(preset["label"]),
        base_url=base_url,
        model=model,
        api_key=api_key,
        fallback_enabled=payload.fallback_enabled,
    )


def _write_env_file(path: Path, updates: dict[str, str]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(updates)
    rewritten: list[str] = []
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            rewritten.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            rewritten.append(f"{key}={remaining.pop(key)}")
        else:
            rewritten.append(line)
    if remaining and rewritten and rewritten[-1]:
        rewritten.append("")
    rewritten.extend(f"{key}={value}" for key, value in remaining.items())

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        temporary_path = Path(handle.name)
        handle.write("\n".join(rewritten).rstrip() + "\n")
    os.chmod(temporary_path, 0o600)
    os.replace(temporary_path, path)


def _plain_connection_error(response: httpx.Response) -> str:
    if response.status_code in {401, 403}:
        return "密钥没有通过验证，请确认它属于所选服务商，并且仍然有效。"
    if response.status_code == 404:
        return "服务地址或模型名称没有找到，请检查模型名称。"
    if response.status_code == 429:
        return "服务商提示额度不足或请求过多，请检查余额后稍后重试。"
    if response.status_code >= 500:
        return "服务商暂时没有正常响应，建议稍后再试；备用规则仍可继续工作。"
    return "服务商拒绝了这次测试，请检查接口地址、模型名称和账号权限。"


async def _record_event(
    session: AsyncSession,
    *,
    category: str,
    event_status: str,
    title: str,
    message: str,
    details: dict,
) -> None:
    session.add(
        SystemEvent(
            category=category,
            status=event_status,
            title=title,
            message=message,
            details=details,
        )
    )
    await session.commit()


@router.get(
    "/config",
    response_model=AdminAIConfigPublic,
    dependencies=[Depends(require_admin)],
)
async def read_ai_config(settings: SettingsDep) -> AdminAIConfigPublic:
    return present_config(settings)


@router.put(
    "/config",
    response_model=AdminAIConfigPublic,
    dependencies=[Depends(require_admin)],
)
async def update_ai_config(
    payload: AdminAIConfigUpdate,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> AdminAIConfigPublic:
    resolved = resolve_ai_config(payload, settings)
    provider_mode = "deterministic" if payload.vendor == "rules" else "openai_compatible"
    updates = {
        "DAZI_AI_PROVIDER": provider_mode,
        "DAZI_AI_VENDOR": resolved.vendor,
        "DAZI_AI_API_KEY": resolved.api_key,
        "DAZI_AI_BASE_URL": resolved.base_url,
        "DAZI_AI_MODEL": resolved.model,
        "DAZI_AI_FALLBACK_ENABLED": str(resolved.fallback_enabled).lower(),
    }
    lock: asyncio.Lock = request.app.state.admin_config_lock
    async with lock:
        await asyncio.to_thread(_write_env_file, Path(settings.config_file_path), updates)
        settings.ai_provider = provider_mode
        settings.ai_vendor = resolved.vendor
        settings.ai_api_key = SecretStr(resolved.api_key) if resolved.api_key else None
        settings.ai_base_url = resolved.base_url
        settings.ai_model = resolved.model
        settings.ai_fallback_enabled = resolved.fallback_enabled

    selected_label = _provider_label(payload.vendor)
    await _record_event(
        session,
        category="configuration",
        event_status="success",
        title="AI 服务设置已更新",
        message=f"系统已切换为{selected_label}，新的匹配请求会立即使用这项设置。",
        details={
            "provider": selected_label,
            "model": resolved.model if payload.vendor != "rules" else "本地规则",
            "fallback_enabled": resolved.fallback_enabled,
        },
    )
    return present_config(settings)


@router.post(
    "/config/test",
    response_model=AdminAIConnectionTestResponse,
    dependencies=[Depends(require_admin)],
)
async def test_ai_connection(
    payload: AdminAIConfigUpdate,
    session: SessionDep,
    settings: SettingsDep,
) -> AdminAIConnectionTestResponse:
    if payload.vendor == "rules":
        result = AdminAIConnectionTestResponse(
            ok=True,
            title="备用规则可以正常使用",
            message="这一模式不需要联网或 API Key，可以直接完成可解释匹配。",
            provider_label="纯规则模式",
            model=None,
            elapsed_ms=0,
        )
        await _record_event(
            session,
            category="connection_test",
            event_status="success",
            title=result.title,
            message=result.message,
            details={"provider": result.provider_label},
        )
        return result

    resolved = resolve_ai_config(payload, settings)
    started_at = time.perf_counter()
    ok = False
    message = ""
    try:
        async with httpx.AsyncClient(timeout=settings.ai_timeout_seconds) as client:
            response = await client.post(
                resolved.base_url.rstrip("/") + "/chat/completions",
                headers={
                    "Authorization": f"Bearer {resolved.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": resolved.model,
                    "messages": [{"role": "user", "content": "这是连接测试，请只回复：连接正常"}],
                    "max_tokens": 16,
                    "stream": False,
                },
            )
        if response.is_success:
            body = response.json()
            ok = bool(body.get("choices"))
            message = (
                "服务商已经正常回复，可以保存并用于搭子匹配。"
                if ok
                else "服务商返回了内容，但格式不符合预期，请确认它兼容聊天接口。"
            )
        else:
            message = _plain_connection_error(response)
    except httpx.TimeoutException:
        message = "等待服务商回复超时，请检查网络或稍后重试。"
    except httpx.RequestError:
        message = "无法连接到服务商，请检查接口地址和当前网络。"
    except (ValueError, KeyError, TypeError):
        message = "服务商返回了无法识别的内容，请检查模型是否支持聊天接口。"

    elapsed_ms = round((time.perf_counter() - started_at) * 1000)
    title = "AI 服务连接成功" if ok else "AI 服务暂时无法使用"
    await _record_event(
        session,
        category="connection_test",
        event_status="success" if ok else "attention",
        title=title,
        message=message,
        details={
            "provider": resolved.provider_label,
            "model": resolved.model,
            "elapsed_ms": elapsed_ms,
        },
    )
    return AdminAIConnectionTestResponse(
        ok=ok,
        title=title,
        message=message,
        provider_label=resolved.provider_label,
        model=resolved.model,
        elapsed_ms=elapsed_ms,
    )


@router.get(
    "/overview",
    response_model=AdminOverview,
    dependencies=[Depends(require_admin)],
)
async def read_overview(
    session: SessionDep,
    settings: SettingsDep,
) -> AdminOverview:
    registered_users = int(await session.scalar(select(func.count(User.id))) or 0)
    match_requests = int(await session.scalar(select(func.count(MatchRequest.id))) or 0)
    completed_runs = int(
        await session.scalar(select(func.count(AgentRun.id)).where(AgentRun.status == "completed"))
        or 0
    )
    pending_invitations = int(
        await session.scalar(
            select(func.count(Invitation.id)).where(Invitation.status == "pending")
        )
        or 0
    )
    fallback_runs = int(
        await session.scalar(
            select(func.count(AgentRun.id)).where(AgentRun.mode == "deterministic_fallback")
        )
        or 0
    )
    selected = _selected_provider(settings)
    if selected == "rules":
        service_status = "rules"
        service_status_label = "备用规则正常"
    elif _has_key(settings):
        service_status = "ready"
        service_status_label = "AI 服务已就绪"
    else:
        service_status = "attention"
        service_status_label = "等待填写密钥"
    return AdminOverview(
        registered_users=registered_users,
        match_requests=match_requests,
        completed_runs=completed_runs,
        pending_invitations=pending_invitations,
        fallback_runs=fallback_runs,
        service_status=service_status,
        service_status_label=service_status_label,
        provider_label=_provider_label(selected),
        model=settings.ai_model if selected != "rules" else None,
    )


def _plain_agent_steps(trace: list[dict]) -> list[AdminLogStep]:
    steps: list[AdminLogStep] = []
    seen: set[str] = set()
    for item in trace:
        result_count = int(item.get("result_count") or 0)
        tool = item.get("tool")
        event = item.get("event")
        step_key = str(tool or event or "")
        if step_key and step_key in seen:
            continue
        if step_key:
            seen.add(step_key)
        if tool == "search_activities":
            steps.append(
                AdminLogStep(
                    title=f"查看了 {result_count} 个已有活动", detail="先找可以直接加入的局"
                )
            )
        elif tool == "search_users":
            steps.append(
                AdminLogStep(
                    title=f"找到了 {result_count} 位时间合适的同学",
                    detail="已排除时间冲突和拉黑关系",
                )
            )
        elif tool == "calculate_match":
            steps.append(
                AdminLogStep(
                    title=f"比较了 {result_count} 个候选",
                    detail="综合时间、地点、兴趣、人数偏好和信用情况",
                )
            )
        elif event == "agent_decision":
            count = int(item.get("recommended_count") or 0)
            steps.append(AdminLogStep(title=f"AI 整理出 {count} 个优先候选"))
        elif event == "provider_fallback":
            steps.append(
                AdminLogStep(
                    title="已自动改用备用规则",
                    detail="AI 服务没有正常回复，但这次匹配没有被中断",
                )
            )
        elif event == "user_confirmation":
            approved = item.get("approved_tools") or []
            detail = "用户确认后，系统才执行建局、邀请或提醒"
            steps.append(
                AdminLogStep(title=f"用户批准了 {len(approved)} 项实际操作", detail=detail)
            )
    return steps


def _agent_log_item(run: AgentRun, match_request: MatchRequest) -> AdminLogItem:
    if run.status == "failed":
        log_status = "failed"
        status_label = "没有完成"
        title = "一次匹配没有完成"
        message = "系统保留了这次请求。建议先到“AI 服务”测试连接，再重新发起匹配。"
    elif run.mode == "deterministic_fallback":
        log_status = "attention"
        status_label = "已自动接管"
        title = "AI 没有响应，备用规则完成了匹配"
        message = run.summary or "匹配已经完成，不需要用户重新填写需求。"
    elif run.status in {"running", "starting"}:
        log_status = "running"
        status_label = "处理中"
        title = "Agent 正在寻找合适搭子"
        message = "系统正在查询活动和用户，完成后会给出可解释的候选结果。"
    else:
        log_status = "success"
        status_label = "已完成"
        title = "一次搭子匹配已完成"
        message = run.summary or "Agent 已完成查询、比较和推荐。"
    duration_ms = None
    if run.finished_at:
        duration_ms = max(0, round((run.finished_at - run.started_at).total_seconds() * 1000))
    context = f"{match_request.title or match_request.category} · {match_request.location}"
    return AdminLogItem(
        id=run.id,
        kind="agent",
        status=log_status,
        status_label=status_label,
        title=title,
        message=message,
        created_at=run.started_at,
        duration_ms=duration_ms,
        context=context,
        steps=_plain_agent_steps(run.trace or []),
        technical_info={
            "记录编号": run.id,
            "运行方式": run.mode,
            "模型": run.model or "本地规则",
            "系统状态": run.status,
        },
    )


def _system_log_item(event: SystemEvent) -> AdminLogItem:
    status_map = {
        "success": ("success", "已完成"),
        "attention": ("attention", "需要留意"),
        "failed": ("failed", "没有完成"),
    }
    log_status, status_label = status_map.get(event.status, ("info", "系统消息"))
    safe_details = {
        str(key): value
        for key, value in (event.details or {}).items()
        if isinstance(value, (str, int, bool)) or value is None
    }
    context_map = {
        "ai_config": "AI 服务设置",
        "ai_connection": "AI 服务连接",
        "activity_membership": "活动参与情况",
        "feedback_review": "活动评价审核",
        "post_activity_agent": "活动结束后的记录整理",
    }
    return AdminLogItem(
        id=event.id,
        kind="system",
        status=log_status,
        status_label=status_label,
        title=event.title,
        message=event.message,
        created_at=event.created_at,
        context=context_map.get(event.category, "系统运行记录"),
        technical_info=safe_details,
    )


@router.get(
    "/logs",
    response_model=list[AdminLogItem],
    dependencies=[Depends(require_admin)],
)
async def read_plain_logs(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> list[AdminLogItem]:
    agent_rows = (
        await session.execute(
            select(AgentRun, MatchRequest)
            .join(MatchRequest, MatchRequest.id == AgentRun.match_request_id)
            .order_by(AgentRun.started_at.desc())
            .limit(limit)
        )
    ).all()
    system_events = (
        await session.scalars(
            select(SystemEvent).order_by(SystemEvent.created_at.desc()).limit(limit)
        )
    ).all()
    items = [_agent_log_item(run, request) for run, request in agent_rows]
    items.extend(_system_log_item(event) for event in system_events)
    items.sort(key=lambda item: item.created_at, reverse=True)
    return items[:limit]
