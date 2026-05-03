from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram import Bot

from app.config import get_settings
from app.i18n import normalize_lang


async def create_private_invite(bot: Bot, lang: str = "uk", name: str = "TelOnyx Predict VIP") -> str:
    """Создать одноразовую ссылку в приватный канал выбранного языка.

    Бот должен быть администратором нужного приватного канала с правом invite users.
    Если канал для языка не заполнен в .env — доступ не выдаётся.
    """
    settings = get_settings()
    lang = normalize_lang(lang)
    channel_id = settings.private_channel_for(lang)

    if not channel_id:
        raise RuntimeError(f"Private channel for language {lang} is empty")

    link = await bot.create_chat_invite_link(
        chat_id=channel_id,
        name=f"{name} {lang.upper()}",
        expire_date=datetime.now(timezone.utc) + timedelta(hours=24),
        member_limit=1,
        creates_join_request=False,
    )
    return link.invite_link
