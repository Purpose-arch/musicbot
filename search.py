import traceback
import yt_dlp

# Disable debug prints and exception stack traces
import builtins
print = lambda *args, **kwargs: None
traceback.print_exc = lambda *args, **kwargs: None

from config import YDL_AUDIO_OPTS, MIN_SONG_DURATION, MAX_SONG_DURATION
from utils import extract_title_and_artist
from vk_music import get_vk_service

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

async def search_vk(query: str, max_results: int = 50):
    """Searches VK for tracks using vkpymusic, returns list of dicts (title, channel, url, duration, source)."""
    try:
        service = get_vk_service()
        tracks = service.search_songs_by_text(query, count=max_results)
        results = []
        for track in tracks:
            artist = getattr(track, 'artist', 'Unknown Artist')
            title = getattr(track, 'title', 'Unknown Title')
            duration = getattr(track, 'duration', 0)
            url = getattr(track, 'url', None) or getattr(track, 'download_url', None) or ''
            if not url:
                continue
            results.append({
                'title': title,
                'channel': artist,
                'url': url,
                'duration': duration,
                'source': 'vk',
            })
        return results
    except Exception as e:
        print(f"An error occurred during VK search: {e}")
        return []