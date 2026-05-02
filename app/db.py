from __future__ import annotations
from pathlib import Path
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.config import get_settings
from app.models import Base

settings = get_settings()
if settings.database_url.startswith("sqlite"):
    Path("./data").mkdir(parents=True, exist_ok=True)

engine = create_async_engine(settings.database_url, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def init_db() -> None:
    """Создать таблицы."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
