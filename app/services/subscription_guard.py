from __future__ import annotations

import logging
from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.i18n import t, normalize_lang
from app.keyboards import renew_subscription_keyboard
from app.models import BotUser

logger = logging.getLogger(__name__)


async def _notify(bot: Bot, user: BotUser, key: str) -> None:
    """Уведомить пользователя о скором окончании / окончании подписки."""
    lang = normalize_lang(user.language_code)
    await bot.send_message(
        chat_id=user.telegram_user_id,
        text=t(lang, key),
        reply_markup=renew_subscription_keyboard(lang),
        disable_web_page_preview=True,
    )


async def check_subscriptions(bot: Bot) -> None:
    """Проверить подписки.

    Логика:
    - за 24 часа, 5 часов и 1 час отправляем предупреждение с кнопками оплаты;
    - после окончания удаляем пользователя из приватного канала его языка;
    - если канал для языка не заполнен — кик пропускается, но уведомление всё равно отправляется.
    """
    settings = get_settings()

    if not settings.subscription_notify_enabled and not settings.subscription_kick_enabled:
        return

    now = datetime.utcnow()
    threshold_24h = now + timedelta(hours=24)
    threshold_5h = now + timedelta(hours=5)
    threshold_1h = now + timedelta(hours=1)

    async with SessionLocal() as session:
        users = (await session.execute(
            select(BotUser).where(BotUser.active_until.is_not(None))
        )).scalars().all()

        for user in users:
            try:
                active_until = user.active_until
                if not active_until:
                    continue

                lang = normalize_lang(user.language_code)
                private_channel_id = settings.private_channel_for(lang)

                if active_until > now:
                    if settings.subscription_notify_enabled:
                        if active_until <= threshold_24h and user.notified_24h_at is None:
                            await _notify(bot, user, "subscription_expire_24h")
                            user.notified_24h_at = now

                        if active_until <= threshold_5h and user.notified_5h_at is None:
                            await _notify(bot, user, "subscription_expire_5h")
                            user.notified_5h_at = now

                        if active_until <= threshold_1h and user.notified_1h_at is None:
                            await _notify(bot, user, "subscription_expire_1h")
                            user.notified_1h_at = now

                    continue

                # Подписка истекла.
                if user.kicked_at is None:
                    if settings.subscription_kick_enabled and private_channel_id:
                        try:
                            await bot.ban_chat_member(
                                chat_id=private_channel_id,
                                user_id=int(user.telegram_user_id),
                            )
                            await bot.unban_chat_member(
                                chat_id=private_channel_id,
                                user_id=int(user.telegram_user_id),
                                only_if_banned=True,
                            )
                        except Exception:
                            logger.exception(
                                "Failed to remove expired user %s from private channel lang=%s",
                                user.telegram_user_id,
                                lang,
                            )

                    try:
                        await _notify(bot, user, "subscription_expired")
                    except Exception:
                        logger.exception("Failed to notify expired user %s", user.telegram_user_id)

                    user.kicked_at = now

            except Exception:
                logger.exception("Subscription guard failed for user %s", user.telegram_user_id)

        await session.commit()
