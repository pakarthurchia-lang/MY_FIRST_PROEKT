from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot
from ozon.scraper import scrape_claims
from db.database import get_unalerted_claims, mark_alerted
from config import OWNER_CHAT_ID, CHECK_INTERVAL_MINUTES, ALERT_BEFORE_HOURS, ALERT_URGENT_HOURS


def format_alert(claim: dict, urgency: str) -> str:
    emoji = "🚨" if urgency == "urgent" else "⚠️"
    hours_left = ""
    if claim.get("deadline"):
        try:
            deadline = datetime.fromisoformat(claim["deadline"])
            diff = deadline - datetime.now()
            hours = int(diff.total_seconds() / 3600)
            if hours < 2:
                mins = int((diff.total_seconds() % 3600) / 60)
                hours_left = f"Осталось: {hours}ч {mins}мин"
            else:
                hours_left = f"Осталось: {hours} часов"
        except Exception:
            pass

    return (
        f"{emoji} <b>{'СРОЧНО' if urgency == 'urgent' else 'Напоминание'} — Ozon</b>\n\n"
        f"Претензия №{claim['id']}\n"
        f"📍 {claim['pvz']}\n"
        f"Причина: {claim.get('reason', '—')}\n"
        f"💰 Сумма: {claim.get('amount', 0):,.2f} руб.\n"
        f"⏰ {hours_left}\n\n"
        f"👉 https://turbo-pvz.ozon.ru/claims/{claim['id']}"
    )


async def check_claims_and_notify(bot: Bot):
    """Проверяет претензии и отправляет уведомления"""
    try:
        await scrape_claims()
    except Exception as e:
        err = str(e)
        if "401" in err or "токен" in err.lower() or "авторизац" in err.lower():
            await bot.send_message(OWNER_CHAT_ID, TOKEN_RENEWAL_INSTRUCTION, parse_mode="HTML")
        else:
            await bot.send_message(
                OWNER_CHAT_ID,
                f"❌ Не удалось обновить претензии: {e}"
            )
        return

    # Срочные (за 2 часа)
    urgent = await get_unalerted_claims(ALERT_URGENT_HOURS)
    for claim in urgent:
        await bot.send_message(OWNER_CHAT_ID, format_alert(claim, "urgent"), parse_mode="HTML")
        await mark_alerted(claim["id"], ALERT_URGENT_HOURS)

    # За 24 часа
    soon = await get_unalerted_claims(ALERT_BEFORE_HOURS)
    for claim in soon:
        await bot.send_message(OWNER_CHAT_ID, format_alert(claim, "warning"), parse_mode="HTML")
        await mark_alerted(claim["id"], ALERT_BEFORE_HOURS)


TOKEN_RENEWAL_INSTRUCTION = (
    "🔑 <b>Нужно обновить токен Ozon</b>\n\n"
    "Это займёт 10 секунд:\n\n"
    "1️⃣ Открой <b>turbo-pvz.ozon.ru</b> в браузере\n"
    "   (уже залогинен, ничего вводить не нужно)\n\n"
    "2️⃣ Нажми закладку <b>«Обновить токен ПВЗ»</b>\n"
    "   Появится сообщение «Скопировано!»\n\n"
    "3️⃣ Открой этот чат и нажми ⌘+V → отправь\n\n"
    "После этого бот продолжит работать в обычном режиме."
)


async def check_ozon_token(bot: Bot):
    """Проверяет токен Ozon и уведомляет если скоро истечёт или уже истёк."""
    import time
    from ozon.http_client import _load_token

    token = _load_token()
    if not token or not token.get("access_token"):
        await bot.send_message(OWNER_CHAT_ID, TOKEN_RENEWAL_INSTRUCTION, parse_mode="HTML")
        return

    # Проверяем refresh_token — если истекает в ближайшие 12 часов
    refresh_exp = token.get("refresh_expire_time", 0)
    if refresh_exp and refresh_exp > 1e12:
        refresh_exp /= 1000

    now = time.time()
    if refresh_exp and now >= refresh_exp - 12 * 3600:
        hours_left = max(0, int((refresh_exp - now) / 3600))
        warning = (
            f"⚠️ <b>Токен Ozon истекает через {hours_left} ч.</b>\n\n"
            + TOKEN_RENEWAL_INSTRUCTION
        )
        await bot.send_message(OWNER_CHAT_ID, warning, parse_mode="HTML")


async def send_daily_summary(bot: Bot):
    """Утренняя сводка в 9:00"""
    from db.database import get_active_claims

    claims = await get_active_claims()
    if not claims:
        await bot.send_message(OWNER_CHAT_ID, "☀️ Доброе утро!\n\n✅ Активных претензий нет.")
        return

    total = sum(c.get("amount", 0) for c in claims)
    expired = [c for c in claims if c.get("deadline") and datetime.fromisoformat(c["deadline"]) < datetime.now()]

    text = f"☀️ <b>Доброе утро! Сводка Ozon</b>\n\n"
    text += f"📋 Активных претензий: {len(claims)} шт.\n"
    text += f"💸 На сумму: {total:,.2f} руб.\n"
    if expired:
        text += f"❌ Просрочено: {len(expired)} шт. — требуют внимания!\n"
    text += "\nПодробнее: /claims"

    await bot.send_message(OWNER_CHAT_ID, text, parse_mode="HTML")


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

    # Проверка претензий каждые N минут
    scheduler.add_job(
        check_claims_and_notify,
        "interval",
        minutes=CHECK_INTERVAL_MINUTES,
        args=[bot],
        id="check_claims",
    )

    # Утренняя сводка в 9:00
    scheduler.add_job(
        send_daily_summary,
        "cron",
        hour=9,
        minute=0,
        args=[bot],
        id="daily_summary",
    )

    # Проверка токена Ozon раз в день в 10:00
    scheduler.add_job(
        check_ozon_token,
        "cron",
        hour=10,
        minute=0,
        args=[bot],
        id="check_ozon_token",
    )

    return scheduler
