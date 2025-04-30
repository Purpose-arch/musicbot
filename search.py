import traceback
import yt_dlp
from config import YDL_AUDIO_OPTS, MIN_SONG_DURATION, MAX_SONG_DURATION
from utils import extract_title_and_artist

async def search_youtube(query, max_results=50):
    """Searches YouTube for tracks matching query"""
    try:
        search_opts = {
            **YDL_AUDIO_OPTS,
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
                if not entry:
                    continue
                duration = entry.get('duration', 0)
                if not duration or not (MIN_SONG_DURATION <= duration <= MAX_SONG_DURATION):
                    continue
                title, artist = extract_title_and_artist(entry.get('title', 'Unknown Title'))
                if artist == "Unknown Artist":
                    artist = entry.get('uploader', 'Unknown Artist')
                results.append({
                    'title': title,
                    'channel': artist,
                    'url': entry.get('url', ''),
                    'duration': duration,
                })
            return results
    except Exception as e:
        print(f"An error occurred during YouTube search: {e}")
        traceback.print_exc()
        return []

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
            print(f"[SoundCloud Search Debug] Querying: scsearch{max_results}:{query}")
            info = ydl.extract_info(f"scsearch{max_results}:{query}", download=False)
            print(f"[SoundCloud Search Debug] Raw info: {info}")
            if not info or 'entries' not in info:
                print("[SoundCloud Search Debug] No entries found.")
                return []

            results = []
            for entry in info['entries'] or []:
                if not entry:
                    continue
                duration = entry.get('duration', 0)
                if not duration or not (MIN_SONG_DURATION <= duration <= MAX_SONG_DURATION):
                    print(f"[SoundCloud Search Debug] Skipping track due to duration: {duration}")
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
            print(f"[SoundCloud Search Debug] Found {len(results)} valid entries.")
            return results
    except Exception as e:
        print(f"An error occurred during SoundCloud search: {e}")
        traceback.print_exc()
        return []

async def search_bandcamp(query, max_results=50):
    """Searches Bandcamp using yt-dlp"""
    try:
        search_opts = {
            **YDL_AUDIO_OPTS,
            'default_search': 'bcsearch',
            'max_downloads': max_results,
            'extract_flat': True,
        }
        with yt_dlp.YoutubeDL(search_opts) as ydl:
            print(f"[Bandcamp Search Debug] Querying: bcsearch{max_results}:{query}")
            info = ydl.extract_info(f"bcsearch{max_results}:{query}", download=False)
            print(f"[Bandcamp Search Debug] Raw info: {info}")
            if not info or 'entries' not in info:
                print("[Bandcamp Search Debug] No entries found.")
                return []

            results = []
            for entry in info['entries'] or []:
                if not entry:
                    continue
                duration = entry.get('duration', 0)
                if not duration or not (MIN_SONG_DURATION <= duration <= MAX_SONG_DURATION):
                    print(f"[Bandcamp Search Debug] Skipping track due to duration: {duration}")
                    continue
                title = entry.get('title', 'Unknown Title')
                artist = entry.get('artist') or entry.get('uploader', 'Unknown Artist')
                if ' - ' in title and not entry.get('artist'):
                    parts = title.split(' - ', 1)
                    potential_artist, potential_title = parts[0].strip(), parts[1].strip()
                    if len(potential_artist) < len(potential_title):
                        artist, title = potential_artist, potential_title
                if artist and title.startswith(f"{artist} - "):
                    title = title[len(artist) + 3:]
                results.append({
                    'title': title,
                    'channel': artist,
                    'url': entry.get('url', ''),
                    'duration': duration,
                    'source': 'bandcamp',
                })
            print(f"[Bandcamp Search Debug] Found {len(results)} valid entries.")
            return results
    except Exception as e:
        print(f"An error occurred during Bandcamp search: {e}")
        traceback.print_exc()
        return [] 