import asyncio
import os
import sys
import json
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeAudio, DocumentAttributeVideo
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# Получаем данные из .env
TELETHON_SESSION = os.getenv('TELETHON_SESSION')
TELETHON_API_ID = os.getenv('TELETHON_API_ID')
TELETHON_API_HASH = os.getenv('TELETHON_API_HASH')

async def send_large_file_as_agent(
    file_path: str,
    original_chat_id: int,
    original_message_id: int,
    bot_chat_id: int,
    media_type: str,
    title: str = None,
    performer: str = None,
    duration: int = None
):
    if not TELETHON_SESSION or not TELETHON_API_ID or not TELETHON_API_HASH:
        print("Ошибка: Переменные окружения TELETHON_SESSION, TELETHON_API_ID или TELETHON_API_HASH не установлены.")
        sys.exit(1)

    try:
        api_id = int(TELETHON_API_ID)
    except ValueError:
        print("Ошибка: TELETHON_API_ID должен быть числом.")
        sys.exit(1)

    # Инициализация клиента Telethon с StringSession
    client = TelegramClient(StringSession(TELETHON_SESSION), api_id, TELETHON_API_HASH)

    try:
        print(f"[AGENT] Подключение к Telegram (файл: {file_path})...")
        await client.connect()

        if not await client.is_user_authorized():
            print("[AGENT] Ошибка: Telethon клиент не авторизован. Запустите generate_session.py")
            sys.exit(1)

        print(f"[AGENT] Отправка файла {file_path} боту {bot_chat_id}...")

        # Подготовка метаданных для передачи через caption
        # Используем JSON для структурированной передачи данных
        caption_data = {
            'user_chat_id': original_chat_id,
            'status_message_id': original_message_id,
            'media_type': media_type,
            'title': title,
            'performer': performer,
            'duration': duration
        }
        caption = json.dumps(caption_data)

        attributes = []
        if media_type == 'audio' and duration is not None and title is not None:
            attributes.append(DocumentAttributeAudio(duration=duration, title=title, performer=performer))
        elif media_type == 'video' and duration is not None:
            # Telethon не предоставляет прямой DocumentAttributeVideo, но можно использовать send_file с video=True
            pass # Обработка будет на стороне send_file

        # Отправка файла
        if media_type == 'photo':
            msg = await client.send_file(bot_chat_id, file_path, caption=caption, force_document=False)
        elif media_type == 'video':
            msg = await client.send_file(bot_chat_id, file_path, caption=caption, video=True, attributes=attributes)
        elif media_type == 'audio':
            msg = await client.send_file(bot_chat_id, file_path, caption=caption, voice=False, attributes=attributes)
        else: # document
            msg = await client.send_file(bot_chat_id, file_path, caption=caption, force_document=True)

        print(f"[AGENT] Файл отправлен. Message ID: {msg.id}, File ID: {msg.media.document.id if hasattr(msg.media, 'document') else 'N/A'}")

    except Exception as e:
        print(f"[AGENT] Ошибка при отправке файла: {e}")
        sys.exit(1)
    finally:
        if client.is_connected():
            await client.disconnect()

if __name__ == '__main__':
    # Аргументы: file_path, original_chat_id, original_message_id, bot_chat_id, media_type, title, performer, duration
    # Пример вызова: python agent.py "path/to/file.mp3" 12345 67890 -100123456 audio "Song Title" "Artist" 300
    args = sys.argv[1:]
    
    if len(args) < 5:
        print("Использование: python agent.py <file_path> <original_chat_id> <original_message_id> <bot_chat_id> <media_type> [title] [performer] [duration]")
        sys.exit(1)
    
    file_path = args[0]
    original_chat_id = int(args[1])
    original_message_id = int(args[2])
    bot_chat_id = int(args[3])
    media_type = args[4]

    title = args[5] if len(args) > 5 else None
    performer = args[6] if len(args) > 6 else None
    duration = int(args[7]) if len(args) > 7 else None

    asyncio.run(send_large_file_as_agent(file_path, original_chat_id, original_message_id, bot_chat_id, media_type, title, performer, duration)) 