from __future__ import annotations

import json
import uuid
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

from app.config import get_settings
from app.i18n import t
from app.keyboards import back_keyboard, lang_keyboard, main_menu, pay_url_keyboard, payment_keyboard, plans_keyboard
from app.services.access import create_private_invite
from app.services.paykassa import PayKassaClient
from app.services.subscriptions import (
    create_transaction,
    get_or_create_user,
    get_price_stars,
    get_price_usdt,
    get_user_lang,
    grant_access,
    mark_transaction_paid,
    plan_days,
    set_language,
    user_transactions,
)

router = Router()


@router.message(CommandStart())
async def start(message: Message) -> None:
    user = await get_or_create_user(message.from_user)
    if not user.language_code:
        await message.answer(t("ru", "choose_lang"), reply_markup=lang_keyboard())
        return

    await message.answer(
        t(user.language_code, "start") + "\n\n" + t(user.language_code, "menu"),
        reply_markup=main_menu(user.language_code),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("lang:"))
async def choose_language(callback: CallbackQuery) -> None:
    lang = callback.data.split(":", 1)[1]
    await get_or_create_user(callback.from_user)
    await set_language(callback.from_user.id, lang)
    await callback.message.edit_text(
        t(lang, "lang_saved") + "\n\n" + t(lang, "start") + "\n\n" + t(lang, "menu"),
        reply_markup=main_menu(lang),
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data == "menu:language")
async def menu_language(callback: CallbackQuery) -> None:
    await callback.message.edit_text(t("ru", "choose_lang"), reply_markup=lang_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:main")
async def menu_main(callback: CallbackQuery) -> None:
    lang = await get_user_lang(callback.from_user.id)
    await callback.message.edit_text(
        t(lang, "start") + "\n\n" + t(lang, "menu"),
        reply_markup=main_menu(lang),
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data == "menu:plans")
async def menu_plans(callback: CallbackQuery) -> None:
    lang = await get_user_lang(callback.from_user.id)
    await callback.message.edit_text(t(lang, "plans"), reply_markup=plans_keyboard(lang))
    await callback.answer()


@router.callback_query(F.data.startswith("plan:"))
async def select_plan(callback: CallbackQuery) -> None:
    lang = await get_user_lang(callback.from_user.id)
    plan_code = callback.data.split(":", 1)[1]
    await callback.message.edit_text(t(lang, "plans"), reply_markup=payment_keyboard(lang, plan_code))
    await callback.answer()


@router.callback_query(F.data.startswith("pay:stars:"))
async def pay_stars(callback: CallbackQuery, bot: Bot) -> None:
    settings = get_settings()
    lang = await get_user_lang(callback.from_user.id)
    plan_code = callback.data.split(":")[-1]
    days = plan_days(plan_code)
    amount_stars = get_price_stars(settings, plan_code)
    external_id = f"stars_{callback.from_user.id}_{plan_code}_{uuid.uuid4().hex[:12]}"

    await create_transaction(
        telegram_user_id=callback.from_user.id,
        provider="stars",
        external_id=external_id,
        plan_code=plan_code,
        amount_stars=amount_stars,
        status="created",
    )

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=t(lang, "invoice_title"),
        description=t(lang, "invoice_desc", days=days),
        payload=external_id,
        provider_token=settings.stars_provider_token,
        currency="XTR",
        prices=[LabeledPrice(label=t(lang, "invoice_title"), amount=amount_stars)],
    )
    await callback.answer()


@router.pre_checkout_query()
async def pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_stars_payment(message: Message, bot: Bot) -> None:
    payload = message.successful_payment.invoice_payload
    tx = await mark_transaction_paid("stars", payload, raw_payload=message.successful_payment.model_dump_json())
    lang = await get_user_lang(message.from_user.id)

    if not tx:
        await message.answer("Payment received, but transaction was not found. Contact support.")
        return

    await grant_access(message.from_user.id, tx.plan_code)
    invite_url = await create_private_invite(bot, name=f"Stars {message.from_user.id}")

    await message.answer(
        t(lang, "paid") + "\n\n" + t(lang, "invite", url=invite_url),
        reply_markup=main_menu(lang),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("pay:paykassa:"))
async def pay_paykassa(callback: CallbackQuery) -> None:
    settings = get_settings()
    lang = await get_user_lang(callback.from_user.id)
    plan_code = callback.data.split(":")[-1]

    if not settings.paykassa_enabled:
        await callback.message.edit_text(t(lang, "paykassa_disabled"), reply_markup=payment_keyboard(lang, plan_code))
        await callback.answer()
        return

    amount = get_price_usdt(settings, plan_code)
    order_id = f"pk_{callback.from_user.id}_{plan_code}_{uuid.uuid4().hex[:12]}"
    payment_url = await PayKassaClient().create_order(
        amount=amount,
        order_id=order_id,
        comment=f"TelOnyx Predict {plan_code} user {callback.from_user.id}",
    )

    await create_transaction(
        telegram_user_id=callback.from_user.id,
        provider="paykassa",
        external_id=order_id,
        plan_code=plan_code,
        amount_usdt=f"{amount:.2f}",
        payment_url=payment_url,
        status="created",
    )

    await callback.message.edit_text(
        t(lang, "payment_created"),
        reply_markup=pay_url_keyboard(lang, payment_url),
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data == "menu:cabinet")
async def cabinet(callback: CallbackQuery) -> None:
    user = await get_or_create_user(callback.from_user)
    lang = await get_user_lang(callback.from_user.id)
    txs = await user_transactions(callback.from_user.id)

    lines = [t(lang, "cabinet_title"), ""]
    if user.active_until and user.active_until > datetime.utcnow():
        lines.append(t(lang, "active_until", date=user.active_until.strftime("%Y-%m-%d %H:%M UTC")))
    else:
        lines.append(t(lang, "no_access"))

    lines.append("")
    lines.append(f"💳 <b>{t(lang, 'transactions')}:</b>")

    if not txs:
        lines.append(t(lang, "no_transactions"))
    else:
        for tx in txs:
            amount = f"{tx.amount_stars} ⭐" if tx.provider == "stars" else f"{tx.amount_usdt} USDT"
            lines.append(f"• {tx.created_at.strftime('%Y-%m-%d')} · {tx.provider} · {tx.plan_code} · {amount} · {tx.status}")

    await callback.message.edit_text("\n".join(lines), reply_markup=back_keyboard(lang), disable_web_page_preview=True)
    await callback.answer()
