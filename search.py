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

async def search_vk(query, max_results=50):
    """Searches VK (VKontakte) for tracks matching query"""
    try:
        from vkpymusic import VkMusic
        from config import VK_TOKEN
        
        if not VK_TOKEN:
            print("[VK Search] Error: VK_TOKEN is not configured")
            return []
            
        vk_music = VkMusic(token=VK_TOKEN)
        tracks = vk_music.search(query, count=max_results)
        
        results = []
        for track in tracks:
            duration = track.duration
            if not duration or not (MIN_SONG_DURATION <= duration <= MAX_SONG_DURATION):
                continue
                
            results.append({
                'title': track.title,
                'channel': track.artist,
                'url': track.download_url,
                'duration': duration,
                'source': 'vk',
                'vk_track_object': track  # Store the original track object for direct download
            })
        
        print(f"[VK Search Debug] Found {len(results)} valid entries.")
        return results
    except ImportError:
        print("VK search requires vkpymusic package. Install it with: pip install vkpymusic")
        return []
    except Exception as e:
        print(f"An error occurred during VK search: {e}")
        traceback.print_exc()
        return [] 