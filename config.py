import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = os.getenv('ADMIN_ID')

# Параметры для личных чатов
TRACKS_PER_PAGE = 10
MAX_TRACKS = 300

# Параметры для групповых чатов
GROUP_TRACKS_PER_PAGE = 5
GROUP_MAX_TRACKS = 150

# DEPRECATED: MAX_RETRIES = 3  
MIN_SONG_DURATION = 30  # seconds - минимальная длительность трека для поиска
MAX_SONG_DURATION = 900  # seconds (15 minutes) - максимальная длительность трека для поиска

# Минимальная длительность для треков, чтобы они не считались превью
MIN_PREVIEW_DURATION = 90  # seconds - если меньше, считаем превью и пытаемся найти полную версию

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