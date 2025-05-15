import os
import logging
from vkpymusic import TokenReceiver, Service

logger = logging.getLogger(__name__)

def init_vk_service():
    """Инициализация сервиса VK Music с использованием переменных окружения"""
    try:
        # Пробуем загрузить сервис из конфига
        service = Service.parse_config()
        logger.info("VK Music: сервис успешно инициализирован из конфига")
        return service
    except Exception as e:
        logger.warning(f"VK Music: не удалось загрузить конфиг: {e}")
        
        # Если не получилось - пробуем авторизоваться заново
        login = os.getenv("VK_LOGIN")
        password = os.getenv("VK_PASSWORD")

        if not login or not password:
            logger.error("VK Music: Переменные окружения VK_LOGIN и VK_PASSWORD не установлены")
            return None
        
        try:
            token_receiver = TokenReceiver(login, password)
            
            if token_receiver.auth():
                logger.info("VK Music: Токен успешно получен")
                token_receiver.save_to_config()
                return Service.parse_config()
            else:
                logger.error("VK Music: Ошибка авторизации")
                return None
        except Exception as e:
            logger.error(f"VK Music: Ошибка при авторизации: {str(e)}")
            return None

async def search_vk_tracks(query, max_results=50):
    """Асинхронная функция для поиска треков ВКонтакте по текстовому запросу"""
    service = init_vk_service()
    if not service:
        logger.error("VK Music: Не удалось инициализировать сервис")
        return []
    
    try:
        tracks = service.search_songs_by_text(query, count=max_results)
        
        if not tracks:
            logger.info(f"VK Music: Треки не найдены по запросу '{query}'")
            return []
        
        results = []
        for track in tracks:
            artist = getattr(track, 'artist', 'Неизвестный исполнитель')
            title = getattr(track, 'title', 'Без названия')
            duration = getattr(track, 'duration', 0)
            
            results.append({
                'title': title,
                'channel': artist,
                'url': f"vk_internal:{track.id}",  # Используем специальный префикс для идентификации треков VK
                'duration': duration,
                'vk_track_obj': track,  # Сохраняем оригинальный объект для скачивания
                'source': 'vk'
            })
        
        logger.info(f"VK Music: Найдено {len(results)} треков по запросу '{query}'")
        return results
    except Exception as e:
        logger.error(f"VK Music: Ошибка при поиске: {str(e)}")
        return []

async def download_vk_track(track_data, target_path):
    """Асинхронная функция для скачивания трека VK"""
    service = init_vk_service()
    if not service:
        logger.error("VK Music: Не удалось инициализировать сервис для скачивания")
        return False
    
    try:
        track = track_data.get('vk_track_obj')
        if not track:
            logger.error("VK Music: Отсутствует объект трека VK")
            return False
        
        # Скачиваем трек
        service.save_music(track, target_path)
        logger.info(f"VK Music: Трек успешно скачан: {target_path}")
        return True
    except Exception as e:
        logger.error(f"VK Music: Ошибка при скачивании: {str(e)}")
        return False
