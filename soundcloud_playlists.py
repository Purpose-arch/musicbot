import asyncio
import os
import tempfile
import uuid
import logging
import yt_dlp
import traceback
from aiogram import types
from aiogram.types import FSInputFile

from bot_instance import bot, ADMIN_ID
from config import MAX_TRACKS, YDL_AUDIO_OPTS
from state import playlist_downloads, download_tasks, download_queues
from track_downloader import download_track
from utils import extract_title_and_artist

# Disable debug prints
import builtins
print = lambda *args, **kwargs: None
traceback.print_exc = lambda *args, **kwargs: None

logger = logging.getLogger(__name__)

# Настройки для поиска плейлистов SoundCloud
SC_PLAYLIST_SEARCH_OPTS = {
    **YDL_AUDIO_OPTS,
    'default_search': 'scsearchplaylist',  # Искать именно плейлисты
    'extract_flat': True,
    'ignoreerrors': True,
    'playlist_items': '1-100',  # Лимит на количество треков в плейлисте
}

async def search_soundcloud_playlists(query, max_results=20):
    """Ищет плейлисты на SoundCloud."""
    try:
        search_opts = {
            **SC_PLAYLIST_SEARCH_OPTS,
            'max_downloads': max_results,
        }
        
        loop = asyncio.get_running_loop()
        
        # Используем yt-dlp для поиска плейлистов
        with yt_dlp.YoutubeDL(search_opts) as ydl:
            search_query = f"scsearchplaylist{max_results}:{query}"
            logger.info(f"[SoundCloud Playlist Search] Querying: {search_query}")
            
            # Выполняем поиск в отдельном потоке
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(search_query, download=False))
            
            if not info or 'entries' not in info:
                logger.info("[SoundCloud Playlist Search] No playlists found.")
                return []

            results = []
            for entry in info.get('entries', []):
                if not entry:
                    continue
                
                # Получаем базовую информацию о плейлисте
                playlist_title = entry.get('title', 'Unknown Playlist')
                uploader = entry.get('uploader', 'Unknown Artist')
                playlist_url = entry.get('webpage_url', entry.get('url', ''))
                
                # Дополнительная информация
                track_count = entry.get('playlist_count', 0)
                if not track_count:
                    track_count = len(entry.get('entries', []))
                
                # Пропускаем плейлисты без треков или без URL
                if not playlist_url or track_count == 0:
                    continue
                
                results.append({
                    'title': playlist_title,
                    'channel': uploader,
                    'url': playlist_url,
                    'track_count': track_count, 
                    'source': 'soundcloud_playlist',
                    'type': 'playlist'
                })
            
            logger.info(f"[SoundCloud Playlist Search] Found {len(results)} playlists")
            return results
    except Exception as e:
        logger.error(f"Error searching SoundCloud playlists: {e}", exc_info=True)
        return []

async def download_soundcloud_playlist(url, original_message, status_message):
    """Скачивает плейлист с SoundCloud."""
    user_id = original_message.from_user.id
    loop = asyncio.get_running_loop()
    
    try:
        # Обновляем сообщение статуса
        await bot.edit_message_text(
            "⏳ Получаю информацию о плейлисте...", 
            chat_id=status_message.chat.id, 
            message_id=status_message.message_id
        )
        
        # Получаем информацию о плейлисте
        info_opts = {'quiet': True, 'no_warnings': True, 'extract_flat': True, 'nocheckcertificate': True}
        with yt_dlp.YoutubeDL(info_opts) as ydl:
            playlist_info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))
        
        if not playlist_info:
            await bot.edit_message_text(
                "❌ Не удалось получить информацию о плейлисте", 
                chat_id=status_message.chat.id, 
                message_id=status_message.message_id
            )
            return
        
        # Проверяем, действительно ли это плейлист
        if playlist_info.get('_type') != 'playlist':
            await bot.edit_message_text(
                "❌ Указанная ссылка не является плейлистом", 
                chat_id=status_message.chat.id, 
                message_id=status_message.message_id
            )
            return
        
        playlist_title = playlist_info.get('title', 'SoundCloud Playlist')
        entries = playlist_info.get('entries') or []
        
        if not entries:
            await bot.edit_message_text(
                f"❌ Плейлист {playlist_title} пуст", 
                chat_id=status_message.chat.id, 
                message_id=status_message.message_id
            )
            return
        
        # Подготавливаем треки для скачивания
        processed_tracks = []
        for idx, entry in enumerate(entries):
            if not entry:
                continue
            
            entry_url = entry.get('webpage_url') or entry.get('url')
            title = entry.get('title', 'Unknown Title')
            artist = entry.get('uploader', 'Unknown Artist')
            
            if not entry_url or not title:
                continue
            
            # Обработка названия трека, если оно содержит исполнителя
            title_extracted, artist_extracted = extract_title_and_artist(title)
            if artist_extracted and artist_extracted != "Unknown Artist":
                title = title_extracted
                artist = artist_extracted
            
            processed_tracks.append({
                'original_index': idx,
                'url': entry_url,
                'title': title,
                'artist': artist,
                'status': 'pending',
                'file_path': None,
                'source': 'soundcloud'
            })
        
        total_tracks = len(processed_tracks)
        if total_tracks == 0:
            await bot.edit_message_text(
                f"❌ Нет треков для загрузки в плейлисте {playlist_title}", 
                chat_id=status_message.chat.id, 
                message_id=status_message.message_id
            )
            return
        
        # Ограничиваем количество треков
        if total_tracks > MAX_TRACKS:
            processed_tracks = processed_tracks[:MAX_TRACKS]
            total_tracks = MAX_TRACKS
        
        # Создаём уникальный ID для этой загрузки плейлиста
        playlist_download_id = str(uuid.uuid4())
        
        # Добавляем информацию о плейлисте в хранилище
        playlist_downloads[playlist_download_id] = {
            'user_id': user_id,
            'chat_id': original_message.chat.id,
            'status_message_id': status_message.message_id,
            'playlist_title': playlist_title,
            'total_tracks': total_tracks,
            'completed_tracks': 0,
            'tracks': processed_tracks
        }
        
        # Обновляем статус
        await bot.edit_message_text(
            f"⏳ Плейлист '{playlist_title}' ({total_tracks} треков) добавлен в очередь загрузки", 
            chat_id=status_message.chat.id, 
            message_id=status_message.message_id
        )
        
        # Добавляем треки в очередь загрузки
        download_queues.setdefault(user_id, [])
        for track in processed_tracks:
            download_queues[user_id].append(({
                'title': track['title'],
                'channel': track['artist'],
                'url': track['url'],
                'source': 'soundcloud'
            }, playlist_download_id))
        
        # Запускаем обработку очереди
        if user_id not in download_tasks:
            download_tasks[user_id] = {}
        
        # Логика запуска скачивания находится в download_queue.py, 
        # которая будет вызвана из handlers.py
        
        return playlist_download_id
    
    except Exception as e:
        logger.error(f"Error downloading SoundCloud playlist: {e}", exc_info=True)
        try:
            await bot.edit_message_text(
                f"❌ Ошибка при загрузке плейлиста: {str(e)}", 
                chat_id=status_message.chat.id, 
                message_id=status_message.message_id
            )
        except Exception:
            pass
        return None 