import logging
import os
ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
from aiogram import Bot, Dispatcher
from config import BOT_TOKEN

# Initialize bot and dispatcher
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher() 