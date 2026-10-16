from pathlib import Path

import app
from app.core import Settings
from app.models import Base
from app.schemas import ActivityPublic, MatchPreviewRequest, UserMe, UserPublic


async def test_release_excludes_commercial_routes_models_and_fields(client):
    """The publishable checkout must not accidentally include local-only products."""
    application = client._transport.app
    assert not any("commerce" in path for path in application.openapi()["paths"])
    forbidden_tables = {
        "user_memberships",
        "commerce_config",
        "commerce_orders",
        "activity_pins",
        "vip_activity_pins",
    }
    assert not forbidden_tables.intersection(Base.metadata.tables)
    for schema in (ActivityPublic, MatchPreviewRequest, UserMe, UserPublic, Settings):
        assert not any(
            any(token in name for token in ("vip", "payment", "pin", "commerce"))
            for name in schema.model_fields
        )


def test_release_imports_its_own_source_and_excludes_commercial_ui():
    root = Path(__file__).resolve().parents[1]
    assert Path(app.__file__).resolve().parent == root / "app"
    assert not (root / "app/commerce.py").exists()
    forbidden_ui = (
        "commerceState",
        "/commerce/",
        "vip-",
        "vip_pin_enabled",
        "is_pinned",
        "pinned_until",
        "checkout-",
        "square-ad-banner",
        "squarePinCountdown",
    )
    for filename in ("web/index.html", "web/app.js", "web/reference-ui.css"):
        source = (root / filename).read_text()
        assert not any(token in source for token in forbidden_ui), filename
