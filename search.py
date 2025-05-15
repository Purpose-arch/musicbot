import traceback
import yt_dlp
import re
import json
import requests
from difflib import SequenceMatcher

# Disable debug prints and exception stack traces
import builtins
print = lambda *args, **kwargs: None
traceback.print_exc = lambda *args, **kwargs: None

from config import YDL_AUDIO_OPTS, MIN_SONG_DURATION, MAX_SONG_DURATION
from utils import extract_title_and_artist

# Константы для улучшенного поиска
CLIENT_ID = "6pDzV3ImgWPohE7UmVQOCCepAaKOgrVL"  # SoundCloud API client_id
MIN_PREVIEW_DURATION = 60  # Минимальная длительность в секундах, чтобы трек не считался превью

def is_similar(str1, str2, threshold=0.8):
    """Проверяет, насколько две строки похожи"""
    if not str1 or not str2:
        return False
    ratio = SequenceMatcher(None, str1.lower(), str2.lower()).ratio()
    return ratio > threshold

def try_get_full_track(track_url):
    """Пытается получить полную версию трека по URL через API SoundCloud"""
    try:
        # Получаем HTML страницы трека
        html = requests.get(track_url, timeout=5)
        # Ищем ID трека в HTML
        track_id_match = re.search(r'soundcloud://sounds:(\d+)"', html.text)
        if not track_id_match:
            track_id_match = re.search(r'"id":(\d+),"kind":"track"', html.text)
            
        if track_id_match:
            track_id = track_id_match.group(1)
            # Получаем данные о потоке через API
            api_url = f"https://api.soundcloud.com/i1/tracks/{track_id}/streams?client_id={CLIENT_ID}"
            response = requests.get(api_url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                # Проверяем наличие полной версии MP3
                if 'http_mp3_128_url' in data:
                    return {
                        'full_url': data['http_mp3_128_url'],
                        'is_full': True
                    }
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
                    
                    # Пытаемся получить полную версию для потенциальных превью
                    if track['is_preview']:
                        full_track_info = try_get_full_track(url)
                        if full_track_info:
                            track['direct_url'] = full_track_info['full_url']
                            track['is_preview'] = not full_track_info['is_full']
                    
                    # Проверяем на дубликаты с улучшенным алгоритмом
                    if not is_duplicate(track, all_results):
                        # Сортируем треки на превью и полные версии
                        if track['is_preview']:
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
                        
                        # Пытаемся получить полную версию для потенциальных превью
                        if track['is_preview']:
                            full_track_info = try_get_full_track(url)
                            if full_track_info:
                                track['direct_url'] = full_track_info['full_url']
                                track['is_preview'] = not full_track_info['is_full']
                        
                        # Проверяем на дубликаты с улучшенным алгоритмом
                        if not is_duplicate(track, all_results) and not is_duplicate(track, preview_tracks):
                            # Сортируем треки на превью и полные версии
                            if track['is_preview']:
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