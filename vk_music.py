import os
import logging
import traceback  # Добавляем для печати стека ошибок
import json
from vkpymusic import TokenReceiver, Service, Account

# Настраиваем логирование
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Устанавливаем уровень DEBUG для всех сообщений

# Для отладки создаем обработчик, который выводит сообщения в консоль
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

def save_token_to_json(token, filename="vk_token.json"):
    """Сохраняет токен ВК в JSON файл"""
    try:
        data = {"token": token}
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f)
        logger.info(f"VK Music: Токен сохранен в файл {filename}")
        return True
    except Exception as e:
        logger.error(f"VK Music: Ошибка при сохранении токена в файл: {e}")
        return False

def load_token_from_json(filename="vk_token.json"):
    """Загружает токен ВК из JSON файла"""
    try:
        if not os.path.exists(filename):
            logger.warning(f"VK Music: Файл с токеном {filename} не найден")
            return None
        
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        token = data.get("token")
        if not token:
            logger.warning("VK Music: В файле отсутствует токен")
            return None
        
        logger.info(f"VK Music: Токен успешно загружен из файла {filename}")
        return token
    except Exception as e:
        logger.error(f"VK Music: Ошибка при загрузке токена из файла: {e}")
        return None

def init_vk_service():
    """Инициализация сервиса VK Music с использованием переменных окружения"""
    # Сначала пробуем загрузить токен из нашего JSON файла
    logger.debug("VK Music DEBUG: Попытка загрузки токена из JSON файла")
    token = load_token_from_json()
    if token:
        try:
            logger.debug(f"VK Music DEBUG: Создаем сервис с токеном из JSON: {token[:10]}***")
            service = Service(token)
            if service:
                logger.info("VK Music: Сервис успешно создан с токеном из JSON")
                return service
        except Exception as e:
            logger.warning(f"VK Music: Не удалось создать сервис с токеном из JSON: {e}")
    
    # Затем пробуем загрузить сервис из конфига библиотеки
    try:
        logger.debug("VK Music DEBUG: Попытка загрузки сервиса из конфига библиотеки")
        service = Service.parse_config()
        if service:
            logger.info("VK Music: сервис успешно инициализирован из конфига библиотеки")
            return service
        else:
            logger.warning("VK Music: сервис не был инициализирован из конфига, хотя исключение не возникло")
    except Exception as e:
        logger.warning(f"VK Music: не удалось загрузить конфиг библиотеки: {e}")
        logger.debug(f"VK Music DEBUG: Трассировка ошибки: {traceback.format_exc()}")
    
    # Если всё не получилось - пробуем авторизоваться заново
    login = os.getenv("VK_LOGIN")
    password = os.getenv("VK_PASSWORD")

    logger.debug(f"VK Music DEBUG: Попытка авторизации с логином: {login[:3] if login and len(login) > 3 else 'None'}*** и паролем: ***")
    
    if not login or not password:
        logger.error("VK Music: Переменные окружения VK_LOGIN и VK_PASSWORD не установлены")
        return None
    
    try:
        # Пробуем прямой подход с Account
        logger.debug("VK Music DEBUG: Пробуем прямой подход авторизации через Account")
        account = Account(login, password)
        logger.debug("VK Music DEBUG: Account создан, пробуем получить токен")
        
        try:
            token = account.get_token()
            logger.debug(f"VK Music DEBUG: Получен токен напрямую: {token[:10]}*** (длина: {len(token)})")
            
            # Сохраняем токен в наш JSON
            save_token_to_json(token)
            
            # Создаем сервис напрямую 
            logger.debug("VK Music DEBUG: Создаем сервис напрямую с полученным токеном")
            service = Service(token)
            logger.info("VK Music: Сервис успешно создан напрямую")
            return service
        except Exception as token_err:
            logger.warning(f"VK Music: Не удалось получить токен напрямую: {token_err}")
            logger.debug(f"VK Music DEBUG: Трассировка ошибки получения токена: {traceback.format_exc()}")
        
        # Если прямой подход не сработал, используем TokenReceiver
        logger.debug("VK Music DEBUG: Переходим к TokenReceiver")
        token_receiver = TokenReceiver(login, password)
        
        logger.debug("VK Music DEBUG: TokenReceiver создан, вызываем auth()")
        auth_result = token_receiver.auth()
        logger.debug(f"VK Music DEBUG: Результат авторизации: {auth_result}")
        
        if auth_result:
            logger.info("VK Music: Токен успешно получен через TokenReceiver")
            
            # Пытаемся получить токен и сохранить в наш JSON
            try:
                token = token_receiver.token
                if token:
                    save_token_to_json(token)
                    service = Service(token)
                    logger.info("VK Music: Сервис успешно создан с токеном из TokenReceiver")
                    return service
            except Exception as e:
                logger.warning(f"VK Music: Не удалось получить токен из TokenReceiver: {e}")
            
            # Если не получилось получить токен напрямую, используем стандартный механизм
            logger.debug("VK Music DEBUG: Сохраняем токен в конфиг библиотеки")
            token_receiver.save_to_config()
            logger.debug("VK Music DEBUG: Загружаем сервис из обновленного конфига библиотеки")
            return Service.parse_config()
        else:
            logger.error("VK Music: Ошибка авторизации через TokenReceiver")
            return None
    except Exception as e:
        logger.error(f"VK Music: Ошибка при авторизации: {str(e)}")
        logger.debug(f"VK Music DEBUG: Трассировка ошибки авторизации: {traceback.format_exc()}")
        return None

async def search_vk_tracks(query, max_results=50):
    """Асинхронная функция для поиска треков ВКонтакте по текстовому запросу"""
    logger.debug(f"VK Music DEBUG: Начат поиск по запросу '{query}', макс. результатов: {max_results}")
    
    service = init_vk_service()
    if not service:
        logger.error("VK Music: Не удалось инициализировать сервис")
        return []
    
    logger.debug("VK Music DEBUG: Сервис инициализирован успешно")
    
    try:
        logger.debug(f"VK Music DEBUG: Вызываем search_songs_by_text с запросом: '{query}'")
        tracks = service.search_songs_by_text(query, count=max_results)
        
        logger.debug(f"VK Music DEBUG: Получено треков: {len(tracks) if tracks else 0}")
        
        if not tracks:
            logger.info(f"VK Music: Треки не найдены по запросу '{query}'")
            return []
        
        results = []
        for i, track in enumerate(tracks):
            try:
                artist = getattr(track, 'artist', 'Неизвестный исполнитель')
                title = getattr(track, 'title', 'Без названия')
                duration = getattr(track, 'duration', 0)
                track_id = getattr(track, 'id', None)
                
                logger.debug(f"VK Music DEBUG: Трек #{i+1}: {artist} - {title}, ID: {track_id}, длительность: {duration}")
                
                # Дамп всех доступных атрибутов трека
                track_attrs = {attr: getattr(track, attr) for attr in dir(track) 
                              if not attr.startswith('_') and not callable(getattr(track, attr))}
                logger.debug(f"VK Music DEBUG: Атрибуты трека: {track_attrs}")
                
                results.append({
                    'title': title,
                    'channel': artist,
                    'url': f"vk_internal:{track_id}" if track_id else f"vk_internal:unknown_{i}",
                    'duration': duration,
                    'vk_track_obj': track,  # Сохраняем оригинальный объект для скачивания
                    'source': 'vk'
                })
            except Exception as e:
                logger.error(f"VK Music: Ошибка обработки трека #{i+1}: {e}")
                logger.debug(f"VK Music DEBUG: Трассировка: {traceback.format_exc()}")
        
        logger.info(f"VK Music: Найдено {len(results)} треков по запросу '{query}'")
        return results
    except Exception as e:
        logger.error(f"VK Music: Ошибка при поиске: {str(e)}")
        logger.debug(f"VK Music DEBUG: Трассировка ошибки поиска: {traceback.format_exc()}")
        return []

async def download_vk_track(track_data, target_path):
    """Асинхронная функция для скачивания трека VK"""
    logger.debug(f"VK Music DEBUG: Начато скачивание трека: {track_data.get('title', 'Unknown')} - {track_data.get('channel', 'Unknown')}")
    
    service = init_vk_service()
    if not service:
        logger.error("VK Music: Не удалось инициализировать сервис для скачивания")
        return False
    
    logger.debug("VK Music DEBUG: Сервис для скачивания инициализирован успешно")
    
    try:
        track = track_data.get('vk_track_obj')
        if not track:
            logger.error("VK Music: Отсутствует объект трека VK")
            return False
        
        logger.debug(f"VK Music DEBUG: Объект трека получен: {type(track)}")
        
        # Вывод информации о треке перед скачиванием
        try:
            track_attrs = {attr: getattr(track, attr) for attr in dir(track) 
                          if not attr.startswith('_') and not callable(getattr(track, attr))}
            logger.debug(f"VK Music DEBUG: Скачивание трека с атрибутами: {track_attrs}")
        except Exception as e:
            logger.debug(f"VK Music DEBUG: Не удалось получить все атрибуты трека: {e}")
        
        # Скачиваем трек
        logger.debug(f"VK Music DEBUG: Вызываем save_music для трека, путь: {target_path}")
        service.save_music(track, target_path)
        
        # Проверяем, что файл действительно скачался
        if os.path.exists(target_path):
            size = os.path.getsize(target_path)
            logger.debug(f"VK Music DEBUG: Файл скачан: {target_path}, размер: {size} байт")
        else:
            logger.error(f"VK Music DEBUG: Файл не был создан: {target_path}")
            return False
            
        logger.info(f"VK Music: Трек успешно скачан: {target_path}")
        return True
    except Exception as e:
        logger.error(f"VK Music: Ошибка при скачивании: {str(e)}")
        logger.debug(f"VK Music DEBUG: Трассировка ошибки скачивания: {traceback.format_exc()}")
        return False
