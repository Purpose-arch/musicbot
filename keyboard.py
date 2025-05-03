# keyboard.py
import math
import base64
import json
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import TRACKS_PER_PAGE

def format_duration(seconds):
    if not seconds: return "--:--"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"

def create_tracks_keyboard(tracks, page=0, search_id=None):
    """Creates a keyboard with tracks pagination"""
    kb = []
    total_tracks = len(tracks)
    
    if not total_tracks:
        return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Нет результатов", callback_data="info")]])
    
    start_idx = page * TRACKS_PER_PAGE
    end_idx = min(start_idx + TRACKS_PER_PAGE, total_tracks)
    
    for i in range(start_idx, end_idx):
        track = tracks[i]
        track_type = track.get('type', 'track')
        duration = format_duration(track.get('duration'))
        track_count = track.get('track_count', 0)
        
        # Иконки в зависимости от типа и источника
        icon = "🎵"
        if track_type == 'playlist':
            icon = "📂"
            title_text = f"{i+1}. {icon} {track['title']} ({track_count} треков)"
        else:
            if 'youtube' in track.get('source', ''):
                icon = "▶️"
            elif 'soundcloud' in track.get('source', ''):
                icon = "🔊"
            title_text = f"{i+1}. {icon} {track['title']} - {track['channel']} ({duration})"
        
        # Создаем данные для кнопки скачивания
        button_data = ""
        if track_type == 'playlist':
            button_data = f"dlpl_{i+1}_{search_id}"
            button_text = "📥 Скачать плейлист"
        else:
            # Используем существующую логику для треков
            if search_id:
                button_data = f"dl_{i+1}_{search_id}"
                button_text = "📥 Скачать"
            else:
                # Прямое скачивание для треков
                data = {
                    'url': track['url'],
                    'title': track['title'],
                    'channel': track['channel'],
                }
                encoded = base64.b64encode(json.dumps(data).encode()).decode()
                button_data = f"d_{encoded}"
                button_text = "📥 Скачать"
        
        # Добавляем кнопку
        kb.append([
            InlineKeyboardButton(text=title_text, callback_data="info"),
            InlineKeyboardButton(text=button_text, callback_data=button_data),
        ])
    
    # Navigation buttons
    nav = []
    pages = (total_tracks + TRACKS_PER_PAGE - 1) // TRACKS_PER_PAGE
    curr_page = page + 1
    
    if pages > 1:
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"page_{page-1}_{search_id}"))
        nav.append(InlineKeyboardButton(text=f"{curr_page}/{pages}", callback_data="info"))
        if curr_page < pages:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"page_{page+1}_{search_id}"))
    
    if nav:
        kb.append(nav)
    
    return InlineKeyboardMarkup(inline_keyboard=kb) 