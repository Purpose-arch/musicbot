import os
import uuid
import time
import asyncio
import aiohttp
import traceback
import socket
from typing import Optional, Dict, Any, Tuple, List, Callable
from aiohttp import ClientTimeout, ClientSession
from aiohttp_socks import ProxyConnector, ProxyType

class AsyncCobaltDownloader:
    """
    Асинхронный модуль для скачивания медиа через Cobalt API
    """
    
    def __init__(self, 
                 api_url: str = "https://co.itsv1eds.ru",
                 api_key: str = "",
                 temp_dir: str = "temp_downloads",
                 auto_fallback: bool = True,
                 session_timeout: int = 60):
        """
        Инициализация асинхронного загрузчика
        
        Args:
            api_url: URL Cobalt API
            api_key: API ключ (если требуется)
            temp_dir: Папка для временных файлов
            auto_fallback: Автоматическое переключение на резервные API
            session_timeout: Таймаут для HTTP сессии
        """
        self.api_url = api_url.rstrip('/')
        self.api_key = api_key
        self.temp_dir = temp_dir
        self.auto_fallback = auto_fallback
        self.session_timeout = session_timeout
        
        # Список резервных API
        self.fallback_apis = [
            "https://co.itsv1eds.ru",
            "https://co.eepy.today", 
            "https://co.otomir23.me",
            "https://cobalt.255x.ru"
        ]
        
        # Настройки качества
        self.video_quality_options = ["144","240","360","480","720","1080","1440","2160","4320","max"]
        self.audio_bitrate_options = ["64","96","128","192","256","320"]
        
        # Создание временной папки
        self._create_temp_dir()
        
        # Настройки по умолчанию
        self.settings = {
            "video_quality": "1080",
            "audio_bitrate": "320", 
            "disable_metadata": False,
            "proxy_type": 0,  # 0-None, 1-HTTP, 2-HTTPS, 3-SOCKS5, 4-MTProto
            "proxy_url": "",
            "proxy_username": "",
            "proxy_password": ""
        }
        
        self._session = None
    
    def _create_temp_dir(self):
        """Создание временной папки для загрузок"""
        try:
            if not os.path.exists(self.temp_dir):
                os.makedirs(self.temp_dir)
        except Exception as e:
            print(f"Ошибка создания временной папки: {e}")
    
    def set_video_quality(self, quality: str):
        """Установка качества видео"""
        if quality in self.video_quality_options:
            self.settings["video_quality"] = quality
        else:
            print(f"Неподдерживаемое качество: {quality}")
    
    def set_audio_bitrate(self, bitrate: str):
        """Установка битрейта аудио"""
        if bitrate in self.audio_bitrate_options:
            self.settings["audio_bitrate"] = bitrate
        else:
            print(f"Неподдерживаемый битрейт: {bitrate}")
    
    def set_proxy(self, proxy_type: int, url: str, username: str = "", password: str = ""):
        """
        Настройка прокси
        
        Args:
            proxy_type: 0-None, 1-HTTP, 2-HTTPS, 3-SOCKS5, 4-MTProto
            url: URL прокси в формате host:port
            username: Логин для прокси
            password: Пароль для прокси
        """
        self.settings.update({
            "proxy_type": proxy_type,
            "proxy_url": url,
            "proxy_username": username,
            "proxy_password": password
        })
    
    def _get_proxy_connector(self) -> Optional[ProxyConnector]:
        """Получение прокси коннектора для aiohttp"""
        proxy_type = self.settings["proxy_type"]
        url = self.settings["proxy_url"].strip()
        username = self.settings["proxy_username"].strip()
        password = self.settings["proxy_password"].strip()
        
        if not url or proxy_type == 0:
            return None
        
        try:
            if proxy_type == 1:  # HTTP
                proxy_type_enum = ProxyType.HTTP
            elif proxy_type == 2:  # HTTPS
                proxy_type_enum = ProxyType.HTTP  # aiohttp-socks использует HTTP для HTTPS прокси
            elif proxy_type == 3:  # SOCKS5
                proxy_type_enum = ProxyType.SOCKS5
            else:
                return None  # MTProto не поддерживается через aiohttp-socks
            
            clean_url = url.split("://", 1)[1] if "://" in url else url
            
            if ":" not in clean_url:
                return None
            
            host, port_str = clean_url.split(":", 1)
            port = int(port_str)
            
            if username and password:
                return ProxyConnector(
                    proxy_type=proxy_type_enum,
                    host=host,
                    port=port,
                    username=username,
                    password=password
                )
            else:
                return ProxyConnector(
                    proxy_type=proxy_type_enum,
                    host=host,
                    port=port
                )
                
        except Exception as e:
            print(f"Ошибка настройки прокси: {e}")
            return None
    
    async def _test_proxy_connection(self, session: ClientSession) -> bool:
        """Асинхронный тест соединения через прокси"""
        try:
            test_url = "http://httpbin.org/ip"
            async with session.get(test_url, timeout=ClientTimeout(total=10)) as response:
                if response.status == 200:
                    result = await response.json()
                    print(f"Прокси работает: {result}")
                    return True
                else:
                    print(f"Прокси тест не пройден, статус: {response.status}")
                    return False
                    
        except Exception as e:
            print(f"Ошибка теста прокси: {e}")
            return False
    
    async def _get_session(self) -> ClientSession:
        """Получение HTTP сессии с настройками прокси"""
        if self._session is None or self._session.closed:
            connector = self._get_proxy_connector()
            timeout = ClientTimeout(total=self.session_timeout)
            
            if connector:
                self._session = ClientSession(
                    connector=connector,
                    timeout=timeout
                )
            else:
                self._session = ClientSession(timeout=timeout)
        
        return self._session
    
    async def _try_download_with_api(self, api_url: str, video_url: str, 
                                   payload: Dict[str, Any], headers: Dict[str, str]) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
        """Асинхронная попытка скачивания через конкретный API"""
        try:
            session = await self._get_session()
            
            async with session.post(f"{api_url}/", json=payload, headers=headers) as resp:
                resp.raise_for_status()
                data = await resp.json()
                
                status = data.get("status")
                if status == "error":
                    code = data.get("error", {}).get("code", "Unknown error")
                    return None, f"API error: {code}"
                
                if status == "picker":
                    items = data.get("picker", [])
                    if not items:
                        return None, "No items to download"
                    
                    item = items[0]
                    direct_url = item.get("url")
                    filename = item.get("filename")
                else:
                    direct_url = data.get("url")
                    filename = data.get("filename")
                
                if not direct_url:
                    return None, "Invalid API response"
                
                return {"url": direct_url, "filename": filename}, None
                
        except Exception as e:
            return None, f"Request failed: {e}"
    
    async def download_media(self, video_url: str, mode: str = "auto", 
                           progress_callback: Optional[Callable[[int], None]] = None) -> Optional[str]:
        """
        Основная асинхронная функция скачивания медиа
        
        Args:
            video_url: URL видео для скачивания
            mode: Режим скачивания ("auto", "audio", "mute")
            progress_callback: Функция для отслеживания прогресса
            
        Returns:
            Путь к скачанному файлу или None при ошибке
        """
        try:
            session = await self._get_session()
            
            # Тест прокси если настроен
            if self.settings["proxy_type"] != 0:
                if not await self._test_proxy_connection(session):
                    print("Прокси не работает, создаем новую сессию без прокси")
                    await self._close_session()
                    self._session = ClientSession(timeout=ClientTimeout(total=self.session_timeout))
                    session = self._session
            
            # Подготовка параметров запроса
            payload = {
                "url": video_url,
                "videoQuality": self.settings["video_quality"],
                "audioBitrate": self.settings["audio_bitrate"],
                "convertGif": True,
                "downloadMode": mode
            }
            
            if self.settings["disable_metadata"]:
                payload["disableMetadata"] = True
            
            # Настройка заголовков
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json"
            }
            
            if self.api_key:
                headers["Authorization"] = f"Api-Key {self.api_key}"
            
            # Список API для попыток
            api_list = [self.api_url]
            
            if self.auto_fallback:
                for api in self.fallback_apis:
                    if api != self.api_url:
                        api_list.append(api.rstrip('/'))
            
            # Попытки скачивания через разные API
            last_error = None
            direct_url = None
            filename = None
            
            for api_url in api_list:
                try:
                    result, error = await self._try_download_with_api(
                        api_url, video_url, payload, headers
                    )
                    
                    if result:
                        direct_url = result["url"]
                        filename = result["filename"]
                        print(f"Успешный запрос к API: {api_url}")
                        break
                    else:
                        last_error = error
                        print(f"API {api_url} не сработал: {error}")
                        continue
                        
                except Exception as e:
                    last_error = f"API {api_url} failed: {e}"
                    print(f"Ошибка API {api_url}: {e}")
                    continue
            
            if not direct_url:
                print(f"Все API не сработали. Последняя ошибка: {last_error}")
                return None
            
            # Асинхронное скачивание файла
            filename = filename or f"media_{uuid.uuid4()}.mp4"
            file_path = os.path.join(self.temp_dir, filename)
            
            print(f"Скачивание файла: {filename}")
            
            try:
                async with session.get(direct_url) as video_resp:
                    video_resp.raise_for_status()
                    
                    # Скачивание с прогрессом
                    content_length = video_resp.headers.get("content-length")
                    total_length = int(content_length) if content_length else 0
                    downloaded = 0
                    
                    with open(file_path, "wb") as f:
                        async for chunk in video_resp.content.iter_chunked(8192):
                            f.write(chunk)
                            
                            if total_length and progress_callback:
                                downloaded += len(chunk)
                                percent = int(downloaded * 100 / total_length)
                                progress_callback(percent)
                                
            except Exception as e:
                # Попытка fallback на HTTP если HTTPS не работает
                if direct_url.startswith("https://"):
                    fallback_url = direct_url.replace("https://", "http://")
                    print(f"HTTPS не работает, пробуем HTTP: {fallback_url}")
                    
                    async with session.get(fallback_url) as video_resp:
                        video_resp.raise_for_status()
                        
                        content_length = video_resp.headers.get("content-length")
                        total_length = int(content_length) if content_length else 0
                        downloaded = 0
                        
                        with open(file_path, "wb") as f:
                            async for chunk in video_resp.content.iter_chunked(8192):
                                f.write(chunk)
                                
                                if total_length and progress_callback:
                                    downloaded += len(chunk)
                                    percent = int(downloaded * 100 / total_length)
                                    progress_callback(percent)
                else:
                    raise
            
            print(f"Файл успешно скачан: {file_path}")
            return file_path
            
        except Exception as e:
            print(f"Ошибка скачивания: {e}")
            print(traceback.format_exc())
            return None
    
    async def download_audio_only(self, video_url: str, 
                                progress_callback: Optional[Callable[[int], None]] = None) -> Optional[str]:
        """Асинхронное скачивание только аудио"""
        return await self.download_media(video_url, mode="audio", progress_callback=progress_callback)
    
    async def download_multiple(self, urls: List[str], mode: str = "auto",
                              progress_callback: Optional[Callable[[str, int], None]] = None) -> List[Optional[str]]:
        """
        Асинхронное скачивание нескольких файлов одновременно
        
        Args:
            urls: Список URL для скачивания
            mode: Режим скачивания
            progress_callback: Функция прогресса с URL и процентами
            
        Returns:
            Список путей к скачанным файлам (None для неудачных)
        """
        async def download_single(url):
            callback = None
            if progress_callback:
                callback = lambda percent: progress_callback(url, percent)
            return await self.download_media(url, mode, callback)
        
        tasks = [download_single(url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Обработка исключений
        processed_results = []
        for result in results:
            if isinstance(result, Exception):
                print(f"Ошибка скачивания: {result}")
                processed_results.append(None)
            else:
                processed_results.append(result)
        
        return processed_results
    
    async def _close_session(self):
        """Закрытие HTTP сессии"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
    
    async def cleanup_old_files(self, max_age_hours: int = 12):
        """Асинхронная очистка старых файлов"""
        try:
            now = time.time()
            max_age_seconds = max_age_hours * 3600
            
            for filename in os.listdir(self.temp_dir):
                file_path = os.path.join(self.temp_dir, filename)
                if os.path.isfile(file_path):
                    if now - os.path.getmtime(file_path) > max_age_seconds:
                        os.remove(file_path)
                        print(f"Удален старый файл: {filename}")
                        
        except Exception as e:
            print(f"Ошибка очистки: {e}")
    
    async def __aenter__(self):
        """Асинхронный контекстный менеджер - вход"""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Асинхронный контекстный менеджер - выход"""
        await self._close_session()
