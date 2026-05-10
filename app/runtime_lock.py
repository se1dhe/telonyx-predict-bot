from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db import IS_POSTGRES, engine

logger = logging.getLogger(__name__)

# Постоянный advisory-lock ID для TelOnyx Predict Bot.
# Значение должно быть стабильным между деплоями и одинаковым для всех инстансов.
TELEGRAM_RUNTIME_LOCK_ID = 8616650530


@dataclass
class RuntimeLock:
    """Глобальный runtime-lock для защиты Telegram polling и scheduler.

    Локальный asyncio.Lock защищает только один Python-процесс. На Railway может
    быть несколько процессов: старая реплика, второй service, ручной запуск или
    параллельный deploy. Telegram long polling при этом конфликтует:
    `Conflict: terminated by other getUpdates request`.

    Поэтому для production/PostgreSQL используем pg_try_advisory_lock().
    Пока соединение открыто — lock удерживается. Если процесс умер, PostgreSQL
    автоматически освободит lock вместе с соединением.
    """

    acquired: bool
    connection: AsyncConnection | None = None

    async def release(self) -> None:
        """Освободить lock при корректном завершении процесса."""
        if not self.acquired or self.connection is None:
            return

        try:
            await self.connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": TELEGRAM_RUNTIME_LOCK_ID},
            )
            logger.info("Runtime singleton lock released")
        except Exception:
            logger.exception("Failed to release runtime singleton lock")
        finally:
            await self.connection.close()
            self.acquired = False
            self.connection = None


async def acquire_runtime_lock() -> RuntimeLock:
    """Захватить межпроцессный lock для единственного Telegram runtime."""
    if not IS_POSTGRES:
        logger.warning("Runtime singleton lock skipped: database is not PostgreSQL")
        return RuntimeLock(acquired=True)

    connection = await engine.connect()
    result = await connection.execute(
        text("SELECT pg_try_advisory_lock(:lock_id)"),
        {"lock_id": TELEGRAM_RUNTIME_LOCK_ID},
    )
    acquired = bool(result.scalar())

    if acquired:
        logger.info("Runtime singleton lock acquired")
        return RuntimeLock(acquired=True, connection=connection)

    await connection.close()
    logger.warning(
        "Runtime singleton lock is already held by another instance. "
        "This process will keep web/health alive but will not start scheduler or Telegram polling."
    )
    return RuntimeLock(acquired=False)


async def idle_without_telegram_runtime() -> None:
    """Держать web/health процесс живым, не запуская Telegram polling."""
    while True:
        await asyncio.sleep(3600)
