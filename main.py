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
from spotdl import Spotdl

# Загрузка переменных окружения
load_dotenv()

# --- Spotify Credentials ---
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')
# -------------------------

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
            info = None # Initialize info
            try:
                info = ydl.extract_info(f"bcsearch{max_results}:{query}", download=False)
            except Exception as e:
                # Catch the specific "Unsupported url scheme" error for bcsearch
                if "Unsupported url scheme" in str(e) and "bcsearch" in str(e):
                    print(f"[Bandcamp Search] Warning: yt-dlp failed due to unsupported 'bcsearch' scheme. Skipping Bandcamp. Error: {e}")
                    return [] # Return empty list to skip this source
                else:
                    # Re-raise other unexpected errors or handle them generally
                    print(f"[Bandcamp Search] An unexpected error occurred during extract_info: {e}\n{traceback.format_exc()}")
                    return [] # Return empty list on other errors too
            
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

async def search_spotify(query, max_results=50):
    """Searches Spotify using spotdl and returns potential matches with download URLs."""
    if not (SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET):
        print("[Spotify Search] Error: Spotify credentials not configured.")
        return []

    loop = asyncio.get_running_loop()
    results = []
    
    try:
        print(f"[Spotify Search] Initializing Spotdl client for query: '{query}'")
        spotdl_client = Spotdl(
            client_id=SPOTIFY_CLIENT_ID, 
            client_secret=SPOTIFY_CLIENT_SECRET, 
            headless=True
        )

        # Search using the query string (blocking operation)
        # We assume spotdl search handles text query and might return more than max_results.
        # We will rely on asyncio.gather structure and MAX_TRACKS limit later.
        print(f"[Spotify Search] Querying spotdl search API for: '{query}'")
        
        # --- ADDED: Isolate spotdl search call with try/except and logging ---
        songs_list = [] # Default to empty list
        try:
             print(f"[Spotify Search DEBUG] Entering run_in_executor for spotdl_client.search...")
             # --- ADDED: Timeout for spotdl search ---
             songs_list = await asyncio.wait_for(
                 loop.run_in_executor(None, spotdl_client.search, [query]), 
                 timeout=20.0 # Set timeout to 20 seconds
             )
             # ----------------------------------------
             print(f"[Spotify Search DEBUG] Exited run_in_executor for spotdl_client.search.")
        except asyncio.TimeoutError:
             print(f"[Spotify Search WARNING] spotdl_client.search timed out after 20 seconds for query: '{query}'")
             # Return empty list on timeout
             return []
        except Exception as spotdl_search_err:
             print(f"[Spotify Search CRITICAL] Error DURING spotdl_client.search execution: {spotdl_search_err}")
             print(traceback.format_exc()) # Print full traceback for this specific error
             # Return empty list immediately if the search itself failed
             return []
        # ---------------------------------------------------------------------
            
        print(f"[Spotify Search] Found {len(songs_list)} potential matches from spotdl.")
        # --- ADDED DEBUG LOG: Print raw songs_list ---
        # print(f"[Spotify Search DEBUG] Raw songs_list: {songs_list}") 
        # Limit log size if too long
        songs_list_repr = repr(songs_list)
        if len(songs_list_repr) > 1500: # Limit log output size
             print(f"[Spotify Search DEBUG] Raw songs_list (first 1500 chars): {songs_list_repr[:1500]}...")
        else:
             print(f"[Spotify Search DEBUG] Raw songs_list: {songs_list_repr}")
        # -------------------------------------------

        processed_count = 0
        for index, song_obj in enumerate(songs_list):
            # Limit results processed from this source if needed (using max_results as a guide)
            if processed_count >= max_results:
                print(f"[Spotify Search] Reached max_results ({max_results}), stopping processing for this source.")
                break
                
            try:
                # --- ADDED DEBUG LOG: Print song object attributes ---
                s_name = getattr(song_obj, 'name', 'N/A')
                s_artists = getattr(song_obj, 'artists', [])
                s_artist = s_artists[0] if s_artists else 'N/A'
                s_url = getattr(song_obj, 'url', 'N/A')
                s_download_url = getattr(song_obj, 'download_url', None)
                print(f"[Spotify Search DEBUG {index}] Processing: Name='{s_name}', Artist='{s_artist}', URL='{s_url}', DownloadURL='{s_download_url}'")
                # ----------------------------------------------------
                
                title = song_obj.name
                artist = song_obj.artists[0] if song_obj.artists else "Unknown Artist"
                download_url = s_download_url # Use the already fetched attribute
                spotify_url = s_url # Use the already fetched attribute
                duration = getattr(song_obj, 'duration', 0) # Duration in ms from spotdl? Convert to s.
                duration_seconds = duration / 1000 if duration else 0

                if not download_url:
                    print(f"[Spotify Search] Skipping '{title} - {artist}' (URL: {spotify_url}) - No download_url found by spotdl.")
                    continue
                
                # Basic validation
                if not title or not artist:
                     print(f"[Spotify Search] Skipping track with missing title/artist. Data: {song_obj}")
                     continue
                     
                # Add to results
                results.append({
                    'title': title,
                    'channel': artist,
                    'url': download_url, # The URL found by spotdl (e.g., YouTube)
                    'duration': int(duration_seconds), # Store as integer seconds
                    'source': 'spotify'
                })
                processed_count += 1

            except Exception as parse_err:
                print(f"[Spotify Search] Error processing individual spotdl song object: {parse_err}. Data: {song_obj}")
                continue # Skip this song
                
        print(f"[Spotify Search] Processed {processed_count} valid tracks with download URLs.")
        return results

    except Exception as e:
        print(f"[Spotify Search] Error during spotdl search for query '{query}': {e}")
        print(traceback.format_exc())
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
        elif track.get('source') == 'bandcamp': # Added bandcamp indicator
             source_indicator = " [BC]"
        elif track.get('source') == 'spotify': # Added spotify indicator
             source_indicator = " [SP]"
        
        buttons.append([
            InlineKeyboardButton(
                text=f"🎧 {track['title']} - {track['channel']}{duration_str}{source_indicator}", # Appended indicator
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

def _blocking_download_and_convert(url, download_opts):
    """Helper function to run blocking yt-dlp download and return info dict."""
    with yt_dlp.YoutubeDL(download_opts) as ydl:
        # Use extract_info with download=True to get info dict with filepath
        info_dict = ydl.extract_info(url, download=True)
        return info_dict

# Adjust download_track signature and logic
async def download_track(user_id, track_data, callback_message=None, status_message=None, original_message_context=None, playlist_download_id=None):
    """Downloads a single track. If part of a playlist (playlist_download_id is set),
    it updates the central playlist tracker instead of sending the file directly."""
    
    # Compatibility: Handle older calls that might still pass messages
    # If playlist_download_id is provided, message arguments are ignored for status updates.
    # If playlist_download_id is None, we expect callback_message and status_message for single downloads.
    
    temp_path = None # Initialize temp_path to prevent NameError in finally
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
        # Check if original_message_context was provided as a fallback (e.g., direct URL download case?)
        if original_message_context:
             chat_id_for_updates = original_message_context.chat.id
             # We don't have a status_message_id in this specific fallback path
             original_status_message_id = None 
             print(f"Warning: download_track using original_message_context for single track, no status_message.")
        else:
            print(f"ERROR: download_track called for single track (playlist_id=None) but missing message context (callback/status messages and original_message_context)!")
            # Clean up task tracking and exit
            if user_id in download_tasks:
                download_tasks[user_id].pop(track_data["url"], None)
                if not download_tasks[user_id]: del download_tasks[user_id]
            return # Exit task
    # Initialize variables for track info
    try:
        title = track_data["title"]
        artist = track_data["channel"] # Changed key internally, was 'artist' in playlist prep
        url = track_data["url"]
    except KeyError as e:
        print(f"ERROR: Missing required track data field: {e}")
        # Clean up task tracking and exit
        if user_id in download_tasks:
            download_tasks[user_id].pop(track_data["url"], None) 
            if not download_tasks[user_id]: del download_tasks[user_id]
        return # Exit task
        # Создаем БОЛЕЕ безопасное имя файла
        # Заменяем пробелы на _, удаляем все кроме букв/цифр/./_/- 
        safe_title = "".join(c if c.isalnum() or c in ('.', '_', '-') else '_' for c in title).strip('_').strip('.').strip('-')
        # Ограничим длину на всякий случай
        safe_title = safe_title[:100] 
        if not safe_title:
             safe_title = f"audio_{uuid.uuid4()}" # Fallback name

        temp_dir = tempfile.gettempdir() # Должен быть /tmp в контейнере
        
        # --- Generate Unique Base Path ---
        if is_playlist_track:
            base_temp_path = os.path.join(temp_dir, f"pl_{playlist_download_id}_{safe_title}")
        else:
            task_uuid = str(uuid.uuid4()) # Unique ID for this single download task
            base_temp_path = os.path.join(temp_dir, f"single_{task_uuid}_{safe_title}")
        print(f"[Download Path] Base temp path set to: {base_temp_path}")
        # ---------------------------------
        
        # Удаляем существующие файлы с разными расширениями перед скачиванием
        # (This check might be less critical now with unique names, but kept for safety)
        for ext in ['.mp3', '.m4a', '.webm', '.mp4', '.opus', '.ogg', '.aac', '.part']:
            potential_path = f"{base_temp_path}{ext}"
            if os.path.exists(potential_path):
                try:
                    os.remove(potential_path)
                    print(f"Removed existing file: {potential_path}")
                except OSError as e:
                    print(f"Warning: Could not remove existing file {potential_path}: {e}")
        
        # --- Download Options setup ---
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

        # --- Blocking Download call --- 
        # (Removed the pre-download status update call that caused AttributeError)
        print(f"\nStarting download for: {title} - {artist}")
        # print(f"URL: {url}") # Debug
        # print(f"Output template: {download_opts['outtmpl']}") # Debug
        # print(f"Expected MP3 path: {expected_mp3_path}") # Debug
        # print(f"Using download options: {download_opts}") # Debug

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
            
            # --- Update Progress Message ---
            completed = playlist_entry['completed_tracks']
            total = playlist_entry['total_tracks']
            playlist_title_for_status = playlist_entry.get('playlist_title', '')
            if original_status_message_id and chat_id_for_updates and completed < total:
                try:
                    # Avoid updating on the very last track, as send_completed_playlist will handle the final message
                    status_text = f"⏳ Загрузка плейлиста '{playlist_title_for_status}': {completed}/{total}"
                    await bot.edit_message_text(
                        status_text,
                        chat_id=chat_id_for_updates,
                        message_id=original_status_message_id
                    )
                except Exception as prog_upd_err:
                    print(f"[Playlist Progress] Warning: Failed to update progress message {original_status_message_id}: {prog_upd_err}")
                # ------------------------------

            # Check if playlist is complete
            if completed >= total: # Use >= for safety
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
                sending_context = callback_message if callback_message else original_message_context # Fallback to original if needed
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
         # More detailed error logging
         print(f"ERROR during download/processing for {title} - {artist}: {type(e).__name__} - {e}")
         print(traceback.format_exc()) # Print full traceback

         error_text = f"❌ блин ошибка {str(e).lower()}"
         if len(error_text) > 4000: error_text = error_text[:3995] + "..."

         if is_playlist_track:
             # Update playlist tracker with failure
             track_updated = False
             # Ensure playlist_entry exists before iterating
             if playlist_entry:
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

                 # --- Update Progress Message (also after failure) ---
                 completed = playlist_entry['completed_tracks']
                 total = playlist_entry['total_tracks']
                 playlist_title_for_status = playlist_entry.get('playlist_title', '')
                 if original_status_message_id and chat_id_for_updates and completed < total:
                     try:
                         status_text = f"⏳ Загрузка плейлиста '{playlist_title_for_status}': {completed}/{total}"
                         await bot.edit_message_text(
                             status_text,
                             chat_id=chat_id_for_updates,
                             message_id=original_status_message_id
                         )
                     except Exception as prog_upd_err:
                         print(f"[Playlist Progress] Warning: Failed to update progress message {original_status_message_id}: {prog_upd_err}")
                 # -----------------------------------------------

                 # Check if playlist is complete even after failure
                 if completed >= total: # Use >= for safety
                     print(f"Playlist {playlist_download_id} ('{playlist_entry['playlist_title']}') completed (with failures). Triggering send function.")
                     asyncio.create_task(send_completed_playlist(playlist_download_id))
             else:
                 print(f"ERROR: Playlist entry {playlist_download_id} was None during exception handling for track {url}.")
             
             # Do not edit the main status message here for individual track failures

         else: # Single track failure - edit the status message or send a new one
             if original_status_message_id and chat_id_for_updates:
                 try:
                     await bot.edit_message_text(
                         chat_id=chat_id_for_updates,
                         message_id=original_status_message_id,
                         text=error_text
                     )
                 except Exception as edit_error:
                     print(f"Failed to edit status message for error: {edit_error}")
                     # Fallback: Try sending a new message using available context
                     try:
                         error_context = callback_message if callback_message else original_message_context # Use appropriate context
                         if error_context: # Check if context exists
                             await error_context.answer(error_text)
                         else:
                             print("[Single Download] Warning: No message context found to send error reply.")
                     except Exception as send_error: # Added except block for the inner try
                         print(f"[Single Download] Warning: Failed to send new message for error: {send_error}")
             else:
                # If we couldn't edit (e.g., no status_message_id), try sending a new message directly
                print(f"[Single Download] No status message to edit, attempting to send error as new message.")
                try:
                   error_context = callback_message if callback_message else original_message_context
                   if error_context:
                       await error_context.answer(error_text)
                   else:
                       print("[Single Download] Warning: No message context found to send error reply.")
                except Exception as send_error:
                   print(f"[Single Download] Warning: Failed to send new message for error: {send_error}")

    finally:
         # --- Cleanup ---
         # For single tracks, temp_path should be deleted.
         # For playlist tracks, temp_path should ONLY be deleted if the download FAILED.
         # Successful playlist tracks are deleted later by send_completed_playlist.
         should_delete_temp_file = False
         # Check temp_path exists and the file itself exists
         if temp_path and os.path.exists(temp_path):
             if not is_playlist_track:
                 # Always delete for single tracks (success or failure)
                 should_delete_temp_file = True
                 print(f"[Cleanup] Marking single track temp file for deletion: {temp_path}")
             else:
                 # For playlists, only delete if this track failed
                 track_failed = False
                 if playlist_entry: # Check playlist_entry exists
                     for track in playlist_entry['tracks']:
                         # Check status only if URL matches (should be unique within playlist)
                         if track['url'] == url:
                             if track['status'] == 'failed':
                                 track_failed = True
                             break # Found the track, no need to check further
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
    max_results_per_source = MAX_TRACKS // 4 # Divide budget among 4 sources now
    spotify_results, youtube_results, soundcloud_results, bandcamp_results = await asyncio.gather(
        search_spotify(query, max_results_per_source), # Added Spotify search
        search_youtube(query, max_results_per_source),
        search_soundcloud(query, max_results_per_source),
        search_bandcamp(query, max_results_per_source)
    )

    # Prioritize Spotify -> SoundCloud -> Bandcamp -> YouTube results
    combined_results = []
    # Add Spotify results first
    for sp_track in spotify_results:
         if 'source' not in sp_track: # Should have source already, but defensive check
             sp_track['source'] = 'spotify'
         combined_results.append(sp_track)
    # Add SoundCloud results
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

    # Limit total results if needed (redundant if MAX_TRACKS was respected by sources?)
    # combined_results = combined_results[:MAX_TRACKS]

    if not combined_results:
        await message.answer("❌ чет ничего не нашлось ни на одном из источников попробуй другой запрос") # Updated message
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
    cancelled_tasks_count = 0
    cancelled_playlists_count = 0
    cleaned_files_count = 0
    active_tasks_urls = []

    # --- Cancel active download tasks --- 
    if user_id in download_tasks:
        # Get URLs of tasks to be cancelled for this user
        tasks_to_cancel = {
            url: task for url, task in download_tasks[user_id].items() 
            if task and not task.done() and not task.cancelled()
        }
        active_tasks_urls = list(tasks_to_cancel.keys())
        
        if tasks_to_cancel:
            print(f"[Cancel] Cancelling {len(tasks_to_cancel)} active tasks for user {user_id}.")
            for url, task in tasks_to_cancel.items():
                task.cancel()
                cancelled_tasks_count += 1
            # Give tasks a moment to cancel (important for file cleanup later)
            await asyncio.sleep(0.2)
            # Clean up cancelled/finished tasks from the user's entry
            download_tasks[user_id] = { 
                url: task for url, task in download_tasks[user_id].items() 
                if task and not task.cancelled() and not task.done() 
            }
        if not download_tasks[user_id]:
            del download_tasks[user_id]
        else:
            print(f"[Cancel] No active download tasks found for user {user_id} in download_tasks.")
            # If no active tasks, but entry exists, clear it
            if not download_tasks.get(user_id):
                del download_tasks[user_id]
        
    # --- Clear user's download queue --- 
    queued_items_count = 0 # Initialize count
    if user_id in download_queues:
        queued_items_count = len(download_queues[user_id])
    if queued_items_count > 0:
        print(f"[Cancel] Clearing {queued_items_count} items from queue for user {user_id}.")
        download_queues[user_id].clear()
        # Remove user entry if queue becomes empty (or was already empty)
        # Check if key exists before deleting
        if user_id in download_queues: # This check might be redundant after clear, but safe
            del download_queues[user_id]

    # --- Cancel and cleanup active playlist downloads --- 
    playlists_to_remove = []
    files_to_delete_from_playlists = []
    for pl_id, pl_entry in list(playlist_downloads.items()): # Iterate over a copy of items
        if pl_entry.get('user_id') == user_id:
            print(f"[Cancel] Found active playlist {pl_id} ('{pl_entry.get('playlist_title')}') for user {user_id}.")
            cancelled_playlists_count += 1
            playlists_to_remove.append(pl_id)
            
            # Mark associated active tasks (if any remaining after first step) as cancelled conceptually
            # And collect file paths for cleanup
            for track in pl_entry.get('tracks', []):
                # Check if this track's task was among those actively cancelled
                # Or if it was already completed but file exists
                task_is_active = track['url'] in active_tasks_urls
                file_exists = track.get('file_path') and os.path.exists(track['file_path'])
                
                if task_is_active or file_exists:
                    if file_exists:
                        files_to_delete_from_playlists.append(track['file_path'])
                        print(f"[Cancel] Marked playlist file for deletion: {track['file_path']}")
            
            # Try to delete the status message associated with this playlist
            status_msg_id = pl_entry.get('status_message_id')
            chat_id = pl_entry.get('chat_id')
            if status_msg_id and chat_id:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=status_msg_id)
                    print(f"[Cancel] Deleted status message {status_msg_id} for cancelled playlist {pl_id}.")
                except Exception as del_err:
                    print(f"[Cancel] Warning: Failed to delete status message {status_msg_id} for playlist {pl_id}: {del_err}")

    # Remove cancelled playlist entries from the main dictionary
    for pl_id in playlists_to_remove:
        if pl_id in playlist_downloads:
            del playlist_downloads[pl_id]
            print(f"[Cancel] Removed playlist entry {pl_id} from tracker.")

    # --- Perform File Cleanup --- 
    # This relies on tasks being given time to cancel above
    # Collect files associated with tasks that were *just* cancelled
    # Note: This might include files from single downloads too
    files_to_delete_from_tasks = []
    # Re-check download_tasks for potentially completed files of cancelled tasks (race condition?)
    # Let's assume the playlist check above is sufficient for playlist files.
    # Need a way to reliably find temp files for cancelled single-downloads.
    # This is hard because download_track cleans up its own failed files.
    # Let's focus cleanup on files explicitly collected from cancelled playlists.

    # Cleanup files collected from cancelled playlists
    for f_path in set(files_to_delete_from_playlists): # Use set for unique paths
        try:
            if os.path.exists(f_path):
                os.remove(f_path)
                cleaned_files_count += 1
                print(f"[Cancel Cleanup] Deleted: {f_path}")
        except Exception as rem_err:
            print(f"[Cancel Cleanup] Warning: Failed to remove {f_path}: {rem_err}")

    # --- Send Confirmation Message --- 
    if cancelled_tasks_count > 0 or cancelled_playlists_count > 0 or queued_items_count > 0:
        response_parts = ["✅ ок"]
        if cancelled_tasks_count > 0:
            response_parts.append(f"отменил {cancelled_tasks_count} активных загрузок")
        if cancelled_playlists_count > 0:
             response_parts.append(f"остановил {cancelled_playlists_count} плейлистов")
        if queued_items_count > 0:
            response_parts.append(f"очистил очередь ({queued_items_count} треков)")
        if cleaned_files_count > 0:
             response_parts.append(f"удалил {cleaned_files_count} временных файлов")
             
        final_response = " ".join(response_parts)
        # Ensure comma separation if multiple parts exist besides "✅ ок"
        if len(response_parts) > 2:
             final_response = response_parts[0] + " " + ", ".join(response_parts[1:])
             
        await message.answer(final_response)
    else:
        await message.answer("❌ так щас ничего и не качается вроде (очередь пуста плейлистов нет)")

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
            # NOTE: Queueing from search results is not fully integrated with the playlist system
            # For now, let's prevent queueing directly from search results to avoid complexity.
            # We could potentially create a 'single item playlist' entry in playlist_downloads,
            # but that adds overhead. Let's just download immediately if possible.
            await callback.answer(
                f"❌ слишком много загрузок ({active_downloads}/{MAX_PARALLEL_DOWNLOADS}) попробуй позже", 
                show_alert=True
            )
            # download_queues[user_id].append((track_data, callback.message)) # Disabled queueing from search for now
            # await callback.answer(
            #     f"⏳ добавил в очередь ({queue_size+1}-й) качаю {active_downloads}/{MAX_PARALLEL_DOWNLOADS}"
            # )
        else:
            # Using answer instead of sending a new message for initial status
            status_message = await callback.message.answer(f"⏳ начинаю скачивать {track_data['title']} - {track_data['channel']}")
            if user_id not in download_tasks: download_tasks[user_id] = {} # Ensure user entry exists
            task = asyncio.create_task(
                # Pass callback.message as original_message_context
                download_track(user_id, track_data, callback.message, status_message, original_message_context=callback.message)
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
                # Disable queueing from search for consistency
                await callback.answer(
                    f"❌ слишком много загрузок ({active_downloads}/{MAX_PARALLEL_DOWNLOADS}) попробуй позже", 
                    show_alert=True
                )
                # download_queues[user_id].append((track_data, callback.message))
                # await callback.answer(
                #     f"⏳ добавил в очередь ({queue_size+1}-й) качаю {active_downloads}/{MAX_PARALLEL_DOWNLOADS}"
                # )
            else:
                status_message = await callback.message.answer(f"⏳ начинаю скачивать {track_data['title']} - {track_data['channel']}")
                if user_id not in download_tasks: download_tasks[user_id] = {} # Ensure user entry exists
                task = asyncio.create_task(
                    # Pass callback.message as original_message_context
                    download_track(user_id, track_data, callback.message, status_message, original_message_context=callback.message)
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
    
    text_content = message.text.strip()
    text_lower = text_content.lower()
    chat_type = message.chat.type

    # --- Group Chat Logic --- 
    if chat_type in ('group', 'supergroup'):
        # Check for 'музыка ' command first
        if text_lower.startswith("музыка "):
            query = text_content[len("музыка "):].strip()
            if query:
                 await handle_group_search(message, query)
            else:
                 await message.reply("❌ после 'музыка' нужно написать запрос для поиска.")
            return
        
        # Then check for any message containing a URL
        # Use regex to find the first http/https url
        url_match = re.search(r'https?://[\S]+', text_content) # Corrected regex to avoid \s
        if url_match:
            url = url_match.group(0)
            print(f"[Group URL Detect] Found URL: {url} in message: '{text_content}'")
            # --- Spotify URL Check ---
            if "open.spotify.com" in url:
                 if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
                      await handle_spotify_url(message, url) # Call the handler
                 else:
                      await message.reply("❌ поддержка ссылок spotify не настроена (отсутствуют client id/secret)")
                 return # Exit after handling or notifying about Spotify link
            # --- Fallback to generic URL download ---
            else:
                 await handle_url_download(message, url)
            return # Exit after handling generic URL
        
        # Ignore other messages in groups
        return 
            
    # --- Private Chat Logic --- 
    elif chat_type == 'private':
        # Check if it's a Spotify URL first
        if "open.spotify.com" in text_content and text_content.startswith(('http://', 'https://')):
             if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
                  await handle_spotify_url(message, text_content) # Call the handler
             else:
                  await message.answer("❌ поддержка ссылок spotify не настроена (отсутствуют client id/secret)")
             return # Exit after handling or notifying about Spotify link
             
        # Check if it's any other URL 
        elif text_content.startswith(('http://', 'https://')):
            await handle_url_download(message, text_content) # Pass URL directly
            return # Exit after handling generic URL
        
        else:
            # Treat as search query
            query = text_content
            searching_message = await message.answer("🔍 ищу музыку...")
            search_id = str(uuid.uuid4())
            # Search all sources concurrently
            try:
                max_results_per_source = MAX_TRACKS // 4 # Divide budget among 4 sources now
                spotify_results, youtube_results, soundcloud_results, bandcamp_results = await asyncio.gather(
                    search_spotify(query, max_results_per_source), # Added Spotify search
                    search_youtube(query, max_results_per_source),
                    search_soundcloud(query, max_results_per_source),
                    search_bandcamp(query, max_results_per_source)
                )

                # Prioritize Spotify -> SoundCloud -> Bandcamp -> YouTube results
                combined_results = []
                # Add Spotify results first
                for sp_track in spotify_results:
                     if 'source' not in sp_track: # Should have source already, but defensive check
                         sp_track['source'] = 'spotify'
                     combined_results.append(sp_track)
                # Add SoundCloud results
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

                # Limit total results if needed (redundant if MAX_TRACKS was respected by sources?)
                # combined_results = combined_results[:MAX_TRACKS]

                if not combined_results:
                    await message.answer("❌ чет ничего не нашлось ни на одном из источников попробуй другой запрос") # Updated message
                    await bot.delete_message(chat_id=searching_message.chat.id, message_id=searching_message.message_id)
                    return
                
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
                 # Add traceback print
                 print(traceback.format_exc())
                 await bot.edit_message_text(
                     chat_id=searching_message.chat.id, 
                     message_id=searching_message.message_id,
                     text=f"❌ блин ошибка при поиске: {e}"
                 )
            # Return should be outside the except block but inside the else block
            return # End of private search logic

# --- Spotify URL Handler ---
async def handle_spotify_url(message: types.Message, url: str):
    print(f"[Spotify Handler] Detected Spotify URL: {url}")
    status_message = await message.answer("⏳ подключаюсь к spotify и ищу треки...")
    user_id = message.from_user.id
    loop = asyncio.get_running_loop()

    try:
        # Initialize Spotdl
        # Note: Ensure headless=True if running without a display
        #       Ensure ffmpeg is available (using path from ydl_opts for consistency?)
        spotdl_client = Spotdl(
            client_id=SPOTIFY_CLIENT_ID, 
            client_secret=SPOTIFY_CLIENT_SECRET, 
            headless=True, 
            # downloader_settings={'ffmpeg': ydl_opts.get('ffmpeg_location', 'ffmpeg')} # Pass ffmpeg path if needed
        )
        
        # Fetch song info using spotdl's search (blocking operation)
        # Assuming spotdl_client.search returns a list of Song objects or similar dicts
        print(f"[Spotify Handler] Querying spotdl for: {url}")
        # The search method might block, run in executor
        songs_list = await loop.run_in_executor(None, spotdl_client.search, [url])
        print(f"[Spotify Handler] Found {len(songs_list)} tracks from spotdl.")

        if not songs_list:
            await bot.edit_message_text(
                "❌ не удалось найти треки по этой ссылке spotify с помощью spotdl",
                chat_id=status_message.chat.id,
                message_id=status_message.message_id
            )
            return

        # Determine if it's a single track or playlist/album/etc.
        is_single_track = len(songs_list) == 1
        # Extract a general title (e.g., playlist name if available, fallback)
        # Spotdl's Song object might have album/playlist attributes, need to check its structure
        # For now, use a generic title
        source_title = songs_list[0].album_name if hasattr(songs_list[0], 'album_name') and not is_single_track else \
                       songs_list[0].name if is_single_track else \
                       "Spotify Selection"

        # Prepare tracks for our internal queue system
        processed_tracks_for_playlist = []
        skipped_count = 0
        
        for index, song_obj in enumerate(songs_list):
            # Extract required info based on assumed spotdl Song object structure
            try:
                entry_title = song_obj.name
                entry_artist = song_obj.artists[0] if song_obj.artists else "Unknown Artist"
                # IMPORTANT ASSUMPTION: song_obj.download_url contains the URL found by spotdl (e.g., YouTube)
                entry_url = getattr(song_obj, 'download_url', None) 
                
                # Basic validation (URL is crucial)
                if not entry_url or not entry_title:
                    print(f"[Spotify Handler] Skipping track '{entry_title}' due to missing URL or Title. Data: {song_obj}")
                    skipped_count += 1
                    continue
                    
                processed_tracks_for_playlist.append({
                    'original_index': index, # Keep original order if needed
                    'url': entry_url, # This is the YouTube/etc. URL
                    'title': entry_title,
                    'artist': entry_artist, 
                    'status': 'pending',
                    'error_message': None,
                    'file_path': None,
                    'source': 'spotify' # Indicate origin
                })
            except Exception as parse_err:
                print(f"[Spotify Handler] Error processing spotdl song object: {parse_err}. Data: {song_obj}")
                skipped_count += 1
                continue

        total_processed = len(processed_tracks_for_playlist)
        if total_processed == 0:
             await bot.edit_message_text(
                f"❌ Не найдено подходящих треков для обработки из '{source_title}' (пропущено {skipped_count}).",
                chat_id=status_message.chat.id,
                message_id=status_message.message_id
             )
             return

        # Limit total tracks if necessary (using existing constant)
        limit_message = ""
        if total_processed > MAX_TRACKS:
            limit_message = f"⚠️ Найдено {total_processed} треков. Будет обработано только {MAX_TRACKS}."
            processed_tracks_for_playlist = processed_tracks_for_playlist[:MAX_TRACKS]
            total_processed = MAX_TRACKS

        # --- If single track, download directly --- 
        if is_single_track and total_processed == 1:
            track_to_download = processed_tracks_for_playlist[0]
            track_data_for_dl = {
                 "title": track_to_download['title'],
                 "channel": track_to_download['artist'],
                 "url": track_to_download['url'],
                 "source": track_to_download['source']
            }
            # Edit status before starting download
            await bot.edit_message_text(
                 f"⏳ Найден трек '{track_data_for_dl['title']} - {track_data_for_dl['channel']}'. Начинаю скачивание...",
                 chat_id=status_message.chat.id,
                 message_id=status_message.message_id
            )
            if user_id not in download_tasks: download_tasks[user_id] = {}
            # Pass necessary context to download_track
            task = asyncio.create_task(
                download_track(
                    user_id, 
                    track_data_for_dl, 
                    callback_message=None, # No callback message here
                    status_message=status_message, # Pass the status message
                    original_message_context=message, # Pass the original message 
                    playlist_download_id=None # Not part of a playlist download batch
                )
            )
            download_tasks[user_id][track_data_for_dl["url"]] = task
            # No callback.answer() here
            return # Finished handling single track

        # --- If multiple tracks (Playlist/Album/Artist) --- 
        else:
            playlist_download_id = str(uuid.uuid4())
            # --- Create entry in playlist_downloads ---
            playlist_downloads[playlist_download_id] = {
                'user_id': user_id,
                'original_message_id': message.message_id,
                'chat_id': message.chat.id,
                'status_message_id': status_message.message_id,
                'playlist_title': source_title, # Use extracted title
                'total_tracks': total_processed, 
                'completed_tracks': 0,
                'tracks': processed_tracks_for_playlist, 
                'final_status_message_id': None
            }

            # Update status message
            status_text = f"""⏳ Обнаружено {total_processed} треков в '{source_title}'.
{limit_message}
Добавляю в очередь..."""
            await bot.edit_message_text(
                status_text,
                chat_id=status_message.chat.id,
                message_id=status_message.message_id
            )

            # --- Queue tracks for download ---
            queued_count = 0
            if user_id not in download_queues: download_queues[user_id] = []

            for track_to_queue in processed_tracks_for_playlist:
                track_data_for_queue = {
                    "title": track_to_queue['title'],
                    "channel": track_to_queue['artist'],
                    "url": track_to_queue['url'], # The YouTube/etc. URL
                    "source": track_to_queue['source']
                }
                download_queues[user_id].append((track_data_for_queue, playlist_download_id))
                queued_count += 1

            print(f"[Spotify Handler] Queued {queued_count} tracks for playlist {playlist_download_id} ('{source_title}') for user {user_id}.")

            # Start processing the queue if possible
            if user_id not in download_tasks: download_tasks[user_id] = {}
            active_downloads = sum(1 for task in download_tasks.get(user_id, {}).values() if not task.done())

            if queued_count > 0 and active_downloads < MAX_PARALLEL_DOWNLOADS:
                 print(f"[Spotify Handler] Triggering queue processing for user {user_id}")
                 asyncio.create_task(process_download_queue(user_id))
            elif queued_count > 0:
                 print(f"[Spotify Handler] Queue for user {user_id} will be processed as existing downloads complete.")
                 
            return # Finished handling playlist/album

    except Exception as e:
        print(f"[Spotify Handler] Error handling Spotify URL {url}: {e}")
        print(traceback.format_exc())
        try:
            await bot.edit_message_text(
                f"❌ Ошибка при обработке ссылки spotify: {e}",
                chat_id=status_message.chat.id,
                message_id=status_message.message_id
            )
        except Exception as final_err:
            print(f"[Spotify Handler] Failed to edit final error message: {final_err}")
            await message.answer(f"❌ Ошибка при обработке ссылки spotify: {e}")


# --- Generic URL Handler (yt-dlp based) ---
async def handle_url_download(message: types.Message, url: str):
    """Handles messages identified as URLs (or via 'медиакот') to initiate download."""
    # Use reply for group trigger, answer for direct URL in private
    reply_method = message.reply if message.chat.type != 'private' else message.answer
    status_message = await reply_method(f"⏳ пытаюсь скачать медиа по ссылке {url[:50]}...", disable_web_page_preview=True)
    
    # Pass the original message for context if needed later, and the status message to update
    await download_media_from_url(url, message, status_message)

async def handle_group_search(message: types.Message, query: str):
    """Handles 'музыкакот' command in groups."""
    status_message = await message.reply("🔍 ищу музыку...")
    search_id = str(uuid.uuid4())
    
    try:
        max_results_per_source = MAX_TRACKS // 4 # Divide budget among 4 sources now
        spotify_results, youtube_results, soundcloud_results, bandcamp_results = await asyncio.gather(
            search_spotify(query, max_results_per_source), # Added Spotify search
            search_youtube(query, max_results_per_source),
            search_soundcloud(query, max_results_per_source),
            search_bandcamp(query, max_results_per_source)
        )

        # Prioritize Spotify -> SoundCloud -> Bandcamp -> YouTube results
        combined_results = []
        # Add Spotify results first
        for sp_track in spotify_results:
             if 'source' not in sp_track:
                 sp_track['source'] = 'spotify'
             combined_results.append(sp_track)
        # Add SoundCloud results
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
        # combined_results = combined_results[:MAX_TRACKS]

        if not combined_results:
            await bot.edit_message_text(
                chat_id=status_message.chat.id,
                message_id=status_message.message_id,
                text="❌ ничего не нашел по твоему запросу ни на одном из источников попробуй еще раз" # Updated message
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
    actual_downloaded_path = None # Initialize here to prevent NameError
    temp_path = None # Also keep this initialized as it might be used in error messages

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

                # --- Get URL for the track (YouTube playlist specific logic) ---
                entry_url = entry.get('webpage_url')
                if not entry_url:
                    video_id = entry.get('id')
                    if video_id:
                        entry_url = f"https://www.youtube.com/watch?v={video_id}"
                        print(f"[Playlist Prep] Using constructed URL: {entry_url}")
                    else:
                        # If no webpage_url and no id, we cannot proceed
                        print(f"[Playlist Prep DEBUG] Skipping entry. Reason: Missing webpage_url and id.")
                        print(f"[Playlist Prep DEBUG] Entry data: {entry}")
                        failed_initial_parse_count += 1
                        continue
                # -------------------------------------------------------------

                entry_title = entry.get('title', None)
                # entry_duration = entry.get('duration', 0) # Keep duration if needed later, but don't filter here

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
                    original_raw_title = entry_title # Store original title before attempting extraction
                    if entry_title: # Only extract if title exists
                        extracted_title, extracted_artist = extract_title_and_artist(entry_title)
                        # Use extracted results only if title is not 'Unknown Title'
                        if extracted_title != 'Unknown Title':
                             entry_title = extracted_title
                             entry_artist = extracted_artist
                             # Fallback to uploader if extracted artist is unknown
                             if entry_artist == "Unknown Artist": 
                                 entry_artist = entry.get('uploader', 'Unknown Artist')
                        else:
                            # Extraction failed, use original title and uploader as artist
                            print(f"[Playlist Prep] Extraction failed for '{original_raw_title}', using raw title.")
                            entry_title = original_raw_title 
                            entry_artist = entry.get('uploader', 'Unknown Artist') 
                    else: 
                        entry_title = 'Unknown Title' # Handle case where original title was missing
                        entry_artist = entry.get('uploader', 'Unknown Artist')

                # Final validation before queuing
                if not entry_url or not entry_title or entry_title == 'Unknown Title': # Check for None/empty title too
                    # Removed detailed debug logging here as URL issue is handled above
                    print(f"[Playlist Prep] Warning: Skipping entry in '{playlist_title}' due to missing URL or essential Title after processing: {entry}")
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
                    f"❌ Не найдено подходящих треков для обработки в плейлисте '{playlist_title}'.", # Removed duration mention
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
        # Cleanup for SINGLE file downloads only. Playlist files are handled by send_completed_playlist/cancel.
        if actual_downloaded_path and os.path.exists(actual_downloaded_path):
            # Check if it was a playlist (unlikely to reach here, but safeguard)
            # The check `if extracted_info and extracted_info.get('_type') == 'playlist':` should prevent this block from running for playlists.
            # So, if we are here, it should be a single download. 
            print(f"[URL Cleanup] Attempting to remove single download file: {actual_downloaded_path}")
            try:
                os.remove(actual_downloaded_path)
                print(f"[URL Cleanup] Successfully removed: {actual_downloaded_path}")
            except Exception as remove_error:
                print(f"[URL Cleanup] Warning: Failed to remove single download file {actual_downloaded_path}: {remove_error}")
        # Removed Task Management and Queue Processing logic from here - it belongs in download_track's finally block.

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main()) 
