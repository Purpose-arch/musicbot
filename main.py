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
MAX_TRACKS = 150
MAX_RETRIES = 3
MIN_SONG_DURATION = 45  # Минимальная длительность трека в секундах
MAX_SONG_DURATION = 720 # Максимальная длительность трека в секундах (12 минут)

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
        status_message = await callback_message.answer(f"⏳ Скачиваю трек: {track_data['title']} - {track_data['channel']}\n●")
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
            raise Exception("Не удалось получить информацию о видео (в executor)")
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
            # Run the blocking download/conversion in a separate thread
            await loop.run_in_executor(
                None,  # Use default ThreadPoolExecutor
                _blocking_download_and_convert,
                url,
                download_opts
            )
            
            # Explicitly define the expected mp3 path
            expected_mp3_path = base_temp_path + '.mp3'
            
            # Check if the expected mp3 file exists
            if not os.path.exists(expected_mp3_path):
                # Check for other possible extensions only as a fallback for debugging/errors
                found_file = None
                other_extensions = ['.m4a', '.webm', '.opus', '.ogg', '.aac'] # Common audio formats
                for ext in other_extensions:
                    potential_path = f"{base_temp_path}{ext}"
                    if os.path.exists(potential_path):
                        print(f"Warning: MP3 post-processing might have failed. Found {potential_path} instead of {expected_mp3_path}")
                        # Optionally, you could try to process this file, but for now, let's treat it as an error.
                        break 
                raise Exception(f"Файл {expected_mp3_path} не был создан после скачивания и конвертации.")
            
            temp_path = expected_mp3_path # Use the expected mp3 path
            
            # Проверяем размер файла
            if os.path.getsize(temp_path) == 0:
                raise Exception("Скачанный файл пуст")
            
            # Устанавливаем метаданные
            if set_mp3_metadata(temp_path, title, artist):
                # Удаляем сообщение о загрузке
                await bot.delete_message(
                    chat_id=callback_message.chat.id,
                    message_id=status_message.message_id
                )
                
                # Отправляем сообщение о отправке
                sending_message = await callback_message.answer("📤 Отправляю трек...")
                
                await bot.send_audio(
                    chat_id=callback_message.chat.id,
                    audio=FSInputFile(temp_path),
                    title=title,
                    performer=artist
                )
                
                # Удаляем сообщение о отправке
                await bot.delete_message(
                    chat_id=callback_message.chat.id,
                    message_id=sending_message.message_id
                )
            else:
                await bot.edit_message_text(
                    chat_id=callback_message.chat.id,
                    message_id=status_message.message_id,
                    text=f"❌ Ошибка при обработке трека: {str(e)}"
                )
        except Exception as e:
            # Catch errors from executor or file checks
            raise Exception(f"Ошибка при скачивании/конвертации: {str(e)}")
    
    except Exception as e:
        await bot.edit_message_text(
            chat_id=callback_message.chat.id,
            message_id=status_message.message_id,
            text=f"❌ Ошибка при скачивании трека: {str(e)}"
        )
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass
        
        # После завершения загрузки проверяем очередь
        if user_id in download_tasks:
            del download_tasks[user_id][track_data["url"]]
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

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Я бот для поиска и скачивания музыки с YouTube.\n\n"
        "🔍 Просто отправь мне название трека или исполнителя, и я найду его для тебя!"
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "🎵 *Как пользоваться ботом:*\n\n"
        "1️⃣ Просто отправь мне название трека или исполнителя\n"
        "2️⃣ Выбери нужный трек из списка\n"
        "3️⃣ Нажми кнопку скачать\n\n"
        "📝 *Доступные команды:*\n"
        "/start - Начать работу с ботом\n"
        "/help - Показать это сообщение\n"
        "/search - Поиск музыки\n"
        "/cancel - Отменить текущее скачивание"
    )
    await message.answer(help_text, parse_mode="Markdown")

@dp.message(Command("search"))
async def cmd_search(message: types.Message):
    if len(message.text.split()) < 2:
        await message.answer("❌ Пожалуйста, укажите запрос для поиска.\nПример: /search Coldplay Yellow")
        return
    
    query = " ".join(message.text.split()[1:])
    await message.answer("🔍 Ищу треки...")
    
    search_id = str(uuid.uuid4())
    tracks = await search_youtube(query, MAX_TRACKS)
    
    if not tracks:
        await message.answer("❌ Ничего не найдено. Попробуйте другой запрос.")
        return
    
    search_results[search_id] = tracks
    keyboard = create_tracks_keyboard(tracks, 0, search_id)
    
    await message.answer(
        f"🎵 Найдено {len(tracks)} треков по запросу '{query}':",
        reply_markup=keyboard
    )

@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message):
    user_id = message.from_user.id
    if user_id in download_tasks:
        for task in download_tasks[user_id].values():
            task.cancel()
        download_tasks[user_id].clear()
        await message.answer("✅ Все текущие загрузки отменены.")
    else:
        await message.answer("❌ Нет активных загрузок для отмены.")

@dp.callback_query(F.data.startswith("d_"))
async def process_download_callback(callback: types.CallbackQuery):
    try:
        track_data = json.loads(base64.b64decode(callback.data[2:]).decode('utf-8'))
        user_id = callback.from_user.id
        
        # Проверяем количество активных загрузок
        active_downloads = sum(1 for task in download_tasks[user_id].values() if not task.done())
        
        if active_downloads >= MAX_PARALLEL_DOWNLOADS:
            # Добавляем в очередь, если достигнут лимит
            download_queues[user_id].append((track_data, callback.message))
            await callback.message.answer(
                f"⏳ Загрузка добавлена в очередь. "
                f"Активных загрузок: {active_downloads}/{MAX_PARALLEL_DOWNLOADS}"
            )
        else:
            # Начинаем загрузку сразу
            status_message = await callback.message.answer(f"⏳ Скачиваю трек: {track_data['title']} - {track_data['channel']}")
            task = asyncio.create_task(
                download_track(user_id, track_data, callback.message, status_message)
            )
            download_tasks[user_id][track_data["url"]] = task
        
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {str(e)}")

@dp.callback_query(F.data.startswith("dl_"))
async def process_download_callback_with_index(callback: types.CallbackQuery):
    try:
        parts = callback.data.split("_")
        track_index = int(parts[1]) - 1
        search_id = parts[2]
        
        if search_id not in search_results:
            await callback.message.answer("❌ Результаты поиска устарели. Пожалуйста, выполните поиск снова.")
            return
        
        tracks = search_results[search_id]
        if 0 <= track_index < len(tracks):
            track_data = tracks[track_index]
            user_id = callback.from_user.id
            
            # Проверяем количество активных загрузок
            active_downloads = sum(1 for task in download_tasks[user_id].values() if not task.done())
            
            if active_downloads >= MAX_PARALLEL_DOWNLOADS:
                # Добавляем в очередь, если достигнут лимит
                download_queues[user_id].append((track_data, callback.message))
                await callback.message.answer(
                    f"⏳ Загрузка добавлена в очередь. "
                    f"Активных загрузок: {active_downloads}/{MAX_PARALLEL_DOWNLOADS}"
                )
            else:
                # Начинаем загрузку сразу
                status_message = await callback.message.answer(f"⏳ Скачиваю трек: {track_data['title']} - {track_data['channel']}")
                task = asyncio.create_task(
                    download_track(user_id, track_data, callback.message, status_message)
                )
                download_tasks[user_id][track_data["url"]] = task
            
        else:
            await callback.message.answer("❌ Трек не найден.")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {str(e)}")

@dp.callback_query(F.data.startswith("page_"))
async def process_page_callback(callback: types.CallbackQuery):
    try:
        parts = callback.data.split("_")
        page = int(parts[1])
        search_id = parts[2]
        
        if search_id not in search_results:
            await callback.answer("❌ Результаты поиска устарели.", show_alert=True)
            return
        
        tracks = search_results[search_id]
        keyboard = create_tracks_keyboard(tracks, page, search_id)
        
        await callback.message.edit_reply_markup(reply_markup=keyboard)
        await callback.answer()
    except Exception as e:
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)

@dp.message()
async def handle_text(message: types.Message):
    if message.text.startswith('/'):
        return
    
    await message.answer("🔍 Ищу треки...")
    
    search_id = str(uuid.uuid4())
    tracks = await search_youtube(message.text, MAX_TRACKS)
    
    if not tracks:
        await message.answer("❌ Ничего не найдено. Попробуйте другой запрос.")
        return
    
    search_results[search_id] = tracks
    keyboard = create_tracks_keyboard(tracks, 0, search_id)
    
    await message.answer(
        f"🎵 Найдено {len(tracks)} треков по запросу '{message.text}':",
        reply_markup=keyboard
    )

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main()) 