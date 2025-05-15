import os
import sys
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
