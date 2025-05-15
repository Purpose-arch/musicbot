import os
import logging
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = os.getenv('ADMIN_ID')

# VK API credentials
VK_LOGIN = os.getenv('VK_LOGIN', '')
VK_PASSWORD = os.getenv('VK_PASSWORD', '')
VK_DEBUG = os.getenv('VK_DEBUG', 'false').lower() == 'true'  # Для расширенной отладки VK

# Проверка наличия учетных данных VK при запуске
if not VK_LOGIN or not VK_PASSWORD:
    print("⚠️ WARNING: VK_LOGIN and/or VK_PASSWORD environment variables are not set.")
    print("❌ VK Music search functionality will be DISABLED.")
    VK_ENABLED = False
else:
    print(f"✅ VK credentials found: login length {len(VK_LOGIN)}, password length {len(VK_PASSWORD)}")
    print(f"🔍 VK_DEBUG mode: {'ENABLED' if VK_DEBUG else 'DISABLED'}")
    VK_ENABLED = True

if VK_DEBUG:
    print("🔧 Configuring VK debugging:")
    vk_logger = logging.getLogger('vkpymusic')
    vk_logger.setLevel(logging.DEBUG)
    vk_handler = logging.StreamHandler()
    vk_handler.setFormatter(logging.Formatter('%(asctime)s | %(filename)s(%(lineno)d) | %(funcName)s(...) | [%(levelname)s] %(message)s'))
    vk_logger.addHandler(vk_handler)
    print("✅ VK library debug logging enabled")

# Параметры для личных чатов
TRACKS_PER_PAGE = 10
MAX_TRACKS = 300

# Параметры для групповых чатов
GROUP_TRACKS_PER_PAGE = 5
GROUP_MAX_TRACKS = 150

# DEPRECATED: MAX_RETRIES = 3  
MIN_SONG_DURATION = 45  # seconds
MAX_SONG_DURATION = 720  # seconds (12 minutes)

MAX_PARALLEL_DOWNLOADS = 5

YDL_AUDIO_OPTS = {
    'format': 'bestaudio[ext=m4a]/bestaudio/best',
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }],
    'quiet': True,
    'no_warnings': True,
    'extract_flat': True,
    'prefer_ffmpeg': True,
    'nocheckcertificate': True,
    'ignoreerrors': True,
    'ffmpeg_location': '/usr/bin/ffmpeg',
}