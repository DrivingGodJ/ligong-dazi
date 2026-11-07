[简体中文](GUIDE.zh-CN.md) | **English**

# Usage, Configuration and Development

[Back to the project](../README.en.md)

## Quick start

Open the website, register, choose the Nanjing or Jiangyin campus and complete your profile. Default rule-based matching does not require a paid model API. Android users can install the native app; iPhone users can add the site to the home screen through Safari.

Local development requires **Python 3.12+** and [uv](https://docs.astral.sh/uv/). From the repository root:

```bash
cp .env.example .env
uv sync --dev
uv run uvicorn app.main:app --reload
```

Open the [local app](http://127.0.0.1:8000/), [admin console](http://127.0.0.1:8000/admin) or [API docs](http://127.0.0.1:8000/docs). SQLite is created under `data/` on first launch. Use `uv run python -m scripts.seed_demo` for development/test demo data.

## Usage

1. Browse your campus's activities, or submit an activity type, time and preferences. Location may remain empty during matching.
2. Review candidates, explanations and profiles, then confirm joining, publishing or inviting. New activities require a location before creation.
3. Manage plans, rescheduling and withdrawal under My Activities. Cancelling within the final hour costs reliability points; cancellation is unavailable after the start.
4. Submit feedback after the activity; serious or contradictory reports go to third-party review.

Push notifications require opt-in; the inbox works without them. Changes to the Android app's bundled interface require a new build. Private account and activity data are not cached offline. See the detailed reference for signing, upgrades and push configuration.

Production requires appropriate CAPTCHA, administrator access, trusted-proxy and persistent-storage configuration. Student cards, avatars and meetup photos have distinct access controls; do not expose development settings publicly. Keep model keys in `.env` or platform secrets. Sensitive attributes are excluded from Agent ranking. See the [detailed reference (Chinese)](../docs/readme-reference.zh-CN.md) for accounts, retention, identity appeals and reliability rules.

## Development and checks

```bash
uv run pytest
uv run ruff check .
```

SQLite supports local startup; production can use PostgreSQL with explicit schema migrations. OpenAI Tool Calling-compatible model services are supported, while rule matching works without a configured model. Real Android/iOS use, installation, upgrades and push delivery require device validation; a successful build does not establish reliable delivery.

## Documentation and support

- [Detailed features, permissions, synchronization and maintenance (Chinese)](../docs/readme-reference.zh-CN.md)
- [Zeabur deployment](../DEPLOY_ZEABUR.md) · [Railway deployment](../DEPLOY_RAILWAY.md)
- [Changelog](../CHANGELOG.md) · [Contributing](../CONTRIBUTING.md)
- [Report an issue](https://github.com/DrivingGodJ/ligong-dazi/issues)

## License and credits

The repository currently provides no standalone repository-wide license. Public visibility does not grant unrestricted redistribution or commercial use; confirm terms with the rights holder.
