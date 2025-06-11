# Используем официальный образ Python 3.10 (slim-версия для экономии места)
FROM python:3.10-slim

# Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# Обновляем список пакетов и устанавливаем ffmpeg и другие необходимые утилиты
# --no-install-recommends экономит место, устанавливая только основные зависимости
# rm -rf /var/lib/apt/lists/* очищает кэш apt после установки
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Копируем файл с зависимостями Python
COPY requirements.txt ./

# Устанавливаем зависимости Python
# --no-cache-dir экономит место, не сохраняя кэш pip
# --upgrade pip обновляем pip перед установкой
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем остальной код приложения в рабочую директорию
COPY . /app

# Указываем команду для запуска бота при старте контейнера
CMD ["python", "src/core/main.py"] 