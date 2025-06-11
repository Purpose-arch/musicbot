import asyncio
from src.core.bot_instance import bot, dp
import src.handlers # register handlers # noqa: F401

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main()) 
