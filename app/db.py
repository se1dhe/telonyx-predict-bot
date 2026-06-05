from __future__ import annotations

from pathlib import Path
import logging
import os
from urllib.parse import quote_plus

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import Base

settings = get_settings()
logger = logging.getLogger(__name__)


def build_postgres_url_from_parts() -> str:
    """Собрать Postgres URL из отдельных Railway/PG переменных.

    Это fallback на случай, если DATABASE_URL в Railway задан неверно
    или reference variable не развернулась.
    """
    host = os.getenv("PGHOST") or os.getenv("POSTGRES_HOST")
    port = os.getenv("PGPORT") or os.getenv("POSTGRES_PORT") or "5432"
    user = os.getenv("PGUSER") or os.getenv("POSTGRES_USER")
    password = os.getenv("PGPASSWORD") or os.getenv("POSTGRES_PASSWORD")
    database = os.getenv("PGDATABASE") or os.getenv("POSTGRES_DB") or os.getenv("POSTGRES_DATABASE")

    if not all([host, user, password, database]):
        return ""

    return (
        "postgresql+asyncpg://"
        f"{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{database}"
    )


def normalize_database_url(raw_url: str) -> str:
    """Подготовить DATABASE_URL для SQLAlchemy async engine.

    Поддерживаются:
    - postgresql+asyncpg://user:pass@host:port/db
    - postgresql://user:pass@host:port/db
    - postgres://user:pass@host:port/db
    - sqlite+aiosqlite:///./data/bot.db

    Если Railway reference variable не развернулась и пришло буквальное
    `${{Postgres.DATABASE_URL}}`, пробуем DATABASE_PUBLIC_URL/POSTGRES_URL
    и затем отдельные PGHOST/PGUSER/PGPASSWORD/PGDATABASE.
    """
    value = (raw_url or "").strip().strip('"').strip("'")

    # Частая ошибка: в Railway указан reference на несуществующее имя сервиса,
    # и приложение получает буквальную строку `${{Postgres.DATABASE_URL}}`.
    if not value or value.startswith("${{") or value.startswith("$"):
        fallback = (
            os.getenv("DATABASE_PUBLIC_URL")
            or os.getenv("POSTGRES_URL")
            or os.getenv("POSTGRES_DATABASE_URL")
            or build_postgres_url_from_parts()
        )

        if fallback:
            logger.warning(
                "DATABASE_URL не является готовым URL, использую fallback из Railway/Postgres переменных"
            )
            value = fallback.strip().strip('"').strip("'")
        else:
            raise RuntimeError(
                "DATABASE_URL задан неверно или Railway reference не развернулся. "
                "В Railway variables для сервиса бота укажи DATABASE_URL как reference на реальную "
                "переменную Postgres service, например ${{Postgres.DATABASE_URL}}, "
                "где Postgres — точное имя твоего Postgres service. "
                "Альтернатива: задай PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE."
            )

    if value.startswith("postgresql+asyncpg://"):
        return value

    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)

    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql+asyncpg://", 1)

    if value.startswith("sqlite+aiosqlite://"):
        return value

    raise RuntimeError(
        "DATABASE_URL имеет неподдерживаемый формат. "
        "Нужен postgresql://, postgres://, postgresql+asyncpg:// или sqlite+aiosqlite://. "
        f"Текущее начало значения: {value[:32]!r}"
    )


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
    """Мини-миграции без Alembic."""
    bot_user_columns = {
        "notified_24h_at": "DATETIME",
        "notified_5h_at": "DATETIME",
        "notified_1h_at": "DATETIME",
        "kicked_at": "DATETIME",
    }

    prediction_columns = {
        "bookmaker_url": "TEXT",
        "bookmaker_name": "TEXT",
        "bookmaker_odds": "TEXT",
        "bookmaker_checked_at": "DATETIME",
        "bookmaker_resolved_at": "DATETIME",
        "private_message_refs": "TEXT",
        "public_message_refs": "TEXT",
        "video_script_sent_at": "DATETIME",
    }

    for name, sqlite_type in bot_user_columns.items():
        try:
            if IS_SQLITE:
                await conn.execute(text(f"ALTER TABLE bot_users ADD COLUMN {name} {sqlite_type}"))
            else:
                await conn.execute(text(f"ALTER TABLE bot_users ADD COLUMN IF NOT EXISTS {name} TIMESTAMP"))
        except Exception:
            pass

    for name, sqlite_type in prediction_columns.items():
        try:
            if IS_SQLITE:
                await conn.execute(text(f"ALTER TABLE predictions ADD COLUMN {name} {sqlite_type}"))
            else:
                pg_type = "TIMESTAMP" if name.endswith("_at") else "TEXT"
                await conn.execute(text(f"ALTER TABLE predictions ADD COLUMN IF NOT EXISTS {name} {pg_type}"))
        except Exception:
            pass
