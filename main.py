import asyncio
from bot_instance import bot, dp
import handlers  # register handlers

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main()) 
