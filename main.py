import os
import asyncio
import tempfile
import json
import base64
import math
import sqlite3
import datetime
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
MAX_TRACKS = 150
MAX_RETRIES = 3
MIN_SONG_DURATION = 45  # Минимальная длительность трека в секундах
MAX_SONG_DURATION = 720 # Максимальная длительность трека в секундах (12 минут)
ADMIN_ID = 5345341969 # Твой ID для доступа к админке
LOGS_PER_PAGE = 15    # Количество записей лога на странице админки
DB_NAME = 'bot_log.db'  # Имя файла базы данных

# Хранилища
download_tasks = defaultdict(dict)
search_results = {}
download_queues = defaultdict(list)  # Очереди загрузок для каждого пользователя
MAX_PARALLEL_DOWNLOADS = 3  # Максимальное количество одновременных загрузок

# Настройки yt-dlp
ydl_opts = {
    'format': 'bestaudio/best',
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
    'audioformat': 'mp3',  # Явно указываем формат аудио
    'audioquality': '0',  # Лучшее качество
    'extractaudio': True,  # Извлекаем только аудио
    'keepvideo': False,  # Не сохраняем видео
    'outtmpl': '%(title)s.%(ext)s',  # Шаблон имени файла
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
            "search_id": search_id
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
        
        # Создаем безопасное имя файла
        safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).strip()
        temp_dir = tempfile.gettempdir()
        base_temp_path = os.path.join(temp_dir, safe_title)
        
        # Удаляем существующие файлы с разными расширениями
        for ext in ['.mp3', '.m4a', '.webm', '.mp4']:
            temp_path = f"{base_temp_path}{ext}"
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except:
                    pass
        
        download_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'outtmpl': base_temp_path + '.mp3',
            'quiet': True,
            'no_warnings': True,
            'prefer_ffmpeg': True,
            'nocheckcertificate': True,
            'ignoreerrors': True,
            'extract_flat': False,
        }
        
        try:
            # Update status message to indicate actual download start
            await bot.edit_message_text(
                f"⏳ качаю трек: {title} - {artist}...",
                chat_id=callback_message.chat.id,
                message_id=status_message.message_id
            )
            
            # Run the blocking download/conversion in a separate thread
            await loop.run_in_executor(
                None,  # Use default ThreadPoolExecutor
                _blocking_download_and_convert,
                url,
                download_opts
            )
            
            # --- File checks after download ---
            expected_mp3_path = base_temp_path + '.mp3'
            if not os.path.exists(expected_mp3_path):
                # Check for other possible extensions only as a fallback for debugging/errors
                other_extensions = ['.m4a', '.webm', '.opus', '.ogg', '.aac'] 
                found_alternative = False
                for ext in other_extensions:
                    potential_path = f"{base_temp_path}{ext}"
                    if os.path.exists(potential_path):
                        print(f"Warning: MP3 post-processing might have failed. Found {potential_path} instead of {expected_mp3_path}")
                        found_alternative = True
                        break 
                raise Exception(f"файл {expected_mp3_path} не создался после скачивания/конвертации.")
            
            temp_path = expected_mp3_path # Use the expected mp3 path
            
            if os.path.getsize(temp_path) == 0:
                raise Exception("скачанный файл пустой, чет не то")
            
            # --- Metadata and Sending ---
            # This part happens only if download and file checks passed
            if set_mp3_metadata(temp_path, title, artist):
                await bot.delete_message(
                    chat_id=callback_message.chat.id,
                    message_id=status_message.message_id
                )
                sending_message = await callback_message.answer("📤 отправляю трек...") 
                await bot.send_audio(
                    chat_id=callback_message.chat.id,
                    audio=FSInputFile(temp_path),
                    title=title,
                    performer=artist
                )
                await bot.delete_message(
                    chat_id=callback_message.chat.id,
                    message_id=sending_message.message_id
                )
            else:
                # Error specific to metadata setting
                raise Exception(f"ошибка при установке метаданных для: {title} - {artist}")

        except Exception as e:
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
            # Re-raise the exception to potentially stop further processing if needed elsewhere
            # (though in this structure, it mainly signals the end of this specific track task)
            # raise e # Decide if re-raising is needed; for now, just informing the user is enough.

    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception as remove_error:
                print(f"Warning: Failed to remove temp file {temp_path}: {remove_error}")
        
        # Clean up task tracking and check queue
        if user_id in download_tasks:
            # Use get to avoid KeyError if URL was already removed (e.g., by cancel)
            if download_tasks[user_id].pop(track_data["url"], None):
                 pass # Successfully removed task entry
            # Remove user entry if no tasks left
            if not download_tasks[user_id]:
                del download_tasks[user_id]
            # Check queue regardless of success/failure of current task
            if download_queues[user_id]: 
                await process_download_queue(user_id)

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

# --- Database Functions ---

def init_db():
    """инициализирует бд и создает таблицу, если её нет."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS query_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            query TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
        ''')
        conn.commit()
        conn.close()
        print("база данных инициализирована.")
    except sqlite3.Error as e:
        print(f"ошибка при инициализации бд: {e}")

def log_query(user_id, username, query):
    """логирует запрос пользователя в бд."""
    timestamp = datetime.datetime.now().isoformat(sep=' ', timespec='seconds')
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO query_log (user_id, username, query, timestamp) VALUES (?, ?, ?, ?)",
            (user_id, username, query, timestamp)
        )
        conn.commit()
        conn.close()
    except sqlite3.Error as e:
        print(f"ошибка при логировании запроса: {e}")

def get_logs(page=0, limit=LOGS_PER_PAGE):
    """получает записи лога из бд с пагинацией."""
    offset = page * limit
    logs = []
    total_count = 0
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # Сначала получаем общее количество записей
        cursor.execute("SELECT COUNT(*) FROM query_log")
        total_count = cursor.fetchone()[0]
        
        # Затем получаем нужную страницу
        cursor.execute(
            "SELECT timestamp, user_id, username, query FROM query_log ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (limit, offset)
        )
        logs = cursor.fetchall() # fetchall вернет список кортежей
        conn.close()
    except sqlite3.Error as e:
        print(f"ошибка при получении логов: {e}")
    return logs, total_count

# --- Admin Panel Functions ---

def create_admin_log_keyboard(logs, page=0, total_count=0):
    """создает клавиатуру для пагинации логов админки."""
    total_pages = math.ceil(total_count / LOGS_PER_PAGE)
    
    buttons = []
    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="⬅️ назад",
                    callback_data=f"adminpage_{page-1}"
                )
            )
        nav_buttons.append(
            InlineKeyboardButton(
                text=f"{page+1}/{total_pages}",
                callback_data="admininfo" # To prevent interaction
            )
        )
        if page < total_pages - 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="вперед ➡️",
                    callback_data=f"adminpage_{page+1}"
                )
            )
        buttons.append(nav_buttons)
        
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- Bot Handlers ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 приветики! я бот для поиска и скачивания музла с ютуба.\n\n"
        "🔍 просто кидай мне название трека или исполнителя, и я попробую найти."
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "🎵 *как тут все работает:*\n\n"
        "1️⃣ кидаешь мне название трека/исполнителя\n"
        "2️⃣ выбираешь нужный из списка\n"
        "3️⃣ жмешь кнопку, чтобы скачать\n\n"
        "🔧 *команды, если что:*\n"
        "/start - начать сначала\n"
        "/help - вот это сообщение\n"
        "/search [запрос] - найти музыку по запросу\n"
        "/cancel - отменить загрузки, которые сейчас идут"
    )
    await message.answer(help_text, parse_mode="Markdown")

@dp.message(Command("search"))
async def cmd_search(message: types.Message):
    if len(message.text.split()) < 2:
        await message.answer("❌ напиши что-нибудь после /search, плиз.\nнапример: /search coldplay yellow")
        return
    
    query = " ".join(message.text.split()[1:])
    # Log the query
    log_query(message.from_user.id, message.from_user.username or message.from_user.first_name, query)
    
    await message.answer("🔍 ищу треки...")
    
    search_id = str(uuid.uuid4())
    tracks = await search_youtube(query, MAX_TRACKS)
    
    if not tracks:
        await message.answer("❌ чет ничего не нашлось. попробуй другой запрос?")
        return
    
    search_results[search_id] = tracks
    keyboard = create_tracks_keyboard(tracks, 0, search_id)
    
    await message.answer(
        f"🎵 нашел вот {len(tracks)} треков по запросу '{query}':",
        reply_markup=keyboard
    )

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
    # Log the query
    log_query(message.from_user.id, message.from_user.username or message.from_user.first_name, query)
    
    await message.answer("🔍 ищу треки...") 
    
    search_id = str(uuid.uuid4())
    tracks = await search_youtube(query, MAX_TRACKS)
    
    if not tracks:
        await message.answer("❌ ничего не нашел по твоему запросу. попробуй еще раз?")
        return
    
    search_results[search_id] = tracks
    keyboard = create_tracks_keyboard(tracks, 0, search_id)
    
    await message.answer(
        f"🎵 нашел вот {len(tracks)} треков по запросу '{query}':",
        reply_markup=keyboard
    )

@dp.message(Command("adminlog"))
async def cmd_adminlog(message: types.Message):
    """отображает лог запросов для админа."""
    print(f"[DEBUG] cmd_adminlog received from user_id: {message.from_user.id} (type: {type(message.from_user.id)}) compared to ADMIN_ID: {ADMIN_ID} (type: {type(ADMIN_ID)})") # Debug print
    
    if message.from_user.id != ADMIN_ID:
        print("[DEBUG] User ID does not match ADMIN_ID.") # Debug print
        await message.answer("упс, эта команда только для админа.")
        return
        
    print("[DEBUG] User ID matches. Proceeding...") # Debug print
    page = 0
    try:
        logs, total_count = get_logs(page=page)
        print(f"[DEBUG] get_logs returned {len(logs)} logs, total_count: {total_count}") # Debug print
        
        if not logs:
            print("[DEBUG] No logs found.") # Debug print
            await message.answer("пока нет записей в логе.")
            return
            
        log_text = "📋 *лог запросов:*\n\n" # Use double backslash for newline in MDV2
        for log_entry in logs:
            timestamp, user_id, username, query = log_entry
            # Escape markdown V2 characters: _, *, [, ], (, ), ~, `, >, #, +, -, =, |, {, }, ., !
            safe_username = username.replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("]", "\\]").replace("(", "\\(").replace(")", "\\)").replace("~", "\\~").replace("`", "\\`").replace(">", "\\>").replace("#", "\\#").replace("+", "\\+").replace("-", "\\-").replace("=", "\\=").replace("|", "\\|").replace("{", "\\{").replace("}", "\\}").replace(".", "\\.").replace("!", "\\!") if username else "_(нет юзернейма)_"
            safe_query = query.replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("]", "\\]").replace("(", "\\(").replace(")", "\\)").replace("~", "\\~").replace("`", "\\`").replace(">", "\\>").replace("#", "\\#").replace("+", "\\+").replace("-", "\\-").replace("=", "\\=").replace("|", "\\|").replace("{", "\\{").replace("}", "\\}").replace(".", "\\.").replace("!", "\\!")
            log_text += f"`{timestamp}` \\| `{user_id}` \\| {safe_username} \\| `{safe_query}`\n" # Use \\| to escape | for MDV2 in f-string
            
        keyboard = create_admin_log_keyboard(logs, page=page, total_count=total_count)
        
        print("[DEBUG] Attempting to send log message...") # Debug print
        try:
            await message.answer(log_text, reply_markup=keyboard, parse_mode="MarkdownV2")
            print("[DEBUG] Log message sent successfully.") # Debug print
        except Exception as e:
            print(f"[DEBUG] Error sending admin log with MarkdownV2: {e}") # Debug print
            # Fallback to sending without markdown if formatting fails
            await message.answer("Ошибка форматирования лога. Отправляю как простой текст:\n" + log_text.replace("\\", ""), reply_markup=keyboard)
            
    except Exception as e:
        print(f"[DEBUG] Error in cmd_adminlog main block: {e}") # Debug print
        await message.answer("ой, произошла внутренняя ошибка при получении логов.")

@dp.callback_query(F.data.startswith("adminpage_"))
async def process_admin_page_callback(callback: types.CallbackQuery):
    """обрабатывает пагинацию в админке."""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("только админ может листать логи.", show_alert=True)
        return
        
    try:
        page = int(callback.data.split("_")[1])
        logs, total_count = get_logs(page=page)
        
        if not logs:
            await callback.answer("на этой странице нет логов.", show_alert=True)
            return
            
        log_text = "📋 *лог запросов:*\n\n" # Double backslash for newline
        for log_entry in logs:
            timestamp, user_id, username, query = log_entry
            # Escape markdown V2 characters
            safe_username = username.replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("]", "\\]").replace("(", "\\(").replace(")", "\\)").replace("~", "\\~").replace("`", "\\`").replace(">", "\\>").replace("#", "\\#").replace("+", "\\+").replace("-", "\\-").replace("=", "\\=").replace("|", "\\|").replace("{", "\\{").replace("}", "\\}").replace(".", "\\.").replace("!", "\\!") if username else "_(нет юзернейма)_"
            safe_query = query.replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("]", "\\]").replace("(", "\\(").replace(")", "\\)").replace("~", "\\~").replace("`", "\\`").replace(">", "\\>").replace("#", "\\#").replace("+", "\\+").replace("-", "\\-").replace("=", "\\=").replace("|", "\\|").replace("{", "\\{").replace("}", "\\}").replace(".", "\\.").replace("!", "\\!")
            log_text += f"`{timestamp}` \\| `{user_id}` \\| {safe_username} \\| `{safe_query}`\n" # Use \\| to escape |
            
        keyboard = create_admin_log_keyboard(logs, page=page, total_count=total_count)
        
        # Edit the message with new page content
        await callback.message.edit_text(log_text, reply_markup=keyboard, parse_mode="MarkdownV2")
        await callback.answer() # Acknowledge the press
        
    except (IndexError, ValueError):
        await callback.answer("ошибка в номере страницы.", show_alert=True)
    except Exception as e:
        print(f"Error processing admin page callback: {e}")
        # Try to edit without markdown on error
        try:
            await callback.message.edit_text("Ошибка форматирования лога. Показываю как простой текст:\n" + log_text.replace("\\", ""), reply_markup=keyboard)
        except Exception as fallback_e:
             print(f"Fallback edit failed: {fallback_e}")
             await callback.answer("ой, какая-то ошибка при перелистывании логов.", show_alert=True)
        
@dp.callback_query(F.data == "admininfo")
async def process_admin_info_callback(callback: types.CallbackQuery):
    # Callback for the page number button in admin log - just acknowledge
    await callback.answer()

async def main():
    # Initialize the database
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main()) 