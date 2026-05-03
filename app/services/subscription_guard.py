from __future__ import annotations

import logging
from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.i18n import t
from app.keyboards import main_menu, renew_subscription_keyboard
from app.models import BotUser

logger = logging.getLogger(__name__)


async def _notify(bot: Bot, user: BotUser, text_key: str) -> None:
    """Отправить уведомление о подписке сразу с кнопками продления."""
    lang = user.language_code if user.language_code in {"ru", "en"} else "ru"

    await bot.send_message(
        chat_id=user.telegram_user_id,
        text=t(lang, text_key) + "\n\n" + t(lang, "renew_subscription"),
        reply_markup=renew_subscription_keyboard(lang),
        disable_web_page_preview=True,
    )


async def check_subscriptions(bot: Bot) -> None:
    """Уведомить о скором окончании и удалить истёкших из приватного канала."""
    settings = get_settings()
    if not settings.telegram_private_channel_id:
        logger.info("Subscription guard skipped: TELEGRAM_PRIVATE_CHANNEL_ID is empty")
        return

    now = datetime.utcnow()
    async with SessionLocal() as session:
        users = (
            await session.execute(
                select(BotUser).where(BotUser.active_until.is_not(None))
            )
        ).scalars().all()

        for user in users:
            if not user.active_until:
                continue

            remaining = user.active_until - now

            try:
                if (
                    settings.subscription_notify_enabled
                    and timedelta(hours=23) <= remaining <= timedelta(hours=25)
                    and not user.notified_24h_at
                ):
                    await _notify(bot, user, "subscription_expire_24h")
                    user.notified_24h_at = now

                if (
                    settings.subscription_notify_enabled
                    and timedelta(hours=4, minutes=30) <= remaining <= timedelta(hours=5, minutes=30)
                    and not user.notified_5h_at
                ):
                    await _notify(bot, user, "subscription_expire_5h")
                    user.notified_5h_at = now

                if (
                    settings.subscription_notify_enabled
                    and timedelta(minutes=30) <= remaining <= timedelta(hours=1, minutes=30)
                    and not user.notified_1h_at
                ):
                    await _notify(bot, user, "subscription_expire_1h")
                    user.notified_1h_at = now

                if remaining <= timedelta(seconds=0) and not user.kicked_at:
                    if settings.subscription_kick_enabled:
                        try:
                            await bot.ban_chat_member(
                                chat_id=settings.telegram_private_channel_id,
                                user_id=int(user.telegram_user_id),
                            )
                            # Сразу снимаем бан, чтобы человек мог снова зайти после продления.
                            await bot.unban_chat_member(
                                chat_id=settings.telegram_private_channel_id,
                                user_id=int(user.telegram_user_id),
                                only_if_banned=True,
                            )
                        except Exception:
                            logger.exception("Failed to remove expired user %s from private channel", user.telegram_user_id)

                    try:
                        await _notify(bot, user, "subscription_expired")
                    except Exception:
                        logger.exception("Failed to notify expired user %s", user.telegram_user_id)

                    user.kicked_at = now

            except Exception:
                logger.exception("Subscription guard failed for user %s", user.telegram_user_id)

        await session.commit()
