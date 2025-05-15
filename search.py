import traceback
import yt_dlp

# Disable debug prints and exception stack traces
import builtins
print = lambda *args, **kwargs: None
traceback.print_exc = lambda *args, **kwargs: None

from config import YDL_AUDIO_OPTS, MIN_SONG_DURATION, MAX_SONG_DURATION, VK_ENABLED
from utils import extract_title_and_artist
from vk_music import search_vk_tracks  # Импортируем функцию поиска VK

async def search_soundcloud(query, max_results=50):
    """Searches SoundCloud using yt-dlp"""
    try:
        search_opts = {
            **YDL_AUDIO_OPTS,
            'default_search': 'scsearch',
            'max_downloads': max_results,
            'extract_flat': True,
        }
        with yt_dlp.YoutubeDL(search_opts) as ydl:
            info = ydl.extract_info(f"scsearch{max_results}:{query}", download=False)
            if not info or 'entries' not in info:
                return []

            results = []
            for entry in info['entries'] or []:
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
                results.append({
                    'title': title,
                    'channel': artist,
                    'url': entry.get('webpage_url', entry.get('url', '')),
                    'duration': duration,
                    'source': 'soundcloud',
                })
            return results
    except Exception as e:
        print(f"An error occurred during SoundCloud search: {e}")
        traceback.print_exc()
        return []

async def search_music(query, max_results=50, source=None):
    """Выполняет поиск музыки во всех доступных источниках или в указанном источнике"""
    results = []
    
    # Поиск в SoundCloud по умолчанию, если не указан другой источник
    if source is None or source == 'soundcloud':
        soundcloud_results = await search_soundcloud(query, max_results)
        for track in soundcloud_results:
            if 'source' not in track:
                track['source'] = 'soundcloud'
            results.append(track)
    
    # Поиск в ВКонтакте только если явно указан или не указан конкретный источник
    # И только если включен (VK_ENABLED == True)
    if (source is None or source == 'vk') and VK_ENABLED:
        try:
            print(f"Searching VK Music for: {query}")
            vk_results = await search_vk_tracks(query, max_results)
            if vk_results:
                print(f"Found {len(vk_results)} tracks in VK Music")
                results.extend(vk_results)
            else:
                print("No results found in VK Music")
        except Exception as e:
            print(f"An error occurred during VK search: {e}")
            traceback.print_exc()
    elif source == 'vk' and not VK_ENABLED:
        print("VK Music search requested but VK is disabled (no credentials)")
    
    return results 