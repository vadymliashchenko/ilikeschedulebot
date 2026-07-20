import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

load_dotenv()

from bot import config, db  # noqa: E402  (config reads env vars, must load after load_dotenv)
from bot.handlers import admin, poll  # noqa: E402
from bot.scheduler import setup_scheduler  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    conn = await db.init_db(config.DB_PATH)

    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp["conn"] = conn

    dp.include_router(admin.router)
    dp.include_router(poll.router)

    scheduler = setup_scheduler(bot, conn)
    scheduler.start()
    logger.info("Scheduler started")

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown()
        await conn.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
