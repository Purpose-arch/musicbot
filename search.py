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

async def search_vk(query, max_results=50):
    """Searches VK (VKontakte) for tracks matching query"""
    try:
        from vkpymusic import VkMusic
        from config import VK_TOKEN
        
        print(f"[VK Search Debug] Starting VK search for: '{query}', max_results={max_results}")
        
        if not VK_TOKEN:
            print("[VK Search] Error: VK_TOKEN is not configured")
            return []
            
        print(f"[VK Search Debug] VK_TOKEN is configured: {VK_TOKEN[:5]}... (частично скрыт)")
        
        vk_music = VkMusic(token=VK_TOKEN)
        print(f"[VK Search Debug] VkMusic instance created")
        
        try:
            tracks = vk_music.search(query, count=max_results)
            print(f"[VK Search Debug] Raw search completed, got {len(tracks) if tracks else 0} tracks")
            
            # Выводим информацию о первых 3 треках для отладки
            for i, track in enumerate(tracks[:3]):
                print(f"[VK Search Debug] Track {i+1}: {track.artist} - {track.title} (duration: {track.duration}s)")
                
        except Exception as inner_e:
            print(f"[VK Search Debug] Error during VK API search: {type(inner_e).__name__}: {inner_e}")
            traceback.print_exc()
            return []
        
        results = []
        filtered_count = 0
        for track in tracks:
            duration = track.duration
            if not duration or not (MIN_SONG_DURATION <= duration <= MAX_SONG_DURATION):
                filtered_count += 1
                print(f"[VK Search Debug] Skipping track due to duration: {track.artist} - {track.title} ({duration}s)")
                continue
                
            # Проверяем наличие URL для скачивания
            if not track.download_url:
                print(f"[VK Search Debug] Track has no download_url: {track.artist} - {track.title}")
                continue
                
            results.append({
                'title': track.title,
                'channel': track.artist,
                'url': track.download_url,
                'duration': duration,
                'source': 'vk',
                'vk_track_object': track  # Store the original track object for direct download
            })
        
        print(f"[VK Search Debug] Found {len(results)} valid entries (filtered out {filtered_count}).")
        
        # Выводим информацию о первых 3 результатах
        for i, result in enumerate(results[:3]):
            print(f"[VK Search Debug] Result {i+1}: {result['channel']} - {result['title']} (duration: {result['duration']}s)")
            
        return results
    except ImportError as imp_err:
        print(f"[VK Search Debug] ImportError: {imp_err}")
        print("VK search requires vkpymusic package. Install it with: pip install vkpymusic")
        return []
    except Exception as e:
        print(f"[VK Search Debug] Critical error: {type(e).__name__}: {e}")
        traceback.print_exc()
        return [] 