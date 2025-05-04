from collections import defaultdict

# Global state storage
# download_tasks: user_id -> {url: asyncio.Task}
# search_results: search_id -> list of track dicts
# download_queues: user_id -> list of queued items (track_data, playlist_id)
# playlist_downloads: playlist_id -> playlist tracking info

download_tasks = defaultdict(dict)
search_results = {}
download_queues = defaultdict(list)
playlist_downloads = {}

# Настройки пользователей (сохраняется в PostgreSQL)
user_settings = {} 