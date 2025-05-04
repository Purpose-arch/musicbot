import os
import tempfile
import uuid
import asyncio
import traceback
import re
import logging
import time

import yt_dlp
from aiogram import types
from aiogram.types import FSInputFile

from bot_instance import bot
from config import MAX_TRACKS, GROUP_MAX_TRACKS, MAX_PARALLEL_DOWNLOADS
from state import download_queues, download_tasks, playlist_downloads
from utils import extract_title_and_artist, set_mp3_metadata
from track_downloader import _blocking_download_and_convert
from download_queue import process_download_queue
from db import get_user_settings

# Disable debug prints and exception stack traces
logger = logging.getLogger(__name__)
print = lambda *args, **kwargs: None
traceback.print_exc = lambda *args, **kwargs: None

# Функция для создания индикатора прогресса
def create_progress_bar(percent, length=6):
    filled = int(percent * length / 100)
    empty = length - filled
    return f"[{'▓' * filled}{'░' * empty}] {percent}%"

class ProgressHook:
    def __init__(self, status_message=None, is_group=False):
        self.status_message = status_message
        self.is_group = is_group
        self.progress = 0
        self.title = ""
        self.artist = ""
        self.last_update_time = 0
        self.start_time = time.time()
        self.size = 0
        self.downloaded = 0
        self.speed = 0
        self.eta = 0
        self.estimated_total = 0

    async def __call__(self, d):
        try:
            if d['status'] == 'downloading':
                # Извлекаем данные о прогрессе
                if '_percent_str' in d:
                    self.progress = float(d['_percent_str'].replace('%', '').strip())
                
                if 'total_bytes' in d:
                    self.size = d['total_bytes']
                elif 'total_bytes_estimate' in d:
                    self.size = d['total_bytes_estimate']
                
                if 'downloaded_bytes' in d:
                    self.downloaded = d['downloaded_bytes']
                
                if 'speed' in d:
                    self.speed = d['speed'] or 0
                
                if 'eta' in d:
                    self.eta = d['eta'] or 0
                    
                # Если это начало загрузки, сохраняем размер для оценки
                if self.progress < 1 and self.downloaded > 0 and self.estimated_total == 0:
                    self.estimated_total = int(self.downloaded / (self.progress / 100)) if self.progress > 0 else 0
                
                # Обновляем сообщение не чаще раза в 2 секунды
                current_time = time.time()
                if current_time - self.last_update_time >= 2.0:
                    self.last_update_time = current_time
                    
                    progress_bar = create_progress_bar(int(self.progress))
                    elapsed = current_time - self.start_time
                    
                    # Формируем сообщение о прогрессе
                    if self.is_group:
                        message = f"⏳ скачиваю... {progress_bar}"
                    else:
                        speed_mb = self.speed / 1024 / 1024 if self.speed else 0
                        
                        # Оцениваем оставшееся время
                        eta_str = ""
                        if self.eta and self.eta < 6000:  # Если ETA меньше 100 минут
                            if self.eta < 60:
                                eta_str = f"⌛ осталось: ~{self.eta} сек"
                            else:
                                eta_str = f"⌛ осталось: ~{self.eta // 60} мин {self.eta % 60} сек"
                        
                        if self.title and self.artist:
                            message = f"🎵 трек: {self.title}\n👤 автор: {self.artist}\n⏳ скачиваю... {progress_bar}\n💾 {speed_mb:.1f} МБ/с\n{eta_str}"
                        else:
                            message = f"⏳ скачиваю медиа... {progress_bar}\n💾 {speed_mb:.1f} МБ/с\n{eta_str}"
                    
                    # Обновляем сообщение
                    try:
                        if hasattr(self.status_message, 'photo'):
                            await self.status_message.edit_caption(message)
                        else:
                            await self.status_message.edit_text(message)
                    except Exception as e:
                        print(f"Ошибка обновления сообщения о прогрессе: {e}")
                        
            elif d['status'] == 'finished':
                if hasattr(self.status_message, 'photo'):
                    await self.status_message.edit_caption("✅ загрузка завершена, обрабатываю...")
                else:
                    await self.status_message.edit_text("✅ загрузка завершена, обрабатываю...")
                
        except Exception as e:
            print(f"Ошибка в progress_hook: {e}")
            traceback.print_exc()

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
    
    # Получаем настройки пользователя
    user_settings = await get_user_settings(user_id)
    audio_quality = user_settings.get('audio_quality', 'high') if user_settings else 'high'
    
    # Устанавливаем качество в зависимости от настроек
    audio_quality_opts = {}
    if audio_quality == 'low':
        audio_quality_opts = {
            'format': 'worstaudio/worst',
            'postprocessor_args': {
                'audio_quality': 5  # Худшее качество (больше сжатие)
            }
        }
    elif audio_quality == 'medium':
        audio_quality_opts = {
            'format': 'bestaudio/best',
            'postprocessor_args': {
                'audio_quality': 3  # Среднее качество
            }
        }
    else:  # high
        audio_quality_opts = {
            'format': 'bestaudio/best',
            'postprocessor_args': {
                'audio_quality': 0  # Лучшее качество (меньше сжатие)
            }
        }

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
        **audio_quality_opts
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

        # Проверяем, есть ли миниатюра, и если да, обновляем сообщение
        thumbnail_url = None
        if extracted_info and 'thumbnails' in extracted_info and extracted_info['thumbnails']:
            thumbnail_url = extracted_info['thumbnails'][-1]['url']
            # Если это видео или трек с миниатюрой, показываем ее
            if not hasattr(status_message, 'photo'):
                try:
                    title = extracted_info.get('title', 'Медиа')
                    uploader = extracted_info.get('uploader', 'Неизвестный автор')
                    
                    # Удаляем старое сообщение о статусе
                    await bot.delete_message(chat_id=status_message.chat.id, message_id=status_message.message_id)
                    
                    # Отправляем новое сообщение с миниатюрой
                    status_text = f"🎵 трек: {title}\n👤 автор: {uploader}\n⏳ скачиваю... [░░░░░░] 0%"
                    if is_group:
                        status_text = "⏳ скачиваю..."
                    
                    cancel_button = types.InlineKeyboardButton(text="❌ отменить", callback_data=f"cancel_{user_id}_{url}")
                    cancel_keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[cancel_button]])
                    
                    status_message = await original_message.answer_photo(
                        thumbnail_url,
                        caption=status_text,
                        reply_markup=cancel_keyboard
                    )
                except Exception as e:
                    print(f"[URL] Error updating status with thumbnail: {e}")
        
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
                entry_url = e.get('webpage_url') or (f"https://www.youtube.com/watch?v={e.get('id')}" if e.get('id') else None)
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
            
            # Обновляем сообщение с миниатюрой плейлиста, если возможно
            if thumbnail_url and not hasattr(status_message, 'photo'):
                try:
                    # Удаляем старое сообщение о статусе
                    await bot.delete_message(chat_id=status_message.chat.id, message_id=status_message.message_id)
                    
                    # Отправляем новое сообщение с миниатюрой плейлиста
                    status_text = f"📂 плейлист: {playlist_title}\n💿 треков: {total}\n⏳ скачиваю..."
                    if is_group:
                        status_text = f"⏳ скачиваю плейлист ({total} треков)"
                    
                    cancel_button = types.InlineKeyboardButton(text="❌ отменить", callback_data=f"cancel_{user_id}_{url}")
                    cancel_keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[cancel_button]])
                    
                    new_status = await original_message.answer_photo(
                        thumbnail_url,
                        caption=status_text,
                        reply_markup=cancel_keyboard
                    )
                    
                    # Обновляем ID сообщения в playlist_downloads
                    playlist_downloads[playlist_id]['status_message_id'] = new_status.message_id
                    status_message = new_status
                except Exception as e:
                    print(f"[URL] Error updating playlist status with thumbnail: {e}")
            else:
                # Если не удалось получить миниатюру, просто обновляем текст
                if hasattr(status_message, 'photo'):
                    if is_group:
                        await status_message.edit_caption(f"⏳ скачиваю плейлист ({total} треков)")
                    else:
                        await status_message.edit_caption(f"📂 плейлист: {playlist_title}\n💿 треков: {total}\n⏳ скачиваю...")
                else:
                    if is_group:
                        await status_message.edit_text(f"⏳ скачиваю плейлист ({total} треков)")
                    else:
                        await status_message.edit_text(f"📂 плейлист: {playlist_title}\n💿 треков: {total}\n⏳ скачиваю...")

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
            # Извлекаем информацию для прогресс-бара
            title = extracted_info.get('title', 'Медиа') if extracted_info else 'Медиа'
            uploader = extracted_info.get('uploader', 'Неизвестный автор') if extracted_info else 'Неизвестный автор'
            
            progress_hook = ProgressHook(status_message, is_group)
            progress_hook.title = title
            progress_hook.artist = uploader
            
            # Сокращаем сообщение в группах
            if is_group:
                if hasattr(status_message, 'photo'):
                    await status_message.edit_caption(f"⏳ скачиваю...")
                else:
                    await status_message.edit_text(f"⏳ скачиваю...")
            else:
                if hasattr(status_message, 'photo'):
                    await status_message.edit_caption(f"🎵 трек: {title}\n👤 автор: {uploader}\n⏳ скачиваю... [░░░░░░] 0%")
                else:
                    await status_message.edit_text(f"🎵 трек: {title}\n👤 автор: {uploader}\n⏳ скачиваю... [░░░░░░] 0%")
        except: pass

        # Добавляем прогресс-хук в опции загрузки
        media_opts['progress_hooks'] = [lambda d: asyncio.create_task(progress_hook(d))]
        
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
        
        # В группах не показываем промежуточное сообщение об отправке
        if not is_group:
            send_msg = await original_message.answer("📤 отправляю медиа")
            
        ext = os.path.splitext(actual_downloaded_path)[1].lower()
        if ext in ['.mp3','.m4a','.ogg','.opus','.aac','.wav','.flac']:
            if ext == '.mp3': set_mp3_metadata(actual_downloaded_path, safe_title, performer or "Unknown")
            await original_message.answer_audio(FSInputFile(actual_downloaded_path), caption=safe_title)
        elif ext in ['.jpg','.jpeg','.png','.gif','.webp']:
            await original_message.answer_photo(FSInputFile(actual_downloaded_path))
        elif ext in ['.mp4','.mkv','.webm','.mov','.avi']:
            await original_message.answer_video(FSInputFile(actual_downloaded_path))
        else:
            await original_message.answer_document(FSInputFile(actual_downloaded_path))
            
        # Удаляем промежуточное сообщение только если оно было создано (не в группах)
        if not is_group and locals().get('send_msg'):
            try: await bot.delete_message(chat_id=send_msg.chat.id, message_id=send_msg.message_id)
            except: pass

    except Exception as e:
        print(f"[URL] ERROR: {e}")
        traceback.print_exc()
        msg = f"❌ ошибка: {str(e)}"
        try: await bot.edit_message_text(msg, chat_id=status_message.chat.id, message_id=status_message.message_id)
        except: 
            try: await bot.edit_message_caption(chat_id=status_message.chat.id, message_id=status_message.message_id, caption=msg)
            except: await original_message.answer(msg)

    finally:
        if actual_downloaded_path and os.path.exists(actual_downloaded_path):
            try: os.remove(actual_downloaded_path)
            except: pass