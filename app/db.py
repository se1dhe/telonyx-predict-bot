from __future__ import annotations

from pathlib import Path
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import Base

settings = get_settings()
logger = logging.getLogger(__name__)


def normalize_database_url(raw_url: str) -> str:
    """Подготовить DATABASE_URL для SQLAlchemy async engine.

    Railway/Postgres часто отдаёт URL в одном из форматов:
    - postgresql://user:pass@host:port/db
    - postgres://user:pass@host:port/db

    Для async SQLAlchemy нужен драйвер asyncpg:
    - postgresql+asyncpg://user:pass@host:port/db

    SQLite оставляем как есть:
    - sqlite+aiosqlite:///./data/bot.db
    """
    value = (raw_url or "").strip()

    if value.startswith("postgresql+asyncpg://"):
        return value

    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)

    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql+asyncpg://", 1)

    return value


DATABASE_URL = normalize_database_url(settings.database_url)
IS_SQLITE = DATABASE_URL.startswith("sqlite")
IS_POSTGRES = DATABASE_URL.startswith("postgresql+asyncpg")

if IS_SQLITE:
    Path("./data").mkdir(parents=True, exist_ok=True)

engine_kwargs = {
    "echo": False,
    "future": True,
    "pool_pre_ping": True,
}

# Для SQLite пул не настраиваем; для Postgres можно держать маленький пул,
# чтобы Railway hobby/free не упирался в лишние подключения.
if IS_POSTGRES:
    engine_kwargs.update(
        {
            "pool_size": 5,
            "max_overflow": 5,
            "pool_recycle": 1800,
        }
    )

engine = create_async_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    """Создать таблицы и добавить безопасные runtime-колонки."""
    logger.info(
        "DB init: driver=%s sqlite=%s postgres=%s",
        DATABASE_URL.split("://", 1)[0],
        IS_SQLITE,
        IS_POSTGRES,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await ensure_runtime_columns(conn)

        if IS_POSTGRES:
            result = await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
            )
            tables = [row[0] for row in result.fetchall()]
            logger.info("DB init complete. PostgreSQL public tables: %s", tables)
        else:
            logger.info("DB init complete. SQLite tables created/checked.")


async def ensure_runtime_columns(conn) -> None:
    """Мини-миграции без Alembic.

    create_all не добавляет новые колонки в уже существующие таблицы.
    Поэтому добавляем runtime-поля вручную и игнорируем ошибку,
    если колонка уже существует.
    """
    columns = {
        "notified_24h_at": "DATETIME",
        "notified_5h_at": "DATETIME",
        "notified_1h_at": "DATETIME",
        "kicked_at": "DATETIME",
    }

    for name, sqlite_type in columns.items():
        try:
            if IS_SQLITE:
                await conn.execute(text(f"ALTER TABLE bot_users ADD COLUMN {name} {sqlite_type}"))
            else:
                await conn.execute(text(f"ALTER TABLE bot_users ADD COLUMN IF NOT EXISTS {name} TIMESTAMP"))
        except Exception:
            # Колонка уже существует или таблицы ещё нет в старом окружении.
            pass
