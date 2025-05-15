import tempfile
import logging
import asyncio
import os
from typing import Optional, Dict, Any
from shazamio import Shazam
from PyLyrics import PyLyrics
import lyricwikia
from musicxmatch_api import MusixMatchAPI
import re

# Добавляем импорты для новых библиотек
import lyricsgenius
import chartlyrics
from yandex_music import Client as YandexMusicClient

# Инициализируем ShazamIO и MusicXMatch
shazam = Shazam()
musicxmatch = MusixMatchAPI()

# Инициализируем Genius API с токеном из переменных окружения
genius_token = os.getenv("GENIUS_TOKEN", "")  # Нужно будет добавить в .env
genius = lyricsgenius.Genius(genius_token) if genius_token else None
if genius:
    # Настройка Genius API
    genius.verbose = False  # Отключаем вывод сообщений
    genius.remove_section_headers = True  # Удаляем заголовки разделов ([Chorus], [Verse] и т.д.)
    genius.skip_non_songs = True  # Пропускаем нетекстовые элементы

# Инициализируем Yandex Music клиент
yandex_token = os.getenv("YANDEX_MUSIC_TOKEN", "")  # Нужно будет добавить в .env
yandex_client = YandexMusicClient(yandex_token).init() if yandex_token else None

async def search_pylyrics(artist: str, track: str) -> Optional[str]:
    try:
        return await asyncio.to_thread(PyLyrics.getLyrics, artist, track)
    except Exception as e:
        logging.error(f"PyLyrics error for {artist} - {track}: {e}")
        return None

async def search_lyricwikia(artist: str, track: str) -> Optional[str]:
    try:
        return await asyncio.to_thread(lyricwikia.get_lyrics, artist, track)
    except Exception as e:
        logging.error(f"LyricWikia error for {artist} - {track}: {e}")
        return None

# Добавляем функцию для поиска текста песни через MusicXMatch
async def search_musicxmatch(artist: str, track: str) -> Optional[str]:
    try:
        # Сначала ищем трек по исполнителю и названию
        search_result = await asyncio.to_thread(musicxmatch.search_tracks, f"{track} {artist}")
        
        # Проверяем, что получили результаты поиска
        if search_result and search_result.get("message", {}).get("body", {}).get("track_list"):
            # Берем первый найденный трек
            first_track = search_result["message"]["body"]["track_list"][0]["track"]
            track_id = first_track["track_id"]
            
            # Получаем текст песни по ID трека
            lyrics_result = await asyncio.to_thread(musicxmatch.get_track_lyrics, track_id)
            
            # Извлекаем текст песни из результата
            if lyrics_result and lyrics_result.get("message", {}).get("body", {}).get("lyrics"):
                lyrics = lyrics_result["message"]["body"]["lyrics"]["lyrics_body"]
                # MusicXMatch часто добавляет свой копирайт в конец, удалим его если есть
                if "******* This Lyrics is NOT" in lyrics:
                    lyrics = lyrics.split("*******")[0].strip()
                return lyrics
        
        return None
    except Exception as e:
        logging.error(f"MusicXMatch error for {artist} - {track}: {e}")
        return None

# Добавляем функцию для поиска текста песни через Genius
async def search_genius(artist: str, track: str) -> Optional[str]:
    if not genius or not genius_token:
        logging.warning("Genius API token not set, skipping Genius search")
        return None
    
    try:
        # Ищем песню через API Genius
        search_result = await asyncio.to_thread(genius.search_song, track, artist)
        if search_result:
            # Получаем текст песни
            lyrics = search_result.lyrics
            # Убираем заголовок вида 'x Contributor (текущий трек) Lyrics' до первого пустого ряда
            lyrics = re.sub(r"^.*?\n\n", "", lyrics, count=1)
            # Genius обычно добавляет свою метку в конец текста
            if lyrics.endswith("Embed"):
                lyrics = lyrics.rsplit("\n", 2)[0].strip()
            return lyrics
        return None
    except Exception as e:
        logging.error(f"Genius error for {artist} - {track}: {e}")
        return None

# Добавляем функцию для поиска текста песни через ChartLyrics (исправленная версия)
async def search_chartlyrics(artist: str, track: str) -> Optional[str]:
    try:
        # Используем правильный синтаксис для chartlyrics
        search_result = await asyncio.to_thread(chartlyrics.search_lyrics, track, artist)
        if search_result and len(search_result) > 0:
            # Берем первый результат
            first_match = search_result[0]
            # Получаем текст песни
            if 'lyrics' in first_match and first_match['lyrics']:
                return first_match['lyrics']
        return None
    except Exception as e:
        logging.error(f"ChartLyrics error for {artist} - {track}: {e}")
        return None

# Добавляем функцию для поиска текста песни через Яндекс.Музыку
async def search_yandex_music(artist: str, track: str) -> Optional[str]:
    if not yandex_client or not yandex_token:
        logging.warning("Yandex Music token not set, skipping Yandex Music search")
        return None
    
    try:
        # Ищем трек по названию и исполнителю
        search_result = await asyncio.to_thread(yandex_client.search, f"{track} {artist}", type_="track")
        if search_result and search_result.tracks and search_result.tracks.results:
            # Берем первый найденный трек
            best_track = search_result.tracks.results[0]
            # Получаем дополнительную информацию о треке, включая текст
            supplement = await asyncio.to_thread(best_track.get_supplement)
            if supplement and supplement.lyrics:
                return supplement.lyrics.full_lyrics
        return None
    except Exception as e:
        logging.error(f"Yandex Music error for {artist} - {track}: {e}")
        return None

# DEPRECATED: Неиспользуемая функция, так как вся логика распознавания и обработки музыки реализована в handlers.py в функции handle_media_recognition
# async def process_music(message, client, chat_id: int, message_id: int):
#     """
#     Загрузка музыкального файла, распознавание трека через ShazamIO,
#     получение текста песни из нескольких источников и отправка
#     текста в виде expandable blockquote.
#     """
#     # Создаем временную директорию для скачивания файла
#     with tempfile.TemporaryDirectory() as tmpdir:
#         path = await message.download_media(tmpdir)
#         if not path:
#             await client.send_message(chat_id, "❌ Не удалось скачать музыкальный файл.", reply_to=message_id)
#             return
# 
#         # Уведомляем пользователя о начале распознавания и сохраняем объект сообщения
#         progress_message = await client.send_message(chat_id, "🔎 Распознаю трек, подождите...", reply_to=message_id)
# 
#         try:
#             # Распознаем трек
#             result = await shazam.recognize(path)
#             track_info = result.get("track", {})
#             title = track_info.get("title") or track_info.get("heading")
#             artist = track_info.get("subtitle")
#             if not title or not artist:
#                 # Редактируем сообщение о прогрессе вместо отправки нового
#                 await progress_message.edit(text="❌ Не удалось распознать трек.")
#                 return
# 
#             # Параллельно ищем текст песни во всех источниках в порядке приоритета
#             lyrics_tasks = [
#                 search_yandex_music(artist, title),
#                 search_musicxmatch(artist, title),
#                 search_genius(artist, title),
#                 search_pylyrics(artist, title),
#                 search_chartlyrics(artist, title),
#                 search_lyricwikia(artist, title),
#             ]
#             # Ожидаем результаты от всех задач
#             lyrics_results = await asyncio.gather(*lyrics_tasks)
# 
#             # Выбираем первый успешный результат
#             lyrics_text = None
#             for txt in lyrics_results:
#                 if txt:
#                     # Формируем текст с найденными словами
#                     lyrics_text = f"🎶 Текст песни '{title}' — {artist}\n\n{txt}"
#                     break
# 
#             if not lyrics_text:
#                 lyrics_text = f"❌ Текст песни '{title}' — {artist} не найден ни в одном источнике."
# 
#             # Редактируем сообщение о прогрессе вместо отправки нового
#             await progress_message.edit(
#                 text=f"<blockquote expandable>{lyrics_text}</blockquote>",
#                 parse_mode="HTML"
#             )
# 
#         except Exception as e:
#             logging.error(f"Error in music recognition or lyrics fetching: {e}", exc_info=True)
#             # Редактируем сообщение о прогрессе вместо отправки нового
#             await progress_message.edit(
#                 text=f"❌ Ошибка распознавания трека или получения текста: {e}"
#             )
