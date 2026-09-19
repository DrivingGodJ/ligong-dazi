from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core import DatabaseRuntime, get_settings, hash_password
from app.models import Activity, ActivityMember, Base, User

DEMO_PASSWORD = "demo-password-123"

DEMO_USERS = [
    {
        "email": "xiaozeng@njust.demo",
        "display_name": "小曾",
        "campus": "南区",
        "department": "设计艺术与传媒学院",
        "grade_year": 3,
        "bio": "喜欢羽毛球、摄影和徒步",
        "interests": ["羽毛球", "摄影", "徒步"],
        "preferred_locations": ["南区体育馆", "图书馆"],
        "social_style": "quiet",
        "preferred_group_min": 2,
        "preferred_group_max": 5,
        "credit_score": 100,
    },
    {
        "email": "xiaowang@njust.demo",
        "display_name": "小王",
        "campus": "南区",
        "department": "设计艺术与传媒学院",
        "grade_year": 3,
        "bio": "羽毛球爱好者，守时，偏安静",
        "interests": ["羽毛球", "电影", "摄影"],
        "preferred_locations": ["南区体育馆"],
        "social_style": "quiet",
        "preferred_group_min": 2,
        "preferred_group_max": 4,
        "credit_score": 98,
    },
    {
        "email": "xiaoli@njust.demo",
        "display_name": "小李",
        "campus": "北区",
        "department": "自动化学院",
        "grade_year": 2,
        "bio": "喜欢篮球和游戏，性格外向",
        "interests": ["篮球", "游戏", "跑步"],
        "preferred_locations": ["北区篮球场"],
        "social_style": "outgoing",
        "preferred_group_min": 3,
        "preferred_group_max": 8,
        "credit_score": 91,
    },
    {
        "email": "xiaozhou@njust.demo",
        "display_name": "小周",
        "campus": "南区",
        "department": "计算机科学与工程学院",
        "grade_year": 3,
        "bio": "常去图书馆自习，也打羽毛球",
        "interests": ["自习", "羽毛球", "编程"],
        "preferred_locations": ["图书馆", "南区体育馆"],
        "social_style": "balanced",
        "preferred_group_min": 2,
        "preferred_group_max": 4,
        "credit_score": 96,
    },
]


async def seed() -> None:
    settings = get_settings()
    if settings.environment == "production":
        raise RuntimeError("禁止在 production 环境运行演示数据脚本")
    database = DatabaseRuntime(settings)
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with database.session_factory() as session:
            users: dict[str, User] = {}
            for profile in DEMO_USERS:
                user = await session.scalar(select(User).where(User.email == profile["email"]))
                if user is None:
                    user = User(
                        password_hash=hash_password(DEMO_PASSWORD),
                        university="南京理工大学",
                        **profile,
                    )
                    session.add(user)
                    await session.flush()
                users[user.email] = user

            owner = users["xiaozhou@njust.demo"]
            existing = await session.scalar(
                select(Activity).where(
                    Activity.owner_id == owner.id,
                    Activity.title == "图书馆安静自习局",
                )
            )
            if existing is None:
                starts_at = datetime.now(UTC) + timedelta(days=2)
                starts_at = starts_at.replace(hour=11, minute=0, second=0, microsecond=0)
                activity = Activity(
                    owner_id=owner.id,
                    title="图书馆安静自习局",
                    category="自习",
                    starts_at=starts_at,
                    ends_at=starts_at + timedelta(hours=2),
                    location="图书馆三楼",
                    capacity=4,
                    description="专注自习，结束后可以一起复盘。",
                    status="open",
                )
                session.add(activity)
                await session.flush()
                session.add(
                    ActivityMember(
                        activity_id=activity.id,
                        user_id=owner.id,
                        role="owner",
                        status="confirmed",
                    )
                )
            await session.commit()
    finally:
        await database.dispose()

    print("演示数据已就绪")
    print("账号：xiaozeng@njust.demo / xiaowang@njust.demo / xiaoli@njust.demo")
    print(f"统一密码：{DEMO_PASSWORD}")


if __name__ == "__main__":
    asyncio.run(seed())
