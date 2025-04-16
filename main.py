import os
import asyncio
import tempfile
import json
import base64
import math
import traceback
import re
from collections import defaultdict
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from mutagen.id3 import ID3, TIT2, TPE1, APIC
from mutagen.mp3 import MP3
import yt_dlp
import uuid
import time

# Загрузка переменных окружения
load_dotenv()

# Инициализация бота
bot = Bot(token=os.getenv('BOT_TOKEN'))
dp = Dispatcher()

# Константы
TRACKS_PER_PAGE = 10
MAX_TRACKS = 300
MAX_RETRIES = 3
MIN_SONG_DURATION = 45  # Минимальная длительность трека в секундах
MAX_SONG_DURATION = 720 # Максимальная длительность трека в секундах (12 минут)

# Хранилища
download_tasks = defaultdict(dict)
search_results = {}
download_queues = defaultdict(list)  # Очереди загрузок для каждого пользователя
playlist_downloads = {} # Отслеживание загрузок плейлистов {playlist_id: {details...}}
MAX_PARALLEL_DOWNLOADS = 5  # Максимальное количество одновременных загрузок

# Настройки yt-dlp
ydl_opts = {
    # Prioritize m4a, then best audio, then best overall
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
    # Note: 'audioformat', 'audioquality', 'extractaudio', 'keepvideo'
    # are implicitly handled by the postprocessor or are download-specific.
    # 'outtmpl' is better handled dynamically in download_track.
    'ffmpeg_location': '/usr/bin/ffmpeg',
}

def extract_title_and_artist(title):
    """Улучшенное извлечение названия трека и исполнителя"""
    # Удаляем общие префиксы
    prefixes = ['Official Video', 'Official Music Video', 'Official Audio', 'Lyric Video', 'Lyrics', 'Topic']
    for prefix in prefixes:
        if title.lower().endswith(f" - {prefix.lower()}"):
            title = title[:-len(prefix)-3]
    
    # Разделяем по разделителям
    separators = [' - ', ' — ', ' – ', ' | ', ' ~ ']
    for sep in separators:
        if sep in title:
            parts = title.split(sep)
            if len(parts) == 2:
                # Проверяем, какая часть больше похожа на название трека
                if len(parts[0]) > len(parts[1]):
                    return parts[0].strip(), parts[1].strip()
                else:
                    return parts[1].strip(), parts[0].strip()
    
    # Если разделитель не найден, пробуем определить по длине и содержанию
    if len(title) > 30:  # Предполагаем, что длинное название - это название трека
        return title, "Unknown Artist"
    elif any(char in title for char in ['(', '[', '{']):  # Если есть скобки, вероятно это название трека
        return title, "Unknown Artist"
    else:
        return title, "Unknown Artist"

async def search_youtube(query, max_results=50):
    try:
        search_opts = {
            **ydl_opts,
            'default_search': 'ytsearch',
            'max_downloads': max_results,
            'extract_flat': True,
        }
        
        with yt_dlp.YoutubeDL(search_opts) as ydl:
            info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
            if not info or 'entries' not in info:
                return []
            
            results = []
            for entry in info['entries']:
                if entry:
                    duration = entry.get('duration', 0)
                    # Filter by duration
                    if not duration or not (MIN_SONG_DURATION <= duration <= MAX_SONG_DURATION):
                        continue # Skip if duration is missing or outside the range
                        
                    title, artist = extract_title_and_artist(entry.get('title', 'Unknown Title'))
                    # Если artist остался Unknown Artist, используем uploader
                    if artist == "Unknown Artist":
                        artist = entry.get('uploader', 'Unknown Artist')
                    results.append({
                        'title': title,
                        'channel': artist,
                        'url': entry.get('url', ''),
                        'duration': entry.get('duration', 0)
                    })
            return results
    except Exception as e:
        print(f"An error occurred during search: {e}")
        return []

async def search_soundcloud(query, max_results=50):
    """Searches SoundCloud using yt-dlp."""
    try:
        # Use scsearch for SoundCloud
        search_opts = {
            **ydl_opts,
            'default_search': 'scsearch',
            'max_downloads': max_results,
            'extract_flat': True,
        }

        with yt_dlp.YoutubeDL(search_opts) as ydl:
            print(f"[SoundCloud Search Debug] Querying: scsearch{max_results}:{query}") # Добавим лог запроса
            info = ydl.extract_info(f"scsearch{max_results}:{query}", download=False)
            
            # Добавим вывод сырого ответа для отладки
            print(f"[SoundCloud Search Debug] Raw info received: {info}") 
            
            if not info or 'entries' not in info:
                print("[SoundCloud Search Debug] No info or entries found in response.") # Лог пустого ответа
                return []

            print(f"[SoundCloud Search Debug] Found {len(info['entries'])} potential entries.") # Лог количества найденных записей
            results = []
            for entry_index, entry in enumerate(info['entries']):
                if entry:
                    duration = entry.get('duration', 0)
                    # Duration from scsearch is already in seconds
                    duration_seconds = duration # Use duration directly
                    
                    # Filter by duration
                    if not duration_seconds or not (MIN_SONG_DURATION <= duration_seconds <= MAX_SONG_DURATION):
                        # Add log for skipped tracks
                        print(f"[SoundCloud Search Debug] Skipping entry {entry_index} ('{entry.get('title')}') due to duration: {duration_seconds}s (Range: {MIN_SONG_DURATION}-{MAX_SONG_DURATION})")
                        continue # Skip if duration is missing or outside the range

                    # SoundCloud often has cleaner titles, but let's try extraction anyway
                    # 'uploader' seems more reliable for artist on SoundCloud via yt-dlp
                    raw_title = entry.get('title', 'Unknown Title')
                    # Basic check: if " - " is present, use that, otherwise keep raw title and use uploader
                    if ' - ' in raw_title:
                         parts = raw_title.split(' - ', 1)
                         title = parts[1].strip() # Assume second part is title
                         artist = parts[0].strip() # Assume first part is artist
                    else:
                         title = raw_title
                         artist = entry.get('uploader', 'Unknown Artist')

                    # Fallback if title/artist extraction yields poor results
                    if not title or title == "Unknown Title":
                        title = raw_title
                    if not artist or artist == "Unknown Artist":
                        artist = entry.get('uploader', 'Unknown Artist')

                    results.append({
                        'title': title,
                        'channel': artist.strip(), # Use 'channel' key for consistency
                        'url': entry.get('webpage_url', entry.get('url', '')), # Prefer webpage_url if available
                        'duration': duration_seconds,
                        'source': 'soundcloud' # Add source identifier
                    })
                else:
                    # Лог, если запись пустая
                    print(f"[SoundCloud Search Debug] Entry at index {entry_index} is None or empty.")
            print(f"[SoundCloud Search Debug] Processed {len(results)} valid entries.") # Лог количества валидных треков
            return results
    except Exception as e:
        # Добавим вывод traceback для большей информации об ошибке
        print(f"An error occurred during SoundCloud search: {e}\n{traceback.format_exc()}")
        return []

async def search_bandcamp(query, max_results=50):
    """Searches Bandcamp using yt-dlp.
    Note: yt-dlp's bandcamp search (`bcsearch:`) primarily finds tracks, 
    but may not always provide accurate artist info directly in search results.
    It might return album artist instead of track artist sometimes.
    """
    try:
        # Use bcsearch for Bandcamp
        search_opts = {
            **ydl_opts,
            'default_search': 'bcsearch',
            'max_downloads': max_results,
            'extract_flat': True,
        }

        with yt_dlp.YoutubeDL(search_opts) as ydl:
            print(f"[Bandcamp Search Debug] Querying: bcsearch{max_results}:{query}")
            info = ydl.extract_info(f"bcsearch{max_results}:{query}", download=False)
            
            print(f"[Bandcamp Search Debug] Raw info received: {info}") 
            
            if not info or 'entries' not in info:
                print("[Bandcamp Search Debug] No info or entries found in response.")
                return []

            print(f"[Bandcamp Search Debug] Found {len(info['entries'])} potential entries.")
            results = []
            for entry_index, entry in enumerate(info['entries']):
                if entry:
                    # Bandcamp often provides duration directly in seconds
                    duration = entry.get('duration') 
                    if not duration or not (MIN_SONG_DURATION <= duration <= MAX_SONG_DURATION):
                        print(f"[Bandcamp Search Debug] Skipping entry {entry_index} ('{entry.get('title')}') due to duration: {duration}s (Range: {MIN_SONG_DURATION}-{MAX_SONG_DURATION})")
                        continue
                        
                    # Title/Artist extraction for Bandcamp can be tricky via search
                    title = entry.get('title', 'Unknown Title')
                    # 'artist' field might be album artist, 'uploader' might be label
                    artist = entry.get('artist', entry.get('uploader', 'Unknown Artist')) 
                    
                    # Sometimes title includes artist "Artist - Track Title"
                    if ' - ' in title and not entry.get('artist'): # Check if artist wasn't already found
                        parts = title.split(' - ', 1)
                        potential_artist = parts[0].strip()
                        potential_title = parts[1].strip()
                        # Heuristic: if first part is shorter or looks like an artist name, assume it is
                        if len(potential_artist) < 30 and len(potential_artist) < len(potential_title):
                             artist = potential_artist
                             title = potential_title
                             
                    # Ensure title doesn't start with artist if already captured
                    if artist != 'Unknown Artist' and title.startswith(f"{artist} - "):
                         title = title[len(artist) + 3:]
                         
                    results.append({
                        'title': title.strip(),
                        'channel': artist.strip(), # Use 'channel' key for consistency
                        'url': entry.get('url', ''),
                        'duration': duration,
                        'source': 'bandcamp'
                    })
                else:
                    print(f"[Bandcamp Search Debug] Entry at index {entry_index} is None or empty.")
            print(f"[Bandcamp Search Debug] Processed {len(results)} valid entries.")
            return results
    except Exception as e:
        print(f"An error occurred during Bandcamp search: {e}\n{traceback.format_exc()}")
        return []

def create_tracks_keyboard(tracks, page=0, search_id=""):
    total_pages = math.ceil(len(tracks) / TRACKS_PER_PAGE)
    start_idx = page * TRACKS_PER_PAGE
    end_idx = min(start_idx + TRACKS_PER_PAGE, len(tracks))
    
    buttons = []
    
    for i in range(start_idx, end_idx):
        track = tracks[i]
        track_data = {
            "title": track['title'],
            "artist": track['channel'],
            "url": track['url'],
            "search_id": search_id,
            # Ensure source is included if available, default to ''
            "source": track.get('source', '') 
        }
        
        track_json = json.dumps(track_data, ensure_ascii=False)
        if len(track_json.encode('utf-8')) > 60:
            callback_data = f"dl_{i+1}_{search_id}"
        else:
            callback_data = f"d_{base64.b64encode(track_json.encode('utf-8')).decode('utf-8')}"
        
        duration = track.get('duration', 0)
        if duration > 0:
            minutes = int(duration // 60)
            seconds = int(duration % 60)
            duration_str = f" ({minutes}:{seconds:02d})"
        else:
            duration_str = ""
        
        # Add source indicator to text
        source_indicator = ""
        if track.get('source') == 'youtube':
            source_indicator = " [YT]"
        elif track.get('source') == 'soundcloud':
            source_indicator = " [SC]"
        
        buttons.append([
            InlineKeyboardButton(
                text=f"🎧 {track['title']} - {track['channel']}{duration_str}",
                callback_data=callback_data
            )
        ])
    
    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"page_{page-1}_{search_id}"
                )
            )
        nav_buttons.append(
            InlineKeyboardButton(
                text=f"{page+1}/{total_pages}",
                callback_data="info"
            )
        )
        if page < total_pages - 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"page_{page+1}_{search_id}"
                )
            )
        buttons.append(nav_buttons)
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

async def process_download_queue(user_id):
    """Обработка очереди загрузок для пользователя"""
    while download_queues[user_id] and len(download_tasks[user_id]) < MAX_PARALLEL_DOWNLOADS:
        queue_item = download_queues[user_id].pop(0)
        
        # Unpack queue item - could be (track_data, message) or (track_data, playlist_id)
        playlist_download_id = None
        if isinstance(queue_item, tuple) and len(queue_item) == 2:
             track_data, second_item = queue_item
             if isinstance(second_item, str): # Assuming playlist_id is a string UUID
                 playlist_download_id = second_item
             # else: # Old format, second_item is callback_message (handle if needed?)
             #    This part is tricky. The old code appended (track_data, callback.message)
             #    The new playlist code appends (track_data, playlist_download_id)
             #    We need download_track to accept playlist_id OR the message.
             #    Let's adjust the structure slightly. Always pass playlist_id (can be None).
             #    Let's assume for now queue items from search are handled differently
             #    and only URL downloads populate the queue this way.
             #    REVISIT: How search downloads (`process_download_callback*`) interact queue/tasks.
             #    For now, focus on playlist download flow initiated from URL.
        else:
             print(f"[Queue Processing] Error: Unexpected item format in queue for user {user_id}: {queue_item}")
             continue # Skip malformed item

        # If it's part of a playlist, update its status in the tracker
        if playlist_download_id and playlist_download_id in playlist_downloads:
             playlist_entry = playlist_downloads[playlist_download_id]
             found_track = False
             for track in playlist_entry['tracks']:
                 if track['url'] == track_data['url'] and track['status'] == 'pending':
                     track['status'] = 'downloading'
                     found_track = True
                     print(f"[Queue Processing] Set track {track_data['url']} in playlist {playlist_download_id} to 'downloading'.")
                     break
             if not found_track:
                 print(f"[Queue Processing] Warning: Could not find pending track {track_data['url']} in playlist {playlist_download_id} to set as 'downloading'.")
                 # Maybe already picked up? Continue processing anyway.

        # --- Task Creation ---
        # Need to decide what context to pass to download_track.
        # For playlists, it needs playlist_id.
        # For single URL downloads (if they use this queue?), maybe the status_message?
        # Let's pass track_data and playlist_id (which can be None).
        # download_track will need access to playlist_downloads if id is not None.
        
        # Ensure user_id exists in download_tasks
        if user_id not in download_tasks: download_tasks[user_id] = {}

        # Check if already downloading THIS EXACT URL (unlikely due to queue pop, but safe)
        if track_data["url"] in download_tasks[user_id]:
             print(f"[Queue Processing] Warning: Task for URL {track_data['url']} already exists for user {user_id}. Skipping queue item.")
             continue

        print(f"[Queue Processing] Creating download task for: {track_data['title']} (Playlist ID: {playlist_download_id})")
        task = asyncio.create_task(
            # Pass playlist_id to download_track
            download_track(user_id, track_data, playlist_download_id=playlist_download_id)
        )
        download_tasks[user_id][track_data["url"]] = task

# Adjust download_track signature and logic
async def download_track(user_id, track_data, callback_message=None, status_message=None, playlist_download_id=None):
    """Downloads a single track. If part of a playlist (playlist_download_id is set),
    it updates the central playlist tracker instead of sending the file directly."""
    
    # Compatibility: Handle older calls that might still pass messages
    # If playlist_download_id is provided, message arguments are ignored for status updates.
    # If playlist_download_id is None, we expect callback_message and status_message for single downloads.
    
    temp_path = None
    loop = asyncio.get_running_loop()
    is_playlist_track = playlist_download_id is not None
    
    # --- Get necessary info from playlist tracker if applicable ---
    playlist_entry = None
    original_status_message_id = None
    chat_id_for_updates = None

    if is_playlist_track:
        if playlist_download_id in playlist_downloads:
            playlist_entry = playlist_downloads[playlist_download_id]
            original_status_message_id = playlist_entry.get('status_message_id')
            chat_id_for_updates = playlist_entry.get('chat_id')
        else:
            # This shouldn't happen if queuing logic is correct
            print(f"ERROR: download_track called with playlist_id {playlist_download_id} but entry not found!")
            # Clean up task tracking and exit? Or try to proceed as single?
            # Let's log error and exit the task gracefully.
            if user_id in download_tasks:
                download_tasks[user_id].pop(track_data["url"], None)
                if not download_tasks[user_id]: del download_tasks[user_id]
            return # Exit task

    elif callback_message and status_message:
        # Use message context for single downloads
        chat_id_for_updates = callback_message.chat.id
        original_status_message_id = status_message.message_id
    else:
        # This case should ideally not happen for single downloads either if triggered correctly
        print(f"ERROR: download_track called for single track (playlist_id=None) but missing message context!")
        # Clean up task tracking and exit
        if user_id in download_tasks:
            download_tasks[user_id].pop(track_data["url"], None)
            if not download_tasks[user_id]: del download_tasks[user_id]
        return # Exit task


    try:
        title = track_data["title"]
        artist = track_data["channel"] # Changed key internally, was 'artist' in playlist prep
        url = track_data["url"]
        
        # Создаем БОЛЕЕ безопасное имя файла
        # Заменяем пробелы на _, удаляем все кроме букв/цифр/./_/- 
        safe_title = "".join(c if c.isalnum() or c in ('.', '_', '-') else '_' for c in title).strip('_').strip('.').strip('-')
        # Ограничим длину на всякий случай
        safe_title = safe_title[:100] 
        if not safe_title:
             safe_title = f"audio_{uuid.uuid4()}" # Fallback name

        temp_dir = tempfile.gettempdir() # Должен быть /tmp в контейнере
        base_temp_path = os.path.join(temp_dir, safe_title) # e.g., /tmp/Ya_uebyvayu_v_dzhaz
        
        # Удаляем существующие файлы с разными расширениями перед скачиванием
        # Важно сделать это ДО вызова ydl.download
        for ext in ['.mp3', '.m4a', '.webm', '.mp4', '.opus', '.ogg', '.aac', '.part']:
            potential_path = f"{base_temp_path}{ext}"
            if os.path.exists(potential_path):
                try:
                    os.remove(potential_path)
                    print(f"Removed existing file: {potential_path}")
                except OSError as e:
                    print(f"Warning: Could not remove existing file {potential_path}: {e}")
        
        # Переопределяем ydl_opts для этой конкретной загрузки
        download_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best', # Предпочитаем m4a для конвертации
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            # ВАЖНО: outtmpl должен включать полный путь и расширение .%(ext)s 
            # чтобы ytdl сам обработал имя до и после конвертации
            'outtmpl': base_temp_path + '.%(ext)s', 
            'quiet': True, # Keep quiet for track download
            'verbose': False, # Keep quiet for track download
            'no_warnings': True, # Keep quiet for track download
            'prefer_ffmpeg': True,
            'nocheckcertificate': True,
            'ignoreerrors': True, # Оставляем, но будем проверять наличие файла
            'extract_flat': False, # Нужно для скачивания, а не только для поиска
            'ffmpeg_location': '/usr/bin/ffmpeg' # Оставляем явное указание пути
        }
        
        expected_mp3_path = base_temp_path + '.mp3'

        try:
            await bot.edit_message_text(
                f"⏳ качаю трек {title} - {artist}",
                chat_id=callback_message.chat.id,
                message_id=status_message.message_id
            )
            
            print(f"\nStarting download for: {title} - {artist}")
            print(f"URL: {url}")
            print(f"Output template: {download_opts['outtmpl']}")
            print(f"Expected MP3 path: {expected_mp3_path}")
            print(f"Using download options: {download_opts}")

            # Запускаем блокирующую загрузку/конвертацию в отдельном потоке
            await loop.run_in_executor(
                None, 
                _blocking_download_and_convert,
                url,
                download_opts # Передаем локальные download_opts
            )
            
            print(f"Finished blocking download call for: {title} - {artist}")

            # --- Проверка наличия файла после скачивания --- 
            if not os.path.exists(expected_mp3_path):
                print(f"ERROR: Expected MP3 file NOT FOUND at {expected_mp3_path} after download attempt.")
                # Дополнительно проверим, не остался ли файл с другим расширением (ошибка конвертации?)
                found_other = False
                for ext in ['.m4a', '.webm', '.opus', '.ogg', '.aac']:
                     potential_path = f"{base_temp_path}{ext}"
                     if os.path.exists(potential_path):
                         print(f"Warning: Found intermediate file {potential_path} instead of MP3. Conversion likely failed.")
                         found_other = True
                         # Попробуем удалить промежуточный файл
                         try: 
                             os.remove(potential_path)
                         except OSError as e:
                             print(f"Could not remove intermediate file {potential_path}: {e}")
                         break
                raise Exception(f"файл {expected_mp3_path} не создался после скачивания/конвертации")
            
            temp_path = expected_mp3_path 
            print(f"Confirmed MP3 file exists at: {temp_path}")
            
            if os.path.getsize(temp_path) == 0:
                print(f"ERROR: Downloaded file {temp_path} is empty.")
                raise Exception("скачанный файл пустой чет не то")
            
            print(f"File size: {os.path.getsize(temp_path)} bytes")

            # --- NEW: Validate MP3 file structure ---
            try:
                print(f"Validating MP3 structure for {temp_path}...")
                audio_check = MP3(temp_path) 
                if not audio_check.info.length > 0:
                     print(f"ERROR: MP3 file {temp_path} loaded but has zero length/duration.")
                     raise Exception("файл mp3 скачался но похоже битый (нулевая длина)")
                print(f"MP3 Validation PASSED for {temp_path}, duration: {audio_check.info.length}s")
            except Exception as validation_error:
                print(f"ERROR: MP3 Validation FAILED for {temp_path}: {validation_error}")
                raise Exception(f"скачанный файл не является валидным mp3 {validation_error}")

            # --- Processing based on whether it's a playlist track ---
            if is_playlist_track:
                print(f"[Playlist Download] Track '{title}' SUCCESS. Storing path: {temp_path}")
                # Update playlist tracker
                track_updated = False
                for track in playlist_entry['tracks']:
                    if track['url'] == url:
                        track['status'] = 'success'
                        track['file_path'] = temp_path
                        track_updated = True
                        break
                
                if not track_updated:
                     print(f"ERROR: Could not find track {url} in playlist {playlist_download_id} tracker to mark success.")
                     # Don't delete file yet, maybe send_completed_playlist can find it?
                     
                playlist_entry['completed_tracks'] += 1
                
                # Check if playlist is complete
                if playlist_entry['completed_tracks'] >= playlist_entry['total_tracks']:
                     print(f"Playlist {playlist_download_id} ('{playlist_entry['playlist_title']}') completed. Triggering send function.")
                     asyncio.create_task(send_completed_playlist(playlist_download_id))
                     # Keep temp_path, send_completed_playlist will handle cleanup
                else:
                     print(f"Playlist {playlist_download_id} progress: {playlist_entry['completed_tracks']}/{playlist_entry['total_tracks']}")
                     # Keep temp_path for later sending
                     
                # DO NOT SEND OR DELETE TEMP FILE HERE FOR PLAYLISTS

            else: # --- Single track download: Send immediately ---
                print(f"Setting metadata for {temp_path}...")
                if set_mp3_metadata(temp_path, title, artist):
                    print(f"Metadata set successfully. Preparing to send {temp_path}.")
                    # Delete original status message only for single downloads
                    if original_status_message_id and chat_id_for_updates:
                        try:
                            await bot.delete_message(chat_id=chat_id_for_updates, message_id=original_status_message_id)
                        except Exception as del_err:
                            print(f"Warning: Failed to delete original status message {original_status_message_id}: {del_err}")

                    # Use callback_message context if available for sending confirmation
                    sending_context = callback_message if callback_message else original_message # Fallback to original if needed
                    sending_message = await sending_context.answer("📤 отправляю трек") # Use answer on the context

                    print(f"Sending audio {temp_path}...")
                    await bot.send_audio(
                        chat_id=chat_id_for_updates, # Use chat_id derived earlier
                        audio=FSInputFile(temp_path),
                        title=title,
                        performer=artist
                    )
                    print(f"Audio sent successfully. Deleting sending message.")
                    await bot.delete_message(
                        chat_id=sending_message.chat.id, # Use chat_id from the message we just sent
                        message_id=sending_message.message_id
                    )
                    print(f"Finished processing track: {title} - {artist}")
                else:
                    print(f"ERROR: Failed to set metadata for {temp_path}.")
                    raise Exception(f"ошибка при установке метаданных для {title} - {artist}")
                 # Cleanup happens in finally block for single tracks too now

        except Exception as e:
            print(f"ERROR during download/processing for {title} - {artist}: {e}")
            error_text = f"❌ блин ошибка {str(e).lower()}"
            if len(error_text) > 4000: error_text = error_text[:3995] + "..."

            if is_playlist_track:
                # Update playlist tracker with failure
                track_updated = False
                for track in playlist_entry['tracks']:
                    if track['url'] == url:
                        track['status'] = 'failed'
                        track['error_message'] = str(e)
                        track_updated = True
                        break
                if not track_updated:
                     print(f"ERROR: Could not find track {url} in playlist {playlist_download_id} tracker to mark failure.")

                playlist_entry['completed_tracks'] += 1
                print(f"Playlist {playlist_download_id} progress after failure: {playlist_entry['completed_tracks']}/{playlist_entry['total_tracks']}")

                # Check if playlist is complete even after failure
                if playlist_entry['completed_tracks'] >= playlist_entry['total_tracks']:
                     print(f"Playlist {playlist_download_id} ('{playlist_entry['playlist_title']}') completed (with failures). Triggering send function.")
                     asyncio.create_task(send_completed_playlist(playlist_download_id))
                
                # Do not edit the main status message here for individual track failures

            else: # Single track failure - edit the status message
                 if original_status_message_id and chat_id_for_updates:
                     try:
                         await bot.edit_message_text(
                             chat_id=chat_id_for_updates,
                             message_id=original_status_message_id,
                             text=error_text
                         )
                     except Exception as edit_error:
                         print(f"Failed to edit message for error: {edit_error}")
                         # Try sending a new message as fallback for single downloads
                         try:
                             # Use callback_message or original_message context
                             error_context = callback_message if callback_message else original_message
                             await error_context.answer(error_text)
                         except Exception as send_error:
                             print(f"[Single Download] Warning: Failed to send new message for error: {send_error}")

    finally:
        # --- Cleanup ---
        # For single tracks, temp_path should be deleted.
        # For playlists, temp_path should ONLY be deleted if the download FAILED.
        # Successful playlist tracks are deleted later by send_completed_playlist.
        should_delete_temp_file = False
        if temp_path and os.path.exists(temp_path):
            if not is_playlist_track:
                # Always delete for single tracks (success or failure)
                should_delete_temp_file = True
                print(f"[Cleanup] Marking single track temp file for deletion: {temp_path}")
            else:
                # For playlists, only delete if this track failed
                track_failed = False
                if playlist_entry: # Ensure entry exists
                    for track in playlist_entry['tracks']:
                        if track['url'] == url and track['status'] == 'failed':
                            track_failed = True
                            break
                if track_failed:
                     should_delete_temp_file = True
                     print(f"[Cleanup] Marking FAILED playlist track temp file for deletion: {temp_path}")
                else:
                     print(f"[Cleanup] Keeping SUCCESSFUL playlist track temp file for later sending: {temp_path}")

            if should_delete_temp_file:
                try:
                    print(f"Cleaning up temporary file: {temp_path}")
                    os.remove(temp_path)
                except Exception as remove_error:
                    print(f"Warning: Failed to remove temp file {temp_path}: {remove_error}")
            
        # --- Task Management ---
        # Remove task entry regardless of playlist/single or success/failure, as the task itself is done.
        if user_id in download_tasks:
            if download_tasks[user_id].pop(track_data["url"], None):
                 print(f"Removed task entry for URL: {track_data['url']}")
            else:
                 print(f"Task entry for URL {track_data['url']} not found or already removed.")
            if not download_tasks[user_id]:
                print(f"No tasks left for user {user_id}, removing user entry.")
                del download_tasks[user_id]
            else:
                 print(f"{len(download_tasks[user_id])} tasks remaining for user {user_id}.")

        # --- Trigger Queue Processing ---
        # Check queue AGAIN after finishing a task, regardless of type.
        # This ensures the next item is picked up if slots are free.
        if user_id in download_queues and download_queues[user_id]:
            # Check if parallel slots are available before triggering
            active_downloads = sum(1 for task in download_tasks.get(user_id, {}).values() if not task.done())
            if active_downloads < MAX_PARALLEL_DOWNLOADS:
                 print(f"Processing next item in queue for user {user_id} (active: {active_downloads}).")
                 # Use create_task to avoid blocking the finally block
                 asyncio.create_task(process_download_queue(user_id))
            else:
                 print(f"Queue for user {user_id} has items, but max parallel downloads ({active_downloads}) reached.")
        # else: No need for an else print here, common case

# --- Function to send completed playlist tracks ---
async def send_completed_playlist(playlist_download_id):
    """Sends all successfully downloaded tracks for a completed playlist in order,
    then cleans up."""
    playlist_entry = playlist_downloads.pop(playlist_download_id, None)
    if not playlist_entry:
        print(f"[Send Playlist] ERROR: Playlist entry {playlist_download_id} not found. Cannot send.")
        return

    user_id = playlist_entry['user_id']
    chat_id = playlist_entry['chat_id']
    original_status_message_id = playlist_entry.get('status_message_id')
    playlist_title = playlist_entry.get('playlist_title', 'Плейлист')
    tracks = playlist_entry.get('tracks', [])
    
    successful_tracks = [t for t in tracks if t['status'] == 'success' and t.get('file_path')]
    failed_tracks = [t for t in tracks if t['status'] == 'failed']
    
    # --- Update Status Message or Send New One ---
    final_status_message = None
    final_status_text = f"✅ Плейлист '{playlist_title}' скачан. Отправляю {len(successful_tracks)} треков..."
    if len(failed_tracks) > 0:
        final_status_text += f" (Не удалось скачать {len(failed_tracks)})"
    
    try:
        if original_status_message_id:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=original_status_message_id,
                text=final_status_text
            )
            # Store the message object if needed later, though we might delete it soon
            # For simplicity, let's assume editing is enough for now.
        else:
            # If original status message ID was lost, send a new one
            final_status_message = await bot.send_message(chat_id, final_status_text)
            # Store its ID in the (now popped) entry? Not straightforward.
            # Let's just send it and potentially delete later if we can get the ID.
    except Exception as e:
        print(f"[Send Playlist] Warning: Failed to update/send final status message for {playlist_download_id}: {e}")
        # Try sending a new message as a fallback
        try:
            final_status_message = await bot.send_message(chat_id, final_status_text)
        except Exception as send_err:
            print(f"[Send Playlist] Error: Could not send final status message either: {send_err}")
            # Proceed with sending tracks anyway if possible

    # --- Send Successful Tracks in Order ---
    sent_count = 0
    send_errors = 0
    files_to_delete = []

    # Add failed track files to cleanup list immediately
    for track in failed_tracks:
        if track.get('file_path') and os.path.exists(track['file_path']):
            files_to_delete.append(track['file_path'])

    # Iterate through original track list to maintain order
    for track in tracks:
        if track['status'] == 'success' and track.get('file_path'):
            file_path = track['file_path']
            title = track.get('title', 'Unknown Title')
            artist = track.get('artist', 'Unknown Artist')
            
            if os.path.exists(file_path):
                try:
                    print(f"[Send Playlist] Sending track {track['original_index']}: {title} from {file_path}")
                    # Setting metadata again just before sending (optional, but maybe safer)
                    set_mp3_metadata(file_path, title, artist)
                    
                    await bot.send_audio(
                        chat_id=chat_id,
                        audio=FSInputFile(file_path),
                        title=title,
                        performer=artist
                    )
                    sent_count += 1
                    files_to_delete.append(file_path) # Add successfully sent file for deletion
                    # Small delay between sends? Optional.
                    # await asyncio.sleep(0.5)
                except Exception as send_audio_err:
                    print(f"[Send Playlist] ERROR sending track {title} ({file_path}): {send_audio_err}")
                    send_errors += 1
                    # Keep file for now? Or add to delete list anyway?
                    # Let's add to delete list, as we can't send it.
                    files_to_delete.append(file_path)
            else:
                print(f"[Send Playlist] ERROR: File path {file_path} for track {title} not found!")
                send_errors += 1

    # --- Final Cleanup and Summary ---
    print(f"[Send Playlist] Finished sending for {playlist_download_id}. Sent: {sent_count}, Failed to send: {send_errors}, Failed to download: {len(failed_tracks)}")

    # Delete temporary files
    deleted_count = 0
    for f_path in set(files_to_delete): # Use set to avoid duplicates
        try:
            if os.path.exists(f_path):
                os.remove(f_path)
                deleted_count += 1
        except Exception as del_err:
            print(f"[Send Playlist] Warning: Failed to delete temp file {f_path}: {del_err}")
    print(f"[Send Playlist] Cleaned up {deleted_count} temporary files for {playlist_download_id}.")

    # Optionally send a final summary message if needed, or delete the status message
    try:
        # Delete the status message ("Отправляю X треков...")
        status_message_to_delete_id = final_status_message.message_id if final_status_message else original_status_message_id
        if status_message_to_delete_id:
            await bot.delete_message(chat_id=chat_id, message_id=status_message_to_delete_id)
            print(f"[Send Playlist] Deleted final status message {status_message_to_delete_id}")
    except Exception as final_del_err:
        print(f"[Send Playlist] Warning: Failed to delete final status message: {final_del_err}")

    # Playlist entry already removed from playlist_downloads at the start

def set_mp3_metadata(file_path, title, artist):
    try:
        try:
            audio = ID3(file_path)
        except:
            audio = ID3()
        
        audio["TIT2"] = TIT2(encoding=3, text=title)
        audio["TPE1"] = TPE1(encoding=3, text=artist)
        audio.save(file_path)
        return True
    except Exception as e:
        print(f"ошибка при установке метаданных: {e}")
        return False

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🐈‍⬛ приветик я\n\n"
        "✅ персональный\n"
        "✅ иксперементальный\n"
        "✅ скачивающий\n"
        "✅ юный\n"
        "✅ новобранец\n\n"
        "🎵 ищу и скачиваю музыку по названию\n"
        "🔗 или просто скинь мне ссылку на видео/аудио и я попробую скачать\n\n"
        "👥 также есть возможность добавить бота в группу и использовать команду\n"
        "«музыка (запрос)»\n"
        "либо отправить ссылку на видео/аудио там"
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    # Using triple quotes for cleaner multiline string
    help_text = """*как пользоваться ботом* 

1️⃣ **поиск музыки** просто напиши название трека или исполнителя я поищу на soundcloud bandcamp и youtube и покажу список

2️⃣ **скачивание по ссылке** отправь мне прямую ссылку на страницу с видео или аудио (например с youtube soundcloud vk insta tiktok и многих других) я попытаюсь скачать медиа

*команды*
/start - показать приветственное сообщение
/help - показать это сообщение
/search [запрос] - искать музыку по запросу
/cancel - отменить активные загрузки (из поиска)"""
    await message.answer(help_text, parse_mode="Markdown")

@dp.message(Command("search"))
async def cmd_search(message: types.Message):
    if len(message.text.split()) < 2:
        await message.answer("❌ напиши что-нибудь после /search плиз\nнапример /search coldplay yellow")
        return
    
    query = " ".join(message.text.split()[1:])
    searching_message = await message.answer("🔍 ищу музыку...")
    
    search_id = str(uuid.uuid4())
    # Search all sources concurrently
    max_results_per_source = MAX_TRACKS // 3 # Divide budget
    youtube_results, soundcloud_results, bandcamp_results = await asyncio.gather(
        search_youtube(query, max_results_per_source),
        search_soundcloud(query, max_results_per_source),
        search_bandcamp(query, max_results_per_source) # Add bandcamp search
    )

    # Prioritize SoundCloud -> Bandcamp -> YouTube results
    combined_results = []
    # Add SoundCloud results first
    for sc_track in soundcloud_results:
        if 'source' not in sc_track:
            sc_track['source'] = 'soundcloud'
        combined_results.append(sc_track)
    # Then add Bandcamp results
    for bc_track in bandcamp_results:
         if 'source' not in bc_track:
             bc_track['source'] = 'bandcamp'
         combined_results.append(bc_track)
    # Then add YouTube results
    for yt_track in youtube_results:
        if 'source' not in yt_track:
            yt_track['source'] = 'youtube'
        combined_results.append(yt_track)

    # Limit total results if needed

    if not combined_results:
        await message.answer("❌ чет ничего не нашлось ни там ни там попробуй другой запрос")
        await bot.delete_message(chat_id=searching_message.chat.id, message_id=searching_message.message_id)
        return
    
    search_results[search_id] = combined_results # Store combined results
    keyboard = create_tracks_keyboard(combined_results, 0, search_id)
    
    await message.answer(
        f"🎵 нашел для тебя {len(combined_results)} треков по запросу «{query}» ⬇",
        reply_markup=keyboard
    )
    await bot.delete_message(chat_id=searching_message.chat.id, message_id=searching_message.message_id)

@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message):
    user_id = message.from_user.id
    if user_id in download_tasks and any(not task.done() for task in download_tasks[user_id].values()):
        active_tasks = [task for task in download_tasks[user_id].values() if not task.done()]
        for task in active_tasks:
            task.cancel()
        # Give tasks a moment to cancel
        await asyncio.sleep(0.1) 
        # Clear only cancelled/finished tasks or the entire user entry if empty
        download_tasks[user_id] = {url: task for url, task in download_tasks[user_id].items() if not task.cancelled() and not task.done()}
        if not download_tasks[user_id]:
            del download_tasks[user_id]
        
        # Also clear the queue for this user
        if user_id in download_queues:
            download_queues[user_id].clear()
            
        await message.answer("✅ ок отменил все активные загрузки и почистил очередь")
    else:
        await message.answer("❌ так щас ничего и не качается вроде")

@dp.callback_query(F.data.startswith("d_"))
async def process_download_callback(callback: types.CallbackQuery):
    try:
        track_data = json.loads(base64.b64decode(callback.data[2:]).decode('utf-8'))
        user_id = callback.from_user.id
        
        # Check if already downloading this specific track
        if track_data["url"] in download_tasks.get(user_id, {}):
            await callback.answer("этот трек уже качается или в очереди", show_alert=True)
            return
            
        # Check queue as well
        if any(item[0]['url'] == track_data['url'] for item in download_queues.get(user_id, [])):
             await callback.answer("этот трек уже качается или в очереди", show_alert=True)
             return
             
        active_downloads = sum(1 for task in download_tasks.get(user_id, {}).values() if not task.done())
        queue_size = len(download_queues.get(user_id, []))

        if active_downloads >= MAX_PARALLEL_DOWNLOADS:
            download_queues[user_id].append((track_data, callback.message))
            await callback.answer(
                f"⏳ добавил в очередь ({queue_size+1}-й) качаю {active_downloads}/{MAX_PARALLEL_DOWNLOADS}"
            )
        else:
            # Using answer instead of sending a new message for initial status
            status_message = await callback.message.answer(f"⏳ начинаю скачивать {track_data['title']} - {track_data['channel']}")
            task = asyncio.create_task(
                download_track(user_id, track_data, callback.message, status_message)
            )
            download_tasks[user_id][track_data["url"]] = task
            await callback.answer("начал скачивание")
            
    except json.JSONDecodeError:
         await callback.message.answer("❌ чет не смог разобрать данные трека попробуй поискать снова")
         await callback.answer()
    except Exception as e:
        print(f"Error in process_download_callback: {e}")
        await callback.message.answer(f"❌ ой ошибка {str(e).lower()}")
        await callback.answer()

@dp.callback_query(F.data.startswith("dl_"))
async def process_download_callback_with_index(callback: types.CallbackQuery):
    try:
        parts = callback.data.split("_")
        track_index = int(parts[1]) - 1
        search_id = parts[2]
        
        if search_id not in search_results:
            await callback.answer("❌ результаты поиска уже устарели найди снова плз", show_alert=True)
            return
        
        tracks = search_results[search_id]
        if 0 <= track_index < len(tracks):
            track_data = tracks[track_index]
            user_id = callback.from_user.id

            # Check if already downloading this specific track
            if track_data["url"] in download_tasks.get(user_id, {}):
                await callback.answer("этот трек уже качается или в очереди", show_alert=True)
                return
                
            # Check queue as well
            if any(item[0]['url'] == track_data['url'] for item in download_queues.get(user_id, [])):
                 await callback.answer("этот трек уже качается или в очереди", show_alert=True)
                 return

            active_downloads = sum(1 for task in download_tasks.get(user_id, {}).values() if not task.done())
            queue_size = len(download_queues.get(user_id, []))

            if active_downloads >= MAX_PARALLEL_DOWNLOADS:
                download_queues[user_id].append((track_data, callback.message))
                await callback.answer(
                    f"⏳ добавил в очередь ({queue_size+1}-й) качаю {active_downloads}/{MAX_PARALLEL_DOWNLOADS}"
                )
            else:
                status_message = await callback.message.answer(f"⏳ начинаю скачивать {track_data['title']} - {track_data['channel']}")
                task = asyncio.create_task(
                    download_track(user_id, track_data, callback.message, status_message)
                )
                download_tasks[user_id][track_data["url"]] = task
                await callback.answer("начал скачивание")
        else:
            await callback.answer("❌ не нашел трек по этому индексу", show_alert=True)
            
    except IndexError:
         await callback.answer("❌ чет не смог разобрать данные для скачивания", show_alert=True)
    except Exception as e:
        print(f"Error in process_download_callback_with_index: {e}")
        await callback.answer(f"❌ ой ошибка {str(e).lower()}", show_alert=True)

@dp.callback_query(F.data.startswith("page_"))
async def process_page_callback(callback: types.CallbackQuery):
    try:
        parts = callback.data.split("_")
        page = int(parts[1])
        search_id = parts[2]
        
        if search_id not in search_results:
            await callback.answer("❌ эти результаты поиска уже старые поищи заново", show_alert=True)
            return
        
        tracks = search_results[search_id]
        keyboard = create_tracks_keyboard(tracks, page, search_id)
        
        await callback.message.edit_reply_markup(reply_markup=keyboard)
        await callback.answer()
    except (IndexError, ValueError):
        await callback.answer("❌ чет не смог понять номер страницы", show_alert=True)
    except Exception as e:
        print(f"Error in process_page_callback: {e}")
        await callback.answer(f"❌ блин ошибка при перелистывании {str(e).lower()}", show_alert=True)
        
@dp.callback_query(F.data == "info")
async def process_info_callback(callback: types.CallbackQuery):
    await callback.answer()

@dp.message()
async def handle_text(message: types.Message):
    # Ignore commands explicitly
    if message.text.startswith('/'):
        return
    
    text_lower = message.text.lower().strip()
    chat_type = message.chat.type

    # --- Group Chat Logic --- 
    if chat_type in ('group', 'supergroup'):
        # Check for 'музыка ' command first
        if text_lower.startswith("музыка "):
            query = message.text.strip()[len("музыка "):].strip()
            if query:
                 await handle_group_search(message, query)
            else:
                 await message.reply("❌ после 'музыка' нужно написать запрос для поиска.")
            return
        
        # Then check for any message containing a URL
        # Use regex to find the first http/https URL in the message text
        url_match = re.search(r'https?://[^\s]+', message.text)
        if url_match:
            url = url_match.group(0)
            print(f"[Group URL Detect] Found URL: {url} in message: '{message.text}'")
            await handle_url_download(message, url)
            return
        
        # Ignore other messages in groups
        return 
            
    # --- Private Chat Logic --- 
    elif chat_type == 'private':
        url_check = message.text.strip()
        if url_check.startswith(('http://', 'https://')):
            await handle_url_download(message, url_check) # Pass URL directly
            return
        else:
            # Treat as search query - Indent this whole block
            query = message.text
            searching_message = await message.answer("🔍 ищу музыку...")
            search_id = str(uuid.uuid4())
            # Search all sources concurrently
            try:
                max_results_per_source = MAX_TRACKS // 3
                youtube_results, soundcloud_results, bandcamp_results = await asyncio.gather(
                    search_youtube(query, max_results_per_source),
                    search_soundcloud(query, max_results_per_source),
                    search_bandcamp(query, max_results_per_source)
                )

                # Prioritize SoundCloud -> Bandcamp -> YouTube results
                combined_results = []
                for sc_track in soundcloud_results:
                    if 'source' not in sc_track: sc_track['source'] = 'soundcloud'
                    combined_results.append(sc_track)
                for bc_track in bandcamp_results:
                    if 'source' not in bc_track: bc_track['source'] = 'bandcamp'
                    combined_results.append(bc_track)
                for yt_track in youtube_results:
                    if 'source' not in yt_track: yt_track['source'] = 'youtube'
                    combined_results.append(yt_track)

                if not combined_results:
                    await bot.edit_message_text(
                         chat_id=searching_message.chat.id, 
                         message_id=searching_message.message_id,
                         text="❌ ничего не нашел по твоему запросу ни там ни там попробуй еще раз"
                    )
                    return # Correctly indented return
    
                search_results[search_id] = combined_results
                keyboard = create_tracks_keyboard(combined_results, 0, search_id)
                await bot.edit_message_text(
                    chat_id=searching_message.chat.id, 
                    message_id=searching_message.message_id,
                    text=f"🎵 нашел для тебя {len(combined_results)} треков по запросу «{query}» ⬇",
                    reply_markup=keyboard
                )
            except Exception as e:
                 print(f"Error during private search for query '{query}': {e}")
                 await bot.edit_message_text(
                     chat_id=searching_message.chat.id, 
                     message_id=searching_message.message_id,
                     text=f"❌ блин ошибка при поиске: {e}"
                 )
            return # End of private search logic

    # If chat type is somehow neither private nor group/supergroup, do nothing
        return
    
async def handle_url_download(message: types.Message, url: str):
    """Handles messages identified as URLs (or via 'медиакот') to initiate download."""
    # Use reply for group trigger, answer for direct URL in private
    reply_method = message.reply if message.chat.type != 'private' else message.answer
    status_message = await reply_method(f"⏳ пытаюсь скачать медиа по ссылке {url[:50]}", disable_web_page_preview=True)
    
    # Pass the original message for context if needed later, and the status message to update
    await download_media_from_url(url, message, status_message)

async def handle_group_search(message: types.Message, query: str):
    """Handles 'музыкакот' command in groups."""
    status_message = await message.reply("🔍 ищу музыку...")
    search_id = str(uuid.uuid4())
    
    try:
        max_results_per_source = MAX_TRACKS // 3
        youtube_results, soundcloud_results, bandcamp_results = await asyncio.gather(
            search_youtube(query, max_results_per_source),
            search_soundcloud(query, max_results_per_source),
            search_bandcamp(query, max_results_per_source)
        )

        combined_results = []
        for sc_track in soundcloud_results:
            if 'source' not in sc_track: sc_track['source'] = 'soundcloud'
            combined_results.append(sc_track)
        for bc_track in bandcamp_results:
            if 'source' not in bc_track: bc_track['source'] = 'bandcamp'
            combined_results.append(bc_track)
        for yt_track in youtube_results:
            if 'source' not in yt_track: yt_track['source'] = 'youtube'
            combined_results.append(yt_track)

        if not combined_results:
            await bot.edit_message_text(
                chat_id=status_message.chat.id,
                message_id=status_message.message_id,
                text="❌ ничего не нашел по твоему запросу ни там ни там попробуй еще раз"
            )
            return

        search_results[search_id] = combined_results
        keyboard = create_tracks_keyboard(combined_results, 0, search_id)
        await bot.edit_message_text(
            chat_id=status_message.chat.id,
            message_id=status_message.message_id,
            text=f"🎵 нашел для тебя {len(combined_results)} треков по запросу «{query}» ⬇",
        reply_markup=keyboard
    )

    except Exception as e:
        print(f"Error during group search for query '{query}': {e}")
        await bot.edit_message_text(
            chat_id=status_message.chat.id,
            message_id=status_message.message_id,
            text=f"❌ блин ошибка при поиске: {e}"
        )

async def download_media_from_url(url: str, original_message: types.Message, status_message: types.Message):
    """Downloads media (audio or video) from a direct URL using yt-dlp.
    Handles both single media links and playlist links.
    For playlists, queues all tracks and sends them together upon completion."""
    loop = asyncio.get_running_loop()
    user_id = original_message.from_user.id # Get user_id early

    download_uuid = str(uuid.uuid4()) # Used for single file downloads
    temp_dir = tempfile.gettempdir()
    base_temp_path = os.path.join(temp_dir, f"media_{download_uuid}")
    actual_downloaded_path = None # Path to the final downloaded file (for single downloads)
    
    # Options for general media download (Try 'best' first, then specific video/audio combos)
    media_ydl_opts = {
        'format': 'best/bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]', # Added 'best/' at the beginning
        'outtmpl': base_temp_path + '.%(ext)s', # Let ytdl determine extension
        'quiet': False,
        'verbose': True,
        'no_warnings': False,
        'prefer_ffmpeg': True,
        'nocheckcertificate': True,
        'ignoreerrors': True, # Important for trying potentially unsupported URLs
        'extract_flat': False,
        'ffmpeg_location': '/usr/bin/ffmpeg',
        # Add merge output format if separate streams are downloaded
        'merge_output_format': 'mp4', 
        # No audio-specific postprocessor here initially
    }

    try:
        # --- 1. Get Info (Optional but good for metadata/title) --- 
        extracted_info = None
        try:
            # Use slightly different opts just for info extraction to avoid downloading accidentally
            info_opts = {
                'quiet': True, 
                'no_warnings': True, 
                'nocheckcertificate': True, 
                'ignoreerrors': True, 
                'extract_flat': False # Need full entries for playlist detection
            }
            with yt_dlp.YoutubeDL(info_opts) as ydl:
                extracted_info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))
                if not extracted_info:
                    print(f"[URL Download] Could not extract info for {url}")
        except Exception as info_err:
            print(f"[URL Download] Error extracting info for {url}: {info_err}")
            # Continue anyway, try downloading

        # --- Check if it's a playlist BEFORE attempting single download ---
        if extracted_info and extracted_info.get('_type') == 'playlist':
            playlist_download_id = str(uuid.uuid4())
            playlist_title = extracted_info.get('title', 'Неизвестный плейлист')
            entries = extracted_info.get('entries', [])
            
            if not entries:
                await bot.edit_message_text(
                    f"❌ Плейлист '{playlist_title}' пуст или не удалось получить список треков.",
                    chat_id=status_message.chat.id,
                    message_id=status_message.message_id
                )
                return # Exit function

            processed_tracks_for_playlist = []
            skipped_count = 0
            failed_initial_parse_count = 0

            # --- Filter and prepare track list for the playlist tracker ---
            for index, entry in enumerate(entries):
                if not entry:
                    failed_initial_parse_count += 1
                    continue

                entry_url = entry.get('url')
                entry_title = entry.get('title', None)
                entry_duration = entry.get('duration', 0)

                if not entry_duration or not (MIN_SONG_DURATION <= entry_duration <= MAX_SONG_DURATION):
                    print(f"[Playlist Prep] Skipping '{entry_title}' due to duration: {entry_duration}s")
                    skipped_count += 1
                    continue

                # Artist extraction (simplified for playlist prep)
                entry_artist = "Unknown Artist"
                source_key = entry.get('ie_key', '').lower()
                if source_key == 'soundcloud':
                     raw_title = entry_title if entry_title else 'Unknown Title'
                     sc_uploader = entry.get('uploader', None)
                     if ' - ' in raw_title:
                         parts = raw_title.split(' - ', 1)
                         potential_artist = parts[0].strip()
                         potential_title = parts[1].strip()
                         if potential_artist and potential_title:
                             entry_title = potential_title; entry_artist = potential_artist
                         else: entry_title = raw_title; entry_artist = sc_uploader if sc_uploader else "Unknown Artist"
                     elif sc_uploader: entry_title = raw_title; entry_artist = sc_uploader
                     else: entry_title = raw_title; entry_artist = "Unknown Artist"
                elif source_key == 'bandcamp':
                     entry_artist = entry.get('artist', entry.get('uploader', 'Unknown Artist'))
                     raw_title = entry_title if entry_title else 'Unknown Title'
                     if entry_artist == 'Unknown Artist' and ' - ' in raw_title:
                          parts = raw_title.split(' - ', 1)
                          if parts[0].strip() and parts[1].strip(): entry_title = parts[1].strip(); entry_artist = parts[0].strip()
                          else: entry_title = raw_title
                     else: entry_title = raw_title
                else: # YouTube/Other
                    if entry_title:
                        entry_title, entry_artist = extract_title_and_artist(entry_title)
                        if entry_artist == "Unknown Artist": entry_artist = entry.get('uploader', 'Unknown Artist')
                    else: entry_title = 'Unknown Title'; entry_artist = entry.get('uploader', 'Unknown Artist')

                if not entry_url or entry_title == 'Unknown Title':
                    print(f"[Playlist Prep] Warning: Skipping entry in '{playlist_title}' due to missing URL or Title: {entry}")
                    failed_initial_parse_count += 1
                    continue

                # Add prepared track data to the list for this playlist
                processed_tracks_for_playlist.append({
                    'original_index': index,
                    'url': entry_url,
                    'title': entry_title,
                    'artist': entry_artist, # Use 'artist' key internally
                    'status': 'pending',
                    'error_message': None,
                    'file_path': None,
                    'source': source_key # Store source for potential future use
                })

            # Limit total tracks AFTER initial processing
            total_processed = len(processed_tracks_for_playlist)
            if total_processed == 0:
                 await bot.edit_message_text(
                    f"❌ Не найдено подходящих треков (в рамках ({MIN_SONG_DURATION}-{MAX_SONG_DURATION}с)) в плейлисте '{playlist_title}'.",
                    chat_id=status_message.chat.id,
                    message_id=status_message.message_id
                 )
                 return

            limit_message = ""
            if total_processed > MAX_TRACKS:
                limit_message = f"⚠️ В плейлисте найдено {total_processed} подходящих треков. Будет обработано только {MAX_TRACKS}."
                processed_tracks_for_playlist = processed_tracks_for_playlist[:MAX_TRACKS]
                total_processed = MAX_TRACKS # Update count after slicing

            # --- Create entry in playlist_downloads ---
            playlist_downloads[playlist_download_id] = {
                'user_id': user_id,
                'original_message_id': original_message.message_id, # Store IDs for reference
                'chat_id': original_message.chat.id,
                'status_message_id': status_message.message_id,
                'playlist_title': playlist_title,
                'total_tracks': total_processed, # Actual number to be queued
                'completed_tracks': 0,
                'tracks': processed_tracks_for_playlist, # The filtered, ordered list
                'final_status_message_id': None # To store ID of the final "Отправляю..." message
            }

            # Update status message
            status_text = f"""⏳ Обнаружен плейлист '{playlist_title}'.
Найдено {total_processed} треков для загрузки.
{limit_message}
Добавляю в очередь..."""
            await bot.edit_message_text(
                status_text,
                chat_id=status_message.chat.id,
                message_id=status_message.message_id
            )

            # --- Queue tracks for download ---
            queued_count = 0
            # Ensure user queue exists
            if user_id not in download_queues: download_queues[user_id] = []

            for track_to_queue in processed_tracks_for_playlist:
                # Construct track_data needed by download_track/process_queue
                track_data_for_queue = {
                    "title": track_to_queue['title'],
                    "channel": track_to_queue['artist'], # download_track expects 'channel'
                    "url": track_to_queue['url'],
                    "source": track_to_queue['source']
                }
                # Add to user's queue with playlist_id
                download_queues[user_id].append((track_data_for_queue, playlist_download_id))
                queued_count += 1

            print(f"[Playlist Prep] Queued {queued_count} tracks for playlist {playlist_download_id} ('{playlist_title}') for user {user_id}.")

            # Start processing the queue if possible
            # Need to manage download_tasks initialization potentially here too
            if user_id not in download_tasks: download_tasks[user_id] = {}
            active_downloads = sum(1 for task in download_tasks.get(user_id, {}).values() if not task.done())

            if queued_count > 0 and active_downloads < MAX_PARALLEL_DOWNLOADS:
                 print(f"[Playlist Prep] Triggering queue processing for user {user_id}")
                 # Use create_task to not block
                 asyncio.create_task(process_download_queue(user_id))
            elif queued_count > 0:
                 print(f"[Playlist Prep] Queue for user {user_id} will be processed as existing downloads complete.")

            return # IMPORTANT: Exit after handling playlist queuing

        # --- IF NOT A PLAYLIST ---
        # --- 2. Download (Single Media) ---
        # (The rest of the original single-file download logic follows)
        # ... (ensure status message edit happens only here for single files) ...
        try:
             await bot.edit_message_text(
                 f"⏳ качаю медиа",
                 chat_id=status_message.chat.id,
                 message_id=status_message.message_id
             )
        except Exception as e:
             print(f"[URL Download] Warning: Failed to edit status message for single download (maybe deleted?): {e}")
             # Proceed silently, user got the initial "пытаюсь скачать" message.
             pass

    except yt_dlp.utils.DownloadError as dl_err:
         # Catch specific yt-dlp download errors
         print(f"ERROR yt-dlp DownloadError for {url}: {dl_err}")
         error_text_base = f"❌ ошибка загрузки yt-dlp возможно ссылка битая или сайт изменился"
         # Attempt to extract a more specific error message if available
         # yt-dlp often includes the reason in the error message string
         error_msg_lower = str(dl_err).lower()
         if 'forbidden' in error_msg_lower or 'unavailable' in error_msg_lower:
             error_text_base = f"❌ видео недоступно или доступ запрещен"
         elif 'private' in error_msg_lower:
             error_text_base = f"❌ это приватное видео нужен логин"
         elif 'ip address is blocked' in error_msg_lower: # Check for IP block specifically
             error_text_base = f"❌ сервис заблокировал доступ с ip адреса бота попробуй позже или другую ссылку"
             
         error_text = error_text_base.replace(',', '').replace('.', '') # Apply style
         try:
             await bot.edit_message_text(
                 chat_id=status_message.chat.id,
                 message_id=status_message.message_id,
                 text=error_text,
                 disable_web_page_preview=True
             )
         except Exception as edit_error:
             print(f"Failed to edit message for DownloadError: {edit_error}")
             # Fallback to sending new message
             try:
                  await original_message.answer(error_text, disable_web_page_preview=True)
                  await bot.delete_message(chat_id=status_message.chat.id, message_id=status_message.message_id)
             except Exception as send_error:
                  print(f"[URL Download] Warning: Failed to send new message for DownloadError: {send_error}")

    except Exception as e:
        # Generic exception handler remains the same
        print(f"ERROR during URL download/processing for {url}: {e}\\n{traceback.format_exc()}")
        error_text_base = f"❌ блин ошибка при скачивании/обработке ссылки {str(e).lower()}"
        if "Unsupported URL" in str(e):
             error_text_base = f"❌ извини ссылка не поддерживается или не содержит медиа {url[:60]}"
        elif "Request Entity Too Large" in str(e): # Handle this specific error nicely
             error_text_base = f"❌ скачанный файл слишком большой чтобы отправить его через телеграм (лимит 50 мб)"
        elif "File too large" in str(e): # Handle our custom large file exception
             error_text_base = f"❌ {str(e).lower()}" # Already formatted
        elif "whoops" in str(e).lower() or "unable to download video data" in str(e).lower():
             error_text_base = f"❌ не получилось скачать данные по ссылке возможно она битая или требует логина"
             
        error_text = error_text_base.replace(',', '').replace('.', '') # Apply final cleanup
        if len(error_text) > 4000: 
            error_text = error_text[:3995] + "..." # Adjusted length for ellipsis
        try:
            await bot.edit_message_text(
                chat_id=status_message.chat.id,
                message_id=status_message.message_id,
                text=error_text,
                disable_web_page_preview=True
            )
        except Exception as edit_error:
            print(f"Failed to edit message for error: {edit_error}")
            # Try sending a new message if editing fails
            try:
                 await original_message.answer(error_text, disable_web_page_preview=True)
                 # Try deleting the original status message if possible
                 await bot.delete_message(chat_id=status_message.chat.id, message_id=status_message.message_id)
            except Exception as send_error:
                 print(f"[URL Download] Warning: Failed to send new message for error: {send_error}")

    finally:
        # --- Cleanup ---
        # For single tracks, temp_path should be deleted.
        # For playlists, temp_path should ONLY be deleted if the download FAILED.
        # Successful playlist tracks are deleted later by send_completed_playlist.
        should_delete_temp_file = False
        if temp_path and os.path.exists(temp_path):
            if not is_playlist_track:
                # Always delete for single tracks (success or failure)
                should_delete_temp_file = True
                print(f"[Cleanup] Marking single track temp file for deletion: {temp_path}")
            else:
                # For playlists, only delete if this track failed
                track_failed = False
                if playlist_entry: # Ensure entry exists
                    for track in playlist_entry['tracks']:
                        if track['url'] == url and track['status'] == 'failed':
                            track_failed = True
                            break
                if track_failed:
                     should_delete_temp_file = True
                     print(f"[Cleanup] Marking FAILED playlist track temp file for deletion: {temp_path}")
                else:
                     print(f"[Cleanup] Keeping SUCCESSFUL playlist track temp file for later sending: {temp_path}")

            if should_delete_temp_file:
                try:
                    print(f"Cleaning up temporary file: {temp_path}")
                    os.remove(temp_path)
                except Exception as remove_error:
                    print(f"[URL Download] Warning: Failed to remove temp file {temp_path}: {remove_error}")
            
        # --- Task Management ---
        # Remove task entry regardless of playlist/single or success/failure, as the task itself is done.
        if user_id in download_tasks:
            if download_tasks[user_id].pop(track_data["url"], None):
                 print(f"Removed task entry for URL: {track_data['url']}")
            else:
                 print(f"Task entry for URL {track_data['url']} not found or already removed.")
            if not download_tasks[user_id]:
                print(f"No tasks left for user {user_id}, removing user entry.")
                del download_tasks[user_id]
            else:
                 print(f"{len(download_tasks[user_id])} tasks remaining for user {user_id}.")

        # --- Trigger Queue Processing ---
        # Check queue AGAIN after finishing a task, regardless of type.
        # This ensures the next item is picked up if slots are free.
        if user_id in download_queues and download_queues[user_id]:
            # Check if parallel slots are available before triggering
            active_downloads = sum(1 for task in download_tasks.get(user_id, {}).values() if not task.done())
            if active_downloads < MAX_PARALLEL_DOWNLOADS:
                 print(f"Processing next item in queue for user {user_id} (active: {active_downloads}).")
                 # Use create_task to avoid blocking the finally block
                 asyncio.create_task(process_download_queue(user_id))
            else:
                 print(f"Queue for user {user_id} has items, but max parallel downloads ({active_downloads}) reached.")
        # else: No need for an else print here, common case

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main()) 
