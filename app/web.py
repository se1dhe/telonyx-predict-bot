from __future__ import annotations

import json
import logging
from aiohttp import web
from aiogram import Bot

from app.i18n import t
from app.services.access import create_private_invite
from app.services.paykassa import PayKassaClient
from app.services.subscriptions import get_user_lang, grant_access, mark_transaction_paid

logger = logging.getLogger(__name__)


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def paykassa_ipn(request: web.Request) -> web.Response:
    bot: Bot = request.app["bot"]

    data = dict(await request.post())
    private_hash = data.get("private_hash") or data.get("hash")

    try:
        if not private_hash:
            raise RuntimeError("private_hash is empty")

        confirmed = await PayKassaClient().confirm_order(private_hash)
        payload = confirmed.get("data", confirmed)
        order_id = str(payload.get("order_id") or payload.get("orderId") or data.get("order_id") or "")

        if not order_id:
            raise RuntimeError(f"order_id not found in PayKassa payload: {confirmed}")

        tx = await mark_transaction_paid("paykassa", order_id, raw_payload=json.dumps({"ipn": data, "confirmed": confirmed}, ensure_ascii=False))
        if not tx:
            raise RuntimeError(f"transaction not found: {order_id}")

        await grant_access(tx.telegram_user_id, tx.plan_code)
        lang = await get_user_lang(tx.telegram_user_id)
        invite_url = await create_private_invite(bot, name=f"PayKassa {tx.telegram_user_id}")

        await bot.send_message(
            chat_id=tx.telegram_user_id,
            text=t(lang, "paid") + "\n\n" + t(lang, "invite", url=invite_url),
            disable_web_page_preview=True,
        )

        return web.Response(text=f"{order_id}|success")

    except Exception as exc:
        logger.exception("PayKassa IPN failed")
        return web.Response(status=400, text=f"error: {exc}")


def create_web_app(bot: Bot) -> web.Application:
    app = web.Application()
    app["bot"] = bot
    app.router.add_get("/health", health)
    app.router.add_post("/paykassa/ipn", paykassa_ipn)
    return app


async def start_web_server(bot: Bot, host: str, port: int) -> web.AppRunner:
    app = create_web_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    return runner
