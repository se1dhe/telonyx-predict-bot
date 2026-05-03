from __future__ import annotations

from datetime import datetime, timedelta, timezone
from aiogram import Bot
from app.config import get_settings

async def create_private_invite(bot: Bot, name: str = "TelOnyx Predict VIP") -> str:
    """Создать одноразовую ссылку в приватный канал.

    Бот должен быть администратором приватного канала с правом invite users.
    """
    settings = get_settings()
    if not settings.telegram_private_channel_id:
        raise RuntimeError("TELEGRAM_PRIVATE_CHANNEL_ID is empty")

    link = await bot.create_chat_invite_link(
        chat_id=settings.telegram_private_channel_id,
        name=name,
        expire_date=datetime.now(timezone.utc) + timedelta(hours=24),
        member_limit=1,
        creates_join_request=False,
    )
    return link.invite_link
