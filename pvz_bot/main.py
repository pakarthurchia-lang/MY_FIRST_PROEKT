import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from bot.handlers import auth, claims, menu, turnover, ym_upload
from db.database import init_db
from scheduler.jobs import setup_scheduler
from config import BOT_TOKEN, OWNER_CHAT_ID

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    await init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(menu.router)
    dp.include_router(turnover.router)
    dp.include_router(ym_upload.router)
    dp.include_router(auth.router)
    dp.include_router(claims.router)

    scheduler = setup_scheduler(bot)
    scheduler.start()

    try:
        await bot.send_message(
            OWNER_CHAT_ID,
            "🟢 <b>ПВЗ бот запущен!</b>\n\n"
            "Используй /login для авторизации в Ozon\n"
            "Затем /claims чтобы увидеть претензии\n"
            "/help — все команды",
            parse_mode="HTML"
        )
    except Exception:
        logger.info("Не могу отправить стартовое сообщение — напиши боту /start в Telegram")

    logger.info("Бот запущен")

    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        scheduler.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
