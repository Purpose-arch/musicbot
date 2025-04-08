import os
import requests
import ssl
import json
import tempfile
import base64
import asyncio
import time
import uuid
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from vkpymusic import Service
from mutagen.id3 import ID3, TIT2, TPE1, APIC
from mutagen.mp3 import MP3
import math
from collections import defaultdict


load_dotenv()


bot = Bot(token=os.getenv('BOT_TOKEN'))
dp = Dispatcher()

# Увеличиваем время ожидания для SSL-соединений
session = requests.Session()
session.request = lambda method, url, **kwargs: requests.Session.request(
    session, method, url, timeout=60, **kwargs  # Увеличил таймаут с 30 до 60 секунд
)

TRACKS_PER_PAGE = 10
MAX_TRACKS = 150
MAX_RETRIES = 3  # Максимальное количество повторных попыток

download_tasks = defaultdict(dict)
# Хранилище результатов поиска, индексированных по уникальному ID поиска
search_results = {}

# Функция с повторными попытками для инициализации сервиса
def init_service_with_retry():
    retries = 0
    last_error = None
    
    while retries < MAX_RETRIES:
        try:
            service = Service.parse_config()
            service.session = session
            return service
        except (requests.exceptions.Timeout, ssl.SSLError, ConnectionError) as e:
            retries += 1
            last_error = e
            print(f"Ошибка подключения (попытка {retries}/{MAX_RETRIES}): {str(e)}")
            time.sleep(2)  # Ждем 2 секунды перед повторной попыткой
    
    # Если не удалось инициализировать сервис через конфиг, пробуем через токен
    try:
        vk_token = os.getenv('VK_TOKEN')
        if vk_token:
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"
            service = Service(user_agent, vk_token)
            service.session = session
            return service
    except Exception as e:
        last_error = e
    
    # Если все попытки не удались
    raise Exception(f"не удалось подключиться к vk после {MAX_RETRIES} попыток: {str(last_error)}")

# Инициализация сервиса с повторными попытками
try:
    # Сначала пробуем использовать токен из переменной окружения
    vk_token = os.getenv('VK_TOKEN')
    if vk_token:
        print("Используем VK токен из переменной окружения")
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        service = Service(user_agent, vk_token)
        service.session = session
        
        # Устанавливаем дополнительные заголовки для обхода ограничений
        service.session.headers.update({
            'User-Agent': user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Referer': 'https://vk.com/',
            'Origin': 'https://vk.com',
            'Sec-Ch-Ua': '"Chromium";v="125", "Google Chrome";v="125", "Not.A/Brand";v="24"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"Windows"'
        })
    else:
        # Если токена нет в переменных окружения, пробуем через конфиг файл
        print("Токен не найден в переменных окружения, пробуем через конфиг")
        service = init_service_with_retry()
except Exception as e:
    print(f"Ошибка при инициализации сервиса: {str(e)}")
    raise Exception("токен vk не найден. сначала запусти get_token.py для получения токена или установи переменную окружения VK_TOKEN.")

# Функция для установки ID3 тегов (метаданных) MP3 файла
def set_mp3_metadata(file_path, title, artist):
    try:
        # Пытаемся открыть существующие теги
        try:
            audio = ID3(file_path)
        except:
            # Если тегов нет, создаем новые
            audio = ID3()
        
        # Устанавливаем название трека
        audio["TIT2"] = TIT2(encoding=3, text=title)
        # Устанавливаем исполнителя
        audio["TPE1"] = TPE1(encoding=3, text=artist)
        
        # Сохраняем теги в файл
        audio.save(file_path)
        return True
    except Exception as e:
        print(f"ошибка при установке метаданных: {e}")
        return False

# Функция для создания клавиатуры с треками и кнопками пагинации
def create_tracks_keyboard(tracks, page=0, search_id=""):
    # Всего страниц
    total_pages = math.ceil(len(tracks) / TRACKS_PER_PAGE)
    
    # Вычисляем индексы начала и конца для текущей страницы
    start_idx = page * TRACKS_PER_PAGE
    end_idx = min(start_idx + TRACKS_PER_PAGE, len(tracks))
    
    # Список кнопок
    buttons = []
    
    # Добавляем кнопки для треков текущей страницы
    for i in range(start_idx, end_idx):
        track = tracks[i]
        
        # Собираем данные трека
        track_data = {
            "title": track.title,
            "artist": track.artist,
            "url": track.url,
            "search_id": search_id  # Добавляем ID поиска к данным трека
        }
        
        # Преобразуем в JSON, затем в Base64 для передачи в callback_data
        track_json = json.dumps(track_data, ensure_ascii=False)
        # Максимальный размер callback_data в Telegram - 64 байта, поэтому сделаем проверку
        if len(track_json.encode('utf-8')) > 60:  # Оставляем место для префикса
            # Если данные слишком большие, передаем только индекс и ID поиска
            callback_data = f"dl_{i+1}_{search_id}"
        else:
            callback_data = f"d_{base64.b64encode(track_json.encode('utf-8')).decode('utf-8')}"
        
        buttons.append([
            InlineKeyboardButton(
                text=f"🎧 {track.title} - {track.artist}",
                callback_data=callback_data
            )
        ])
    
    # Добавляем кнопки навигации если нужно
    if total_pages > 1:
        nav_buttons = []
        
        # Кнопка Предыдущая страница
        if page > 0:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"page_{page-1}_{search_id}"
                )
            )
        
        # Индикатор текущей страницы
        nav_buttons.append(
            InlineKeyboardButton(
                text=f"{page+1}/{total_pages}",
                callback_data="info"
            )
        )
        
        # Кнопка Следующая страница
        if page < total_pages - 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"page_{page+1}_{search_id}"
                )
            )
        
        buttons.append(nav_buttons)
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# Асинхронная функция для скачивания трека с повторными попытками
async def download_track(user_id, track_data, callback_message, status_message):
    temp_path = None
    # Создаем и запускаем задачу анимации
    animation_task = asyncio.create_task(animate_loading_dots(status_message, track_data["title"], track_data["artist"]))
    
    try:
        title = track_data["title"]
        artist = track_data["artist"]
        url = track_data["url"]
        
        # Создаем временный файл для сохранения трека
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as temp_file:
            temp_path = temp_file.name
        
        # Скачиваем трек через отдельный поток с повторными попытками
        retry_count = 0
        download_success = False
        
        while retry_count < MAX_RETRIES and not download_success:
            try:
                if retry_count > 0:
                    # Не обновляем сообщение здесь, так как это делает анимация
                    pass
                
                # Добавляем специальные заголовки для скачивания
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
                    'Referer': 'https://vk.com/',
                    'Origin': 'https://vk.com',
                    'Accept': '*/*',
                    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7'
                }
                
                response = await asyncio.to_thread(
                    lambda: requests.get(url, headers=headers, timeout=60)
                )
                
                # Проверка, что ответ не является ошибкой или перенаправлением
                if response.status_code != 200:
                    raise Exception(f"Не удалось скачать трек: код ответа {response.status_code}")
                    
                # Проверка контента ответа
                content_type = response.headers.get('Content-Type', '')
                if 'audio' not in content_type and 'application/octet-stream' not in content_type:
                    if len(response.content) < 10000:  # Если ответ слишком маленький, возможно это сообщение об ошибке
                        raise Exception("Аудио недоступно. Возможно, трек доступен только через вк")
                
                with open(temp_path, 'wb') as f:
                    f.write(response.content)
                
                # Проверка, что файл действительно MP3
                file_size = os.path.getsize(temp_path)
                if file_size < 10000:  # Если файл слишком маленький
                    raise Exception("Скачанный файл слишком мал и, скорее всего, не является аудиозаписью")
                
                download_success = True
            except (requests.exceptions.Timeout, ssl.SSLError, ConnectionError) as e:
                retry_count += 1
                if retry_count >= MAX_RETRIES:
                    raise
                await asyncio.sleep(2)  # Ждем 2 секунды перед повторной попыткой
        
        # Устанавливаем метаданные MP3 через отдельный поток
        await asyncio.to_thread(
            lambda: set_mp3_metadata(temp_path, title, artist)
        )
        
        # Отменяем задачу анимации
        animation_task.cancel()
        
        # Отправляем аудио в чат (без caption)
        audio = FSInputFile(temp_path, filename=f"{artist} - {title}.mp3")
        await callback_message.answer_audio(
            audio=audio,
            title=title,
            performer=artist
        )
        
        # Удаляем сообщение о статусе загрузки
        await status_message.delete()
        
    except Exception as e:
        # Отменяем задачу анимации
        animation_task.cancel()
        
        # Проверяем текст ошибки на наличие специфичных ошибок VK
        error_str = str(e)
        if "аудио недоступно" in error_str.lower() or "слишком мал" in error_str.lower():
            await status_message.edit_text(f"❌ Этот трек недоступен для скачивания через бота.\nДоступно только на сайте vk.com и в официальных приложениях.")
        else:
            # В случае обычной ошибки обновляем сообщение о статусе
            await status_message.edit_text(f"❌ не получилось скачать трек: {error_str}")
    finally:
        # Удаляем временный файл
        if temp_path:
            try:
                os.unlink(temp_path)
            except:
                pass
        
        # Удаляем задачу из списка активных
        if user_id in download_tasks and id(asyncio.current_task()) in download_tasks[user_id]:
            del download_tasks[user_id][id(asyncio.current_task())]

# Функция для анимации точек загрузки
async def animate_loading_dots(message, title, artist, interval=0.5):
    # Создаем анимацию с движущейся точкой ●
    animations = ["● \u2009 \u2009 \u2009", " \u2009● \u2009 \u2009", " \u2009 \u2009● \u2009", " \u2009 \u2009 \u2009●", " \u2009 \u2009● \u2009", " \u2009● \u2009 \u2009"]
    idx = 0
    
    try:
        while True:
            await message.edit_text(f"⏳ скачиваю трек: {title} - {artist} {animations[idx]}")
            idx = (idx + 1) % len(animations)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        # Когда задача отменена, просто выходим из функции
        pass
    except Exception as e:
        print(f"ошибка в анимации: {str(e)}")

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🤠 привет\n"
        "это чистый не нагруженный бот для поиска песен\n"
        "ты уже знаещь как искать поэтому не буду говорить лишнего"
    )

@dp.message(Command("downloads"))
async def cmd_downloads(message: types.Message):
    user_id = message.from_user.id
    
    if user_id not in download_tasks or not download_tasks[user_id]:
        await message.answer("у тебя нет активных загрузок")
        return
    
    downloads_count = len(download_tasks[user_id])
    await message.answer(f"у тебя {downloads_count} активных загрузок")

@dp.message()
async def search_music(message: types.Message):
    try:
        loading_msg = await message.answer("🔍 ищу треки...")
        
        query = message.text
        
        try:
            # Поиск треков с повторными попытками
            tracks = None
            retry_count = 0
            
            while retry_count < MAX_RETRIES and tracks is None:
                try:
                    if retry_count > 0:
                        await loading_msg.edit_text(f"🔍 ищу треки... (попытка {retry_count+1})")
                    
                    tracks = service.search_songs_by_text(query, count=MAX_TRACKS)
                except (requests.exceptions.Timeout, ssl.SSLError, ConnectionError) as e:
                    retry_count += 1
                    if retry_count >= MAX_RETRIES:
                        raise
                    await asyncio.sleep(2)  # Ждем 2 секунды перед повторной попыткой
            
        except (requests.exceptions.Timeout, ssl.SSLError, ConnectionError) as e:
            await loading_msg.edit_text("⏱️ превышено время ожидания соединения с vk, попробуй еще раз позже или используй vpn")
            return
        except Exception as e:
            # Проверяем, является ли ошибка SSL handshake timeout
            error_str = str(e)
            if "_ssl.c:989: The handshake operation timed out" in error_str:
                await loading_msg.edit_text("такое иногда случается\nпопробуй еще раз пожалуста")
            else:
                await loading_msg.edit_text(f"❌ ошибка поиска: {error_str}")
            return
        
        if not tracks:
            await loading_msg.edit_text("😔 ничего не нашлось, попробуй другой запрос")
            return
            
        # Фильтруем треки, проверяя наличие url
        valid_tracks = [track for track in tracks if hasattr(track, 'url') and track.url]
        
        if not valid_tracks:
            await loading_msg.edit_text("😔 нашлись треки, но они недоступны для скачивания.\nПопробуй другой запрос")
            return
            
        # Создаем уникальный идентификатор для этого поиска
        search_id = str(uuid.uuid4())
        
        # Сохраняем результаты поиска с уникальным ID
        search_results[search_id] = {
            "tracks": valid_tracks,
            "query": query,
            "user_id": message.from_user.id
        }
        
        # Получаем клавиатуру с треками первой страницы
        keyboard = create_tracks_keyboard(valid_tracks, page=0, search_id=search_id)
        
        # Формируем заголовок с информацией о количестве найденных треков
        response = f"🎵 нашлось треков: {len(valid_tracks)}"
        
        await loading_msg.edit_text(response, reply_markup=keyboard)
        
    except Exception as e:
        await message.answer(f"❌ что-то пошло не так: {str(e)}")

@dp.callback_query(F.data.startswith("page_"))
async def handle_page_navigation(callback: types.CallbackQuery):
    try:
        # Получаем номер страницы и ID поиска из callback_data
        parts = callback.data.split("_")
        page = int(parts[1])
        search_id = parts[2]
        
        # Получаем треки из кэша по ID поиска
        if search_id not in search_results:
            await callback.answer("❌ информация о треках устарела, сделай новый поиск")
            return
        
        # Проверяем, что результаты принадлежат этому пользователю
        if search_results[search_id]["user_id"] != callback.from_user.id:
            await callback.answer("❌ эти результаты принадлежат другому пользователю")
            return
        
        tracks = search_results[search_id]["tracks"]
        
        # Создаем клавиатуру для новой страницы
        keyboard = create_tracks_keyboard(tracks, page=page, search_id=search_id)
        
        # Формируем заголовок с информацией о количестве найденных треков
        response = f"🎵 нашлись треки:"
        
        # Обновляем сообщение с новой клавиатурой
        await callback.message.edit_text(response, reply_markup=keyboard)
        await callback.answer()
        
    except Exception as e:
        await callback.answer(f"❌ ошибка: {str(e)}")

@dp.callback_query(F.data == "info")
async def handle_info_button(callback: types.CallbackQuery):
    # Просто отображаем информационное сообщение при нажатии на индикатор страницы
    await callback.answer("текущая страница / всего страниц")

@dp.callback_query(F.data.startswith("d_"))
async def download_track_by_data(callback: types.CallbackQuery):
    try:
        user_id = callback.from_user.id
        
        # Отображаем сообщение, но не блокируем пользователя
        await callback.answer("⏳ скачиваю трек...")
        
        # Декодируем данные трека из callback_data
        encoded_data = callback.data[2:]  # Убираем префикс "d_"
        track_data = json.loads(base64.b64decode(encoded_data).decode('utf-8'))
        
        title = track_data["title"]
        artist = track_data["artist"]
        
        # Проверяем, есть ли search_id в данных трека
        if "search_id" in track_data and track_data["search_id"] not in search_results:
            await callback.message.answer("❌ информация о треке устарела, сделай новый поиск")
            return
        
        # Отправляем начальное сообщение о загрузке
        status_message = await callback.message.answer(f"⏳ скачиваю трек: {title} - {artist}")
        
        # Создаем асинхронную задачу для скачивания трека
        task = asyncio.create_task(
            download_track(user_id, track_data, callback.message, status_message)
        )
        
        # Сохраняем задачу в список активных скачиваний
        download_tasks[user_id][id(task)] = task
        
    except Exception as e:
        await callback.message.answer(f"❌ что-то пошло не так: {str(e)}")

@dp.callback_query(F.data.startswith("dl_"))
async def download_track_by_index(callback: types.CallbackQuery):
    try:
        user_id = callback.from_user.id
        
        # Отображаем сообщение, но не блокируем пользователя
        await callback.answer("⏳ скачиваю трек...")
        
        # Парсим данные из callback_data
        parts = callback.data.split("_")
        track_index = int(parts[1]) - 1
        search_id = parts[2]  # Получаем ID поиска
        
        # Проверяем, есть ли треки в кэше по ID поиска
        if search_id not in search_results:
            await callback.message.answer("❌ информация о треке устарела, сделай новый поиск")
            return
        
        # Проверяем, что результаты принадлежат этому пользователю
        if search_results[search_id]["user_id"] != user_id:
            await callback.answer("❌ эти результаты принадлежат другому пользователю")
            return
        
        tracks = search_results[search_id]["tracks"]
        
        if track_index < 0 or track_index >= len(tracks):
            await callback.message.answer("❌ что-то не так с индексом трека, сделай новый поиск")
            return
        
        track = tracks[track_index]
        title = track.title
        artist = track.artist
        
        # Создаем данные трека для скачивания
        track_data = {
            "title": title,
            "artist": artist,
            "url": track.url,
            "search_id": search_id
        }
        
        # Отправляем начальное сообщение о загрузке
        status_message = await callback.message.answer(f"⏳ скачиваю трек: {title} - {artist}")
        
        # Создаем асинхронную задачу для скачивания трека
        task = asyncio.create_task(
            download_track(user_id, track_data, callback.message, status_message)
        )
        
        # Сохраняем задачу в список активных скачиваний
        download_tasks[user_id][id(task)] = task
        
    except Exception as e:
        await callback.message.answer(f"❌ что-то пошло не так: {str(e)}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main()) 