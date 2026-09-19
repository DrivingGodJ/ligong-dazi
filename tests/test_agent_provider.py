from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
from pydantic import SecretStr

from app.agent import OpenAICompatibleMatchingAgent
from app.core import Settings
from app.matching import MatchContext, Personalization, ScoredCandidate


class FakeToolRuntime:
    def __init__(self) -> None:
        requester = SimpleNamespace(
            id="requester",
            department="设计艺术与传媒学院",
            grade_year=3,
            interests=["羽毛球", "摄影"],
            social_style="quiet",
        )
        starts_at = datetime.now(UTC) + timedelta(days=1)
        self.context = MatchContext(
            requester=requester,
            category="羽毛球",
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=2),
            location="南区体育馆",
            people_needed=2,
            personal_requirement="想找安静、靠谱的搭子",
        )
        self.personalization = Personalization()
        self.users = {
            "user-a": SimpleNamespace(id="user-a"),
            "user-b": SimpleNamespace(id="user-b"),
        }
        self.activities = {}
        self.scored: list[ScoredCandidate] = []
        self.trace: list[dict] = []

    async def call(self, name: str, arguments: dict | None = None) -> list[dict]:
        self.trace.append({"tool": name, "arguments": arguments or {}})
        if name == "search_users":
            return [{"id": "user-a"}, {"id": "user-b"}]
        if name == "calculate_match":
            self.scored = [
                ScoredCandidate("user", "user-a", 95.0, {"credit": 100.0}, ["候选 A"]),
                ScoredCandidate("user", "user-b", 92.0, {"credit": 98.0}, ["候选 B"]),
            ]
            return [
                {
                    "candidate_type": item.candidate_type,
                    "candidate_id": item.candidate_id,
                    "score": item.score,
                }
                for item in self.scored
            ]
        raise AssertionError(f"unexpected tool: {name}")

    async def ensure_scored(self) -> list[ScoredCandidate]:
        if not self.scored:
            await self.call("calculate_match", {})
        return self.scored


async def test_openai_compatible_agent_executes_tools_and_validates_final_ids() -> None:
    responses = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-search",
                                "type": "function",
                                "function": {
                                    "name": "search_users",
                                    "arguments": '{"limit": 20, "min_credit": 90}',
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-score",
                                "type": "function",
                                "function": {
                                    "name": "calculate_match",
                                    "arguments": '{"candidate_user_ids":["user-a","user-b"]}',
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "summary": "综合个性化需求后优先推荐 B。",
                                "recommended_user_ids": ["user-b", "invented-user"],
                                "recommended_activity_ids": [],
                            },
                            ensure_ascii=False,
                        ),
                    }
                }
            ]
        },
    ]

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses.pop(0))

    settings = Settings(
        environment="test",
        jwt_secret=SecretStr("test-secret-with-enough-entropy-for-tests"),
        ai_provider="openai_compatible",
        ai_api_key=SecretStr("test-key"),
        ai_base_url="https://model.example/v1",
        ai_model="test-model",
    )
    runtime = FakeToolRuntime()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        decision = await OpenAICompatibleMatchingAgent(settings, client=client).run(runtime)

    assert [item.candidate_id for item in decision.candidates] == ["user-b", "user-a"]
    assert "invented-user" not in [item.candidate_id for item in decision.candidates]
    assert [item["tool"] for item in runtime.trace[:2]] == [
        "search_users",
        "calculate_match",
    ]
    assert decision.mode == "openai_compatible"
    assert decision.model == "test-model"
