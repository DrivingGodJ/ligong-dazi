FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DAZI_ENVIRONMENT=production \
    DAZI_AI_PROVIDER=deterministic \
    DAZI_CORS_ORIGINS="[]" \
    DAZI_DATABASE_URL="sqlite+aiosqlite:////app/data/ligong_dazi.db" \
    DAZI_ACTIVITY_PHOTO_DIRECTORY="/app/data/activity_photos" \
    DAZI_CONFIG_FILE_PATH="/app/data/admin.env" \
    PATH="/app/.venv/bin:$PATH"

COPY . /app
RUN pip install --no-cache-dir uv==0.12.17 \
    && uv sync --locked --no-dev

EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
