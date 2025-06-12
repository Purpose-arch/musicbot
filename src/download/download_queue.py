# download_queue.py
import asyncio

from src.core.state import download_queues, download_tasks, playlist_downloads
from src.core.config import MAX_PARALLEL_DOWNLOADS

async def process_download_queue(user_id):
    # local import to avoid circular dependency
    from .track_downloader import download_track
    """Обработка очереди загрузок для пользователя"""
    while download_queues[user_id] and len(download_tasks[user_id]) < MAX_PARALLEL_DOWNLOADS:
        queue_item = download_queues[user_id].pop(0)
        playlist_download_id = None
        if isinstance(queue_item, tuple) and len(queue_item) == 2:
            track_data, second_item = queue_item
            if isinstance(second_item, str):  # playlist ID
                playlist_download_id = second_item
        else:
            print(f"[Queue Processing] Error: Unexpected item format in queue for user {user_id}: {queue_item}")
            continue

        # Update playlist status if applicable
        if playlist_download_id and playlist_download_id in playlist_downloads:
            playlist_entry = playlist_downloads[playlist_download_id]
            for track in playlist_entry['tracks']:
                if track['url'] == track_data['url'] and track['status'] == 'pending':
                    track['status'] = 'downloading'
                    print(f"[Queue Processing] Set track {track_data['url']} in playlist {playlist_download_id} to 'downloading'.")
                    break

        # Ensure tasks dict
        if user_id not in download_tasks:
            download_tasks[user_id] = {}

        # Skip if already downloading this URL
        if track_data['url'] in download_tasks[user_id]:
            print(f"[Queue Processing] Warning: Task for URL {track_data['url']} already exists for user {user_id}.")
            continue

        # Create and store task
        print(f"[Queue Processing] Creating download task for: {track_data.get('title')} (Playlist ID: {playlist_download_id})")
        task = asyncio.create_task(
            download_track(user_id, track_data, playlist_download_id=playlist_download_id)
        )
        download_tasks[user_id][track_data['url']] = task 