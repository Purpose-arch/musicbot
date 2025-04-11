import os
import asyncio
import tempfile
import json
import base64
import math
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
    prefixes = ['Official Video', 'Official Music Video', 'Official Audio', 'Lyric Video', 'Lyrics']
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
        import traceback
        print(f"An error occurred during SoundCloud search: {e}\n{traceback.format_exc()}")
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
        status_message = await callback_message.answer(f"⏳ ставлю в очередь на скачивание: {track_data['title']} - {track_data['channel']}\n...") 
        task = asyncio.create_task(
            download_track(user_id, track_data, callback_message, status_message)
        )
        download_tasks[user_id][track_data["url"]] = task

def _blocking_download_and_convert(url, download_opts):
    """Helper function to run blocking yt-dlp download/conversion."""
    with yt_dlp.YoutubeDL(download_opts) as ydl:
        # Check info first (optional, but good practice)
        info = ydl.extract_info(url, download=False)
        if not info:
            raise Exception("не удалось получить инфу о видео (в executor)")
        # Perform the download and conversion
        ydl.download([url])

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
            'quiet': False, # Отключаем quiet
            'verbose': True, # Включаем подробный лог для отладки
            'no_warnings': False,
            'prefer_ffmpeg': True,
            'nocheckcertificate': True,
            'ignoreerrors': True, # Оставляем, но будем проверять наличие файла
            'extract_flat': False, # Нужно для скачивания, а не только для поиска
            'ffmpeg_location': '/usr/bin/ffmpeg' # Оставляем явное указание пути
        }
        
        expected_mp3_path = base_temp_path + '.mp3'

        try:
            await bot.edit_message_text(
                f"⏳ качаю трек: {title} - {artist}...",
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
                None,  # Используем стандартный ThreadPoolExecutor
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
                raise Exception(f"файл {expected_mp3_path} не создался после скачивания/конвертации.")
            
            temp_path = expected_mp3_path 
            print(f"Confirmed MP3 file exists at: {temp_path}")
            
            if os.path.getsize(temp_path) == 0:
                print(f"ERROR: Downloaded file {temp_path} is empty.")
                raise Exception("скачанный файл пустой, чет не то")
            
            print(f"File size: {os.path.getsize(temp_path)} bytes")

            # --- NEW: Validate MP3 file structure ---
            try:
                print(f"Validating MP3 structure for {temp_path}...")
                audio_check = MP3(temp_path) 
                if not audio_check.info.length > 0:
                     print(f"ERROR: MP3 file {temp_path} loaded but has zero length/duration.")
                     raise Exception("файл MP3 скачался, но похоже битый (нулевая длина)")
                print(f"MP3 Validation PASSED for {temp_path}, duration: {audio_check.info.length}s")
            except Exception as validation_error:
                print(f"ERROR: MP3 Validation FAILED for {temp_path}: {validation_error}")
                raise Exception(f"скачанный файл не является валидным MP3: {validation_error}")

            # --- Metadata and Sending ---
            print(f"Setting metadata for {temp_path}...")
            if set_mp3_metadata(temp_path, title, artist):
                print(f"Metadata set successfully. Preparing to send {temp_path}.")
                await bot.delete_message(
                    chat_id=callback_message.chat.id,
                    message_id=status_message.message_id
                )
                sending_message = await callback_message.answer("📤 отправляю трек...") 
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
                raise Exception(f"ошибка при установке метаданных для: {title} - {artist}")

        except Exception as e:
            print(f"ERROR during download/processing for {title} - {artist}: {e}")
            # Catch errors from download, file checks, or metadata setting
            error_text = f"❌ блин, ошибка: {str(e)}"
            if len(error_text) > 4000: 
                error_text = error_text[:4000] + "..."
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
                    print(f"Failed to send new message for error: {send_error}")

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
        "👋 приветики! я бот для скачивания музыки\n\n"
        "🔍 просто кидай мне название трека или исполнителя и я попробую найти"
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "🎵 *как тут все работает:*\n\n"
        "1️⃣ кидаешь мне название трека/исполнителя\n"
        "2️⃣ выбираешь нужный из списка\n"
        "3️⃣ жмешь кнопку, чтобы скачать\n\n"
        " *команды, если что:*\n"
        "/start - начать сначала\n"
        "/help - вот это сообщение\n"
        "/search [запрос] - найти музыку по запросу\n"
        "/cancel - отменить загрузки, которые сейчас идут"
    )
    await message.answer(help_text, parse_mode="Markdown")

@dp.message(Command("search"))
async def cmd_search(message: types.Message):
    if len(message.text.split()) < 2:
        await message.answer("❌ напиши что-нибудь после /search, плиз\nнапример: /search coldplay yellow")
        return

    query = " ".join(message.text.split()[1:])
    searching_message = await message.answer("🔍 ищу треки...")

    search_id = str(uuid.uuid4())
    # Search both sources concurrently
    max_results_per_source = MAX_TRACKS // 2
    youtube_results, soundcloud_results = await asyncio.gather(
        search_youtube(query, max_results_per_source),
        search_soundcloud(query, max_results_per_source)
    )

    # Interleave results
    combined_results = []
    len_yt = len(youtube_results)
    len_sc = len(soundcloud_results)
    max_len = max(len_yt, len_sc)
    for i in range(max_len):
        if i < len_yt:
            # Add source identifier if not already present
            if 'source' not in youtube_results[i]:
                 youtube_results[i]['source'] = 'youtube'
            combined_results.append(youtube_results[i])
        if i < len_sc:
            # Ensure source identifier is present
            if 'source' not in soundcloud_results[i]:
                 soundcloud_results[i]['source'] = 'soundcloud'
            combined_results.append(soundcloud_results[i])

    # Limit total results if needed (optional, already limited by source searches)

    if not combined_results:
        await message.answer("❌ чет ничего не нашлось ни там, ни там. попробуй другой запрос?")
        await bot.delete_message(chat_id=searching_message.chat.id, message_id=searching_message.message_id)
        return

    search_results[search_id] = combined_results # Store combined results
    keyboard = create_tracks_keyboard(combined_results, 0, search_id)

    await message.answer(
        f"🎵 нашел вот {len(combined_results)} треков по запросу '{query}':",
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
            
        await message.answer("✅ ок, отменил все активные загрузки и почистил очередь.")
    else:
        await message.answer("❌ так щас ничего и не качается вроде...")

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
                f"⏳ добавил в очередь ({queue_size+1}-й). качаю {active_downloads}/{MAX_PARALLEL_DOWNLOADS}"
            )
        else:
            # Using answer instead of sending a new message for initial status
            status_message = await callback.message.answer(f"⏳ начинаю скачивать: {track_data['title']} - {track_data['channel']}") 
            task = asyncio.create_task(
                download_track(user_id, track_data, callback.message, status_message)
            )
            download_tasks[user_id][track_data["url"]] = task
            await callback.answer("начал скачивание") # Acknowledge callback
            
    except json.JSONDecodeError:
         await callback.message.answer("❌ чет не смог разобрать данные трека. попробуй поискать снова.")
         await callback.answer()
    except Exception as e:
        print(f"Error in process_download_callback: {e}")
        await callback.message.answer(f"❌ ой, ошибка: {str(e)}")
        await callback.answer() # Acknowledge callback even on error

@dp.callback_query(F.data.startswith("dl_"))
async def process_download_callback_with_index(callback: types.CallbackQuery):
    try:
        parts = callback.data.split("_")
        track_index = int(parts[1]) - 1
        search_id = parts[2]
        
        if search_id not in search_results:
            await callback.answer("❌ результаты поиска уже устарели. найди снова, плз.", show_alert=True)
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
                    f"⏳ добавил в очередь ({queue_size+1}-й). качаю {active_downloads}/{MAX_PARALLEL_DOWNLOADS}"
                )
            else:
                status_message = await callback.message.answer(f"⏳ начинаю скачивать: {track_data['title']} - {track_data['channel']}")
                task = asyncio.create_task(
                    download_track(user_id, track_data, callback.message, status_message)
                )
                download_tasks[user_id][track_data["url"]] = task
                await callback.answer("начал скачивание") # Acknowledge callback
        else:
            await callback.answer("❌ не нашел трек по этому индексу.", show_alert=True)
            
    except IndexError:
         await callback.answer("❌ чет не смог разобрать данные для скачивания.", show_alert=True)
    except Exception as e:
        print(f"Error in process_download_callback_with_index: {e}")
        await callback.answer(f"❌ ой, ошибка: {str(e)}", show_alert=True)

@dp.callback_query(F.data.startswith("page_"))
async def process_page_callback(callback: types.CallbackQuery):
    try:
        parts = callback.data.split("_")
        page = int(parts[1])
        search_id = parts[2]
        
        if search_id not in search_results:
            await callback.answer("❌ эти результаты поиска уже старые. поищи заново.", show_alert=True)
            return
        
        tracks = search_results[search_id]
        keyboard = create_tracks_keyboard(tracks, page, search_id)
        
        await callback.message.edit_reply_markup(reply_markup=keyboard)
        await callback.answer() # Simple ack for page turn
    except (IndexError, ValueError):
        await callback.answer("❌ чет не смог понять номер страницы.", show_alert=True)
    except Exception as e:
        print(f"Error in process_page_callback: {e}")
        await callback.answer(f"❌ блин, ошибка при перелистывании: {str(e)}", show_alert=True)
        
@dp.callback_query(F.data == "info")
async def process_info_callback(callback: types.CallbackQuery):
    # Simple ack for the info button (page number)
    await callback.answer()

@dp.message()
async def handle_text(message: types.Message):
    # Ignore commands explicitly
    if message.text.startswith('/'):
        # Maybe add a hint for unknown commands?
        # await message.answer("хм, не знаю такую команду. попробуй /help")
        return

    # Treat as search query
    query = message.text
    searching_message = await message.answer("🔍 ищу треки...")

    search_id = str(uuid.uuid4())
    # Search both sources concurrently
    max_results_per_source = MAX_TRACKS // 2
    youtube_results, soundcloud_results = await asyncio.gather(
        search_youtube(query, max_results_per_source),
        search_soundcloud(query, max_results_per_source)
    )

    # Interleave results
    combined_results = []
    len_yt = len(youtube_results)
    len_sc = len(soundcloud_results)
    max_len = max(len_yt, len_sc)
    for i in range(max_len):
        if i < len_yt:
             if 'source' not in youtube_results[i]:
                 youtube_results[i]['source'] = 'youtube'
             combined_results.append(youtube_results[i])
        if i < len_sc:
             if 'source' not in soundcloud_results[i]:
                 soundcloud_results[i]['source'] = 'soundcloud'
             combined_results.append(soundcloud_results[i])

    # Limit total results if needed
    # combined_results = combined_results[:MAX_TRACKS]

    if not combined_results:
        await message.answer("❌ ничего не нашел по твоему запросу ни там, ни там. попробуй еще раз?")
        await bot.delete_message(chat_id=searching_message.chat.id, message_id=searching_message.message_id)
        return

    search_results[search_id] = combined_results # Store combined results
    keyboard = create_tracks_keyboard(combined_results, 0, search_id)

    await message.answer(
        f"🎵 нашел вот {len(combined_results)} треков по запросу '{query}':",
        reply_markup=keyboard
    )
    await bot.delete_message(chat_id=searching_message.chat.id, message_id=searching_message.message_id)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main()) 