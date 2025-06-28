import asyncio
import subprocess
import os
import time
import httpx # Для асинхронных HTTP-запросов

from src.core.bot_instance import bot, dp
import src.handlers # register handlers # noqa: F401
from src.core.config import BOT_TOKEN, WEB_APP_URL, PORT # BOT_TOKEN теперь не используется напрямую для base_url

from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# URL локального сервера Bot API
LOCAL_API_URL = "http://localhost:8080"

async def start_telegram_api_server():
    """Starts the local Telegram Bot API server and waits for it to be ready."""
    logger.info("Attempting to start local Telegram Bot API server...")

    # Read API credentials from environment variables
    # Эти переменные теперь будут считываться непосредственно в main.py, а не в Dockerfile
    # Убедитесь, что они установлены в среде Railway
    telegram_api_id = os.getenv('TELEGRAM_API_ID')
    telegram_api_hash = os.getenv('TELEGRAM_API_HASH')
    allowed_bot_ids = os.getenv('ALLOWED_BOT_IDS') # Числовой ID бота

    if not all([telegram_api_id, telegram_api_hash, allowed_bot_ids]):
        logger.error("Missing TELEGRAM_API_ID, TELEGRAM_API_HASH, or ALLOWED_BOT_IDS environment variables.")
        raise ValueError("API server environment variables are not set.")

    # Command to run the local Telegram Bot API server
    # Note: --local flag is important for file uploads
    # --max-webhook-connections allows more connections for better performance
    cmd = [
        "/usr/local/bin/telegram-bot-api", # Путь, куда мы скопировали исполняемый файл в Dockerfile
        "--api-id", telegram_api_id,
        "--api-hash", telegram_api_hash,
        "--allowed-bot-ids", allowed_bot_ids,
        "--local",
        "--http-port", str(PORT), # Убедимся, что сервер слушает на том же порту, что и вебхук бота
        "--max-webhook-connections", "100" # Увеличиваем количество webhook-соединений
    ]
    
    # Запускаем сервер как подпроцесс
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    logger.info(f"Telegram Bot API server started with PID: {process.pid}")

    # Health check for the API server
    max_retries = 30
    delay = 1 # seconds
    for i in range(max_retries):
        try:
            async with httpx.AsyncClient() as client:
                # Используем URL с токеном бота для проверки (любой токен разрешенного бота)
                # Хотя /getMe не требует токена, это хорошая практика
                # Для упрощения, можно использовать просто /getMe без токена
                response = await client.get(f"{LOCAL_API_URL}/bot{BOT_TOKEN}/getMe", timeout=5)
                response.raise_for_status()
                logger.info(f"Local Telegram Bot API server is ready after {i+1} attempts.")
                return process # Возвращаем процесс, чтобы держать его в живых
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            logger.warning(f"Attempt {i+1}/{max_retries}: Local Telegram Bot API server not ready. Retrying in {delay}s. Error: {e}")
            await asyncio.sleep(delay)
    
    logger.error("Failed to start local Telegram Bot API server within the retry limit.")
    # Если сервер не запустился, завершаем приложение
    process.terminate()
    raise RuntimeError("Local Telegram Bot API server failed to start.")


async def main():
    # Запускаем локальный API сервер перед ботом
    api_server_process = await start_telegram_api_server()

    # WEBHOOK_PATH и WEBHOOK_URL остаются такими же, как и раньше
    WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
    WEBHOOK_URL = WEB_APP_URL + WEBHOOK_PATH # WEB_APP_URL должен быть публичным URL от Railway

    logger.info(f"Configuring webhook at {WEBHOOK_URL}")
    logger.info(f"Attempting to set webhook with URL: {WEBHOOK_URL}")

    # Set webhook
    # Убедитесь, что base_url для бота установлен в src/core/bot_instance.py на localhost:8080
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
    # Сервер должен слушать на 0.0.0.0, чтобы быть доступным извне контейнера Railway
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    logger.info(f"Starting web server on port {PORT}...")
    await site.start()

    # Keep the main coroutine running indefinitely
    # Убедимся, что процесс API сервера тоже не завершится
    try:
        await asyncio.Event().wait()
    finally:
        # Убедимся, что процесс API сервера будет завершен при остановке бота
        if api_server_process and api_server_process.poll() is None:
            logger.info("Terminating local Telegram Bot API server process...")
            api_server_process.terminate()
            await asyncio.to_thread(api_server_process.wait, timeout=5) # Ждем завершения
            if api_server_process.poll() is None:
                logger.warning("Local Telegram Bot API server did not terminate gracefully. Killing...")
                api_server_process.kill()


if __name__ == "__main__":
    asyncio.run(main()) 
