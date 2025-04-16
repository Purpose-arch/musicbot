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
                        'channel': artist, # Use 'channel' key for consistency
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
        track_data, callback_message = download_queues[user_id].pop(0)
        # Slightly informal status message
        status_message = await callback_message.answer(f"⏳ ставлю в очередь на скачивание {track_data['title']} - {track_data['channel']}")
        task = asyncio.create_task(
            download_track(user_id, track_data, callback_message, status_message)
        )
        download_tasks[user_id][track_data["url"]] = task

def _blocking_download_and_convert(url, download_opts):
    """Helper function to run blocking yt-dlp download and return info dict."""
    with yt_dlp.YoutubeDL(download_opts) as ydl:
        # Use extract_info with download=True to get info dict with filepath
        info_dict = ydl.extract_info(url, download=True)
        return info_dict

async def download_track(user_id, track_data, callback_message, status_message):
    temp_path = None
    loop = asyncio.get_running_loop()
    
    try:
        title = track_data["title"]
        artist = track_data["channel"]
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

            # --- Metadata and Sending ---
            print(f"Setting metadata for {temp_path}...")
            if set_mp3_metadata(temp_path, title, artist):
                print(f"Metadata set successfully. Preparing to send {temp_path}.")
                await bot.delete_message(
                    chat_id=callback_message.chat.id,
                    message_id=status_message.message_id
                )
                sending_message = await callback_message.answer("📤 отправляю трек")
                print(f"Sending audio {temp_path}...")
                await bot.send_audio(
                    chat_id=callback_message.chat.id,
                    audio=FSInputFile(temp_path),
                    title=title,
                    performer=artist
                )
                print(f"Audio sent successfully. Deleting sending message.")
                await bot.delete_message(
                    chat_id=callback_message.chat.id,
                    message_id=sending_message.message_id
                )
                print(f"Finished processing track: {title} - {artist}")
            else:
                print(f"ERROR: Failed to set metadata for {temp_path}.")
                raise Exception(f"ошибка при установке метаданных для {title} - {artist}")

        except Exception as e:
            print(f"ERROR during download/processing for {title} - {artist}: {e}")
            # Catch errors from download, file checks, or metadata setting
            error_text = f"❌ блин ошибка {str(e).lower()}"
            if len(error_text) > 4000: 
                error_text = error_text[:3995] + "..."
            try:
                await bot.edit_message_text(
                    chat_id=callback_message.chat.id,
                    message_id=status_message.message_id,
                    text=error_text
                )
            except Exception as edit_error:
                print(f"Failed to edit message for error: {edit_error}")
                try:
                    await callback_message.answer(error_text)
                except Exception as send_error:
                    print(f"[URL Download] Warning: Failed to send new message for error: {send_error}")

    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                print(f"Cleaning up temporary file: {temp_path}")
                os.remove(temp_path)
            except Exception as remove_error:
                print(f"Warning: Failed to remove temp file {temp_path}: {remove_error}")
        else:
            print(f"No temporary file found at {temp_path} to clean up, or path is None.")
        
        # Clean up task tracking and check queue
        if user_id in download_tasks:
            # Use get to avoid KeyError if URL was already removed (e.g., by cancel)
            if download_tasks[user_id].pop(track_data["url"], None):
                 print(f"Removed task entry for URL: {track_data['url']}")
            else:
                 print(f"Task entry for URL {track_data['url']} not found or already removed.")
            # Remove user entry if no tasks left
            if not download_tasks[user_id]:
                print(f"No tasks left for user {user_id}, removing user entry.")
                del download_tasks[user_id]
            else:
                 print(f"{len(download_tasks[user_id])} tasks remaining for user {user_id}.")
            # Check queue regardless of success/failure of current task
            if user_id in download_queues and download_queues[user_id]: 
                print(f"Processing next item in queue for user {user_id}.")
                await process_download_queue(user_id)
            else:
                 print(f"Download queue for user {user_id} is empty or user not found.")

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
    """Downloads media (audio or video) from a direct URL using yt-dlp."""
    loop = asyncio.get_running_loop()
    
    download_uuid = str(uuid.uuid4())
    temp_dir = tempfile.gettempdir()
    base_temp_path = os.path.join(temp_dir, f"media_{download_uuid}")
    actual_downloaded_path = None # Path to the final downloaded file
    
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

        # --- NEW: Check if it's a playlist BEFORE attempting single download ---
        if extracted_info and extracted_info.get('_type') == 'playlist':
            playlist_title = extracted_info.get('title', 'Неизвестный плейлист')
            entries = extracted_info.get('entries', [])
            user_id = original_message.from_user.id # Get user_id here

            if not entries:
                await bot.edit_message_text(
                    f"❌ Плейлист '{playlist_title}' пуст или не удалось получить список треков.",
                    chat_id=status_message.chat.id, 
                    message_id=status_message.message_id
                )
                return # Exit function, cleanup will happen in finally block

            # Limit the number of tracks from a playlist
            max_items_to_queue = MAX_TRACKS # Use existing constant
            original_count = len(entries)
            if original_count > max_items_to_queue:
                # Send a separate message about the limit, don't edit the status message yet
                await original_message.answer(f"⚠️ В плейлисте '{playlist_title}' {original_count} треков. Будет добавлено только {max_items_to_queue} в очередь.")
                entries = entries[:max_items_to_queue]
            
            items_to_process_count = len(entries) # Count after potentially slicing
            
            await bot.edit_message_text(
                f"⏳ Обнаружен плейлист '{playlist_title}'. Добавляю {items_to_process_count} треков в очередь...",
                chat_id=status_message.chat.id,
                message_id=status_message.message_id
            )

            queued_count = 0
            skipped_count = 0
            failed_count = 0

            for entry in entries:
                if not entry: # Skip None entries sometimes returned by yt-dlp
                    failed_count += 1
                    continue

                # Try extracting necessary info for download_track
                entry_url = entry.get('url')
                entry_title = entry.get('title', None) # Explicitly check for None/empty later
                entry_duration = entry.get('duration', 0)

                # Basic filtering similar to search (e.g., duration)
                if not entry_duration or not (MIN_SONG_DURATION <= entry_duration <= MAX_SONG_DURATION):
                     print(f"[Playlist Download] Skipping '{entry_title}' due to duration: {entry_duration}s")
                     skipped_count += 1
                     continue

                # Artist extraction logic similar to search functions
                entry_artist = "Unknown Artist" # Default
                source_key = entry.get('ie_key', '').lower()
                
                if source_key == 'soundcloud':
                     # SC often has 'uploader' as artist, title might be "Artist - Title"
                     raw_title = entry_title if entry_title else 'Unknown Title'
                     sc_uploader = entry.get('uploader', None)
                     if ' - ' in raw_title:
                         parts = raw_title.split(' - ', 1)
                         potential_artist = parts[0].strip()
                         potential_title = parts[1].strip()
                         # Prefer split result if both parts seem valid
                         if potential_artist and potential_title:
                             entry_title = potential_title
                             entry_artist = potential_artist
                         else: # Fallback if split is weird
                             entry_title = raw_title
                             entry_artist = sc_uploader if sc_uploader else "Unknown Artist"
                     elif sc_uploader: # If no ' - ' but uploader exists, use that
                         entry_title = raw_title
                         entry_artist = sc_uploader
                     else: # Worst case
                         entry_title = raw_title
                         entry_artist = "Unknown Artist"
                         
                elif source_key == 'bandcamp':
                     # BC might have 'artist' (album artist) or 'uploader' (label)
                     entry_artist = entry.get('artist', entry.get('uploader', 'Unknown Artist'))
                     raw_title = entry_title if entry_title else 'Unknown Title'
                     # Try splitting title if artist seems missing
                     if entry_artist == 'Unknown Artist' and ' - ' in raw_title:
                          parts = raw_title.split(' - ', 1)
                          potential_artist = parts[0].strip()
                          potential_title = parts[1].strip()
                          if potential_artist and potential_title:
                               entry_title = potential_title
                               entry_artist = potential_artist
                          else:
                               entry_title = raw_title # Keep original if split fails
                     else:
                          entry_title = raw_title # Keep original if artist was found directly

                else: # Assume YouTube or other (use standard extractor)
                    if entry_title: # Only extract if title exists
                        entry_title, entry_artist = extract_title_and_artist(entry_title)
                        if entry_artist == "Unknown Artist": # Fallback to uploader for YT-like
                            entry_artist = entry.get('uploader', 'Unknown Artist')
                    else:
                        entry_title = 'Unknown Title' # Handle case where title is missing
                        entry_artist = entry.get('uploader', 'Unknown Artist')

                # Final validation before queuing
                if not entry_url or entry_title == 'Unknown Title':
                    print(f"[Playlist Download] Warning: Skipping entry in '{playlist_title}' due to missing URL or essential Title: {entry}")
                    failed_count += 1
                    continue

                # Construct track_data for the queue
                track_data = {
                    "title": entry_title,
                    "channel": entry_artist, # Use 'channel' key consistently
                    "url": entry_url,
                    "source": source_key # Store source if available
                }

                # Check if already downloading or queued (use the same logic as callback handlers)
                # Ensure user_id is initialized for download_tasks/queues
                if user_id not in download_tasks: download_tasks[user_id] = {}
                if user_id not in download_queues: download_queues[user_id] = []

                if track_data["url"] in download_tasks[user_id]:
                    print(f"[Playlist Download] Skipping {track_data['url']} - already downloading.")
                    skipped_count += 1
                    continue
                if any(item[0]['url'] == track_data['url'] for item in download_queues[user_id]):
                    print(f"[Playlist Download] Skipping {track_data['url']} - already in queue.")
                    skipped_count += 1
                    continue

                # Add to queue
                # Pass the original_message context for download_track?
                # download_track uses callback_message for context/updates.
                # Passing original_message might be okay, but let's stick to the pattern
                # where the status_message is passed. However, we have only one status_message
                # for the whole playlist. 
                # Let's pass original_message and download_track can use its .answer() method.
                download_queues[user_id].append((track_data, original_message)) 
                queued_count += 1

            # Final message after iterating through entries
            final_message_parts = [f"✅ Плейлист '{playlist_title}':"]
            if queued_count > 0:
                final_message_parts.append(f"добавлено {queued_count} треков в очередь.")
            if skipped_count > 0:
                final_message_parts.append(f"{skipped_count} пропущено (уже в очереди/скачиваются или не прошли фильтр).")
            if failed_count > 0:
                final_message_parts.append(f"{failed_count} не удалось обработать (ошибка данных).")
            
            # Use the status message to show the final summary
            try:
                await bot.edit_message_text(
                    " ".join(final_message_parts),
                    chat_id=status_message.chat.id,
                    message_id=status_message.message_id
                )
            except Exception as edit_final_error:
                 print(f"Error editing final playlist status: {edit_final_error}")
                 # Fallback to sending a new message
                 await original_message.answer(" ".join(final_message_parts))
                 try: # Try to delete the intermediate status message
                     await bot.delete_message(chat_id=status_message.chat.id, message_id=status_message.message_id)
                 except Exception: pass # Ignore delete error

            # Start processing the queue if needed
            if queued_count > 0:
                # Check if processing can start immediately
                active_downloads = sum(1 for task in download_tasks.get(user_id, {}).values() if not task.done())
                if active_downloads < MAX_PARALLEL_DOWNLOADS:
                     print(f"[Playlist Download] Triggering queue processing for user {user_id}")
                     asyncio.create_task(process_download_queue(user_id)) # Don't await this
                else:
                     print(f"[Playlist Download] Queue for user {user_id} will be processed as existing downloads complete.")
            
            return # IMPORTANT: Exit after handling playlist

        # --- 2. Download (Only runs if NOT a playlist) --- 
        # Edit status message only if it wasn't a playlist that already finished
        # (The message would have been edited or deleted by the playlist logic)
        # Check if status_message still exists conceptually
        try:
             # This check isn't perfect but better than editing blindly
             # If the message was deleted, this might fail, hence the try-except
             await bot.edit_message_text( 
                 f"⏳ качаю медиа",
                 chat_id=status_message.chat.id,
                 message_id=status_message.message_id
             )
        except Exception:
             # If editing failed (likely message deleted), create a new status message? Or just proceed?
             # Let's proceed silently, the user got the initial "пытаюсь скачать" message.
             print("[URL Download] Status message likely deleted by playlist handler, proceeding with single download.")
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
        # --- 5. Cleanup --- 
        # First, try cleaning the exact path found (if any)
        if actual_downloaded_path and os.path.exists(actual_downloaded_path):
            try:
                print(f"[URL Download] Cleaning up temporary file: {actual_downloaded_path}")
                os.remove(actual_downloaded_path)
            except Exception as remove_error:
                print(f"[URL Download] Warning: Failed to remove exact temp file {actual_downloaded_path}: {remove_error}")
        
        # Also attempt cleanup based on base_temp_path, just in case 
        # (e.g., if download failed before path was confirmed, or intermediate files left)
        print(f"[URL Download] Attempting additional cleanup for base path: {base_temp_path}")
        # Add image extensions to cleanup as well
        possible_extensions = ['.mp4', '.mkv', '.webm', '.mov', '.avi', '.mp3', '.m4a', '.ogg', '.opus', '.aac', '.wav', '.flac', '.jpg', '.jpeg', '.png', '.gif', '.webp']
        possible_extensions.extend([".mp4.part", ".webm.part", ".mkv.part", ".ytdl", ".part"])
        
        cleaned_a_file = False
        for ext in possible_extensions:
            potential_path = base_temp_path + ext
            if os.path.exists(potential_path):
                try:
                    print(f"[URL Download] Removing found file: {potential_path}")
                    os.remove(potential_path)
                    cleaned_a_file = True
                except Exception as remove_error:
                    print(f"[URL Download] Warning: Failed to remove temp file {potential_path}: {remove_error}")
                    
        if not cleaned_a_file:
            print(f"[URL Download] No temporary files found matching base path {base_temp_path} for cleanup.")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main()) 
