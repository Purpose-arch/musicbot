import traceback
import yt_dlp
import re
import json
import requests
from difflib import SequenceMatcher
import time

# Disable debug prints and exception stack traces
import builtins
print = lambda *args, **kwargs: None
traceback.print_exc = lambda *args, **kwargs: None

from config import YDL_AUDIO_OPTS, MIN_SONG_DURATION, MAX_SONG_DURATION
from utils import extract_title_and_artist

# Константы для улучшенного поиска
CLIENT_ID = "6pDzV3ImgWPohE7UmVQOCCepAaKOgrVL"  # SoundCloud API client_id - базовый, будет обновляться
MIN_PREVIEW_DURATION = 60  # Минимальная длительность в секундах, чтобы трек не считался превью
CLIENT_ID_CACHE_TIME = 0  # Время последнего обновления client_id
CLIENT_ID_CACHE_DURATION = 3600  # Период обновления client_id в секундах (1 час)

def is_similar(str1, str2, threshold=0.8):
    """Проверяет, насколько две строки похожи"""
    if not str1 or not str2:
        return False
    ratio = SequenceMatcher(None, str1.lower(), str2.lower()).ratio()
    return ratio > threshold

def get_fresh_client_id():
    """Получает свежий client_id от SoundCloud, анализируя их веб-страницу"""
    global CLIENT_ID, CLIENT_ID_CACHE_TIME
    
    # Проверяем, нужно ли обновлять client_id
    current_time = int(time.time())
    if current_time - CLIENT_ID_CACHE_TIME < CLIENT_ID_CACHE_DURATION:
        return CLIENT_ID
    
    try:
        # Получаем HTML главной страницы SoundCloud
        response = requests.get("https://soundcloud.com/", timeout=10)
        if response.status_code != 200:
            return CLIENT_ID
            
        # Ищем ссылки на JS файлы
        js_urls = re.findall(r'<script crossorigin src="(https://a-v2\.sndcdn\.com/assets/[^"]+\.js)"', response.text)
        
        # Проверяем каждый JS файл на наличие client_id
        for js_url in js_urls:
            try:
                js_content = requests.get(js_url, timeout=10).text
                client_id_match = re.search(r'client_id:"([a-zA-Z0-9]+)"', js_content)
                if client_id_match:
                    new_client_id = client_id_match.group(1)
                    if new_client_id and len(new_client_id) > 10:  # Проверка, что ID достаточно длинный
                        CLIENT_ID = new_client_id
                        CLIENT_ID_CACHE_TIME = current_time
                        print(f"Updated SoundCloud client_id: {CLIENT_ID}")
                        return CLIENT_ID
            except Exception as e:
                print(f"Error checking JS file for client_id: {e}")
                continue
    
    except Exception as e:
        print(f"Error getting fresh client_id: {e}")
    
    return CLIENT_ID

def try_get_full_track(track_url):
    """Пытается получить полную версию трека по URL через API SoundCloud"""
    try:
        # Обновляем client_id, если нужно
        client_id = get_fresh_client_id()
        
        # Получаем HTML страницы трека
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        headers = {"User-Agent": user_agent}
        html = requests.get(track_url, headers=headers, timeout=10)
        
        # Метод 1: Ищем ID трека в HTML
        track_id_match = re.search(r'soundcloud://sounds:(\d+)"', html.text)
        if not track_id_match:
            track_id_match = re.search(r'"id":(\d+),"kind":"track"', html.text)
        if not track_id_match:
            track_id_match = re.search(r'data-sound-id="(\d+)"', html.text)
        
        # Извлекаем все возможные ID треков из HTML
        all_track_ids = re.findall(r'data-id="(\d+)"', html.text)
        if not all_track_ids:
            all_track_ids = re.findall(r'"id":(\d+)', html.text)
        
        track_id = None
        if track_id_match:
            track_id = track_id_match.group(1)
        elif all_track_ids:
            # Берем первый найденный ID, соответствующий формату трека
            for potential_id in all_track_ids:
                # Проверяем, что ID числовой и разумной длины
                if potential_id.isdigit() and 8 <= len(potential_id) <= 12:
                    track_id = potential_id
                    break
        
        if not track_id:
            # Метод 2: Запрашиваем информацию через yt-dlp
            try:
                ydl_opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'skip_download': True,
                    'extract_flat': False,
                    'dumpjson': True,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(track_url, download=False)
                    if info and 'id' in info:
                        track_id = info['id']
            except Exception as e:
                print(f"Error getting track info via yt-dlp: {e}")
        
        if track_id:
            # Пробуем несколько эндпоинтов API для получения аудиопотока
            endpoints = [
                f"https://api.soundcloud.com/i1/tracks/{track_id}/streams?client_id={client_id}",
                f"https://api-v2.soundcloud.com/tracks/{track_id}/streams?client_id={client_id}",
                f"https://api-v2.soundcloud.com/media/soundcloud:tracks:{track_id}/stream/hls?client_id={client_id}",
            ]
            
            for api_url in endpoints:
                try:
                    response = requests.get(api_url, headers=headers, timeout=10)
                    if response.status_code == 200:
                        data = response.json()
                        # Проверяем наличие полной версии MP3
                        if 'http_mp3_128_url' in data:
                            return {
                                'full_url': data['http_mp3_128_url'],
                                'is_full': True
                            }
                        elif 'hls_mp3_128_url' in data:
                            return {
                                'full_url': data['hls_mp3_128_url'],
                                'is_full': True
                            }
                        # Проверяем другие возможные ключи
                        for key in data:
                            if isinstance(data[key], str) and 'mp3' in key and data[key].startswith('http'):
                                return {
                                    'full_url': data[key],
                                    'is_full': True
                                }
                except Exception as e:
                    print(f"Error with API endpoint {api_url}: {e}")
                    continue
            
            # Метод 3: Использование MusicVerter API для получения MP3 ссылки
            try:
                mv_url = f"https://musicverter.com/api/track?url={track_url}"
                mv_response = requests.get(mv_url, headers=headers, timeout=10)
                if mv_response.status_code == 200:
                    mv_data = mv_response.json()
                    if 'mp3_url' in mv_data and mv_data['mp3_url']:
                        return {
                            'full_url': mv_data['mp3_url'],
                            'is_full': True
                        }
            except Exception as e:
                print(f"Error using MusicVerter API: {e}")
                
            # Метод 4: Использование прямого запроса к yt-dlp для получения URL
            try:
                ydl_opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'skip_download': True,
                    'format': 'bestaudio',
                    'extract_flat': False,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(track_url, download=False)
                    if info and 'url' in info:
                        return {
                            'full_url': info['url'],
                            'is_full': True
                        }
            except Exception as e:
                print(f"Error getting direct URL via yt-dlp: {e}")
    
    except Exception as e:
        print(f"Error getting full track: {e}")
    
    return None

def is_duplicate(track, all_results):
    """Проверяет, является ли трек дубликатом с улучшенным алгоритмом"""
    title = track['title'].lower()
    artist = track['channel'].lower()
    duration = track.get('duration', 0)
    
    # Проверяем на полные совпадения URL
    for existing in all_results:
        if track['url'] == existing['url']:
            return True
    
    # Проверяем похожие треки по названию и исполнителю
    for existing in all_results:
        # Если точное совпадение по названию и исполнителю
        if title == existing['title'].lower() and artist == existing['channel'].lower():
            return True
        
        # Если они очень похожи и разница в длительности небольшая
        if (is_similar(title, existing['title']) and 
            is_similar(artist, existing['channel']) and 
            abs(duration - existing.get('duration', 0)) < 10):
            return True
    
    return False

async def search_soundcloud(query, max_results=50):
    """Searches SoundCloud using yt-dlp with improved search capabilities"""
    try:
        # Обновляем client_id перед поиском
        get_fresh_client_id()
        
        # Увеличиваем max_results чтобы получить больше треков
        max_search_items = 200  # Максимальное значение для API
        
        search_opts = {
            **YDL_AUDIO_OPTS,
            'default_search': 'scsearch',
            'max_downloads': max_search_items,
            'extract_flat': True,
            'ignoreerrors': True,
        }
        
        # Создаем список результатов
        all_results = []
        preview_tracks = []  # Треки, которые могут быть превью
        
        # Выполняем основной поиск
        with yt_dlp.YoutubeDL(search_opts) as ydl:
            info = ydl.extract_info(f"scsearch{max_search_items}:{query}", download=False)
            if info and 'entries' in info:
                for entry in info.get('entries', []) or []:
                    if not entry:
                        continue
                    duration = entry.get('duration', 0)
                    raw_title = entry.get('title', 'Unknown Title')
                    if ' - ' in raw_title:
                        parts = raw_title.split(' - ', 1)
                        artist = parts[0].strip()
                        title = parts[1].strip()
                    else:
                        title = raw_title
                        artist = entry.get('uploader', 'Unknown Artist')
                    if not title:
                        title = raw_title
                    if not artist:
                        artist = entry.get('uploader', 'Unknown Artist')
                    
                    url = entry.get('webpage_url', entry.get('url', ''))
                    
                    track = {
                        'title': title,
                        'channel': artist,
                        'url': url,
                        'duration': duration,
                        'source': 'soundcloud',
                        'is_preview': duration < MIN_PREVIEW_DURATION
                    }
                    
                    # Пытаемся получить полную версию для всех треков
                    # Раньше только для превью, теперь для всех для надежности
                    full_track_info = try_get_full_track(url)
                    if full_track_info:
                        track['direct_url'] = full_track_info['full_url']
                        track['is_preview'] = not full_track_info['is_full']
                    
                    # Проверяем на дубликаты с улучшенным алгоритмом
                    if not is_duplicate(track, all_results):
                        # Сортируем треки на превью и полные версии
                        if track.get('is_preview', False):
                            preview_tracks.append(track)
                        else:
                            all_results.append(track)
        
        # Всегда выполняем дополнительный поиск для увеличения количества результатов
        # Вариант 1: Убираем предлоги и союзы
        stop_words = ['и', 'в', 'на', 'с', 'от', 'к', 'у', 'о', 'по', 'из', 'за', 'для', 'the', 'a', 'an', 'and', 'in', 'on', 'by', 'of', 'to', 'at']
        words = query.split()
        filtered_query = ' '.join([w for w in words if w.lower() not in stop_words])
        
        if filtered_query and filtered_query != query:
            with yt_dlp.YoutubeDL(search_opts) as ydl:
                info = ydl.extract_info(f"scsearch{max_search_items}:{filtered_query}", download=False)
                if info and 'entries' in info:
                    for entry in info.get('entries', []) or []:
                        if not entry:
                            continue
                        duration = entry.get('duration', 0)
                        raw_title = entry.get('title', 'Unknown Title')
                        if ' - ' in raw_title:
                            parts = raw_title.split(' - ', 1)
                            artist = parts[0].strip()
                            title = parts[1].strip()
                        else:
                            title = raw_title
                            artist = entry.get('uploader', 'Unknown Artist')
                        if not title:
                            title = raw_title
                        if not artist:
                            artist = entry.get('uploader', 'Unknown Artist')
                        
                        url = entry.get('webpage_url', entry.get('url', ''))
                        
                        track = {
                            'title': title,
                            'channel': artist,
                            'url': url,
                            'duration': duration,
                            'source': 'soundcloud',
                            'is_preview': duration < MIN_PREVIEW_DURATION
                        }
                        
                        # Пытаемся получить полную версию для всех треков
                        full_track_info = try_get_full_track(url)
                        if full_track_info:
                            track['direct_url'] = full_track_info['full_url']
                            track['is_preview'] = not full_track_info['is_full']
                        
                        # Проверяем на дубликаты с улучшенным алгоритмом
                        if not is_duplicate(track, all_results) and not is_duplicate(track, preview_tracks):
                            # Сортируем треки на превью и полные версии
                            if track.get('is_preview', False):
                                preview_tracks.append(track)
                            else:
                                all_results.append(track)
        
        # Добавляем дополнительные стратегии поиска здесь...
        # (остальные стратегии поиска могут быть добавлены аналогично)
        
        # Сортируем результаты по релевантности к исходному запросу
        query_words = [w.lower() for w in query.split()]
        for result in all_results:
            title_words = [w.lower() for w in result['title'].split()]
            artist_words = [w.lower() for w in result['channel'].split()]
            
            # Вычисляем релевантность
            title_relevance = sum(1 for w in query_words if any(w in tw for tw in title_words))
            artist_relevance = sum(1 for w in query_words if any(w in aw for aw in artist_words))
            result['relevance'] = title_relevance * 2 + artist_relevance  # Заголовок важнее
        
        # Сортируем по релевантности (более релевантные в начале)
        all_results.sort(key=lambda x: x.pop('relevance', 0), reverse=True)
        
        # Добавляем превью треки в конец списка только если нет полной версии
        for preview in preview_tracks:
            if not is_duplicate(preview, all_results):
                all_results.append(preview)
        
        # Возвращаем ВСЕ найденные результаты, без ограничения
        return all_results
    except Exception as e:
        print(f"An error occurred during SoundCloud search: {e}")
        traceback.print_exc()
        return []