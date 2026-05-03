from __future__ import annotations

from datetime import datetime, timedelta
from sqlalchemy import select
from app.db import SessionLocal
from app.models import BotUser, Transaction

PLANS = {
    "1d": {"days": 1, "label_ru": "1 день", "label_en": "1 day"},
    "3d": {"days": 3, "label_ru": "3 дня", "label_en": "3 days"},
    "30d": {"days": 30, "label_ru": "1 месяц", "label_en": "1 month"},
}

def plan_days(plan_code: str) -> int:
    return int(PLANS.get(plan_code, PLANS["1d"])["days"])

def get_price_usdt(settings, plan_code: str) -> float:
    if plan_code == "1d":
        return settings.price_1_day_usdt
    if plan_code == "3d":
        return settings.price_3_days_usdt
    return settings.price_30_days_usdt

def get_price_stars(settings, plan_code: str) -> int:
    return max(1, int(round(get_price_usdt(settings, plan_code) * settings.stars_per_usdt)))

async def get_or_create_user(tg_user) -> BotUser:
    async with SessionLocal() as session:
        user = (await session.execute(
            select(BotUser).where(BotUser.telegram_user_id == str(tg_user.id))
        )).scalar_one_or_none()

        if not user:
            user = BotUser(
                telegram_user_id=str(tg_user.id),
                username=tg_user.username or "",
                first_name=tg_user.first_name or "",
                language_code="",
            )
            session.add(user)
        else:
            user.username = tg_user.username or user.username
            user.first_name = tg_user.first_name or user.first_name
            user.updated_at = datetime.utcnow()

        await session.commit()
        await session.refresh(user)
        return user

async def set_language(telegram_user_id: int | str, lang: str) -> None:
    async with SessionLocal() as session:
        user = (await session.execute(
            select(BotUser).where(BotUser.telegram_user_id == str(telegram_user_id))
        )).scalar_one_or_none()
        if user:
            user.language_code = lang
            user.updated_at = datetime.utcnow()
            await session.commit()

async def get_user_lang(telegram_user_id: int | str) -> str:
    async with SessionLocal() as session:
        user = (await session.execute(
            select(BotUser).where(BotUser.telegram_user_id == str(telegram_user_id))
        )).scalar_one_or_none()
        return user.language_code if user and user.language_code in {"ru", "en"} else "ru"

async def grant_access(telegram_user_id: int | str, plan_code: str) -> datetime:
    days = plan_days(plan_code)
    now = datetime.utcnow()

    async with SessionLocal() as session:
        user = (await session.execute(
            select(BotUser).where(BotUser.telegram_user_id == str(telegram_user_id))
        )).scalar_one_or_none()

        if not user:
            user = BotUser(telegram_user_id=str(telegram_user_id), language_code="ru")
            session.add(user)
            await session.flush()

        base = user.active_until if user.active_until and user.active_until > now else now
        user.active_until = base + timedelta(days=days)
        user.notified_24h_at = None
        user.notified_5h_at = None
        user.notified_1h_at = None
        user.kicked_at = None
        user.updated_at = now
        await session.commit()
        return user.active_until

async def create_transaction(
    telegram_user_id: int | str,
    provider: str,
    external_id: str,
    plan_code: str,
    amount_usdt: str = "",
    amount_stars: int = 0,
    payment_url: str = "",
    status: str = "created",
    raw_payload: str = "",
) -> Transaction:
    async with SessionLocal() as session:
        tx = Transaction(
            telegram_user_id=str(telegram_user_id),
            provider=provider,
            external_id=external_id,
            plan_code=plan_code,
            amount_usdt=amount_usdt,
            amount_stars=amount_stars,
            payment_url=payment_url,
            status=status,
            raw_payload=raw_payload,
        )
        session.add(tx)
        await session.commit()
        await session.refresh(tx)
        return tx

async def mark_transaction_paid(provider: str, external_id: str, raw_payload: str = "") -> Transaction | None:
    async with SessionLocal() as session:
        tx = (await session.execute(
            select(Transaction).where(Transaction.provider == provider, Transaction.external_id == external_id)
        )).scalar_one_or_none()

        if not tx:
            return None

        tx.status = "paid"
        tx.paid_at = datetime.utcnow()
        tx.raw_payload = raw_payload or tx.raw_payload
        await session.commit()
        await session.refresh(tx)
        return tx

async def user_transactions(telegram_user_id: int | str, limit: int = 8) -> list[Transaction]:
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(Transaction)
            .where(Transaction.telegram_user_id == str(telegram_user_id))
            .order_by(Transaction.created_at.desc())
            .limit(limit)
        )).scalars().all()
        return list(rows)
