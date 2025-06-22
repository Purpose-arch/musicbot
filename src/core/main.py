import asyncio
from src.core.bot_instance import bot, dp
import src.handlers # register handlers # noqa: F401
from src.core.config import BOT_TOKEN, WEB_APP_URL, PORT
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
import logging

# Configure logging (similar to mainexample.py)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
    WEBHOOK_URL = WEB_APP_URL + WEBHOOK_PATH

    logger.info(f"Configuring webhook at {WEBHOOK_URL}")
    logger.info(f"Attempting to set webhook with URL: {WEBHOOK_URL}")

    # Set webhook
    await bot.set_webhook(WEBHOOK_URL)

    # Create aiohttp web application
    app = web.Application()

    # Create an instance of request handler, it will handle all webhook requests
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        handle_in_background=True, # Recommended for handling updates asynchronously
    )

    # Register webhook handler
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)

    # Setup application for graceful shutdown
    setup_application(app, dp, bot=bot)

    # Start aiohttp server
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT) # Use 0.0.0.0 to listen on all interfaces
    logger.info(f"Starting web server on port {PORT}...")
    await site.start()

    # Keep the main coroutine running indefinitely
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main()) 
