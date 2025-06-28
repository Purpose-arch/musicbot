import asyncio
import os
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

async def generate_telethon_session():
    print("--- Генерация Telethon StringSession ---")
    
    # Запрашиваем API ID и API Hash
    api_id_str = input("Введите ваш API ID (из my.telegram.org/apps): ")
    api_hash = input("Введите ваш API Hash (из my.telegram.org/apps): ")

    try:
        api_id = int(api_id_str)
    except ValueError:
        print("Ошибка: API ID должен быть числом.")
        return

    session_name = "tg_agent_session" # Имя файла сессии (не будет использоваться напрямую, но нужно для клиента)

    print("Подключение к Telegram...")
    # Создаем клиент TelegramClient
    # Используем 'session_name' для временного хранения сессии на диске перед получением stringsession
    client = TelegramClient(session_name, api_id, api_hash)

    try:
        await client.connect()

        if not await client.is_user_authorized():
            print("Авторизация...")
            phone = input("Введите ваш номер телефона (например, +79123456789): ")
            try:
                await client.send_code_request(phone)
                code = input("Введите код подтверждения из Telegram: ")
                await client.sign_in(phone, code)
            except Exception as e:
                print(f"Ошибка авторизации: {e}")
                await client.disconnect()
                return
        
        print("Авторизация успешна. Генерирую StringSession...")
        # Получаем StringSession
        string_session = client.session.save()
        print("\n--- Ваша Telethon StringSession (скопируйте и добавьте в .env как TELETHON_SESSION): ---")
        print(string_session)
        print("--------------------------------------------------------------------------------------\n")

    except Exception as e:
        print(f"Произошла ошибка: {e}")
    finally:
        if client.is_connected():
            await client.disconnect()
        # Удаляем временный файл сессии
        if os.path.exists(f"{session_name}.session"):
            os.remove(f"{session_name}.session")

if __name__ == '__main__':
    asyncio.run(generate_telethon_session()) 