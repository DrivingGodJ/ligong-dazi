from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Literal

import httpx

from app.core import Settings

Verdict = Literal["approved", "rejected", "manual_review"]


@dataclass(slots=True)
class StudentCardReview:
    verdict: Verdict
    reason: str
    confidence: float
    extracted_student_id: str | None = None

    def as_dict(self) -> dict[str, str | float | None]:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "confidence": self.confidence,
            "extracted_student_id": self.extracted_student_id,
        }


def _manual(reason: str) -> StudentCardReview:
    return StudentCardReview(verdict="manual_review", reason=reason[:300], confidence=0.0)


def _parse_review(content: str, expected_student_id: str) -> StudentCardReview:
    cleaned = content.strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1]).strip()
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        return _manual("AI 没有返回可核验的结构化结果")
    verdict = data.get("verdict")
    reason = str(data.get("reason") or "AI 未说明判断依据").strip()[:300]
    try:
        confidence = max(0.0, min(float(data.get("confidence", 0)), 1.0))
    except (TypeError, ValueError):
        confidence = 0.0
    extracted = str(data.get("student_id") or "").strip().upper() or None
    if verdict not in {"approved", "rejected", "manual_review"}:
        return _manual("AI 返回了无法识别的审核结论")
    if verdict == "approved" and extracted != expected_student_id:
        return StudentCardReview(
            verdict="rejected",
            reason="学生卡上识别到的学号与申诉学号不一致",
            confidence=max(confidence, 0.8),
            extracted_student_id=extracted,
        )
    if verdict == "approved" and confidence < 0.85:
        verdict = "manual_review"
        reason = "AI 判断置信度不足，已转人工复核"
    return StudentCardReview(verdict, reason, confidence, extracted)


async def review_student_card(
    content: bytes,
    media_type: str,
    expected_student_id: str,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> StudentCardReview:
    """Ask the configured multimodal model for a narrow identity-document triage decision."""

    if not settings.use_llm or settings.ai_api_key is None:
        return _manual("当前未配置可用的图片审核模型")
    encoded = base64.b64encode(content).decode("ascii")
    payload = {
        "model": settings.ai_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是校园账号身份材料初审 Agent。"
                    "只判断图片是否清晰展示南京理工大学学生卡人像面，"
                    "以及可见学号是否与给定学号一致。不要推断姓名、性别、民族等无关信息。"
                    "图片模糊、遮挡、疑似截图篡改、无法确认学校或学号时必须 manual_review。"
                    "只返回 JSON：verdict(approved/rejected/manual_review)、student_id、"
                    "confidence(0到1)、reason。"
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"申诉学号：{expected_student_id}"},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{encoded}"},
                    },
                ],
            },
        ],
        "temperature": 0,
    }
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=settings.ai_timeout_seconds)
    try:
        response = await http_client.post(
            settings.ai_base_url.rstrip("/") + "/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.ai_api_key.get_secret_value()}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        content_text = response.json()["choices"][0]["message"]["content"]
        return _parse_review(content_text, expected_student_id)
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        return _manual(f"图片审核服务暂时不可用：{type(exc).__name__}")
    finally:
        if owns_client:
            await http_client.aclose()
