import os
import asyncio
import tempfile
import json
import base64
import math
import re
from collections import defaultdict
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from mutagen.id3 import ID3, TIT2, TPE1, APIC
from mutagen.mp3 import MP3
import yt_dlp
import uuid
import time
import subprocess
import aiohttp

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
URL_REGEX = r'(https?://\S+)'

# Хранилища
download_tasks = defaultdict(dict)
search_results = {}
download_queues = defaultdict(list)  # Очереди загрузок для каждого пользователя
user_modes = defaultdict(lambda: 'audio')
MAX_PARALLEL_DOWNLOADS = 3  # Максимальное количество одновременных загрузок

# Настройки yt-dlp
ydl_opts = {
    'format': 'bestaudio/best',
    'quiet': True,
    'no_warnings': True,
    'extract_flat': True,
    'prefer_ffmpeg': True,
    'nocheckcertificate': True,
    'ignoreerrors': True,
    'ffmpeg_location': '/usr/bin/ffmpeg',
}

# --- Клавиатура для переключения режимов ---
def get_mode_keyboard(current_mode: str) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    if current_mode == 'audio':
        builder.button(text="📹 Режим Видео")
    else:
        builder.button(text="🎵 Режим Аудио")
    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)

# --- Обработчик переключения режимов ---
@dp.message((F.text == "📹 Режим Видео") | (F.text == "🎵 Режим Аудио"))
async def switch_mode(message: types.Message):
    user_id = message.from_user.id
    if message.text == "📹 Режим Видео":
        user_modes[user_id] = 'video'
        new_mode = 'video'
        response_text = "✅ Переключено в режим Видео. Теперь буду искать и скачивать видео (MP4)."
    else:
        user_modes[user_id] = 'audio'
        new_mode = 'audio'
        response_text = "✅ Переключено в режим Аудио. Теперь буду искать и скачивать аудио (MP3)."
    await message.answer(response_text, reply_markup=get_mode_keyboard(new_mode))

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

async def search_youtube(query, max_results=50, mode='audio'):
    try:
        # Глобальные опции поиска
        search_opts = {
            **ydl_opts, # Используем базовые глобальные опции
            'default_search': 'ytsearch',
            'max_downloads': max_results,
            'extract_flat': True, # Плоский список для быстрого поиска
        }
        
        with yt_dlp.YoutubeDL(search_opts) as ydl:
            info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
            if not info or 'entries' not in info:
                return []
            
            results = []
            for entry in info['entries']:
                if entry:
                    duration = entry.get('duration', 0)
                    # Filter by duration ONLY for audio mode
                    if mode == 'audio' and (not duration or not (MIN_SONG_DURATION <= duration <= MAX_SONG_DURATION)):
                        continue # Skip if duration is missing or outside the range for audio
                        
                    title, artist = extract_title_and_artist(entry.get('title', 'Unknown Title'))
                    if artist == "Unknown Artist":
                        artist = entry.get('uploader', 'Unknown Artist')
                    results.append({
                        'title': title,
                        'channel': artist,
                        'url': entry.get('url', ''),
                        'duration': entry.get('duration', 0),
                        'thumbnail': entry.get('thumbnail') # <--- Добавляем URL обложки
                    })
            return results
    except Exception as e:
        print(f"An error occurred during search: {e}")
        return []

def create_tracks_keyboard(tracks, page=0, search_id="", mode='audio'):
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
            "mode": mode, # <--- Передаем режим
            "thumbnail": track.get('thumbnail') # <--- Передаем обложку
        }
        
        track_json = json.dumps(track_data, ensure_ascii=False)
        # Используем индексный колбек если данные слишком длинные
        # (увеличиваем запас, т.к. добавились mode и thumbnail)
        if len(track_json.encode('utf-8')) > 55:
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
        
        # Добавляем иконку режима
        icon = "🎬" if mode == 'video' else "🎧"

        buttons.append([
            InlineKeyboardButton(
                text=f"{icon} {track['title']} - {track['channel']}{duration_str}",
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
    final_extension = '.mp3' # По умолчанию
    loop = asyncio.get_running_loop()
    mode = track_data.get('mode', 'audio') # Определяем режим
    thumbnail_url = track_data.get('thumbnail') # Получаем обложку

    try:
        title = track_data["title"]
        artist = track_data["channel"]
        url = track_data["url"]
        
        # --- Безопасное имя файла ---
        safe_title = "".join(c if c.isalnum() or c in ('.', '_', '-') else '_' for c in title).strip('_').strip('.').strip('-')
        safe_title = safe_title[:100] 
        if not safe_title:
             safe_title = f"media_{uuid.uuid4()}"
        temp_dir = tempfile.gettempdir()
        base_temp_path = os.path.join(temp_dir, safe_title) # e.g., /tmp/Media_Title
        
        # --- Очистка перед скачиванием ---
        print(f"Cleaning potential old files for base: {base_temp_path}")
        for ext in ['.mp3', '.mp4', '.mkv', '.webm', '.m4a', '.opus', '.ogg', '.aac', '.part']:
            potential_path = f"{base_temp_path}{ext}"
            if os.path.exists(potential_path):
                try:
                    os.remove(potential_path)
                    print(f"Removed existing file: {potential_path}")
                except OSError as e:
                    print(f"Warning: Could not remove existing file {potential_path}: {e}")
        
        # --- Опции скачивания в зависимости от режима ---
        download_opts = {
            # Общие опции
            'verbose': True,
            'quiet': False,
            'no_warnings': False,
            'prefer_ffmpeg': True,
            'nocheckcertificate': True,
            'ignoreerrors': False, # Важно: False, чтобы видеть ошибки скачивания/конвертации
            'extract_flat': False, 
            'ffmpeg_location': '/usr/bin/ffmpeg',
            # Переменные опции
            'format': '',
            'postprocessors': [],
            'outtmpl': ''
        }

        if mode == 'audio':
            print("Setting options for AUDIO download")
            download_opts['format'] = 'bestaudio[ext=m4a]/bestaudio/best'
            download_opts['postprocessors'].append({
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            })
            download_opts['outtmpl'] = base_temp_path + '.%(ext)s'
            final_extension = '.mp3'
        elif mode == 'video':
            print("Setting options for VIDEO download")
            # Скачиваем лучшее видео с лучшим аудио, объединяем в MP4
            # Prefer mp4 directly if available
            download_opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best'
            download_opts['postprocessors'].append({
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4', # Конвертируем результат в MP4
            })
            download_opts['outtmpl'] = base_temp_path + '.%(ext)s'
            # Если скачивается видео + аудио отдельно, ytdl может использовать .mkv по умолчанию перед postprocessing
            # Поэтому ожидаемый файл может быть .mp4 (после postprocessing) или .mkv (если postprocessing не сработал)
            final_extension = '.mp4' # Ожидаем MP4 после конвертации
        else:
             raise ValueError(f"Unknown download mode: {mode}")

        expected_final_path = base_temp_path + final_extension

        try:
            await bot.edit_message_text(
                f"⏳ качаю {'видео' if mode == 'video' else 'трек'}: {title} - {artist}...",
                chat_id=callback_message.chat.id,
                message_id=status_message.message_id
            )
            
            print(f"\nStarting download ({mode.upper()}) for: {title} - {artist}")
            print(f"URL: {url}")
            print(f"Output template: {download_opts['outtmpl']}")
            print(f"Expected final path: {expected_final_path}")
            print(f"Using download options: {download_opts}")

            # Запускаем скачивание/конвертацию
            await loop.run_in_executor(
                None, 
                _blocking_download_and_convert,
                url,
                download_opts 
            )
            
            print(f"Finished blocking download call for: {title} - {artist}")

            # --- Проверка наличия файла после скачивания --- 
            if not os.path.exists(expected_final_path):
                print(f"ERROR: Expected final file NOT FOUND at {expected_final_path} after download attempt.")
                # Для видео проверим, не остался ли MKV (если конвертация в MP4 не удалась)
                potential_mkv_path = base_temp_path + '.mkv'
                if mode == 'video' and os.path.exists(potential_mkv_path):
                     print(f"Warning: Found MKV file {potential_mkv_path} instead of expected MP4. Using MKV.")
                     expected_final_path = potential_mkv_path # Используем MKV
                     final_extension = '.mkv'
                else:
                     # Проверим другие возможные промежуточные файлы
                     intermediate_extensions = ['.m4a', '.webm', '.opus', '.ogg', '.aac']
                     found_other = False
                     for ext in intermediate_extensions:
                         potential_path = f"{base_temp_path}{ext}"
                         if os.path.exists(potential_path):
                             print(f"Warning: Found intermediate file {potential_path}. Conversion likely failed.")
                             found_other = True
                             try: os.remove(potential_path) 
                             except OSError as e: print(f"Could not remove intermediate file {potential_path}: {e}")
                             break
                     raise Exception(f"файл {expected_final_path} не создался после скачивания/конвертации.")
            
            temp_path = expected_final_path 
            print(f"Confirmed final file exists at: {temp_path}")
            
            if os.path.getsize(temp_path) == 0:
                print(f"ERROR: Downloaded file {temp_path} is empty.")
                raise Exception("скачанный файл пустой.")
            
            print(f"File size: {os.path.getsize(temp_path)} bytes")

            # --- Валидация и метаданные только для АУДИО --- 
            if mode == 'audio':
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
                
                print(f"Setting metadata for {temp_path}...")
                if not set_mp3_metadata(temp_path, title, artist):
                     print(f"ERROR: Failed to set metadata for {temp_path}.")
                     raise Exception(f"ошибка при установке метаданных для: {title} - {artist}")
                print(f"Metadata set successfully for {temp_path}.")
            
            # --- Отправка файла --- 
            print(f"Preparing to send {temp_path} (Mode: {mode.upper()}).")
            await bot.delete_message(
                chat_id=callback_message.chat.id,
                message_id=status_message.message_id
            )
            sending_message = await callback_message.answer(f"📤 отправляю {'видео' if mode == 'video' else 'трек'}...") 
            print(f"Sending {mode} {temp_path}...")

            if mode == 'audio':
                await bot.send_audio(
                    chat_id=callback_message.chat.id,
                    audio=FSInputFile(temp_path),
                    title=title,
                    performer=artist
                )
            elif mode == 'video':
                # Попытаемся скачать обложку для видео
                thumbnail_path = None
                if thumbnail_url:
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(thumbnail_url) as resp:
                                if resp.status == 200:
                                    thumb_temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
                                    thumb_temp_file.write(await resp.read())
                                    thumbnail_path = thumb_temp_file.name
                                    thumb_temp_file.close()
                                    print(f"Thumbnail downloaded to {thumbnail_path}")
                    except Exception as thumb_e:
                        print(f"Warning: Failed to download thumbnail {thumbnail_url}: {thumb_e}")

                await bot.send_video(
                    chat_id=callback_message.chat.id,
                    video=FSInputFile(temp_path),
                    caption=f"{title} - {artist}",
                    thumbnail=FSInputFile(thumbnail_path) if thumbnail_path else None,
                    # Можно добавить duration, width, height если они есть в info и нужны
                )
                
                # Удаляем временный файл обложки, если он был создан
                if thumbnail_path and os.path.exists(thumbnail_path):
                     try:
                         os.remove(thumbnail_path)
                         print(f"Removed temporary thumbnail file: {thumbnail_path}")
                     except OSError as e:
                         print(f"Warning: Could not remove thumbnail file {thumbnail_path}: {e}")

            print(f"{mode.capitalize()} sent successfully. Deleting sending message.")
            await bot.delete_message(
                chat_id=callback_message.chat.id,
                message_id=sending_message.message_id
            )
            print(f"Finished processing {mode}: {title} - {artist}")

        except Exception as e:
            # Обработка ошибок скачивания/конвертации/отправки
            print(f"ERROR during download/processing for {title} - {artist}: {e}")
            error_text = f"❌ блин, ошибка при скачивании/обработке: {str(e)}"
            if len(error_text) > 4000: error_text = error_text[:4000] + "..."
            try: await bot.edit_message_text(chat_id=callback_message.chat.id, message_id=status_message.message_id, text=error_text)
            except Exception as edit_error: print(f"Failed to edit message for error: {edit_error}")
            try: await callback_message.answer(error_text)
            except Exception as send_error: print(f"Failed to send new message for error: {send_error}")

    finally:
        # Очистка основного временного файла (mp3/mp4/mkv)
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
    user_id = message.from_user.id
    current_mode = user_modes[user_id]
    await message.answer(
        "👋 приветики! я бот для скачивания музыки и видео\n\n" 
        "🔍 кидай мне название, ссылку или используй кнопки ниже 👇",
        reply_markup=get_mode_keyboard(current_mode)
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    user_id = message.from_user.id
    current_mode = user_modes[user_id]
    help_text = (
        f"🎵 *Текущий режим:* {'Аудио (MP3)' if current_mode == 'audio' else 'Видео (MP4)'}\n\n"
        "*как тут все работает:*"
        "1️⃣ Кидаешь мне название трека/видео или ссылку (YouTube и др.)\n"
        "2️⃣ Выбираешь нужный из списка (если искал по названию)\n"
        "3️⃣ Жмешь кнопку, чтобы скачать\n"
        "4️⃣ Используй кнопку внизу для переключения режима Аудио/Видео\n\n"
        "⚙️ *команды, если что:*"
        "/start - начать сначала\n"
        "/help - вот это сообщение\n"
        "/search [запрос] - найти музыку/видео по запросу\n"
        "/cancel - отменить текущие загрузки"
    )
    await message.answer(help_text, parse_mode="Markdown", reply_markup=get_mode_keyboard(current_mode))

@dp.message(Command("search"))
async def cmd_search(message: types.Message):
    if len(message.text.split()) < 2:
        await message.answer("❌ напиши что-нибудь после /search, плиз.\nнапример: /search coldplay yellow")
        return
    
    query = " ".join(message.text.split()[1:])
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
    current_mode = user_modes[user_id]
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
            
        await message.answer("✅ ок, отменил все активные загрузки и почистил очередь.", reply_markup=get_mode_keyboard(current_mode))
    else:
        await message.answer("❌ так щас ничего и не качается вроде...", reply_markup=get_mode_keyboard(current_mode))

@dp.callback_query(F.data.startswith("d_"))
async def process_download_callback(callback: types.CallbackQuery):
    try:
        track_data_json = base64.b64decode(callback.data[2:]).decode('utf-8')
        track_data = json.loads(track_data_json)
        user_id = callback.from_user.id
        mode = track_data.get('mode', 'audio') # Получаем режим
        
        # Check if already downloading this specific track
        if track_data["url"] in download_tasks.get(user_id, {}):
            await callback.answer(f"Этот {'видео' if mode=='video' else 'трек'} уже качается или в очереди", show_alert=True)
            return
            
        # Check queue as well
        if any(item[0]['url'] == track_data['url'] for item in download_queues.get(user_id, [])):
             await callback.answer(f"Этот {'видео' if mode=='video' else 'трек'} уже качается или в очереди", show_alert=True)
             return
             
        active_downloads = sum(1 for task in download_tasks.get(user_id, {}).values() if not task.done())
        queue_size = len(download_queues.get(user_id, []))

        if active_downloads >= MAX_PARALLEL_DOWNLOADS:
            download_queues[user_id].append((track_data, callback.message))
            await callback.answer(
                f"⏳ Добавил в очередь ({queue_size+1}-й). Качаю {active_downloads}/{MAX_PARALLEL_DOWNLOADS}"
            )
        else:
            status_message = await callback.message.answer(f"⏳ Начинаю скачивать {'видео' if mode == 'video' else 'трек'}: {track_data['title']} - {track_data['channel']}") 
            task = asyncio.create_task(
                download_track(user_id, track_data, callback.message, status_message)
            )
            download_tasks[user_id][track_data["url"]] = task
            await callback.answer(f"Начал скачивание {'видео' if mode == 'video' else 'трека'}") # Acknowledge callback
            
    except json.JSONDecodeError:
         await callback.message.answer("❌ Не смог разобрать данные. Попробуй поискать снова.")
         await callback.answer()
    except Exception as e:
        print(f"Error in process_download_callback: {e}")
        await callback.message.answer(f"❌ Ошибка: {str(e)}")
        await callback.answer()

@dp.callback_query(F.data.startswith("dl_"))
async def process_download_callback_with_index(callback: types.CallbackQuery):
    try:
        parts = callback.data.split("_")
        track_index = int(parts[1]) - 1
        search_id = parts[2]
        
        if search_id not in search_results:
            await callback.answer("❌ Результаты поиска устарели. Найди снова.", show_alert=True)
            return
        
        tracks = search_results[search_id]
        if 0 <= track_index < len(tracks):
            # Получаем track_data из кеша, он уже должен содержать mode и thumbnail
            track_data = tracks[track_index]
            user_id = callback.from_user.id
            mode = track_data.get('mode', 'audio') # Получаем режим

            # Check if already downloading this specific track
            if track_data["url"] in download_tasks.get(user_id, {}):
                await callback.answer(f"Этот {'видео' if mode=='video' else 'трек'} уже качается или в очереди", show_alert=True)
                return
                
            # Check queue as well
            if any(item[0]['url'] == track_data['url'] for item in download_queues.get(user_id, [])):
                 await callback.answer(f"Этот {'видео' if mode=='video' else 'трек'} уже качается или в очереди", show_alert=True)
                 return

            active_downloads = sum(1 for task in download_tasks.get(user_id, {}).values() if not task.done())
            queue_size = len(download_queues.get(user_id, []))

            if active_downloads >= MAX_PARALLEL_DOWNLOADS:
                download_queues[user_id].append((track_data, callback.message))
                await callback.answer(
                    f"⏳ Добавил в очередь ({queue_size+1}-й). Качаю {active_downloads}/{MAX_PARALLEL_DOWNLOADS}"
                )
            else:
                status_message = await callback.message.answer(f"⏳ Начинаю скачивать {'видео' if mode == 'video' else 'трек'}: {track_data['title']} - {track_data['channel']}")
                task = asyncio.create_task(
                    download_track(user_id, track_data, callback.message, status_message)
                )
                download_tasks[user_id][track_data["url"]] = task
                await callback.answer(f"Начал скачивание {'видео' if mode == 'video' else 'трека'}") # Acknowledge callback
        else:
            await callback.answer("❌ Не нашел трек/видео по этому индексу.", show_alert=True)
            
    except (IndexError, ValueError):
         await callback.answer("❌ Не смог разобрать данные для скачивания.", show_alert=True)
    except Exception as e:
        print(f"Error in process_download_callback_with_index: {e}")
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)

@dp.callback_query(F.data.startswith("page_"))
async def process_page_callback(callback: types.CallbackQuery):
    try:
        parts = callback.data.split("_")
        page = int(parts[1])
        search_id = parts[2]
        
        if search_id not in search_results:
            await callback.answer("❌ Эти результаты поиска устарели. Поищи заново.", show_alert=True)
            return
        
        tracks = search_results[search_id]
        # Определяем режим из первого трека (предполагаем, что он одинаковый для всего поиска)
        mode = tracks[0].get('mode', 'audio') if tracks else 'audio' 
        keyboard = create_tracks_keyboard(tracks, page, search_id, mode=mode)
        
        await callback.message.edit_reply_markup(reply_markup=keyboard)
        await callback.answer() # Simple ack for page turn
    except (IndexError, ValueError):
        await callback.answer("❌ Не смог понять номер страницы.", show_alert=True)
    except Exception as e:
        print(f"Error in process_page_callback: {e}")
        await callback.answer(f"❌ Ошибка при перелистывании: {str(e)}", show_alert=True)
        
@dp.callback_query(F.data == "info")
async def process_info_callback(callback: types.CallbackQuery):
    # Simple ack for the info button (page number)
    await callback.answer()

# --- Главный обработчик текста ---
@dp.message()
async def handle_text(message: types.Message):
    # Игнорируем команды явно
    if message.text.startswith('/'):
        # Можно добавить подсказку для неизвестных команд
        # await message.answer("хм, не знаю такую команду. попробуй /help")
        return

    # Игнорируем кнопки переключения режима
    if message.text in ["📹 Режим Видео", "🎵 Режим Аудио"]:
        # Обработчик switch_mode уже сработал
        return

    user_id = message.from_user.id
    current_mode = user_modes[user_id]

    # Проверяем, является ли текст URL
    url_match = re.search(URL_REGEX, message.text)
    if url_match:
        url = url_match.group(1)
        await process_direct_url(url, user_id, message, current_mode)
    else:
        # Если не URL, считаем поисковым запросом
        query = message.text
        await message.answer(f"🔍 ищу {'видео' if current_mode == 'video' else 'аудио'} по запросу '{query}'...") 
        
        search_id = str(uuid.uuid4())
        # Передаем режим в функцию поиска
        tracks = await search_youtube(query, MAX_TRACKS, mode=current_mode)
        
        if not tracks:
            await message.answer(f"❌ ничего не нашел ({{'видео' if current_mode == 'video' else 'аудио'}}). попробуй еще раз?", reply_markup=get_mode_keyboard(current_mode))
            return
        
        search_results[search_id] = tracks
        # Передаем режим для создания клавиатуры
        keyboard = create_tracks_keyboard(tracks, 0, search_id, mode=current_mode)
        
        await message.answer(
            f"🎵 нашел вот {len(tracks)} {'видео' if current_mode == 'video' else 'треков'} по запросу '{query}':",
            reply_markup=keyboard
        )

# --- Функция обработки прямой ссылки ---
async def process_direct_url(url: str, user_id: int, message: types.Message, mode: str):
    await message.answer(f"⏳ Анализирую ссылку на {'видео' if mode == 'video' else 'аудио'}...")
    try:
        # Используем yt-dlp для получения информации о ссылке без скачивания
        info_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False, # Нам нужна полная информация об одном видео
            'skip_download': True, # Не скачивать на этом этапе
            'nocheckcertificate': True,
            'ignoreerrors': True,
            'ffmpeg_location': '/usr/bin/ffmpeg'
        }
        with yt_dlp.YoutubeDL(info_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                await message.answer("❌ Не удалось получить информацию по этой ссылке.", reply_markup=get_mode_keyboard(mode))
                return

        # Проверяем длительность для аудио режима
        duration = info.get('duration', 0)
        if mode == 'audio' and not (MIN_SONG_DURATION <= duration <= MAX_SONG_DURATION):
             await message.answer(f"❌ Трек по ссылке имеет длительность вне допустимого диапазона ({MIN_SONG_DURATION}-{MAX_SONG_DURATION} сек) для аудио режима.", reply_markup=get_mode_keyboard(mode))
             return

        # Формируем track_data для скачивания
        title, artist = extract_title_and_artist(info.get('title', 'Unknown Title'))
        if artist == "Unknown Artist":
            artist = info.get('uploader', 'Unknown Artist')
            
        track_data = {
            'title': title,
            'artist': artist, # Название канала/исполнителя
            'url': info.get('webpage_url', url), # Используем исходный URL
            'mode': mode, # Передаем режим
            'thumbnail': info.get('thumbnail') # Добавляем обложку
        }

        # Логика постановки в очередь или старта скачивания (аналогично колбекам)
        if track_data["url"] in download_tasks.get(user_id, {}):
            await message.answer("Этот трек/видео уже качается или в очереди", reply_markup=get_mode_keyboard(mode))
            return
        if any(item[0]['url'] == track_data['url'] for item in download_queues.get(user_id, [])):
             await message.answer("Этот трек/видео уже качается или в очереди", reply_markup=get_mode_keyboard(mode))
             return

        active_downloads = sum(1 for task in download_tasks.get(user_id, {}).values() if not task.done())
        queue_size = len(download_queues.get(user_id, []))

        if active_downloads >= MAX_PARALLEL_DOWNLOADS:
            download_queues[user_id].append((track_data, message))
            await message.answer(
                f"⏳ Добавил в очередь ({queue_size+1}-й). Качаю {active_downloads}/{MAX_PARALLEL_DOWNLOADS}",
                reply_markup=get_mode_keyboard(mode)
            )
        else:
            status_message = await message.answer(f"⏳ Начинаю скачивать {'видео' if mode == 'video' else 'трек'}: {track_data['title']} - {track_data['artist']}")
            task = asyncio.create_task(
                download_track(user_id, track_data, message, status_message)
            )
            download_tasks[user_id][track_data["url"]] = task
            # Не нужно await callback.answer() здесь
            
    except Exception as e:
        print(f"Error processing direct URL {url}: {e}")
        await message.answer(f"❌ Ошибка при обработке ссылки: {e}", reply_markup=get_mode_keyboard(mode))

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main()) 