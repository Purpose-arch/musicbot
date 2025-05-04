# track_downloader.py
import os
import asyncio
import tempfile
import traceback
import uuid
import logging
import time

# Disable debug prints
import builtins
print = lambda *args, **kwargs: None
traceback.print_exc = lambda *args, **kwargs: None

import yt_dlp
from aiogram.types import FSInputFile
from mutagen.mp3 import MP3
from pathlib import Path
from aiogram import types

from bot_instance import bot
from config import MAX_PARALLEL_DOWNLOADS, GROUP_MAX_TRACKS
from state import download_tasks, download_queues, playlist_downloads
from utils import set_mp3_metadata
from music_recognition import shazam, search_genius, search_yandex_music, search_musicxmatch, search_pylyrics, search_chartlyrics, search_lyricwikia
from db import get_user_settings
from media_downloader import create_progress_bar

logger = logging.getLogger(__name__)

YDL_AUDIO_OPTS = {
    'format': 'bestaudio/best',
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }],
    'quiet': True,
    'verbose': False,
    'no_warnings': True,
    'prefer_ffmpeg': True,
    'nocheckcertificate': True,
    'ignoreerrors': True,
    'extract_flat': False
}

def _blocking_download_and_convert(url: str, download_opts: dict = None):
    """Блокирующая функция для скачивания, выполняется в отдельном потоке"""
    if download_opts is None:
        download_opts = YDL_AUDIO_OPTS
    try:
        with yt_dlp.YoutubeDL(download_opts) as ydl:
            ydl.download([url])
        return True
    except Exception as e:
        logger.error(f"Download error: {e}")
        traceback.print_exc()
        return False

class TrackProgressHook:
    def __init__(self, status_message=None, is_group=False):
        self.status_message = status_message
        self.is_group = is_group
        self.progress = 0
        self.title = ""
        self.artist = ""
        self.last_update_time = 0
        self.start_time = time.time()
        self.speed = 0
        self.eta = 0

    async def __call__(self, d):
        try:
            if d['status'] == 'downloading':
                # Извлекаем данные о прогрессе
                if '_percent_str' in d:
                    self.progress = float(d['_percent_str'].replace('%', '').strip())
                
                if 'speed' in d:
                    self.speed = d['speed'] or 0
                
                if 'eta' in d:
                    self.eta = d['eta'] or 0
                
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
                        
                        message = f"🎵 трек: {self.title}\n👤 автор: {self.artist}\n⏳ скачиваю... {progress_bar}\n💾 {speed_mb:.1f} МБ/с\n{eta_str}"
                    
                    # Обновляем сообщение
                    try:
                        if hasattr(self.status_message, 'photo'):
                            await self.status_message.edit_caption(message)
                        else:
                            await self.status_message.edit_text(message)
                    except Exception as e:
                        logger.error(f"Ошибка обновления сообщения о прогрессе: {e}")
                        
            elif d['status'] == 'finished':
                if hasattr(self.status_message, 'photo'):
                    await self.status_message.edit_caption("✅ загрузка завершена, обрабатываю...")
                else:
                    await self.status_message.edit_text("✅ загрузка завершена, обрабатываю...")
                
        except Exception as e:
            logger.error(f"Ошибка в progress_hook: {e}")

async def download_track(user_id, track_data, message_context, status_message, original_message_context=None, playlist_id=None):
    """Скачивает трек и отправляет его пользователю"""
    import json
    loop = asyncio.get_running_loop()
    is_group = message_context.chat.type in ('group', 'supergroup')
    original_message_context = original_message_context or message_context
    track_uuid = str(uuid.uuid4())
    
    # Получаем настройки пользователя
    user_settings = await get_user_settings(user_id)
    audio_quality = user_settings.get('audio_quality', 'high') if user_settings else 'high'
    auto_lyrics = user_settings.get('auto_lyrics', True) if user_settings else True
    
    # Настраиваем качество аудио
    audio_quality_settings = {}
    if audio_quality == 'low':
        audio_quality_settings = {'preferredquality': '96'}
    elif audio_quality == 'medium':
        audio_quality_settings = {'preferredquality': '128'}
    else:  # high
        audio_quality_settings = {'preferredquality': '192'}
    
    try:
        temp_dir = tempfile.gettempdir()
        safe_title = ''.join(c if c.isalnum() or c in ('_','-') else '_' for c in track_data['title']).strip('_.-')
        if not safe_title: safe_title = f"audio_{track_uuid}"
        base_temp_path = os.path.join(temp_dir, f"{safe_title}_{track_uuid}")
        
        # Удаляем конфликтующие файлы, если они есть
        for ext in ['.mp3', '.m4a', '.ogg', '.wav', '.flac']:
            p = base_temp_path + ext
            if os.path.exists(p):
                try: os.remove(p)
                except: pass
        
        # Настраиваем опции загрузки
        ydl_opts = {
            **YDL_AUDIO_OPTS,
            'outtmpl': base_temp_path + '.%(ext)s',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                **audio_quality_settings
            }]
        }
        
        # Создаем хук для отображения прогресса
        progress_hook = TrackProgressHook(status_message, is_group)
        progress_hook.title = track_data['title']
        progress_hook.artist = track_data['channel']
        
        # Добавляем прогресс-хук в опции загрузки
        ydl_opts['progress_hooks'] = [lambda d: asyncio.create_task(progress_hook(d))]
        
        # Загружаем трек
        result = await loop.run_in_executor(None, _blocking_download_and_convert, track_data['url'], ydl_opts)
        if not result:
            raise Exception("Ошибка при скачивании")
        
        # Находим скачанный файл
        track_path = base_temp_path + '.mp3'
        if not os.path.exists(track_path):
            raise Exception(f"Файл трека не найден после скачивания")
        
        # Обновляем метаданные
        set_mp3_metadata(track_path, track_data['title'], track_data['channel'])
        
        # Готовим сообщение об успешном скачивании
        if playlist_id:
            # Для треков из плейлиста сохраняем путь к файлу
            from state import playlist_downloads
            for tr in playlist_downloads[playlist_id]['tracks']:
                if tr['url'] == track_data['url']:
                    tr['file_path'] = track_path
                    tr['status'] = 'completed'
                    break
            
            # Обновляем статус загрузки плейлиста
            playlist_downloads[playlist_id]['completed_tracks'] += 1
            completed = playlist_downloads[playlist_id]['completed_tracks']
            total = playlist_downloads[playlist_id]['total_tracks']
            playlist_title = playlist_downloads[playlist_id]['playlist_title']
            
            # Если плейлист не завершен, обновляем статус
            if completed < total:
                try:
                    chat_id = playlist_downloads[playlist_id]['chat_id']
                    status_id = playlist_downloads[playlist_id]['status_message_id']
                    status_msg = await bot.get_message(chat_id, status_id)
                    
                    if hasattr(status_msg, 'photo'):
                        await status_msg.edit_caption(
                            f"📂 плейлист: {playlist_title}\n"
                            f"💿 треков: {total} (загружено {completed})\n"
                            f"⏳ {completed}/{total} [{completed*100//total}%]"
                        )
                    else:
                        await status_msg.edit_text(
                            f"📂 плейлист: {playlist_title}\n"
                            f"💿 треков: {total} (загружено {completed})\n"
                            f"⏳ {completed}/{total} [{completed*100//total}%]"
                        )
                except Exception as e:
                    logger.error(f"Error updating playlist status: {e}")
            
            # Если плейлист завершен, отправляем все треки
            if completed == total:
                try:
                    # Удаляем сообщение о статусе
                    chat_id = playlist_downloads[playlist_id]['chat_id']
                    status_id = playlist_downloads[playlist_id]['status_message_id']
                    await bot.delete_message(chat_id=chat_id, message_id=status_id)
                    
                    # Формируем сообщение перед отправкой треков
                    await bot.send_message(
                        chat_id,
                        f"✅ плейлист '{playlist_title}' ({total} треков) загружен"
                    )
                    
                    # Отправляем все треки
                    for i, tr in enumerate(sorted(playlist_downloads[playlist_id]['tracks'], key=lambda x: x['original_index'])):
                        if tr['status'] != 'completed' or not tr.get('file_path') or not os.path.exists(tr['file_path']):
                            continue
                            
                        # Отправляем аудио
                        try:
                            await bot.send_audio(
                                chat_id,
                                FSInputFile(tr['file_path']),
                                caption=tr.get('title', f"Track {i+1}"),
                                performer=tr.get('artist', 'Unknown'),
                                title=tr.get('title', f"Track {i+1}")
                            )
                            
                            # Удаляем файл после отправки
                            try: os.remove(tr['file_path'])
                            except: pass
                            
                        except Exception as e:
                            logger.error(f"Error sending track {i}: {e}")
                            
                except Exception as e:
                    logger.error(f"Error finalizing playlist: {e}")
                    
                # Удаляем плейлист из памяти
                from state import playlist_downloads
                playlist_downloads.pop(playlist_id, None)
                
            # Возвращаемся без отправки трека (он в плейлисте)
            return

        # Для одиночных треков - отправляем сразу
        try:
            # Удаляем сообщение о статусе
            await bot.delete_message(chat_id=status_message.chat.id, message_id=status_message.message_id)
            
            # Отправляем аудио
            if not is_group:
                sending = await original_message_context.answer("📤 отправляю трек...")
                
            await original_message_context.answer_audio(
                FSInputFile(track_path),
                caption=track_data['title'],
                performer=track_data['channel'],
                title=track_data['title']
            )
            
            # Удаляем сообщение "отправляю трек..."
            if not is_group and locals().get('sending'):
                await bot.delete_message(chat_id=sending.chat.id, message_id=sending.message_id)
            
        except Exception as e:
            logger.error(f"Error sending track: {e}")
            raise
    
    except asyncio.CancelledError:
        # Если задача была отменена, удаляем скачанные файлы и не отправляем ошибку
        for ext in ['.mp3', '.m4a', '.ogg', '.wav', '.flac']:
            p = base_temp_path + ext if 'base_temp_path' in locals() else os.path.join(tempfile.gettempdir(), f"audio_{track_uuid}{ext}")
            if os.path.exists(p):
                try: os.remove(p)
                except: pass
        raise
        
    except Exception as e:
        logger.error(f"Download error: {e}", exc_info=True)
        error_msg = f"❌ ошибка при скачивании: {str(e)}"
        try:
            if hasattr(status_message, 'photo'):
                await status_message.edit_caption(error_msg)
            else:
                await status_message.edit_text(error_msg)
        except:
            try:
                await original_message_context.answer(error_msg)
            except:
                pass
    
    finally:
        # Удаляем временные файлы
        if 'base_temp_path' in locals():
            for ext in ['.mp3', '.m4a', '.ogg', '.wav', '.flac']:
                p = base_temp_path + ext
                if os.path.exists(p):
                    try: os.remove(p)
                    except: pass
        if user_id in download_tasks:
            download_tasks[user_id].pop(track_data['url'], None)
            if not download_tasks[user_id]:
                del download_tasks[user_id]
        # Trigger next
        if download_queues.get(user_id):
            active = sum(1 for t in download_tasks.get(user_id, {}).values() if not t.done())
            if active < MAX_PARALLEL_DOWNLOADS:
                # local import to avoid circular dependency
                from download_queue import process_download_queue
                asyncio.create_task(process_download_queue(user_id))

async def send_completed_playlist(playlist_download_id):
    """Sends all tracks of a completed playlist"""
    entry = playlist_downloads.pop(playlist_download_id, None)
    if not entry: return
    user_id = entry['user_id']; chat_id = entry['chat_id']
    is_group = 'chat_type' in entry and entry['chat_type'] in ('group', 'supergroup')
    
    succ = [t for t in entry['tracks'] if t['status']=='success']
    failed = [t for t in entry['tracks'] if t['status']=='failed']
    
    # Сокращаем сообщение в группах
    if is_group:
        text = f"✅ плейлист: отправляю {len(succ)} треков"
    else:
        text = f"✅ плейлист '{entry['playlist_title']}' скачан, отправляю {len(succ)}"
        
    if failed: 
        if is_group:
            text += f" ({len(failed)} не удалось)"
        else:
            text += f" (не удалось {len(failed)})"
            
    if entry['status_message_id']:
        try: await bot.edit_message_text(text, chat_id, entry['status_message_id'])
        except: pass
        
    for t in succ:
        if t.get('file_path') and os.path.exists(t['file_path']):
            await bot.send_audio(chat_id, FSInputFile(t['file_path']), title=t['title'], performer=t.get('artist'))
            try: os.remove(t['file_path'])
            except: pass
            
    # Cleanup failed files
    for t in failed:
        p = t.get('file_path')
        if p and os.path.exists(p):
            try: os.remove(p)
            except: pass
            
    # delete the playlist status message after sending all tracks
    if entry.get('status_message_id'):
        try:
            await bot.delete_message(chat_id, entry['status_message_id'])
        except: pass 