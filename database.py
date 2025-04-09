import aiosqlite
import os
import time

DB_FILE = "playlists.db"

async def init_db():
    """инициализирует базу данных и создает таблицы, если их нет."""
    async with aiosqlite.connect(DB_FILE) as db:
        # таблица плейлистов
        await db.execute('''
            CREATE TABLE IF NOT EXISTS playlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, name) 
            )
        ''')
        
        # таблица треков в плейлистах
        await db.execute('''
            CREATE TABLE IF NOT EXISTS playlist_tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                url TEXT NOT NULL,
                duration INTEGER,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
                UNIQUE(playlist_id, url) 
            )
        ''')
        await db.commit()
    print("база данных инициализирована.")

async def create_playlist(user_id: int, name: str) -> int | None:
    """создает новый плейлист для пользователя. возвращает id плейлиста или none если уже существует."""
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            cursor = await db.execute(
                "INSERT INTO playlists (user_id, name) VALUES (?, ?)",
                (user_id, name)
            )
            await db.commit()
            return cursor.lastrowid
    except aiosqlite.IntegrityError:
        # плейлист с таким именем уже существует у этого пользователя
        print(f"playlist '{name}' already exists for user {user_id}")
        return None
    except Exception as e:
        print(f"error creating playlist: {e}")
        return None

async def get_user_playlists(user_id: int) -> list[tuple[int, str]]:
    """получает список плейлистов пользователя (id, name)."""
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute(
            "SELECT id, name FROM playlists WHERE user_id = ? ORDER BY name",
            (user_id,)
        )
        rows = await cursor.fetchall()
        return rows if rows else []

async def get_playlist_id(user_id: int, name: str) -> int | None:
    """получает id плейлиста по имени и user_id."""
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute(
            "SELECT id FROM playlists WHERE user_id = ? AND name = ?",
            (user_id, name)
        )
        row = await cursor.fetchone()
        return row[0] if row else None

async def check_track_exists(playlist_id: int, url: str) -> bool:
    """проверяет, существует ли трек с таким url в плейлисте."""
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute(
            "SELECT 1 FROM playlist_tracks WHERE playlist_id = ? AND url = ?",
            (playlist_id, url)
        )
        row = await cursor.fetchone()
        return row is not None

async def add_track_to_playlist(playlist_id: int, title: str, artist: str, url: str, duration: int | None) -> bool:
    """добавляет трек в плейлист. возвращает true если успешно, false если трек уже был."""
    if await check_track_exists(playlist_id, url):
        return False
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute(
                "INSERT INTO playlist_tracks (playlist_id, title, artist, url, duration) VALUES (?, ?, ?, ?, ?)",
                (playlist_id, title, artist, url, duration)
            )
            await db.commit()
            return True
    except Exception as e:
        print(f"error adding track: {e}")
        return False

async def get_tracks_in_playlist(playlist_id: int) -> list[dict]:
    """получает список треков в плейлисте."""
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row # возвращать результаты как словари
        cursor = await db.execute(
            "SELECT id, title, artist, url, duration FROM playlist_tracks WHERE playlist_id = ? ORDER BY added_at",
            (playlist_id,)
        )
        rows = await cursor.fetchall()
        # преобразуем строки в обычные словари для удобства
        return [dict(row) for row in rows] if rows else []

async def delete_track_from_playlist(track_id: int) -> bool:
    """удаляет трек из плейлиста по его id в таблице playlist_tracks."""
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            cursor = await db.execute("DELETE FROM playlist_tracks WHERE id = ?", (track_id,))
            await db.commit()
            return cursor.rowcount > 0 # возвращает true если строка была удалена
    except Exception as e:
        print(f"error deleting track: {e}")
        return False

async def delete_playlist(playlist_id: int) -> bool:
    """удаляет плейлист и все его треки (используя ON DELETE CASCADE)."""
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            # foreign key с ON DELETE CASCADE должен удалить треки автоматически
            cursor = await db.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
            await db.commit()
            return cursor.rowcount > 0 # возвращает true если строка была удалена
    except Exception as e:
        print(f"error deleting playlist: {e}")
        return False 