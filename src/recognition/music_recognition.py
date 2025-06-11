import tempfile
import logging
import asyncio
import os
from typing import Optional, Dict, Any
from shazamio import Shazam
from musicxmatch_api import MusixMatchAPI
import re

# Добавляем импорты для новых библиотек
import lyricsgenius
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

# Универсальная функция для очистки текста песни от лишних заголовков и форматирования
def clean_lyrics(lyrics: str) -> str:
    if not lyrics:
        return ""
        
    # Удаляем информацию о контрибьюторах и название песни с "Lyrics" в начале
    lyrics = re.sub(r"^\d+\s+Contributors?.*?Lyrics", "", lyrics, flags=re.DOTALL)
    
    # Удаляем другие возможные заголовки в начале текста (например, "Текст песни" и т.п.)
    lyrics = re.sub(r"^(Текст песни|Lyrics|Lyrics for).*?\n", "", lyrics, flags=re.IGNORECASE)
    
    # Удаляем информацию о копирайтах и другие метки в конце текста
    if "******* This Lyrics is NOT" in lyrics:
        lyrics = lyrics.split("*******")[0]
    if lyrics.endswith("Embed"):
        lyrics = lyrics.rsplit("\n", 2)[0]
        
    # Очистка от лишних пробелов и пустых строк в начале и конце
    lyrics = lyrics.strip()
    
    return lyrics

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
                return clean_lyrics(lyrics)
        
        return None
    except Exception as e:
        logging.error(f"MusicXMatch error for {artist} - {track}: {e}")
        return None

async def search_genius(artist: str, track: str) -> Optional[str]:
    if not genius or not genius_token:
        logging.warning("Genius API token not set, skipping Genius search")
        return None
    
    try:
        # Ищем песню через API Genius
        search_result = await asyncio.to_thread(genius.search_song, track, artist)
        if search_result:
            # Получаем текст песни и очищаем его
            lyrics = search_result.lyrics
            return clean_lyrics(lyrics)
        return None
    except Exception as e:
        logging.error(f"Genius error for {artist} - {track}: {e}")
        return None

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
                return clean_lyrics(supplement.lyrics.full_lyrics)
        return None
    except Exception as e:
        logging.error(f"Yandex Music error for {artist} - {track}: {e}")
        return None

async def search_lyrics_parallel(artist: str, title: str, timeout: float = 10.0) -> Optional[str]:
    """
    Search for lyrics using multiple services in parallel with timeout.
    Returns the first successful result or None if all searches fail.
    
    Args:
        artist: Artist name
        title: Track title
        timeout: Maximum time to wait for all searches in seconds
    
    Returns:
        Optional[str]: Found lyrics or None if not found
    """
    # Define search functions with their relative priority (lower number = higher priority)
    search_functions = [
        (1, search_genius),        # Usually has high quality lyrics
        (2, search_yandex_music),  # Good for Russian content
        (2, search_musicxmatch),   # Good quality but may have truncated lyrics
    ]
    
    async def _search_with_timeout(priority: int, search_func, artist: str, title: str) -> tuple[int, Optional[str]]:
        """Wrapper for search function with timeout"""
        try:
            lyrics = await asyncio.wait_for(search_func(artist, title), timeout=timeout)
            if lyrics:
                return priority, lyrics
        except asyncio.TimeoutError:
            logging.warning(f"{search_func.__name__} timed out after {timeout}s")
        except Exception as e:
            logging.error(f"{search_func.__name__} error: {e}")
        return priority, None

    # Create tasks for all search functions
    tasks = [
        asyncio.create_task(_search_with_timeout(priority, func, artist, title))
        for priority, func in search_functions
    ]
    
    # Wait for first successful result or all tasks to complete
    while tasks:
        done, tasks = await asyncio.wait(
            tasks,
            return_when=asyncio.FIRST_COMPLETED
        )
        
        # Check completed tasks for results
        for task in done:
            try:
                priority, lyrics = await task
                if lyrics:
                    # Cancel remaining tasks
                    for t in tasks:
                        t.cancel()
                    return lyrics
            except Exception as e:
                logging.error(f"Error processing search result: {e}")
                
    return None
