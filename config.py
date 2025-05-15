import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = os.getenv('ADMIN_ID')

# VK API credentials
VK_LOGIN = os.getenv('VK_LOGIN', '')
VK_PASSWORD = os.getenv('VK_PASSWORD', '')

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