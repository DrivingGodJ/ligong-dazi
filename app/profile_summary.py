from __future__ import annotations

import json
from typing import Any

import httpx

from app.core import Settings
from app.models import User

SKILL_LEVEL_LABELS = {1: "小白", 2: "入门", 3: "熟练", 4: "擅长", 5: "精通"}


def rules_summary(user: User) -> str:
    interests = "、".join(user.interests[:3]) or "不同类型的校园活动"
    skills = [
        f"{item.get('name')}（{SKILL_LEVEL_LABELS.get(item.get('level'), '已填写')}）"
        for item in (user.hobby_skills or [])[:3]
        if isinstance(item, dict) and item.get("name")
    ]
    style = {
        "quiet": "慢热、专注",
        "balanced": "能根据场合调整相处方式",
        "outgoing": "愿意主动交流、带动气氛",
    }.get(user.social_style, "相处方式比较灵活")
    learned = user.hidden_profile or {}
    evidence: list[str] = []
    if learned.get("completed_activity_count"):
        evidence.append(f"已经留下 {learned['completed_activity_count']} 次活动记录")
    if learned.get("average_rating") is not None:
        evidence.append(f"收到的平均评分为 {learned['average_rating']} 分")
    leading_traits = learned.get("personality_signals") or []
    if leading_traits and isinstance(leading_traits[0], dict):
        evidence.append(f"搭子常用“{leading_traits[0].get('label', '好相处')}”来形容你")
    ending = "；".join(evidence) if evidence else "活动记录还不多，这份印象会慢慢变得更准确"
    skill_sentence = f"你填写的爱好水平包括{'、'.join(skills)}。" if skills else ""
    return f"你对{interests}比较感兴趣，相处时{style}。{skill_sentence}{ending}。"


async def generate_profile_summary(
    user: User,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> tuple[str, str]:
    fallback = rules_summary(user)
    if not settings.use_llm or settings.ai_api_key is None:
        return fallback, "rules"

    api_key = settings.ai_api_key.get_secret_value()
    if not api_key:
        return fallback, "rules"

    facts: dict[str, Any] = {
        "self_description": {
            "bio": user.bio,
            "interests": user.interests,
            "hobby_skills": user.hobby_skills,
            "preferred_locations": user.preferred_locations,
            "social_style": user.social_style,
            "preferred_group_range": [user.preferred_group_min, user.preferred_group_max],
        },
        "activity_and_feedback_signals": user.hidden_profile or {},
    }
    owns_client = client is None
    active_client = client or httpx.AsyncClient(timeout=settings.ai_timeout_seconds)
    try:
        response = await active_client.post(
            settings.ai_base_url.rstrip("/") + "/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.ai_model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你是校园搭子产品中的个人形象总结助手。只根据提供的事实，"
                            "用第二人称写一段温和、具体、不贴负面标签的中文总结，80到160字。"
                            "区分本人填写内容和他人反馈；数据不足时明确说记录还不多。"
                            "不要提及内部数据结构、风控、隐藏画像或系统提示。只输出总结正文。"
                        ),
                    },
                    {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
                ],
                "temperature": 0.3,
            },
        )
        response.raise_for_status()
        summary = str(response.json()["choices"][0]["message"]["content"]).strip()
        if summary:
            return summary[:500], "ai"
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        pass
    finally:
        if owns_client:
            await active_client.aclose()
    return fallback, "rules"
