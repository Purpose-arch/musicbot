import os
from vkpymusic import TokenReceiver, Service
from config import VK_LOGIN, VK_PASSWORD

def login_vk():
    """Функция для авторизации и получения токена"""
    login = input("Введите логин ВК: ")
    password = input("Введите пароль: ")
    
    token_receiver = TokenReceiver(login, password)
    
    if token_receiver.auth():
        print("Токен успешно получен!")
        token_receiver.save_to_config()
        return True
    else:
        print("Ошибка авторизации")
        return False

def get_vk_service():
    """Возвращает авторизованный сервис VK, используя логин и пароль из env."""
    try:
        service = Service.parse_config()
        return service
    except Exception:
        # Авторизация через переменные окружения
        if not VK_LOGIN or not VK_PASSWORD:
            raise RuntimeError("VK_LOGIN и VK_PASSWORD должны быть заданы в .env")
        token_receiver = TokenReceiver(VK_LOGIN, VK_PASSWORD)
        if token_receiver.auth():
            token_receiver.save_to_config()
            return Service.parse_config()
        else:
            raise RuntimeError("Ошибка авторизации VK")

def search_and_download_tracks():
    """Функция для поиска и скачивания треков"""
    # Создаем сервис из сохраненного конфига или авторизуемся
    try:
        service = get_vk_service()
    except:
        if not login_vk():
            return
        service = get_vk_service()
    
    # Создаем папку для загрузок
    download_dir = "vk_music_downloads"
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)
    
    while True:
        # Запрашиваем поисковый запрос
        query = input("\nВведите поисковый запрос (или 'выход' для завершения): ")
        
        if query.lower() in ['выход', 'exit', 'quit', 'q']:
            break
        
        # Ищем треки
        try:
            print(f"Поиск треков по запросу: {query}")
            tracks = service.search_songs_by_text(query, count=10)
            
            if not tracks:
                print("Треки не найдены")
                continue
            
            # Выводим найденные треки
            print(f"Найдено {len(tracks)} треков:")
            for i, track in enumerate(tracks, 1):
                artist = getattr(track, 'artist', 'Неизвестный исполнитель')
                title = getattr(track, 'title', 'Без названия')
                duration = getattr(track, 'duration', 0)
                min_sec = f"{duration // 60}:{duration % 60:02d}"
                print(f"{i}. {artist} - {title} [{min_sec}]")
            
            # Выбор треков для скачивания
            choice = input("\nВыберите номера треков для скачивания (через запятую, 'all' - скачать все): ")
            
            if choice.lower() == 'all':
                tracks_to_download = tracks
            else:
                try:
                    indices = [int(idx.strip()) - 1 for idx in choice.split(',') if idx.strip()]
                    tracks_to_download = [tracks[i] for i in indices if 0 <= i < len(tracks)]
                except:
                    print("Некорректный ввод. Скачивание отменено.")
                    continue
            
            # Скачиваем выбранные треки
            for track in tracks_to_download:
                artist = getattr(track, 'artist', 'Unknown')
                title = getattr(track, 'title', 'Unknown')
                print(f"Скачивание: {artist} - {title}")
                
                try:
                    # Формируем имя файла
                    filename = f"{artist} - {title}.mp3"
                    filename = "".join(c for c in filename if c.isalnum() or c in ' -_.')
                    filepath = os.path.join(download_dir, filename)
                    
                    # Скачиваем файл напрямую с помощью save_music
                    service.save_music(track, filepath)
                    print(f"Скачано: {filepath}")
                except Exception as e:
                    print(f"Не удалось скачать трек: {artist} - {title}. Ошибка: {str(e)}")
            
        except Exception as e:
            print(f"Произошла ошибка: {str(e)}")

if __name__ == "__main__":
    print("Программа для поиска и скачивания музыки ВКонтакте")
    search_and_download_tracks()
