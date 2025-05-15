import traceback
import yt_dlp
import re

# Disable debug prints and exception stack traces
import builtins
print = lambda *args, **kwargs: None
traceback.print_exc = lambda *args, **kwargs: None

from config import YDL_AUDIO_OPTS, MIN_SONG_DURATION, MAX_SONG_DURATION
from utils import extract_title_and_artist

async def search_soundcloud(query, max_results=50):
    """Searches SoundCloud using yt-dlp with improved search capabilities"""
    try:
        search_opts = {
            **YDL_AUDIO_OPTS,
            'default_search': 'scsearch',
            'max_downloads': max_results,
            'extract_flat': True,
        }
        
        # Создаем список результатов
        all_results = []
        
        # Выполняем основной поиск
        with yt_dlp.YoutubeDL(search_opts) as ydl:
            info = ydl.extract_info(f"scsearch{max_results}:{query}", download=False)
            if info and 'entries' in info:
                for entry in info.get('entries', []) or []:
                    if not entry:
                        continue
                    duration = entry.get('duration', 0)
                    if not duration or not (MIN_SONG_DURATION <= duration <= MAX_SONG_DURATION):
                        continue
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
                    
                    # Создаем уникальный идентификатор для трека
                    track_id = f"{title.lower()}_{artist.lower()}_{entry.get('url', '')}"
                    
                    # Добавляем трек, если он еще не в списке
                    if not any(track_id == f"{t['title'].lower()}_{t['channel'].lower()}_{t['url']}" for t in all_results):
                        all_results.append({
                            'title': title,
                            'channel': artist,
                            'url': entry.get('webpage_url', entry.get('url', '')),
                            'duration': duration,
                            'source': 'soundcloud',
                        })
        
        # Если мало результатов, пробуем дополнительный поиск с модифицированным запросом
        if len(all_results) < 5 and len(query.split()) > 1:
            # Вариант 1: Убираем предлоги и союзы
            stop_words = ['и', 'в', 'на', 'с', 'от', 'к', 'у', 'о', 'по', 'из', 'за', 'для', 'the', 'a', 'an', 'and', 'in', 'on', 'by', 'of', 'to', 'at']
            words = query.split()
            filtered_query = ' '.join([w for w in words if w.lower() not in stop_words])
            
            if filtered_query and filtered_query != query:
                with yt_dlp.YoutubeDL(search_opts) as ydl:
                    info = ydl.extract_info(f"scsearch{max_results}:{filtered_query}", download=False)
                    if info and 'entries' in info:
                        for entry in info.get('entries', []) or []:
                            if not entry:
                                continue
                            duration = entry.get('duration', 0)
                            if not duration or not (MIN_SONG_DURATION <= duration <= MAX_SONG_DURATION):
                                continue
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
                            
                            # Создаем уникальный идентификатор для трека
                            track_id = f"{title.lower()}_{artist.lower()}_{entry.get('url', '')}"
                            
                            # Добавляем трек, если он еще не в списке
                            if not any(track_id == f"{t['title'].lower()}_{t['channel'].lower()}_{t['url']}" for t in all_results):
                                all_results.append({
                                    'title': title,
                                    'channel': artist,
                                    'url': entry.get('webpage_url', entry.get('url', '')),
                                    'duration': duration,
                                    'source': 'soundcloud',
                                })
            
            # Вариант 2: Ищем по каждому ключевому слову отдельно (если результатов мало)
            if len(all_results) < 10 and len(words) >= 2:
                for word in words:
                    if len(word) >= 4:  # Только длинные слова
                        with yt_dlp.YoutubeDL(search_opts) as ydl:
                            try:
                                info = ydl.extract_info(f"scsearch{max_results//2}:{word}", download=False)
                                if info and 'entries' in info:
                                    for entry in info.get('entries', []) or []:
                                        if not entry:
                                            continue
                                        duration = entry.get('duration', 0)
                                        if not duration or not (MIN_SONG_DURATION <= duration <= MAX_SONG_DURATION):
                                            continue
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
                                        
                                        # Создаем уникальный идентификатор для трека
                                        track_id = f"{title.lower()}_{artist.lower()}_{entry.get('url', '')}"
                                        
                                        # Проверяем, содержит ли название или исполнитель все слова из запроса
                                        full_text = f"{title.lower()} {artist.lower()}"
                                        relevance = sum(1 for w in words if w.lower() in full_text)
                                        
                                        # Добавляем только если трек имеет отношение к запросу
                                        if relevance >= len(words) // 2:
                                            # Добавляем трек, если он еще не в списке
                                            if not any(track_id == f"{t['title'].lower()}_{t['channel'].lower()}_{t['url']}" for t in all_results):
                                                all_results.append({
                                                    'title': title,
                                                    'channel': artist,
                                                    'url': entry.get('webpage_url', entry.get('url', '')),
                                                    'duration': duration,
                                                    'source': 'soundcloud',
                                                })
                            except Exception:
                                continue
        
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
        
        return all_results[:max_results]  # Возвращаем не больше, чем запрошено
    except Exception as e:
        print(f"An error occurred during SoundCloud search: {e}")
        traceback.print_exc()
        return []