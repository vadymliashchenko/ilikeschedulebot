import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

load_dotenv()

from bot import config, db, tolik, tolik_db  # noqa: E402  (config reads env vars, must load after load_dotenv)
from bot.handlers import admin, corrections, manager, poll  # noqa: E402
from bot.handlers import tolik as tolik_handlers  # noqa: E402
from bot.scheduler import setup_scheduler  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    conn = await db.init_db(config.DB_PATH)
    await tolik_db.init(conn)

    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp["conn"] = conn

    dp.include_router(tolik_handlers.router)  # Толік: лише свої команди й кнопки «tk:»
    dp.include_router(admin.router)
    dp.include_router(manager.router)
    dp.include_router(poll.router)
    dp.include_router(corrections.router)

    scheduler = setup_scheduler(bot, conn)
    tolik.add_jobs(scheduler, bot, conn)
    scheduler.start()
    logger.info("Scheduler started")

    from bot import tolik_adjust
    web_runner = None
    try:
        web_runner = await tolik_adjust.run_web(conn)
    except Exception:  # noqa: BLE001 — сервер для HA не має валити бота
        logger.exception("Tolik web failed to start")

    try:
        await dp.start_polling(bot)
    finally:
        if web_runner is not None:
            await web_runner.cleanup()
        scheduler.shutdown()
        await conn.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
