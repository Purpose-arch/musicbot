import os
import asyncio
import tempfile
import json
import base64
import math
from collections import defaultdict, OrderedDict
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.exceptions import TelegramBadRequest
from mutagen.id3 import ID3, TIT2, TPE1, APIC
from mutagen.mp3 import MP3
import yt_dlp
import uuid
import time
import subprocess

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

# Хранилища
download_tasks = defaultdict(dict)
search_results = {}
download_queues = defaultdict(list)  # Очереди загрузок для каждого пользователя
MAX_PARALLEL_DOWNLOADS = 3  # Максимальное количество одновременных загрузок

# Добавим хранилище для сообщений статуса загрузки
download_status_messages = defaultdict(dict) # user_id -> {download_url: message_object}

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

# --- НОВАЯ ФУНКЦИЯ для кнопки отмены --- 
def create_cancel_markup(download_url: str) -> InlineKeyboardMarkup:
    """Создает клавиатуру с одной кнопкой 'Отмена' для конкретной загрузки."""
    buttons = [[InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_dl_{download_url}")]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
# --- Конец новой функции ---

async def process_download_queue(user_id):
    """Обработка очереди загрузок для пользователя"""
    while download_queues[user_id] and len(download_tasks.get(user_id, {})) < MAX_PARALLEL_DOWNLOADS:
        track_data, original_message = download_queues[user_id].pop(0)
        download_url = track_data['url']
        
        # --- Изменено: Отправляем сообщение с кнопкой отмены --- 
        try:
            status_message = await original_message.answer(
                f"⏳ ставлю в очередь на скачивание: {track_data['title']} - {track_data['channel']} ({len(download_queues.get(user_id, []))} в очереди)",
                reply_markup=create_cancel_markup(download_url) # <-- Добавляем кнопку
            )
            download_status_messages[user_id][download_url] = status_message # Сохраняем сообщение
            
            task = asyncio.create_task(
                download_track(user_id, track_data, status_message) # Передаем новое сообщение статуса
            )
            download_tasks[user_id][download_url] = task
        except TelegramBadRequest as e:
             print(f"Failed to send status message (maybe deleted?): {e}")
             # Если не удалось отправить сообщение, просто пропускаем этот трек
             continue 
        except Exception as e:
            print(f"Error creating download task from queue: {e}")
            # Удаляем сообщение, если оно было создано и сохранено
            if user_id in download_status_messages and download_url in download_status_messages[user_id]:
                del download_status_messages[user_id][download_url]
            continue

def _blocking_download_and_convert(url, download_opts):
    """Helper function to run blocking yt-dlp download/conversion."""
    with yt_dlp.YoutubeDL(download_opts) as ydl:
        # Check info first (optional, but good practice)
        info = ydl.extract_info(url, download=False)
        if not info:
            raise Exception("не удалось получить инфу о видео (в executor)")
        # Perform the download and conversion
        ydl.download([url])

async def download_track(user_id, track_data, status_message):
    # --- Изменено: status_message передается как аргумент --- 
    temp_path = None
    loop = asyncio.get_running_loop()
    download_url = track_data["url"] 
    
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
             # --- Изменено: Редактируем существующее сообщение статуса --- 
            await bot.edit_message_text(
                f"⏳ качаю трек: {track_data['title']} - {track_data['channel']}...",
                chat_id=status_message.chat.id,
                message_id=status_message.message_id,
                reply_markup=create_cancel_markup(download_url) # Оставляем кнопку отмены
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
                download_opts 
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
                try:
                    await bot.delete_message(
                        chat_id=status_message.chat.id,
                        message_id=status_message.message_id
                    )
                    if user_id in download_status_messages and download_url in download_status_messages[user_id]:
                        del download_status_messages[user_id][download_url] # Убираем из хранилища
                except TelegramBadRequest:
                    print("Status message already deleted?") 
                except Exception as del_err:
                     print(f"Error deleting status message: {del_err}")

                sending_message = await status_message.reply("📤 отправляю трек...") # Используем reply для связи
                print(f"Sending audio {temp_path}...")
                await bot.send_audio(
                    chat_id=status_message.chat.id,
                    audio=FSInputFile(temp_path),
                    title=title,
                    performer=artist
                )
                print(f"Audio sent successfully. Deleting sending message.")
                await bot.delete_message(
                    chat_id=sending_message.chat.id, # Используем chat_id из sending_message
                    message_id=sending_message.message_id
                )
                print(f"Finished processing track: {title} - {artist}")
            else:
                print(f"ERROR: Failed to set metadata for {temp_path}.")
                raise Exception(f"ошибка при установке метаданных для: {title} - {artist}")

        except asyncio.CancelledError:
             print(f"Download task for {title} - {artist} ({download_url}) was cancelled.")
             # --- Изменено: Редактируем сообщение при отмене --- 
             try:
                 await bot.edit_message_text(
                     f"🚫 Загрузка отменена: {track_data['title']} - {track_data['channel']}",
                     chat_id=status_message.chat.id,
                     message_id=status_message.message_id,
                     reply_markup=None # Убираем кнопку
                 )
             except TelegramBadRequest:
                 print("Status message already deleted during cancel?")
             except Exception as edit_err:
                 print(f"Error editing message on cancel: {edit_err}")
             # Не перевыбрасываем CancelledError, просто завершаем задачу

        except Exception as e:
            print(f"ERROR during download/processing for {title} - {artist}: {e}")
            # Catch errors from download, file checks, or metadata setting
            error_text = f"❌ блин, ошибка: {str(e)}"
            if len(error_text) > 4000: 
                error_text = error_text[:4000] + "..."
            try:
                await bot.edit_message_text(
                    chat_id=status_message.chat.id,
                    message_id=status_message.message_id,
                    text=error_text,
                    reply_markup=None # Убираем кнопку
                )
            except Exception as edit_error:
                print(f"Failed to edit message for error: {edit_error}")
                try:
                    await status_message.reply(error_text)
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
        
        # --- Изменено: Убираем задачу и сообщение статуса --- 
        if user_id in download_tasks:
            download_tasks[user_id].pop(download_url, None)
            if not download_tasks[user_id]:
                del download_tasks[user_id]
        # Убираем сообщение из хранилища, если оно еще там
        if user_id in download_status_messages:
            download_status_messages[user_id].pop(download_url, None)
            if not download_status_messages[user_id]:
                del download_status_messages[user_id]
        
        # Проверяем очередь только если задача не была отменена 
        # (чтобы не запустить следующую, если нажали /cancel) 
        # или если она завершилась сама (успешно или с ошибкой)    
        # Проверка на CancelledError может быть сложной внутри finally, 
        # поэтому просто проверяем наличие очереди.
        if user_id in download_queues and download_queues[user_id]:
             print(f"Processing next item in queue for user {user_id} after task completion/error.")
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
        "🎵 *команды, если что:*\n"
        "/start - начать сначала\n"
        "/help - вот это сообщение\n"
        "/search [запрос] - найти музыку по запросу\n"
        "/cancel - отменить загрузки, которые сейчас идут"
    )
    await message.answer(help_text, parse_mode="Markdown")

@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message):
    user_id = message.from_user.id
    cancelled_count = 0
    active_tasks = []

    # Отмена активных задач
    if user_id in download_tasks:
        active_tasks = [task for task in download_tasks[user_id].values() if not task.done()]
        for task in active_tasks:
            task.cancel()
            cancelled_count += 1
        # Даем время на отмену
        if active_tasks: 
            await asyncio.sleep(0.2)
        # Очищаем словарь задач (завершенные/отмененные удалятся сами в finally)
        # download_tasks[user_id] = {url: task for url, task in download_tasks[user_id].items() if not task.done()}
        # if not download_tasks[user_id]:
        #      del download_tasks[user_id]

    # Очистка очереди
    queued_count = 0
    if user_id in download_queues:
        queued_count = len(download_queues[user_id])
        # --- Изменено: Нужно отредактировать сообщения для треков в очереди --- 
        for track_data, _ in download_queues[user_id]: # Игнорируем старое original_message
            download_url = track_data['url']
            if user_id in download_status_messages and download_url in download_status_messages[user_id]:
                status_message = download_status_messages[user_id].pop(download_url)
                try:
                    await bot.edit_message_text(
                        f"🚫 Убрано из очереди: {track_data['title']} - {track_data['channel']}",
                        chat_id=status_message.chat.id,
                        message_id=status_message.message_id,
                        reply_markup=None
                    )
                except Exception as e:
                     print(f"Error editing queued message on /cancel: {e}")
            else: 
                print(f"Warning: Status message for queued item {download_url} not found during /cancel.")
        download_queues[user_id].clear()
        cancelled_count += queued_count

    # Очистка оставшихся сообщений статуса (если задачи завершились с ошибкой до /cancel)
    if user_id in download_status_messages:
         # Создаем копию ключей перед итерацией
         urls_to_remove = list(download_status_messages[user_id].keys()) 
         for url in urls_to_remove:
             if url in download_status_messages[user_id]: # Проверяем еще раз, т.к. могли быть удалены выше
                status_message = download_status_messages[user_id].pop(url)
                try:
                    await bot.delete_message(
                        chat_id=status_message.chat.id, 
                        message_id=status_message.message_id
                    )
                except Exception as e:
                    print(f"Error deleting remaining status message on /cancel: {e}")
         if not download_status_messages[user_id]:
             del download_status_messages[user_id]

    if cancelled_count > 0:
        await message.answer(f"✅ ок, отменил {cancelled_count} загрузок и почистил очередь.")
    else:
        await message.answer("❌ так щас ничего и не качается или в очереди нет.")

@dp.callback_query(F.data.startswith("d_"))
async def process_download_callback(callback: types.CallbackQuery):
    try:
        track_data = json.loads(base64.b64decode(callback.data[2:]).decode('utf-8'))
        user_id = callback.from_user.id
        download_url = track_data['url']
        
        if download_url in download_tasks.get(user_id, {}) or \
           any(item[0]['url'] == download_url for item in download_queues.get(user_id, [])):
            await callback.answer("этот трек уже качается или в очереди", show_alert=True)
            return
            
        active_downloads = sum(1 for task in download_tasks.get(user_id, {}).values() if not task.done())
        queue_size = len(download_queues.get(user_id, []))

        if active_downloads >= MAX_PARALLEL_DOWNLOADS:
            download_queues[user_id].append((track_data, callback.message))
            # --- Изменено: Отправляем сообщение о добавлении в очередь с кнопкой --- 
            status_message = await callback.message.answer(
                 f"⏳ добавил в очередь ({queue_size+1}-й): {track_data['title']} - {track_data['channel']}",
                 reply_markup=create_cancel_markup(download_url)
            )
            download_status_messages[user_id][download_url] = status_message
            await callback.answer(f"добавил в очередь ({queue_size+1}-й)")
        else:
            # --- Изменено: Отправляем сообщение о начале скачивания с кнопкой --- 
            status_message = await callback.message.answer(
                f"⏳ начинаю скачивать: {track_data['title']} - {track_data['channel']}",
                reply_markup=create_cancel_markup(download_url)
            )
            download_status_messages[user_id][download_url] = status_message
            task = asyncio.create_task(
                download_track(user_id, track_data, status_message)
            )
            download_tasks[user_id][download_url] = task
            await callback.answer("начал скачивание")
            
    except json.JSONDecodeError:
         await callback.message.answer("❌ чет не смог разобрать данные трека. попробуй поискать снова.")
         await callback.answer()
    except Exception as e:
        print(f"Error in process_download_callback: {e}")
        await callback.message.answer(f"❌ ой, ошибка: {str(e)}")
        await callback.answer() # Acknowledge callback in all cases, even errors

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
            download_url = track_data['url']

            if download_url in download_tasks.get(user_id, {}) or \
               any(item[0]['url'] == download_url for item in download_queues.get(user_id, [])):
                await callback.answer("этот трек уже качается или в очереди", show_alert=True)
                return
                
            active_downloads = sum(1 for task in download_tasks.get(user_id, {}).values() if not task.done())
            queue_size = len(download_queues.get(user_id, []))

            if active_downloads >= MAX_PARALLEL_DOWNLOADS:
                download_queues[user_id].append((track_data, callback.message))
                # --- Изменено: Отправляем сообщение о добавлении в очередь с кнопкой --- 
                status_message = await callback.message.answer(
                     f"⏳ добавил в очередь ({queue_size+1}-й): {track_data['title']} - {track_data['channel']}",
                     reply_markup=create_cancel_markup(download_url)
                )
                download_status_messages[user_id][download_url] = status_message
                await callback.answer(f"добавил в очередь ({queue_size+1}-й)")
            else:
                # --- Изменено: Отправляем сообщение о начале скачивания с кнопкой --- 
                status_message = await callback.message.answer(
                    f"⏳ начинаю скачивать: {track_data['title']} - {track_data['channel']}",
                    reply_markup=create_cancel_markup(download_url)
                )
                download_status_messages[user_id][download_url] = status_message
                task = asyncio.create_task(
                    download_track(user_id, track_data, status_message)
                )
                download_tasks[user_id][download_url] = task
                await callback.answer("начал скачивание")
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

# --- НОВЫЙ ОБРАБОТЧИК для кнопки отмены --- 
@dp.callback_query(F.data.startswith("cancel_dl_"))
async def cancel_download_callback(callback: types.CallbackQuery):
    download_url = callback.data[len("cancel_dl_"):]
    user_id = callback.from_user.id
    cancelled = False
    
    # Попытка отменить активную задачу
    if user_id in download_tasks and download_url in download_tasks[user_id]:
        task = download_tasks[user_id][download_url]
        if not task.done():
            task.cancel()
            cancelled = True
            print(f"Cancelled active task via button: {download_url}")
            # Сообщение будет отредактировано в finally блока download_track
            await callback.answer("загрузка отменена")
        else:
             # Задача уже завершилась (успешно/ошибка)
             await callback.answer("эта загрузка уже завершена")
             # Удаляем кнопку, если сообщение еще существует
             if user_id in download_status_messages and download_url in download_status_messages[user_id]:
                 status_message = download_status_messages[user_id].pop(download_url)
                 try:
                      await bot.edit_message_reply_markup(chat_id=status_message.chat.id, 
                                                          message_id=status_message.message_id, 
                                                          reply_markup=None)
                 except Exception as e:
                      print(f"Error removing markup from completed task message: {e}")
             return # Выходим, т.к. делать больше нечего
    else:
        # Проверяем очередь
        original_queue_len = len(download_queues.get(user_id, []))
        # Фильтруем очередь, удаляя нужный элемент
        download_queues[user_id] = [item for item in download_queues.get(user_id, []) if item[0]['url'] != download_url]
        
        if len(download_queues.get(user_id, [])) < original_queue_len:
            cancelled = True
            print(f"Removed from queue via button: {download_url}")
            # Находим и редактируем сообщение статуса
            if user_id in download_status_messages and download_url in download_status_messages[user_id]:
                status_message = download_status_messages[user_id].pop(download_url)
                try:
                    track_title = "трек" # Дефолт, если не найдем
                    # Найдем title для сообщения (опционально, можно и без него)
                    # Это дорогая операция, возможно стоит убрать
                    # original_data = next((item[0] for item in download_queues.get(user_id, []) if item[0]['url'] == download_url), None)
                    # if original_data: track_title = original_data.get('title', 'трек')
                        
                    await bot.edit_message_text(
                        f"🚫 Убрано из очереди: {status_message.text.split(': ')[1]}", # Пытаемся извлечь имя из текста сообщения
                        chat_id=status_message.chat.id,
                        message_id=status_message.message_id,
                        reply_markup=None
                    )
                    await callback.answer("убрано из очереди")
                except Exception as e:
                    print(f"Error editing queued message on cancel: {e}")
                    await callback.answer("ошибка при обновлении сообщения") # Сообщаем об ошибке
            else:
                 print(f"Status message for cancelled queue item {download_url} not found.")
                 await callback.answer("убрано из очереди (сообщение не найдено)") # Сообщаем об успехе, но без обновления сообщения

    if not cancelled:
        print(f"Cancel button pressed for {download_url}, but task/queue item not found.")
        await callback.answer("не удалось найти эту загрузку для отмены", show_alert=True)
        # Попытаемся удалить кнопку у сообщения, если оно есть
        if user_id in download_status_messages and download_url in download_status_messages[user_id]:
             status_message = download_status_messages[user_id].pop(download_url)
             try:
                  await bot.edit_message_reply_markup(chat_id=status_message.chat.id, 
                                                      message_id=status_message.message_id, 
                                                      reply_markup=None)
             except Exception as e:
                  print(f"Error removing markup from lost task message: {e}")
# --- Конец нового обработчика --- 

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main()) 