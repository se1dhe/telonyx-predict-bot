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


def telonyx_page(title: str, subtitle: str, status: str, accent: str) -> str:
    """Мини-страница в стиле TelOnyx для redirect-страниц PayKassa."""
    return f"""<!doctype html>
<html lang="uk">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title} · TelOnyx Predict</title>
  <meta name="robots" content="noindex,nofollow" />
  <style>
    :root {{
      --bg: #07111f;
      --card: rgba(17, 30, 48, .88);
      --card2: rgba(24, 43, 68, .72);
      --text: #f3f7ff;
      --muted: #9fb2cc;
      --line: rgba(115, 135, 170, .24);
      --blue: #4f7cff;
      --cyan: #27d4ff;
      --green: #22c55e;
      --red: #ef4444;
      --accent: {accent};
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      min-height: 100vh;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at 20% 10%, rgba(39, 212, 255, .18), transparent 32%),
        radial-gradient(circle at 80% 0%, rgba(79, 124, 255, .2), transparent 28%),
        linear-gradient(135deg, #06101d 0%, #0a1525 48%, #07111f 100%);
      color: var(--text);
      display: grid;
      place-items: center;
      padding: 24px;
    }}

    .wrap {{
      width: min(720px, 100%);
    }}

    .brand {{
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 18px;
      color: var(--muted);
      letter-spacing: .08em;
      text-transform: uppercase;
      font-size: 13px;
      font-weight: 700;
    }}

    .logo {{
      width: 42px;
      height: 42px;
      border-radius: 16px;
      display: grid;
      place-items: center;
      background: linear-gradient(135deg, var(--blue), var(--cyan));
      box-shadow: 0 14px 50px rgba(39, 212, 255, .22);
      color: white;
      font-size: 22px;
    }}

    .card {{
      border: 1px solid var(--line);
      background: linear-gradient(180deg, var(--card), var(--card2));
      border-radius: 28px;
      box-shadow: 0 30px 100px rgba(0, 0, 0, .38);
      overflow: hidden;
    }}

    .top {{
      padding: 34px;
      border-bottom: 1px solid var(--line);
    }}

    .status {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      padding: 10px 14px;
      border-radius: 999px;
      background: color-mix(in srgb, var(--accent) 16%, transparent);
      color: var(--accent);
      font-weight: 800;
      margin-bottom: 18px;
    }}

    h1 {{
      font-size: clamp(34px, 6vw, 58px);
      line-height: .98;
      margin: 0 0 16px;
      letter-spacing: -0.06em;
    }}

    p {{
      font-size: 18px;
      line-height: 1.55;
      color: var(--muted);
      margin: 0;
    }}

    .actions {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
      padding: 24px 34px 34px;
    }}

    a {{
      color: inherit;
      text-decoration: none;
    }}

    .btn {{
      display: flex;
      justify-content: center;
      align-items: center;
      min-height: 54px;
      padding: 14px 18px;
      border-radius: 18px;
      font-weight: 800;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, .06);
      transition: .18s ease;
    }}

    .btn.primary {{
      background: linear-gradient(135deg, var(--blue), var(--cyan));
      color: white;
      border-color: transparent;
      box-shadow: 0 18px 50px rgba(39, 212, 255, .24);
    }}

    .btn:hover {{
      transform: translateY(-1px);
      filter: brightness(1.08);
    }}

    .hint {{
      padding: 0 34px 28px;
      color: var(--muted);
      font-size: 14px;
    }}

    @media (max-width: 560px) {{
      .actions {{
        grid-template-columns: 1fr;
      }}
      .top, .actions {{
        padding-left: 22px;
        padding-right: 22px;
      }}
      .hint {{
        padding-left: 22px;
        padding-right: 22px;
      }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <div class="brand">
      <div class="logo">⚽</div>
      <div>TelOnyx Predict</div>
    </div>

    <section class="card">
      <div class="top">
        <div class="status">{status}</div>
        <h1>{title}</h1>
        <p>{subtitle}</p>
      </div>

      <div class="actions">
        <a class="btn primary" href="https://t.me/telonyx_predict">Відкрити канал</a>
        <a class="btn" href="https://t.me/telonyx_predict_bot">Відкрити бота</a>
      </div>

      <div class="hint">
        Якщо доступ не з’явився одразу — поверніться в Telegram-бота. Зазвичай підтвердження приходить автоматично після callback від PayKassa.
      </div>
    </section>
  </main>
</body>
</html>"""


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "telonyx-predict"})


async def payment_success(request: web.Request) -> web.Response:
    html = telonyx_page(
        title="Оплату прийнято",
        subtitle="Платіж успішно створено або завершено. Після підтвердження PayKassa бот автоматично видасть доступ до приватного каналу.",
        status="✅ Успішна оплата",
        accent="#22c55e",
    )
    return web.Response(text=html, content_type="text/html")


async def payment_fail(request: web.Request) -> web.Response:
    html = telonyx_page(
        title="Оплату не завершено",
        subtitle="Платіж було скасовано, не проведено або він закінчився за часом. Ви можете повернутися в бот і створити новий рахунок.",
        status="⚠️ Помилка оплати",
        accent="#ef4444",
    )
    return web.Response(text=html, content_type="text/html")


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
        invite_url = await create_private_invite(bot, lang=lang, name=f"PayKassa {tx.telegram_user_id}")

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
    app.router.add_get("/payment/success", payment_success)
    app.router.add_get("/payment/fail", payment_fail)
    app.router.add_post("/paykassa/ipn", paykassa_ipn)
    return app


async def start_web_server(bot: Bot, host: str, port: int) -> web.AppRunner:
    app = create_web_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    return runner
