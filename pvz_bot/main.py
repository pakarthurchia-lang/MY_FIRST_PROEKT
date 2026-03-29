import asyncio
import logging
import os
import signal
import sys
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from bot.handlers import auth, claims, menu, ym_upload, wb_upload, audit, location_setup
from db.database import init_db
from scheduler.jobs import setup_scheduler
from config import BOT_TOKEN, OWNER_CHAT_ID

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

PIDFILE = "data/bot.pid"


def _kill_previous():
    """Убивает предыдущий экземпляр бота если он запущен."""
    if not os.path.exists(PIDFILE):
        return
    try:
        with open(PIDFILE) as f:
            old_pid = int(f.read().strip())
        if old_pid != os.getpid():
            try:
                os.kill(old_pid, signal.SIGKILL)
                logger.info(f"Остановлен предыдущий экземпляр (pid {old_pid})")
                # Ждём завершения чтобы освободить Telegram polling
                import time
                time.sleep(2)
            except (ProcessLookupError, PermissionError):
                pass
    except (ValueError, OSError):
        pass


def _write_pid():
    os.makedirs("data", exist_ok=True)
    with open(PIDFILE, "w") as f:
        f.write(str(os.getpid()))


def _remove_pid():
    try:
        os.remove(PIDFILE)
    except FileNotFoundError:
        pass


async def main():
    await init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(auth.router)
    dp.include_router(menu.router)
    dp.include_router(location_setup.router)
    dp.include_router(audit.router)
    dp.include_router(ym_upload.router)
    dp.include_router(wb_upload.router)
    dp.include_router(claims.router)

    scheduler = setup_scheduler(bot)
    scheduler.start()

    try:
        await bot.send_message(
            OWNER_CHAT_ID,
            "🟢 <b>ПВЗ бот запущен!</b>\n\n"
            "/login — авторизация Ozon\n"
            "/wb_login — авторизация Wildberries\n"
            "/claims — претензии\n"
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
    _kill_previous()
    _write_pid()
    try:
        asyncio.run(main())
    finally:
        _remove_pid()
