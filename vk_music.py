import os
import sys
import re
from pathlib import Path
from vkpymusic import TokenReceiver, Service
from config import VK_LOGIN, VK_PASSWORD

# Получаем корневую директорию проекта
ROOT_DIR = Path(__file__).parent.absolute()

def get_vk_service():
    """Возвращает авторизованный сервис VK, используя конфиг из корневой директории проекта."""
    config_path = ROOT_DIR / "config_vk.ini"
    
    try:
        # Пытаемся использовать существующий конфиг в корне проекта
        service = Service.parse_config(config_path)
        return service
    except Exception as e:
        # Если не получилось, авторизуемся через переменные окружения
        if not VK_LOGIN or not VK_PASSWORD:
            raise RuntimeError("VK_LOGIN и VK_PASSWORD должны быть заданы в .env")
        
        token_receiver = TokenReceiver(VK_LOGIN, VK_PASSWORD)
        if token_receiver.auth():
            # Сохраняем конфиг в корневую директорию проекта
            token_receiver.save_to_config(config_path)
            return Service.parse_config(config_path)
        else:
            raise RuntimeError("Ошибка авторизации VK")

def search_tracks(query, count=10):
    """
    Поиск треков по запросу
    
    Args:
        query (str): Поисковый запрос
        count (int, optional): Количество треков для поиска. По умолчанию 10.
        
    Returns:
        list: Список найденных треков
    """
    service = get_vk_service()
    return service.search_songs_by_text(query, count=count)

def download_track(track, download_dir=None):
    """
    Скачивание трека
    
    Args:
        track: Объект трека
        download_dir (str, optional): Путь к директории для скачивания. 
                                     По умолчанию используется директория vk_music_downloads в корне проекта.
    
    Returns:
        str: Путь к скачанному файлу
    """
    service = get_vk_service()
    
    # Если директория не указана, используем директорию в корне проекта
    if download_dir is None:
        download_dir = ROOT_DIR / "vk_music_downloads"
    
    # Создаем папку для загрузок, если её нет
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)
    
    artist = getattr(track, 'artist', 'Unknown')
    title = getattr(track, 'title', 'Unknown')
    
    # Формируем имя файла
    filename = f"{artist} - {title}.mp3"
    filename = "".join(c for c in filename if c.isalnum() or c in ' -_.')
    filepath = os.path.join(download_dir, filename)
    
    # Скачиваем файл
    service.save_music(track, filepath)
    return filepath

def parse_playlist_url(url):
    """
    Парсинг URL плейлиста или альбома ВКонтакте
    
    Args:
        url (str): URL плейлиста или альбома ВКонтакте
    
    Returns:
        tuple: (owner_id, playlist_id, access_hash)
    """
    # Проверка основных условий
    if not url:
        raise ValueError("Пустой URL")
    
    # Определяем тип ссылки
    is_album = "vk.com/music/album" in url
    is_playlist = "vk.com/music/playlist" in url
    
    if not (is_album or is_playlist):
        raise ValueError(f"URL {url} не является ссылкой на плейлист или альбом ВКонтакте")
    
    # Извлекаем части URL
    try:
        # Получаем последнюю часть URL
        last_part = url.split('/')[-1]
        
        # Извлекаем части разделенные '_'
        parts = last_part.split('_')
        
        if len(parts) >= 3:
            # Если формат: album/-2000086173_23086173_hash
            if '/' in parts[0]:
                type_and_owner = parts[0].split('/')
                if len(type_and_owner) == 2:
                    owner_id = int(type_and_owner[1])
                    playlist_id = int(parts[1])
                    access_hash = parts[2]
                    return owner_id, playlist_id, access_hash
            
            # Формат: -2000086173_23086173_hash
            owner_id = int(parts[0])
            playlist_id = int(parts[1])
            access_hash = parts[2]
            return owner_id, playlist_id, access_hash
        else:
            raise ValueError(f"Неправильный формат URL: {url}")
    except Exception as e:
        raise ValueError(f"Ошибка при парсинге URL {url}: {str(e)}")

def get_playlist_tracks(playlist_url, count=100):
    """
    Получение треков из плейлиста ВКонтакте по URL
    
    Args:
        playlist_url (str): URL плейлиста ВКонтакте
        count (int, optional): Максимальное количество треков для получения. По умолчанию 100.
    
    Returns:
        list: Список треков из плейлиста
    """
    service = get_vk_service()
    
    # Парсим URL плейлиста
    owner_id, playlist_id, access_hash = parse_playlist_url(playlist_url)
    
    try:
        # Используем встроенный метод для получения треков из плейлиста
        # get_songs_by_playlist_id доступен в библиотеке vkpymusic
        tracks = service.get_songs_by_playlist_id(owner_id, playlist_id, access_key=access_hash, count=count)
        
        return tracks
    except Exception as e:
        raise RuntimeError(f"Ошибка при получении треков из плейлиста: {str(e)}")

def download_playlist(playlist_url, download_dir=None):
    """
    Скачивание плейлиста ВКонтакте
    
    Args:
        playlist_url (str): URL плейлиста ВКонтакте
        download_dir (str, optional): Путь к директории для скачивания. 
                                     По умолчанию используется директория vk_playlist_downloads в корне проекта.
    
    Returns:
        list: Список путей к скачанным файлам
    """
    # Получаем треки из плейлиста
    tracks = get_playlist_tracks(playlist_url)
    
    # Если директория не указана, используем директорию в корне проекта
    if download_dir is None:
        # Получаем имя плейлиста из его ID
        owner_id, playlist_id, _ = parse_playlist_url(playlist_url)
        playlist_dir_name = f"vk_playlist_{owner_id}_{playlist_id}"
        download_dir = ROOT_DIR / playlist_dir_name
    
    # Создаем папку для загрузок, если её нет
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)
    
    # Скачиваем каждый трек
    downloaded_files = []
    service = get_vk_service()
    
    for i, track in enumerate(tracks, 1):
        try:
            artist = getattr(track, 'artist', 'Unknown')
            title = getattr(track, 'title', 'Unknown')
            
            # Формируем имя файла
            filename = f"{i:03d}. {artist} - {title}.mp3"
            filename = "".join(c for c in filename if c.isalnum() or c in ' -_.')
            filepath = os.path.join(download_dir, filename)
            
            # Скачиваем файл
            service.save_music(track, filepath)
            downloaded_files.append(filepath)
            
            print(f"Скачан трек {i}/{len(tracks)}: {artist} - {title}")
            
        except Exception as e:
            print(f"Ошибка при скачивании трека {getattr(track, 'artist', '')} - {getattr(track, 'title', '')}: {str(e)}")
    
    return downloaded_files
