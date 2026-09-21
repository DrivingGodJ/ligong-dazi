from __future__ import annotations

CAMPUSES = ("南京", "江阴")


def infer_campus(value: str | None, locations: list[str] | None = None) -> str | None:
    """Recognize legacy campus descriptions without guessing from vague addresses."""

    candidates = [value or "", *(locations or [])]
    for candidate in candidates:
        text = candidate.strip().lower()
        if "江阴" in text or "jiangyin" in text:
            return "江阴"
        nanjing_markers = ("南京", "南区", "北区", "孝陵卫", "南体", "北体", "主校区")
        if any(marker in text for marker in nanjing_markers):
            return "南京"
    return None


def require_campus(value: str | None) -> str | None:
    if value is None:
        return None
    aliases = {
        "南京": "南京",
        "南京校区": "南京",
        "南区": "南京",
        "北区": "南京",
        "江阴": "江阴",
        "江阴校区": "江阴",
    }
    campus = aliases.get(value.strip())
    if campus is None:
        raise ValueError("校区只能选南京或江阴")
    return campus
