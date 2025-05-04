# keyboard.py
import math
import base64
import json
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import TRACKS_PER_PAGE, GROUP_TRACKS_PER_PAGE

def create_tracks_keyboard(tracks, page=0, search_id="", is_group=False):
    """Генерация инлайн-клавиатуры для списка треков"""
    tracks_per_page = GROUP_TRACKS_PER_PAGE if is_group else TRACKS_PER_PAGE
    total_pages = math.ceil(len(tracks) / tracks_per_page)
    start_idx = page * tracks_per_page
    end_idx = min(start_idx + tracks_per_page, len(tracks))
    buttons = []
    for i in range(start_idx, end_idx):
        track = tracks[i]
        track_data = {
            "title": track['title'],
            "artist": track['channel'],
            "url": track['url'],
            "search_id": search_id,
            "source": track.get('source', '')
        }
        track_json = json.dumps(track_data, ensure_ascii=False)
        if len(track_json.encode('utf-8')) > 60:
            callback_data = f"dl_{i+1}_{search_id}"
        else:
            callback_data = f"d_{base64.b64encode(track_json.encode('utf-8')).decode('utf-8')}"
        duration = track.get('duration', 0)
        if duration > 0:
            minutes = int(duration // 60)
            seconds = int(duration % 60)
            duration_str = f" ({minutes}:{seconds:02d})"
        else:
            duration_str = ""
        buttons.append([
            InlineKeyboardButton(
                text=f"🎧 {track['title']} - {track['channel']}{duration_str}",
                callback_data=callback_data
            )
        ])
    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"page_{page-1}_{search_id}"
                )
            )
        nav_buttons.append(
            InlineKeyboardButton(
                text=f"{page+1}/{total_pages}",
                callback_data="info"
            )
        )
        if page < total_pages - 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"page_{page+1}_{search_id}"
                )
            )
        buttons.append(nav_buttons)
    return InlineKeyboardMarkup(inline_keyboard=buttons) 