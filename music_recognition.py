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

async def search_pylyrics(artist: str, track: str) -> Optional[str]:
    try:
        lyrics = await asyncio.to_thread(PyLyrics.getLyrics, artist, track)
        return clean_lyrics(lyrics) if lyrics else None
    except Exception as e:
        logging.error(f"PyLyrics error for {artist} - {track}: {e}")
        return None

async def search_lyricwikia(artist: str, track: str) -> Optional[str]:
    try:
        lyrics = await asyncio.to_thread(lyricwikia.get_lyrics, artist, track)
        return clean_lyrics(lyrics) if lyrics else None
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
                return clean_lyrics(lyrics)
        
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
            # Получаем текст песни и очищаем его
            lyrics = search_result.lyrics
            return clean_lyrics(lyrics)
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
                return clean_lyrics(first_match['lyrics'])
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
                return clean_lyrics(supplement.lyrics.full_lyrics)
        return None
    except Exception as e:
        logging.error(f"Yandex Music error for {artist} - {track}: {e}")
        return None
