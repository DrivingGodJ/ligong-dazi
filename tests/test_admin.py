from __future__ import annotations

import stat
from pathlib import Path

import httpx


async def test_local_admin_page_config_and_plain_logs(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    page = await client.get("/admin")
    assert page.status_code == 200
    assert "不用看代码" in page.text

    config = await client.get("/api/v1/admin/config")
    assert config.status_code == 200
    assert config.json()["selected_provider"] == "rules"
    assert config.json()["key_configured"] is False
    assert "api_key" not in config.json()

    overview = await client.get("/api/v1/admin/overview")
    assert overview.status_code == 200
    assert overview.json()["service_status"] == "rules"
    assert overview.json()["registered_users"] == 0

    invalid_custom = await client.put(
        "/api/v1/admin/config",
        json={
            "vendor": "custom",
            "api_key": None,
            "base_url": "https://model.example/v1",
            "model": "example-model",
            "fallback_enabled": True,
        },
    )
    assert invalid_custom.status_code == 422
    assert "API Key" in invalid_custom.text

    rules_test = await client.post(
        "/api/v1/admin/config/test",
        json={
            "vendor": "rules",
            "api_key": None,
            "base_url": "",
            "model": "",
            "fallback_enabled": True,
        },
    )
    assert rules_test.status_code == 200
    assert rules_test.json()["ok"] is True
    assert "不需要联网" in rules_test.json()["message"]

    test_key = "test-deepseek-key-not-real"
    saved = await client.put(
        "/api/v1/admin/config",
        json={
            "vendor": "deepseek",
            "api_key": test_key,
            "base_url": "",
            "model": "deepseek-flash",
            "fallback_enabled": True,
        },
    )
    assert saved.status_code == 200, saved.text
    saved_body = saved.json()
    assert saved_body["selected_provider"] == "deepseek"
    assert saved_body["key_configured"] is True
    assert "api_key" not in saved_body
    assert test_key not in saved.text

    ready = await client.get("/health/ready")
    assert ready.json()["agent_mode"] == "llm"

    config_path = tmp_path / ".env"
    assert config_path.exists()
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    saved_text = config_path.read_text(encoding="utf-8")
    assert "DAZI_AI_VENDOR=deepseek" in saved_text
    assert f"DAZI_AI_API_KEY={test_key}" in saved_text

    logs = await client.get("/api/v1/admin/logs")
    assert logs.status_code == 200
    titles = {item["title"] for item in logs.json()}
    assert "备用规则可以正常使用" in titles
    assert "AI 服务设置已更新" in titles
    assert test_key not in logs.text
