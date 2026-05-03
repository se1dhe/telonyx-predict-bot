from __future__ import annotations
from pathlib import Path
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy import text
from app.config import get_settings
from app.models import Base

settings = get_settings()
if settings.database_url.startswith("sqlite"):
    Path("./data").mkdir(parents=True, exist_ok=True)

engine = create_async_engine(settings.database_url, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def init_db() -> None:
    """Создать таблицы и добавить безопасные runtime-колонки."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await ensure_runtime_columns(conn)


async def ensure_runtime_columns(conn) -> None:
    """Мини-миграции без Alembic.

    Railway часто уже имеет созданную sqlite-базу. create_all не добавляет новые колонки,
    поэтому добавляем нужные поля вручную и игнорируем ошибку, если колонка уже есть.
    """
    columns = {
        "notified_24h_at": "DATETIME",
        "notified_5h_at": "DATETIME",
        "notified_1h_at": "DATETIME",
        "kicked_at": "DATETIME",
    }

    for name, sql_type in columns.items():
        try:
            if settings.database_url.startswith("sqlite"):
                await conn.execute(text(f"ALTER TABLE bot_users ADD COLUMN {name} {sql_type}"))
            else:
                await conn.execute(text(f"ALTER TABLE bot_users ADD COLUMN IF NOT EXISTS {name} TIMESTAMP"))
        except Exception:
            # Колонка уже существует или таблицы ещё нет в старом окружении.
            pass
