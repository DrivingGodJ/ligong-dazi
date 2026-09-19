from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import Settings
from app.matching import (
    MatchContext,
    Personalization,
    ScoredCandidate,
    interpret_personal_requirement,
    score_activity_candidate,
    score_user_candidate,
    search_available_users,
    search_open_activities,
)
from app.models import Activity, User

TOOL_CATALOG = [
    {
        "name": "search_activities",
        "description": "查询时间、活动类型和地点相符且仍有名额的已有搭子局",
        "effect": "read",
        "requires_confirmation": False,
    },
    {
        "name": "search_users",
        "description": "查询同校、未被拉黑、时间无冲突的潜在搭子",
        "effect": "read",
        "requires_confirmation": False,
    },
    {
        "name": "calculate_match",
        "description": "按可解释权重计算候选用户与活动的匹配度",
        "effect": "read",
        "requires_confirmation": False,
    },
    {
        "name": "create_activity",
        "description": "根据已确认的匹配请求创建活动",
        "effect": "write",
        "requires_confirmation": True,
    },
    {
        "name": "invite_user",
        "description": "向用户发送活动邀请",
        "effect": "write",
        "requires_confirmation": True,
    },
    {
        "name": "join_activity",
        "description": "加入仍有名额的已有活动",
        "effect": "write",
        "requires_confirmation": True,
    },
    {
        "name": "create_group",
        "description": "活动人数满足条件后形成搭子局",
        "effect": "write",
        "requires_confirmation": True,
    },
    {
        "name": "send_notification",
        "description": "发送邀请、状态变化或活动通知",
        "effect": "write",
        "requires_confirmation": True,
    },
    {
        "name": "schedule_reminder",
        "description": "为已确认参与者设置活动提醒",
        "effect": "write",
        "requires_confirmation": True,
    },
    {
        "name": "submit_feedback",
        "description": "提交活动后的评价与履约结果",
        "effect": "write",
        "requires_confirmation": True,
    },
    {
        "name": "leave_activity",
        "description": "按距离活动开始的时间计算后果，并在用户再次确认后退出活动",
        "effect": "write",
        "requires_confirmation": True,
    },
    {
        "name": "review_feedback",
        "description": "结合结构化标签、文字、同场评价和历史信誉审核评价真实性",
        "effect": "write",
        "requires_confirmation": False,
    },
    {
        "name": "request_peer_review",
        "description": "高影响或相互矛盾的评价交给同场第三位参与者复核",
        "effect": "write",
        "requires_confirmation": False,
    },
    {
        "name": "analyze_hidden_profile",
        "description": "活动结束后汇总用户活动习惯、社交信号与履约表现到隐藏画像",
        "effect": "write",
        "requires_confirmation": False,
    },
    {
        "name": "update_credit",
        "description": "根据可审计的履约事件更新搭子信用",
        "effect": "write",
        "requires_confirmation": True,
    },
]


LLM_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_activities",
            "description": "查询与本次需求时间重叠、同活动类型且仍有名额的已有活动。",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 20}},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_users",
            "description": "查询同校、时间无冲突且未互相拉黑的候选用户。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "min_credit": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_match",
            "description": "对已查到的候选用户和活动执行服务端可解释评分。",
            "parameters": {
                "type": "object",
                "properties": {
                    "candidate_user_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "candidate_activity_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "additionalProperties": False,
            },
        },
    },
]

COMMON_INTERESTS = {
    "羽毛球",
    "篮球",
    "足球",
    "乒乓球",
    "跑步",
    "骑行",
    "徒步",
    "自习",
    "学习",
    "摄影",
    "电影",
    "游戏",
    "音乐",
    "吃饭",
}


@dataclass(slots=True)
class AgentDecision:
    mode: str
    model: str | None
    summary: str
    personalization: Personalization
    candidates: list[ScoredCandidate]
    users: dict[str, User]
    activities: dict[str, tuple[Activity, int]]
    trace: list[dict[str, Any]]
    error: str | None = None


class ToolRuntime:
    def __init__(self, session: AsyncSession, context: MatchContext) -> None:
        self.session = session
        self.context = context
        known_interests = set(context.requester.interests) | {context.category} | COMMON_INTERESTS
        self.personalization = interpret_personal_requirement(
            context.personal_requirement,
            known_interests,
        )
        self.users: dict[str, User] = {}
        self.activities: dict[str, tuple[Activity, int]] = {}
        self.scored: list[ScoredCandidate] = []
        self.trace: list[dict[str, Any]] = []

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        arguments = arguments or {}
        started_at = datetime.now(UTC).isoformat()
        if name == "search_activities":
            limit = max(1, min(int(arguments.get("limit", 10)), 20))
            rows = await search_open_activities(self.session, self.context, limit=limit)
            self.activities = {activity.id: (activity, count) for activity, count in rows}
            result: Any = [
                {
                    "id": activity.id,
                    "title": activity.title,
                    "category": activity.category,
                    "starts_at": activity.starts_at.isoformat(),
                    "ends_at": activity.ends_at.isoformat(),
                    "location": activity.location,
                    "capacity": activity.capacity,
                    "participant_count": count,
                }
                for activity, count in rows
            ]
        elif name == "search_users":
            requested_min = max(0, min(int(arguments.get("min_credit", 0)), 100))
            if requested_min > self.personalization.min_credit:
                self.personalization.min_credit = requested_min
                self.personalization.applied.append(f"Agent 将最低信用分设为 {requested_min}")
            limit = max(1, min(int(arguments.get("limit", 50)), 100))
            users = await search_available_users(
                self.session,
                self.context,
                self.personalization,
                limit=limit,
            )
            self.users = {user.id: user for user in users}
            result = [
                {
                    "id": user.id,
                    "display_name": user.display_name,
                    "campus": user.campus,
                    "department": user.department,
                    "grade_year": user.grade_year,
                    "interests": user.interests,
                    "preferred_locations": user.preferred_locations,
                    "social_style": user.social_style,
                    "preferred_group_range": [
                        user.preferred_group_min,
                        user.preferred_group_max,
                    ],
                    "credit_score": user.credit_score,
                }
                for user in users
            ]
        elif name == "calculate_match":
            if not self.users:
                await self.call("search_users", {})
            if not self.activities:
                await self.call("search_activities", {})
            requested_user_ids = set(arguments.get("candidate_user_ids") or self.users)
            requested_activity_ids = set(arguments.get("candidate_activity_ids") or self.activities)
            user_scores = [
                score_user_candidate(self.context, user, self.personalization)
                for user_id, user in self.users.items()
                if user_id in requested_user_ids
            ]
            activity_scores = [
                score_activity_candidate(self.context, activity, count)
                for activity_id, (activity, count) in self.activities.items()
                if activity_id in requested_activity_ids
            ]
            self.scored = sorted(
                [*activity_scores, *user_scores],
                key=lambda item: (-item.score, item.candidate_id),
            )
            result = [asdict(item) for item in self.scored]
        else:
            raise ValueError(f"预览阶段不允许调用工具：{name}")

        self.trace.append(
            {
                "tool": name,
                "effect": "read",
                "arguments": arguments,
                "result_count": len(result) if isinstance(result, list) else 1,
                "started_at": started_at,
                "finished_at": datetime.now(UTC).isoformat(),
            }
        )
        return result

    async def ensure_scored(self) -> list[ScoredCandidate]:
        if not self.scored:
            await self.call("calculate_match", {})
        return self.scored


class DeterministicMatchingAgent:
    async def run(self, runtime: ToolRuntime) -> AgentDecision:
        await runtime.call("search_activities", {"limit": 10})
        await runtime.call("search_users", {"limit": 50})
        candidates = await runtime.ensure_scored()
        top_users = sum(item.candidate_type == "user" for item in candidates[:10])
        top_activities = sum(item.candidate_type == "activity" for item in candidates[:10])
        summary = f"已找到 {top_activities} 个可加入活动和 {top_users} 位优先候选搭子。"
        if runtime.personalization.unresolved:
            summary += " 当前无模型额度，未识别的自然语言要求已明确标记，未静默猜测。"
        return AgentDecision(
            mode="deterministic",
            model=None,
            summary=summary,
            personalization=runtime.personalization,
            candidates=candidates[:20],
            users=runtime.users,
            activities=runtime.activities,
            trace=runtime.trace,
        )


class OpenAICompatibleMatchingAgent:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.client = client

    async def run(self, runtime: ToolRuntime) -> AgentDecision:
        api_key = self.settings.ai_api_key
        if api_key is None or not api_key.get_secret_value():
            raise RuntimeError("AI_PROVIDER=openai_compatible 时必须配置 DAZI_AI_API_KEY")

        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "你是校园搭子匹配 Agent。先调用查询工具，再调用 calculate_match。"
                    "只能使用工具实际返回的候选，不得编造用户。预览阶段禁止任何写操作。"
                    "服务端评分是最终安全基线，你可以在候选中重新排序。"
                    "最后仅输出 JSON：summary、recommended_user_ids、recommended_activity_ids。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "requester": {
                            "id": runtime.context.requester.id,
                            "department": runtime.context.requester.department,
                            "grade_year": runtime.context.requester.grade_year,
                            "interests": runtime.context.requester.interests,
                            "social_style": runtime.context.requester.social_style,
                        },
                        "request": {
                            "category": runtime.context.category,
                            "starts_at": runtime.context.starts_at.isoformat(),
                            "ends_at": runtime.context.ends_at.isoformat(),
                            "location": runtime.context.location,
                            "people_needed": runtime.context.people_needed,
                            "personal_requirement": runtime.context.personal_requirement,
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        ]

        owns_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=self.settings.ai_timeout_seconds)
        final_content = ""
        try:
            for _ in range(self.settings.ai_max_tool_rounds):
                response = await client.post(
                    self.settings.ai_base_url.rstrip("/") + "/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key.get_secret_value()}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.settings.ai_model,
                        "messages": messages,
                        "tools": LLM_TOOLS,
                        "tool_choice": "auto",
                        "temperature": 0.1,
                    },
                )
                response.raise_for_status()
                body = response.json()
                message = body["choices"][0]["message"]
                messages.append(message)
                tool_calls = message.get("tool_calls") or []
                if not tool_calls:
                    final_content = message.get("content") or ""
                    break
                for call in tool_calls:
                    function = call.get("function") or {}
                    name = function.get("name", "")
                    try:
                        arguments = json.loads(function.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    result = await runtime.call(name, arguments)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "name": name,
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )
            candidates = await runtime.ensure_scored()
        finally:
            if owns_client:
                await client.aclose()

        summary = "模型已通过工具查询并完成候选排序。"
        preferred_order: list[tuple[str, str]] = []
        try:
            parsed = json.loads(final_content)
            if isinstance(parsed.get("summary"), str):
                summary = parsed["summary"][:500]
            preferred_order.extend(
                ("activity", item) for item in parsed.get("recommended_activity_ids", [])
            )
            preferred_order.extend(
                ("user", item) for item in parsed.get("recommended_user_ids", [])
            )
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

        by_key = {(item.candidate_type, item.candidate_id): item for item in candidates}
        ordered = [by_key[key] for key in preferred_order if key in by_key]
        seen = {(item.candidate_type, item.candidate_id) for item in ordered}
        ordered.extend(
            item for item in candidates if (item.candidate_type, item.candidate_id) not in seen
        )
        runtime.trace.append(
            {
                "event": "agent_decision",
                "model": self.settings.ai_model,
                "recommended_count": len(ordered[:20]),
                "finished_at": datetime.now(UTC).isoformat(),
            }
        )
        return AgentDecision(
            mode="openai_compatible",
            model=self.settings.ai_model,
            summary=summary,
            personalization=runtime.personalization,
            candidates=ordered[:20],
            users=runtime.users,
            activities=runtime.activities,
            trace=runtime.trace,
        )


async def run_matching_agent(
    session: AsyncSession,
    context: MatchContext,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> AgentDecision:
    runtime = ToolRuntime(session, context)
    if not settings.use_llm:
        return await DeterministicMatchingAgent().run(runtime)
    try:
        return await OpenAICompatibleMatchingAgent(settings, client=client).run(runtime)
    except (httpx.HTTPError, KeyError, RuntimeError, ValueError) as exc:
        if not settings.ai_fallback_enabled:
            raise
        fallback = await DeterministicMatchingAgent().run(runtime)
        fallback.mode = "deterministic_fallback"
        fallback.error = str(exc)[:500]
        fallback.summary = "模型调用失败，已自动切换到可解释规则 Agent。" + fallback.summary
        fallback.trace.append(
            {
                "event": "provider_fallback",
                "reason": str(exc)[:500],
                "finished_at": datetime.now(UTC).isoformat(),
            }
        )
        return fallback
