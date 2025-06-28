import os
import sys
import asyncio
import logging
import json
import traceback

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeAudio

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def main():
    # Получаем данные из переменных окружения
    api_id = os.getenv('API_ID')
    api_hash = os.getenv('API_HASH')
    string_session = os.getenv('STRING_SESSION')
    bot_username = os.getenv('TELETHON_BOT_USERNAME') # Username вашего основного бота, куда агент будет отправлять файлы

    if not all([api_id, api_hash, string_session, bot_username]):
        logger.error("Не все переменные окружения (API_ID, API_HASH, STRING_SESSION, TELETHON_BOT_USERNAME) установлены.")
        sys.exit(1)

    try:
        api_id = int(api_id)
    except ValueError:
        logger.error("API_ID должен быть целым числом.")
        sys.exit(1)

    # Инициализация клиента Telethon
    client = TelegramClient(StringSession(string_session), api_id, api_hash)

    try:
        logger.info("Подключение Telethon клиента...")
        await client.connect()
        logger.info("Telethon клиент подключен.")

        if not await client.is_user_authorized():
            logger.error("Клиент Telethon не авторизован. Пожалуйста, убедитесь, что STRING_SESSION верен.")
            # В реальном приложении здесь можно добавить логику для получения кода и авторизации
            sys.exit(1)

        # Парсим аргументы командной строки
        # Ожидаемый формат: python telethon_agent.py <file_path> <original_chat_id> <original_message_id> <status_message_id> <file_type> <title> <performer> <duration>
        if len(sys.argv) < 9:
            logger.error("Недостаточно аргументов. Ожидается: file_path, original_chat_id, original_message_id, status_message_id, file_type, title, performer, duration")
            sys.exit(1)

        file_path = sys.argv[1]
        original_chat_id = int(sys.argv[2])
        original_message_id = int(sys.argv[3])
        status_message_id = int(sys.argv[4])
        file_type = sys.argv[5]
        title = sys.argv[6]
        performer = sys.argv[7]
        duration = int(sys.argv[8]) if sys.argv[8].isdigit() else 0

        if not os.path.exists(file_path):
            logger.error(f"Файл не найден: {file_path}")
            sys.exit(1)

        logger.info(f"Отправка файла: {file_path} в чат бота @{bot_username} для пересылки в чат {original_chat_id}")

        # Подготовка метаданных для caption
        metadata = {
            "original_chat_id": original_chat_id,
            "original_message_id": original_message_id,
            "status_message_id": status_message_id,
            "file_type": file_type,
            "title": title,
            "performer": performer,
            "duration": duration,
            "source_type": "telethon_agent" # Добавим, чтобы бот мог различать сообщения от агента
        }
        caption_json = json.dumps(metadata)

        # Отправка файла
        attributes = []
        if file_type == 'audio':
            attributes.append(DocumentAttributeAudio(duration=duration, title=title, performer=performer))

        msg = await client.send_file(
            bot_username, # Отправляем файл нашему боту
            file_path,
            caption=caption_json,
            attributes=attributes,
            supports_streaming=True # Важно для больших файлов
        )
        
        logger.info(f"Файл успешно отправлен боту. Message ID: {msg.id}")
        # Можно вывести file_id, если это потребуется для отладки, но бот получит его из сообщения
        # print(f"FILE_ID_FROM_AGENT:{msg.media.document.id}")

    except Exception as e:
        logger.error(f"Ошибка в Telethon агенте: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if client.is_connected():
            logger.info("Отключение Telethon клиента...")
            await client.disconnect()
            logger.info("Telethon клиент отключен.")

if __name__ == '__main__':
    asyncio.run(main()) 