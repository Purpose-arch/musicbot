from aiogram import Bot, types

async def send_log_message(bot: Bot, chat_id: int | str, text: str, parse_mode: str = types.ParseMode.HTML):
    """
    Отправляет лог-сообщение в указанный чат.

    Args:
        bot: Объект бота.
        chat_id: ID чата (группы).
        text: Текст сообщения.
        parse_mode: Режим парсинга текста.
    """
    try:
        await bot.send_message(
            chat_id,
            text,
            parse_mode=parse_mode
        )
    except Exception as e:
        # Логируем ошибку, если не удалось отправить сообщение
        print(f"Error sending log message to chat ID {chat_id}: {e}") 