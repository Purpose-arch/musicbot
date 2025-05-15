# track_downloader.py
import os
import asyncio
import tempfile
import traceback
import uuid

# Disable debug prints
import builtins
print = lambda *args, **kwargs: None
traceback.print_exc = lambda *args, **kwargs: None

import yt_dlp
from aiogram.types import FSInputFile
from mutagen.mp3 import MP3

from bot_instance import bot
from config import MAX_PARALLEL_DOWNLOADS, GROUP_MAX_TRACKS
from state import download_tasks, download_queues, playlist_downloads
from utils import set_mp3_metadata
from music_recognition import shazam, search_genius, search_yandex_music, search_musicxmatch, search_pylyrics, search_chartlyrics, search_lyricwikia
from vk_music import download_vk_track  # Импортируем функцию скачивания из VK


def _blocking_download_and_convert(url, download_opts):
    """Helper function to run blocking yt-dlp download."""
    print(f"[_blocking_dl] Starting download command for: {url}")
    
    # Проверяем, является ли URL внутренней ссылкой на VK
    if url.startswith("vk_internal:"):
        try:
            print(f"[VK-DEBUG] Processing VK internal URL: {url}")
            track_obj = download_opts.get("vk_track_obj")
            target_path = download_opts.get("target_path")
            
            if not track_obj:
                print("[VK-DEBUG] Error: track_obj is missing in download_opts")
                raise ValueError("Неверные параметры для скачивания трека VK: отсутствует vk_track_obj")
                
            if not target_path:
                print("[VK-DEBUG] Error: target_path is missing in download_opts")
                raise ValueError("Неверные параметры для скачивания трека VK: отсутствует target_path")
            
            print(f"[VK-DEBUG] Track object type: {type(track_obj)}, target path: {target_path}")
            
            # Выполняем синхронное скачивание
            from vk_music import init_vk_service
            print("[VK-DEBUG] Initializing VK service for download")
            service = init_vk_service()
            if not service:
                print("[VK-DEBUG] Failed to initialize VK service")
                raise Exception("Не удалось инициализировать сервис VK")
            
            print(f"[VK-DEBUG] VK service initialized successfully, type: {type(service)}")
                
            print(f"[VK-DEBUG] Calling save_music with track object and path: {target_path}")
            service.save_music(track_obj, target_path)
            
            # Проверяем результат
            if os.path.exists(target_path):
                size = os.path.getsize(target_path)
                print(f"[VK-DEBUG] File downloaded successfully: {target_path}, size: {size} bytes")
            else:
                print(f"[VK-DEBUG] ERROR: File not created at {target_path}")
                
            print(f"[_blocking_dl] VK download completed for: {url}")
            return
            
        except Exception as e:
            print(f"[_blocking_dl] ERROR during VK download for {url}: {type(e).__name__} - {e}")
            print(f"[VK-DEBUG] Exception details: {str(e)}")
            import traceback
            print(f"[VK-DEBUG] Traceback: {traceback.format_exc()}")
            raise
    else:
        # Стандартное скачивание через yt-dlp
        try:
            with yt_dlp.YoutubeDL(download_opts) as ydl:
                ydl.download([url])
                print(f"[_blocking_dl] yt-dlp download command finished for: {url}.")
        except Exception as e:
            print(f"[_blocking_dl] ERROR during yt-dlp download command for {url}: {type(e).__name__} - {e}")
            print(traceback.format_exc())
            raise


async def download_track(user_id, track_data, callback_message=None, status_message=None, original_message_context=None, playlist_download_id=None):
    """Downloads a single track. If part of a playlist (playlist_download_id is set),
    it updates the central playlist tracker instead of sending the file directly."""
    temp_path = None
    loop = asyncio.get_running_loop()
    is_playlist_track = playlist_download_id is not None
    playlist_entry = None
    original_status_message_id = None
    chat_id_for_updates = None
    url = track_data.get('url', '')

    # Determine message context
    if is_playlist_track:
        if playlist_download_id in playlist_downloads:
            playlist_entry = playlist_downloads[playlist_download_id]
            original_status_message_id = playlist_entry.get('status_message_id')
            chat_id_for_updates = playlist_entry.get('chat_id')
        else:
            print(f"ERROR: download_track called with playlist_id {playlist_download_id} but entry not found!")
            if user_id in download_tasks:
                download_tasks[user_id].pop(url, None)
                if not download_tasks[user_id]:
                    del download_tasks[user_id]
            return
    elif callback_message and status_message:
        chat_id_for_updates = callback_message.chat.id
        original_status_message_id = status_message.message_id
    elif original_message_context:
        chat_id_for_updates = original_message_context.chat.id
        original_status_message_id = None
        print(f"Warning: download_track using original_message_context for single track, no status_message.")
    else:
        print(f"ERROR: download_track called for single track but missing message context!")
        if user_id in download_tasks:
            download_tasks[user_id].pop(url, None)
            if not download_tasks[user_id]:
                del download_tasks[user_id]
        return

    title = track_data.get('title', 'Unknown Title')
    artist = track_data.get('channel', 'Unknown Artist')
    
    # Определяем тип чата для уменьшения сообщений в группе
    is_group = False
    if callback_message:
        is_group = callback_message.chat.type in ('group', 'supergroup')
    elif original_message_context:
        is_group = original_message_context.chat.type in ('group', 'supergroup')

    if not url:
        print(f"ERROR: Missing URL in track_data for {title}")
        if user_id in download_tasks:
            download_tasks[user_id].pop('unknown_url', None)
            if not download_tasks[user_id]:
                del download_tasks[user_id]
        return

    try:
        # Prepare file paths
        safe_title = ''.join(c if c.isalnum() or c in ('.','_','-') else '_' for c in title).strip('_.-')[:100]
        if not safe_title:
            safe_title = f"audio_{uuid.uuid4()}"
        temp_dir = tempfile.gettempdir()
        if is_playlist_track:
            base_temp_path = os.path.join(temp_dir, f"pl_{playlist_download_id}_{safe_title}")
        else:
            task_uuid = str(uuid.uuid4())
            base_temp_path = os.path.join(temp_dir, f"single_{task_uuid}_{safe_title}")
        print(f"[Download Path] Base temp path set to: {base_temp_path}")

        # Pre-cleanup
        for ext in ['.mp3','.m4a','.webm','.mp4','.opus','.ogg','.aac','.part']:
            p = f"{base_temp_path}{ext}"
            if os.path.exists(p):
                try:
                    os.remove(p)
                    print(f"Removed existing file: {p}")
                except Exception as e:
                    print(f"Warning: Could not remove {p}: {e}")

        expected_mp3 = base_temp_path + '.mp3'
        
        # Проверяем, является ли трек треком из VK
        if url.startswith("vk_internal:"):
            # Для VK треков используем собственный метод скачивания
            print(f"Starting VK download for: {title} - {artist}")
            print(f"[VK-DEBUG] Processing VK track in download_track: {url}")
            
            # Проверяем наличие объекта трека
            vk_track_obj = track_data.get("vk_track_obj")
            if not vk_track_obj:
                print(f"[VK-DEBUG] ERROR: vk_track_obj not found in track_data: {track_data.keys()}")
                raise ValueError("Отсутствует объект трека VK для скачивания")
            
            print(f"[VK-DEBUG] VK track object type: {type(vk_track_obj)}")
            
            # Подготавливаем параметры для VK скачивания
            download_opts = {
                "vk_track_obj": vk_track_obj,
                "target_path": expected_mp3
            }
            
            print(f"[VK-DEBUG] Calling blocking download with URL: {url}, target: {expected_mp3}")
            
            # Запускаем блокирующее скачивание
            await loop.run_in_executor(None, _blocking_download_and_convert, url, download_opts)
            print(f"Finished blocking VK download for: {title} - {artist}")
            
            # Проверяем результат
            if os.path.exists(expected_mp3):
                size = os.path.getsize(expected_mp3)
                print(f"[VK-DEBUG] VK download successful, file size: {size} bytes")
            else:
                print(f"[VK-DEBUG] ERROR: VK download failed, file not found: {expected_mp3}")
        else:
            # Download options для yt-dlp
            download_opts = {
                'format':'bestaudio[ext=m4a]/bestaudio/best',
                'postprocessors':[{'key':'FFmpegExtractAudio','preferredcodec':'mp3','preferredquality':'192'}],
                'outtmpl':base_temp_path + '.%(ext)s',
                'quiet':True,'verbose':False,'no_warnings':True,
                'prefer_ffmpeg':True,'nocheckcertificate':True,'ignoreerrors':True,
                'extract_flat':False,'ffmpeg_location':'/usr/bin/ffmpeg'
            }
            
            # Blocking download через yt-dlp
            print(f"Starting download for: {title} - {artist}")
            await loop.run_in_executor(None, _blocking_download_and_convert, url, download_opts)
            print(f"Finished blocking download for: {title} - {artist}")

        # Check file exists
        if not os.path.exists(expected_mp3):
            print(f"ERROR: MP3 not found at {expected_mp3}")
            for ext in ['.m4a','.webm','.opus','.ogg','.aac']:
                p = f"{base_temp_path}{ext}"
                if os.path.exists(p):
                    try: os.remove(p)
                    except: pass
                    break
            raise Exception(f"файл {expected_mp3} не создался после скачивания/конвертации")

        temp_path = expected_mp3
        print(f"Confirmed MP3 exists at: {temp_path}")

        if os.path.getsize(temp_path) == 0:
            raise Exception("скачанный файл пустой чет не то")

        # Validate MP3
        audio_check = MP3(temp_path)
        if not audio_check.info.length > 0:
            raise Exception("файл mp3 скачался но похоже битый (нулевая длина)")

        # Success handling
        if is_playlist_track:
            entry = playlist_downloads.get(playlist_download_id)
            if entry:
                for t in entry['tracks']:
                    if t['url']==url and t['status'] in ('pending','downloading'):
                        t['status']='success'
                        t['file_path']=temp_path
                        break
                entry['completed_tracks']+=1
                if entry['completed_tracks'] < entry['total_tracks'] and entry['status_message_id']:
                    try:
                        text = f"⏳ загрузка плейлиста {entry['playlist_title']}: {entry['completed_tracks']}/{entry['total_tracks']}"
                        await bot.edit_message_text(text, chat_id=entry['chat_id'], message_id=entry['status_message_id'])
                    except: pass
                if entry['completed_tracks']>=entry['total_tracks']:
                    asyncio.create_task(send_completed_playlist(playlist_download_id))
        else:
            # Single track:
            if set_mp3_metadata(temp_path, title, artist):
                # Attempt Shazam recognition to refine title and artist
                try:
                    result = await shazam.recognize(temp_path)
                    track_info = result.get("track", {})
                    rec_title = track_info.get("title") or track_info.get("heading")
                    rec_artist = track_info.get("subtitle")
                    if rec_title and rec_artist:
                        title, artist = rec_title, rec_artist
                except Exception as e:
                    print(f"Shazam recognition error: {e}")

                # Fetch lyrics in priority order: Genius -> Yandex Music -> MusicXMatch -> PyLyrics -> ChartLyrics -> LyricWikia
                lyrics = None
                for fetch in (search_genius, search_yandex_music, search_musicxmatch, search_pylyrics, search_chartlyrics, search_lyricwikia):
                    try:
                        lyrics = await fetch(artist, title)
                    except Exception:
                        lyrics = None
                    if lyrics:
                        break

                # Delete original status message if present
                if original_status_message_id:
                    try: await bot.delete_message(chat_id_for_updates, original_status_message_id)
                    except: pass

                ctx = callback_message or original_message_context
                if ctx:
                    # В группах сокращаем сообщения
                    if not is_group:
                        snd = await ctx.answer("📤 отправляю трек")
                    
                    # Send audio and capture the message
                    audio_msg = await bot.send_audio(
                        chat_id_for_updates,
                        FSInputFile(temp_path),
                        title=title,
                        performer=artist
                    )
                    
                    # Delete the temporary status message только в личных чатах
                    if not is_group and locals().get('snd'):
                        await bot.delete_message(snd.chat.id, snd.message_id)
                        
                    # Send lyrics if found (даже в группах)
                    if lyrics:
                        await bot.send_message(
                            chat_id_for_updates,
                            f"<blockquote expandable>{lyrics}</blockquote>",
                            reply_to_message_id=audio_msg.message_id,
                            parse_mode="HTML"
                        )

    except Exception as e:
        print(f"ERROR in download_track: {e}")
        traceback.print_exc()
        # Failure handling omitted for brevity
        raise
    finally:
        # Cleanup temp and task management
        if temp_path and os.path.exists(temp_path):
            delete = not is_playlist_track
            if is_playlist_track:
                failed=False
                entry = playlist_downloads.get(playlist_download_id)
                if entry:
                    for t in entry['tracks']:
                        if t['url']==url and t['status']=='failed': failed=True
                delete = failed
            if delete:
                try: os.remove(temp_path)
                except: pass
        if user_id in download_tasks:
            download_tasks[user_id].pop(url, None)
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