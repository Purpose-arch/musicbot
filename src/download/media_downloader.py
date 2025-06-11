import os
import tempfile
import uuid
import asyncio
import traceback
import logging
import re

import yt_dlp
from aiogram import types
from aiogram.types import FSInputFile

from src.core.bot_instance import bot
from src.core.config import MAX_TRACKS, GROUP_MAX_TRACKS, MAX_PARALLEL_DOWNLOADS
from src.core.state import download_queues, download_tasks, playlist_downloads
from src.core.utils import extract_title_and_artist, set_mp3_metadata
from .track_downloader import _blocking_download_and_convert
from .download_queue import process_download_queue
from src.search.vk_music import parse_playlist_url, get_playlist_tracks

# Disable debug prints and exception stack traces
logger = logging.getLogger(__name__)
print = lambda *args, **kwargs: None
traceback.print_exc = lambda *args, **kwargs: None

async def download_media_from_url(url: str, original_message: types.Message, status_message: types.Message):
    """Downloads media (audio/video) or playlists from URL using yt-dlp."""
    loop = asyncio.get_running_loop()
    user_id = original_message.from_user.id
    is_group = original_message.chat.type in ('group', 'supergroup')
    download_uuid = str(uuid.uuid4())
    temp_dir = tempfile.gettempdir()
    base_temp_path = os.path.join(temp_dir, f"media_{download_uuid}")
    actual_downloaded_path = None
    temp_path = None

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
            tracks = await loop.run_in_executor(None, lambda: get_playlist_tracks(url))
            
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
            print(f"[URL] VK Playlist/Album error: {e}")
            traceback.print_exc()
            await bot.edit_message_text(f"❌ ошибка при обработке плейлиста/альбома ВКонтакте: {str(e)}", chat_id=status_message.chat.id, message_id=status_message.message_id)
            return

    # media download options
    media_opts = {
        'format': 'bestvideo+bestaudio/best/bestaudio',
        'outtmpl': base_temp_path + '.%(ext)s',
        'quiet': False,
        'verbose': True,
        'no_warnings': False,
        'prefer_ffmpeg': True,
        'nocheckcertificate': True,
        'ignoreerrors': True,
        'extract_flat': False,
        'ffmpeg_location': '/usr/bin/ffmpeg',
        'merge_output_format': 'mp4',
    }

    try:
        # extract info
        extracted_info = None
        print(f"[URL] Extracting info for: {url}")
        try:
            info_opts = {'quiet': True, 'no_warnings': True, 'nocheckcertificate': True, 'ignoreerrors': True, 'extract_flat': False}
            with yt_dlp.YoutubeDL(info_opts) as ydl:
                extracted_info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))
        except Exception as e:
            print(f"[URL] Info extraction error: {e}")
            traceback.print_exc()

        # playlist detection
        if extracted_info and extracted_info.get('_type') == 'playlist':
            print(f"[URL] Playlist detected: {url}")
            playlist_id = str(uuid.uuid4())
            playlist_title = extracted_info.get('title', 'Плейлист')
            entries = extracted_info.get('entries') or []
            if not entries:
                await bot.edit_message_text(f"❌ плейлист {playlist_title} пуст", chat_id=status_message.chat.id, message_id=status_message.message_id)
                return

            # prepare tracks
            processed = []
            for idx, e in enumerate(entries):
                if not e:
                    continue
                entry_url = e.get('webpage_url') or e.get('url')
                title = e.get('title')
                artist = e.get('uploader', 'Unknown Artist')
                if not entry_url or not title:
                    continue
                # simple extraction
                title_extracted, artist_extracted = extract_title_and_artist(title)
                # override only if a valid artist was extracted
                if artist_extracted and artist_extracted != "Unknown Artist":
                    title = title_extracted
                    artist = artist_extracted
                processed.append({'original_index': idx, 'url': entry_url, 'title': title, 'artist': artist, 'status':'pending','file_path':None,'source':e.get('ie_key','')})

            # Используем разные лимиты для групп и личных чатов
            max_tracks = GROUP_MAX_TRACKS if is_group else MAX_TRACKS
            
            total = len(processed)
            if total == 0:
                await bot.edit_message_text(f"❌ нет треков для {playlist_title}", chat_id=status_message.chat.id, message_id=status_message.message_id)
                return
            if total > max_tracks:
                processed = processed[:max_tracks]; total = max_tracks

            # add to playlist_downloads
            playlist_downloads[playlist_id] = {
                'user_id': user_id,
                'chat_id': original_message.chat.id,
                'chat_type': original_message.chat.type,  # Сохраняем тип чата
                'status_message_id': status_message.message_id,
                'playlist_title': playlist_title,
                'total_tracks': total,
                'completed_tracks': 0,
                'tracks': processed
            }
            
            await bot.edit_message_text(f"⏳ скачиваю плейлист '{playlist_title}' ({total} треков)", chat_id=status_message.chat.id, message_id=status_message.message_id)

            # queue tracks
            download_queues.setdefault(user_id,[])
            for t in processed:
                download_queues[user_id].append(({'title':t['title'],'channel':t['artist'],'url':t['url'],'source':t['source']},playlist_id))
            # trigger queue
            if user_id not in download_tasks: download_tasks[user_id]={}
            active = sum(1 for t in download_tasks[user_id].values() if not t.done())
            if active < MAX_PARALLEL_DOWNLOADS:
                asyncio.create_task(process_download_queue(user_id))
            return

        # single media
        print(f"[URL] Single media download for: {url}")
        try:
            await bot.edit_message_text(f"⏳ скачиваю...", chat_id=status_message.chat.id, message_id=status_message.message_id)
        except: pass

        # download
        await loop.run_in_executor(None, _blocking_download_and_convert, url, media_opts)

        # find file
        exts = ['.mp4','.mkv','.webm','.mov','.avi','.mp3','.m4a','.ogg','.opus','.aac','.wav','.flac']
        for ext in exts:
            p = base_temp_path + ext
            if os.path.exists(p) and os.path.getsize(p)>0:
                actual_downloaded_path = p; break
        if not actual_downloaded_path:
            raise Exception(f"не найден файл после скачивания {url}")

        # size check
        size = os.path.getsize(actual_downloaded_path)
        if size > 50*1024*1024:
            mb = size/1024/1024
            raise Exception(f"слишком большой файл {mb:.1f}МБ (лимит 50)")

        # metadata
        title = extracted_info.get('title') if extracted_info else 'media'
        safe_title,_ = extract_title_and_artist(title)
        performer = extracted_info.get('uploader') if extracted_info else None

        # send
        await bot.delete_message(chat_id=status_message.chat.id, message_id=status_message.message_id)
        
        ext = os.path.splitext(actual_downloaded_path)[1].lower()
        if ext in ['.mp3','.m4a','.ogg','.opus','.aac','.wav','.flac']:
            if ext == '.mp3': set_mp3_metadata(actual_downloaded_path, safe_title, performer or "Unknown")
            await original_message.answer_audio(
                FSInputFile(actual_downloaded_path),
                title=safe_title,
                performer=performer or "Unknown Artist"
            )
        elif ext in ['.jpg','.jpeg','.png','.gif','.webp']:
            await original_message.answer_photo(FSInputFile(actual_downloaded_path))
        elif ext in ['.mp4','.mkv','.webm','.mov','.avi']:
            await original_message.answer_video(FSInputFile(actual_downloaded_path))
        else:
            await original_message.answer_document(FSInputFile(actual_downloaded_path))
            

    except Exception as e:
        print(f"[URL] ERROR: {e}")
        traceback.print_exc()
        msg = f"❌ ошибка: {str(e)}"
        try: await bot.edit_message_text(msg, chat_id=status_message.chat.id, message_id=status_message.message_id)
        except: await original_message.answer(msg)

    finally:
        if actual_downloaded_path and os.path.exists(actual_downloaded_path):
            try: os.remove(actual_downloaded_path)
            except: pass