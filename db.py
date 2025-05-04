# db.py
import os
import json
import asyncio
import asyncpg
import logging
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger(__name__)

# Переменные подключения к базе данных
DB_HOST = os.environ.get('DB_HOST', 'localhost')
DB_PORT = os.environ.get('DB_PORT', '5432')
DB_NAME = os.environ.get('DB_NAME', 'musicbot')
DB_USER = os.environ.get('DB_USER', 'postgres')
DB_PASSWORD = os.environ.get('DB_PASSWORD', 'postgres')

# Пул соединений с базой данных
db_pool = None

async def init_db():
    """Инициализирует соединение с базой данных и создает необходимые таблицы"""
    global db_pool
    try:
        # Создаем пул соединений
        db_pool = await asyncpg.create_pool(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME
        )
        
        # Создаем таблицу для настроек пользователей, если она не существует
        async with db_pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id BIGINT PRIMARY KEY,
                    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
            ''')
            
        logger.info("Database initialized successfully")
        return True
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        # Если не удалось подключиться, будем использовать локальное хранилище
        db_pool = None
        return False

async def get_user_settings(user_id: int) -> Dict[str, Any]:
    """Получает настройки пользователя из базы данных"""
    if not db_pool:
        # Если нет соединения с базой данных, возвращаем настройки по умолчанию
        from state import user_settings
        return user_settings.get(user_id, {})
    
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT settings FROM user_settings WHERE user_id = $1',
                user_id
            )
            if row:
                return row['settings']
            else:
                # Если настроек нет, возвращаем пустой словарь
                return {}
    except Exception as e:
        logger.error(f"Error getting user settings: {e}")
        # В случае ошибки возвращаем настройки из локального хранилища
        from state import user_settings
        return user_settings.get(user_id, {})

async def save_user_settings(user_id: int, settings: Dict[str, Any]) -> bool:
    """Сохраняет настройки пользователя в базу данных"""
    if not db_pool:
        # Если нет соединения с базой данных, сохраняем локально
        from state import user_settings
        user_settings[user_id] = settings
        return True
    
    try:
        async with db_pool.acquire() as conn:
            # Используем UPSERT (INSERT ... ON CONFLICT DO UPDATE)
            await conn.execute(
                '''
                INSERT INTO user_settings (user_id, settings)
                VALUES ($1, $2)
                ON CONFLICT (user_id) 
                DO UPDATE SET settings = $2, updated_at = NOW()
                ''',
                user_id, settings
            )
            
            # Дублируем в локальном хранилище для быстрого доступа
            from state import user_settings
            user_settings[user_id] = settings
            
            return True
    except Exception as e:
        logger.error(f"Error saving user settings: {e}")
        # В случае ошибки сохраняем только локально
        from state import user_settings
        user_settings[user_id] = settings
        return False

# Инициализируем базу данных при импорте модуля
asyncio.create_task(init_db()) 