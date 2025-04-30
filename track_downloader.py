# track_downloader.py
import os
import asyncio
import tempfile
import traceback
import uuid

import yt_dlp
from aiogram.types import FSInputFile
from mutagen.mp3 import MP3

from bot_instance import bot
from config import MAX_PARALLEL_DOWNLOADS
from state import download_tasks, download_queues, playlist_downloads
from utils import set_mp3_metadata
# We'll import process_download_queue here to trigger next downloads
from download_queue import process_download_queue


def _blocking_download_and_convert(url, download_opts):
    """Helper function to run blocking yt-dlp download."""
    print(f"[_blocking_dl] Starting yt-dlp download command for: {url}")
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

    # Determine message context
    if is_playlist_track:
        if playlist_download_id in playlist_downloads:
            playlist_entry = playlist_downloads[playlist_download_id]
            original_status_message_id = playlist_entry.get('status_message_id')
            chat_id_for_updates = playlist_entry.get('chat_id')
        else:
            print(f"ERROR: download_track called with playlist_id {playlist_download_id} but entry not found!")
            if user_id in download_tasks:
                download_tasks[user_id].pop(track_data.get('url', 'unknown_url'), None)
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
            download_tasks[user_id].pop(track_data.get('url', 'unknown_url'), None)
            if not download_tasks[user_id]:
                del download_tasks[user_id]
        return

    title = track_data.get('title', 'Unknown Title')
    artist = track_data.get('channel', 'Unknown Artist')
    url = track_data.get('url')

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

        # Download options
        download_opts = {
            'format':'bestaudio[ext=m4a]/bestaudio/best',
            'postprocessors':[{'key':'FFmpegExtractAudio','preferredcodec':'mp3','preferredquality':'192'}],
            'outtmpl':base_temp_path + '.%(ext)s',
            'quiet':True,'verbose':False,'no_warnings':True,
            'prefer_ffmpeg':True,'nocheckcertificate':True,'ignoreerrors':True,
            'extract_flat':False,'ffmpeg_location':'/usr/bin/ffmpeg'
        }
        expected_mp3 = base_temp_path + '.mp3'

        # Blocking download
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
                    if t['url']==url and t['status']=='pending':
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
                if original_status_message_id:
                    try: await bot.delete_message(chat_id_for_updates, original_status_message_id)
                    except: pass
                ctx = callback_message or original_message_context
                if ctx:
                    snd = await ctx.answer("📤 отправляю трек")
                    await bot.send_audio(chat_id_for_updates, FSInputFile(temp_path), title=title, performer=artist)
                    await bot.delete_message(snd.chat.id, snd.message_id)

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
                asyncio.create_task(process_download_queue(user_id))

async def send_completed_playlist(playlist_download_id):
    """Sends all tracks of a completed playlist"""
    entry = playlist_downloads.pop(playlist_download_id, None)
    if not entry: return
    user_id = entry['user_id']; chat_id = entry['chat_id']
    succ = [t for t in entry['tracks'] if t['status']=='success']
    failed = [t for t in entry['tracks'] if t['status']=='failed']
    text = f"✅ плейлист '{entry['playlist_title']}' скачан, отправляю {len(succ)}"
    if failed: text += f" (не удалось {len(failed)})"
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