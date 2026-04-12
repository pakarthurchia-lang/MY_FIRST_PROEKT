import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

import config
from db.database import init_db
from bot.handlers import start, food_input, diary, stats, settings, journal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    await init_db()
    logger.info("Database initialized.")

    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    # Order matters: settings FSM must be registered before the generic text handler
    dp.include_router(start.router)
    dp.include_router(settings.router)   # FSM: goals
    dp.include_router(diary.router)      # FSM: edit weight/name
    dp.include_router(journal.router)    # FSM: close day note
    dp.include_router(stats.router)
    dp.include_router(food_input.router) # catches all remaining text + voice

    logger.info("Starting polling...")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
