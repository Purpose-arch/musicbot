import os
import tempfile
import uuid
import asyncio
import traceback
import logging
import re
import subprocess
import sys

import yt_dlp # NEW
from aiogram import types
from aiogram.types import FSInputFile
# DEPRECATED: from mutagen.ogg import OggFile # This import is causing ImportError and is not used

from src.core.bot_instance import bot
from src.core.config import MAX_TRACKS, GROUP_MAX_TRACKS, MAX_PARALLEL_DOWNLOADS
from src.core.state import download_queues, download_tasks, playlist_downloads
from src.core.utils import extract_title_and_artist, set_mp3_metadata
# DEPRECATED: from .track_downloader import _blocking_download_and_convert
from .download_queue import process_download_queue
from src.search.vk_music import parse_playlist_url, get_playlist_tracks
from src.download.cobalt_api import AsyncCobaltDownloader

# Disable debug prints and exception stack traces
logger = logging.getLogger(__name__)
# print = lambda *args, **kwargs: None # Keep print for debugging for now, might uncomment later.
# traceback.print_exc = lambda *args, **kwargs: None # Keep traceback for debugging for now.

# Constants for Telethon agent
TELETHON_THRESHOLD_MB = 48 # Files larger than this will be handled by Telethon agent

async def download_media_from_url(url: str, original_message: types.Message, status_message: types.Message):
    """Downloads media (audio/video) or playlists from URL using Cobalt API or specific VK handling."""
    user_id = original_message.from_user.id
    is_group = original_message.chat.type in ('group', 'supergroup')
    download_uuid = str(uuid.uuid4())
    temp_dir = tempfile.gettempdir()
    base_temp_path = os.path.join(temp_dir, f"media_{download_uuid}")
    actual_downloaded_path = None
    temp_path = None # This variable seems unused, can be removed
    telethon_agent_used = False

    # Check if URL is a VK playlist or album
    # Ссылки на плейлисты: https://vk.com/music/playlist/123_456_hash
    # Ссылки на альбомы: https://vk.com/music/album/-2000086173_23086173_hash
    if "vk.com/music/playlist" in url or "vk.com/music/album" in url:
        print(f"[URL] VK Playlist/Album detected: {url}")
        try:
            # Парсим URL плейлиста/альбома
            owner_id, playlist_id, access_hash = parse_playlist_url(url)
            
            # Определяем тип (плейлист или альбом)
            playlist_type = "альбома" if "album" in url else "плейлиста"
            
            await bot.edit_message_text(f"⏳ получаю информацию о {playlist_type}...", chat_id=status_message.chat.id, message_id=status_message.message_id)
            
            # Получаем треки из плейлиста/альбома
            tracks = await asyncio.get_running_loop().run_in_executor(None, lambda: get_playlist_tracks(url))
            
            if not tracks:
                await bot.edit_message_text(f"❌ {playlist_type} пуст или нет доступа к трекам", chat_id=status_message.chat.id, message_id=status_message.message_id)
                return
            
            # Используем разные лимиты для групп и личных чатов
            max_tracks = GROUP_MAX_TRACKS if is_group else MAX_TRACKS
            
            # Готовим информацию о треках
            playlist_id_str = str(uuid.uuid4())
            playlist_title = f"{'альбом' if 'album' in url else 'плейлист'} VK {owner_id}_{playlist_id}"
            
            processed = []
            for idx, track in enumerate(tracks):
                artist = getattr(track, 'artist', 'Unknown Artist')
                title = getattr(track, 'title', 'Unknown Title')
                track_url = getattr(track, 'url', None)
                
                if not title or not track_url:
                    continue
                
                processed.append({
                    'original_index': idx, 
                    'url': track_url, 
                    'title': title, 
                    'artist': artist, 
                    'status': 'pending',
                    'file_path': None,
                    'source': 'vk'
                })
            
            total = len(processed)
            if total == 0:
                await bot.edit_message_text(f"❌ нет доступных треков в {playlist_type}", chat_id=status_message.chat.id, message_id=status_message.message_id)
                return
            
            if total > max_tracks:
                processed = processed[:max_tracks]
                total = max_tracks
            
            # Добавляем в playlist_downloads
            playlist_downloads[playlist_id_str] = {
                'user_id': user_id,
                'chat_id': original_message.chat.id,
                'chat_type': original_message.chat.type,
                'status_message_id': status_message.message_id,
                'playlist_title': playlist_title,
                'total_tracks': total,
                'completed_tracks': 0,
                'tracks': processed
            }
            
            await bot.edit_message_text(f"⏳ скачиваю {playlist_type} ({total} треков)", chat_id=status_message.chat.id, message_id=status_message.message_id)
            
            # Добавляем треки в очередь
            download_queues.setdefault(user_id, [])
            for t in processed:
                download_queues[user_id].append(({'title': t['title'], 'channel': t['artist'], 'url': t['url'], 'source': t['source']}, playlist_id_str))
            
            # Запускаем обработку очереди
            if user_id not in download_tasks:
                download_tasks[user_id] = {}
            
            active = sum(1 for t in download_tasks[user_id].values() if not t.done())
            if active < MAX_PARALLEL_DOWNLOADS:
                asyncio.create_task(process_download_queue(user_id))
            
            return
        
        except Exception as e:
            logger.error(f"[URL] VK Playlist/Album error: {e}", exc_info=True)
            await bot.edit_message_text(f"❌ ошибка при обработке плейлиста/альбома ВКонтакте: {str(e)}", chat_id=status_message.chat.id, message_id=status_message.message_id)
            return

    try:
        # For all non-VK URLs, directly attempt to download using Cobalt API.
        # This removes the problematic yt-dlp info extraction step.
        print(f"[URL] Attempting direct download with Cobalt API for: {url}")
        
        # Initialize CobaltDownloader
        cobalt_downloader = AsyncCobaltDownloader(temp_dir=temp_dir)
        
        # Download using Cobalt API
        actual_downloaded_path = await cobalt_downloader.download_media(url)
        
        # Close the session after download
        await cobalt_downloader._close_session()

        if not actual_downloaded_path:
            raise Exception(f"не удалось скачать файл с помощью Cobalt API для {url}. Возможно, это не поддерживаемый URL или плейлист.")

        # metadata
        # Since Cobalt API does not provide rich metadata, we derive it from the filename
        file_name_without_ext = os.path.splitext(os.path.basename(actual_downloaded_path))[0]
        safe_title, performer = extract_title_and_artist(file_name_without_ext)

        # Determine file extension (needed for both direct send and Telethon agent)
        ext = os.path.splitext(actual_downloaded_path)[1].lower()

        # Send the file
        size = os.path.getsize(actual_downloaded_path)
        if size > TELETHON_THRESHOLD_MB * 1024 * 1024: # If file is larger than threshold, use Telethon agent
            telethon_agent_used = True
            mb = size / 1024 / 1024
            await bot.edit_message_text(f"⏳ файл слишком большой ({mb:.1f}МБ), отправка чуть задержится...", chat_id=status_message.chat.id, message_id=status_message.message_id)

            file_type = "document" # Default type for agent
            if ext in ['.mp3','.m4a','.ogg','.opus','.aac','.wav','.flac']:
                file_type = "audio"
            elif ext in ['.mp4','.mkv','.webm','.mov','.avi']:
                file_type = "video"
            elif ext in ['.jpg','.jpeg','.png','.gif','.webp']:
                file_type = "photo"

            # Duration is not reliably available from Cobalt API, set to 0 for now.
            # If needed, extract duration before calling agent for audio files.
            duration_for_agent = 0 

            # Call Telethon agent in a separate process
            command = [
                sys.executable, # Use the same python executable
                "telethon_agent.py",
                actual_downloaded_path,
                str(original_message.chat.id),
                str(original_message.message_id),
                str(status_message.message_id),
                file_type,
                safe_title,
                performer or "Unknown Artist",
                str(duration_for_agent)
            ]
            print(f"[AGENT] Calling Telethon agent: {' '.join(command)}")
            subprocess.Popen(command)
            # Agent will handle sending the file and updating status, so we return here.
            return
        else: # If file is within limits, send directly
            await bot.delete_message(chat_id=status_message.chat.id, message_id=status_message.message_id)
            
            if ext in ['.mp3','.m4a','.ogg','.opus','.aac','.wav','.flac']:
                if ext == '.mp3': set_mp3_metadata(actual_downloaded_path, safe_title, performer or "Unknown")
                await original_message.answer_audio(
                    FSInputFile(actual_downloaded_path),
                    title=safe_title,
                    performer=performer or "Unknown Artist"
                )
            elif ext in ['.jpg','.jpeg','.png','.gif','.webp']:
                await original_message.answer_photo(FSInputFile(actual_downloaded_path))
            elif ext in ['.mp4','.mkv','.webm','.mov','.ov']:
                await original_message.answer_video(FSInputFile(actual_downloaded_path))
            else:
                await original_message.answer_document(FSInputFile(actual_downloaded_path))

    except Exception as e:
        logger.error(f"[URL] ERROR: {e}", exc_info=True)
        msg = f"❌ ошибка: {str(e)}"
        try: await bot.edit_message_text(msg, chat_id=status_message.chat.id, message_id=status_message.message_id)
        except: await original_message.answer(msg)

    finally:
        if actual_downloaded_path and os.path.exists(actual_downloaded_path):
            if not telethon_agent_used: # Only remove if not passed to agent
                try: os.remove(actual_downloaded_path)
                except Exception as e:
                    logger.warning(f"Could not remove downloaded file {actual_downloaded_path}: {e}")