import asyncio
from src.core.bot_instance import bot, dp
import src.handlers # register handlers # noqa: F401
from src.core.config import BOT_TOKEN
import logging

# Configure logging (similar to mainexample.py)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    logger.info("Starting bot in polling mode...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main()) 
