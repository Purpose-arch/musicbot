import logging
import os
ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
from aiogram import Bot, Dispatcher
from src.core.config import BOT_TOKEN

TELEGRAM_API_URL = os.getenv('TELEGRAM_API_URL')

# Initialize bot and dispatcher
bot = Bot(token=BOT_TOKEN, base_url=TELEGRAM_API_URL)
dp = Dispatcher() 