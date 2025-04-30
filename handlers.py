# handlers.py
import uuid
import asyncio
import os
import json
import base64
import re

from aiogram import F, types
from aiogram.filters import Command

from bot_instance import dp, bot
from config import TRACKS_PER_PAGE, MAX_TRACKS, MAX_PARALLEL_DOWNLOADS
from state import search_results, download_tasks, download_queues, playlist_downloads
from search import search_youtube, search_soundcloud, search_bandcamp
from keyboard import create_tracks_keyboard
from track_downloader import download_track
from media_downloader import download_media_from_url
from queue import process_download_queue

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🐈‍⬛ приветик я\n\n"
        "✅ персональный\n"
        "✅ иксперементальный\n"
        "✅ скачивающий\n"
        "✅ юный\n"
        "✅ новобранец\n\n"
        "🎵 ищу музыку по названию\n"
        "🔗 скачиваю треки и плейлисты по ссылке (youtube soundcloud), а также видео (тикток)\n\n"
        "👥 также можно добавить меня в группу и использовать команду\n"
        "«музыка (запрос)»\n"
        "либо отправить ссылку там"
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = """*как пользоваться ботом* 

1️⃣ **поиск музыки** 
просто напиши название трека или исполнителя я поищу на soundcloud bandcamp и youtube и покажу список

2️⃣ **скачивание по ссылке** 
отправь мне прямую ссылку на трек или плейлист (youtube soundcloud и др) я попытаюсь скачать
(плейлисты отправляются целиком после загрузки всех треков)

*команды*
/start - показать приветственное сообщение
/help - показать это сообщение
/search [запрос] - искать музыку по запросу
/cancel - отменить активные загрузки и очистить очередь"""
    await message.answer(help_text, parse_mode="Markdown")

@dp.message(Command("search"))
async def cmd_search(message: types.Message):
    if len(message.text.split()) < 2:
        await message.answer("❌ напиши что-нибудь после /search плиз\nнапример /search coldplay yellow")
        return
    query = " ".join(message.text.split()[1:])
    searching_message = await message.answer("🔍 ищу музыку...")
    search_id = str(uuid.uuid4())
    max_results = MAX_TRACKS // 3
    yt, sc, bc = await asyncio.gather(
        search_youtube(query, max_results),
        search_soundcloud(query, max_results),
        search_bandcamp(query, max_results)
    )
    combined = []
    for t in sc:
        if 'source' not in t: t['source'] = 'soundcloud'
        combined.append(t)
    for t in bc:
        if 'source' not in t: t['source'] = 'bandcamp'
        combined.append(t)
    for t in yt:
        if 'source' not in t: t['source'] = 'youtube'
        combined.append(t)
    if not combined:
        await message.answer("❌ чет ничего не нашлось ни там ни там попробуй другой запрос")
        await bot.delete_message(chat_id=searching_message.chat.id, message_id=searching_message.message_id)
        return
    search_results[search_id] = combined
    keyboard = create_tracks_keyboard(combined, 0, search_id)
    await message.answer(
        f"🎵 нашел для тебя {len(combined)} треков по запросу «{query}» ⬇",
        reply_markup=keyboard
    )
    await bot.delete_message(chat_id=searching_message.chat.id, message_id=searching_message.message_id)

@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message):
    user_id = message.from_user.id
    cancelled_tasks = 0
    cancelled_playlists = 0
    cleaned_files = 0
    active_urls = []
    queued_count = 0

    if user_id in download_tasks:
        to_cancel = {u:t for u,t in download_tasks[user_id].items() if not t.done() and not t.cancelled()}
        active_urls = list(to_cancel.keys())
        for t in to_cancel.values():
            t.cancel(); cancelled_tasks += 1
        await asyncio.sleep(0.2)
        download_tasks[user_id] = {u:t for u,t in download_tasks[user_id].items() if not t.done() and not t.cancelled()}
        if not download_tasks.get(user_id):
            download_tasks.pop(user_id, None)
    if user_id in download_queues:
        queued_count = len(download_queues[user_id])
        download_queues.pop(user_id, None)
    to_remove = []
    files = []
    for pl_id, pl in list(playlist_downloads.items()):
        if pl.get('user_id') == user_id:
            cancelled_playlists += 1
            to_remove.append(pl_id)
            for tr in pl.get('tracks', []):
                if tr['url'] in active_urls and tr.get('file_path') and os.path.exists(tr['file_path']):
                    files.append(tr['file_path'])
            if pl.get('status_message_id'):
                try: await bot.delete_message(chat_id=pl['chat_id'], message_id=pl['status_message_id'])
                except: pass
    for rid in to_remove:
        playlist_downloads.pop(rid, None)
    for f in set(files):
        try: os.remove(f); cleaned_files += 1
        except: pass
    parts = []
    if cancelled_tasks: parts.append(f"отменил {cancelled_tasks} загрузок")
    if cancelled_playlists: parts.append(f"остановил {cancelled_playlists} плейлистов")
    if queued_count: parts.append(f"очистил очередь ({queued_count})")
    if cleaned_files: parts.append(f"удалил {cleaned_files} файлов")
    if parts:
        await message.answer("✅ ok, " + ", ".join(parts))
    else:
        await message.answer("❌ ничего не было активного")

@dp.callback_query(F.data.startswith("d_"))
async def process_download_callback(callback: types.CallbackQuery):
    try:
        data = json.loads(base64.b64decode(callback.data[2:]).decode('utf-8'))
        user = callback.from_user.id
        if data['url'] in download_tasks.get(user, {}):
            await callback.answer("этот трек уже качается или в очереди", show_alert=True); return
        if any(item[0]['url']==data['url'] for item in download_queues.get(user, [])):
            await callback.answer("этот трек уже качается или в очереди", show_alert=True); return
        active = sum(1 for t in download_tasks.get(user, {}).values() if not t.done())
        if active >= MAX_PARALLEL_DOWNLOADS:
            await callback.answer(f"❌ слишком много загрузок ({active}/{MAX_PARALLEL_DOWNLOADS})", show_alert=True)
        else:
            status = await callback.message.answer(f"⏳ начинаю скачивать {data['title']} - {data['channel']}")
            download_tasks.setdefault(user, {})
            task = asyncio.create_task(download_track(user, data, callback.message, status, original_message_context=callback.message))
            download_tasks[user][data['url']] = task
            await callback.answer("начал скачивание")
    except Exception as e:
        await callback.message.answer(f"❌ ошибка: {e}")
        await callback.answer()

@dp.callback_query(F.data.startswith("dl_"))
async def process_download_callback_with_index(callback: types.CallbackQuery):
    try:
        _, idx, sid = callback.data.split('_',2)
        idx = int(idx)-1
        if sid not in search_results:
            await callback.answer("❌ результаты устарели", show_alert=True); return
        tracks = search_results[sid]
        if idx<0 or idx>=len(tracks):
            await callback.answer("❌ не найден трек", show_alert=True); return
        data = tracks[idx]
        user = callback.from_user.id
        if data['url'] in download_tasks.get(user, {}) or any(item[0]['url']==data['url'] for item in download_queues.get(user, [])):
            await callback.answer("этот трек уже качается или в очереди", show_alert=True); return
        active = sum(1 for t in download_tasks.get(user, {}).values() if not t.done())
        if active >= MAX_PARALLEL_DOWNLOADS:
            await callback.answer(f"❌ слишком много загрузок ({active}/{MAX_PARALLEL_DOWNLOADS})", show_alert=True)
        else:
            status = await callback.message.answer(f"⏳ начинаю скачивать {data['title']} - {data['channel']}")
            download_tasks.setdefault(user, {})
            task = asyncio.create_task(download_track(user, data, callback.message, status, original_message_context=callback.message))
            download_tasks[user][data['url']] = task
            await callback.answer("начал скачивание")
    except Exception as e:
        await callback.answer(f"❌ ошибка: {e}", show_alert=True)

@dp.callback_query(F.data.startswith("page_"))
async def process_page_callback(callback: types.CallbackQuery):
    try:
        _, p, sid = callback.data.split('_',2)
        page = int(p)
        if sid not in search_results:
            await callback.answer("❌ результаты устарели", show_alert=True); return
        kb = create_tracks_keyboard(search_results[sid], page, sid)
        await callback.message.edit_reply_markup(reply_markup=kb)
        await callback.answer()
    except:
        await callback.answer("❌ ошибка при переключении страницы", show_alert=True)

@dp.callback_query(F.data=="info")
async def process_info_callback(callback: types.CallbackQuery):
    await callback.answer()

@dp.message()
async def handle_text(message: types.Message):
    if message.text.startswith('/'):
        return
    txt = message.text.lower().strip()
    ctype = message.chat.type
    if ctype in ('group','supergroup'):
        if txt.startswith("музыка "):
            q=message.text.strip()[len("музыка "):].strip()
            if q: await handle_group_search(message,q)
            else: await message.reply("❌ после 'музыка' нужен запрос")
            return
        m = re.search(r'https?://[^\s]+',message.text)
        if m: await handle_url_download(message,m.group(0)); return
        return
    elif ctype=='private':
        if message.text.strip().startswith(('http://','https://')):
            await handle_url_download(message,message.text.strip()); return
        # treat as search
        searching = await message.answer("🔍 ищу музыку...")
        sid = str(uuid.uuid4())
        try:
            maxr=MAX_TRACKS//3
            yt,sc,bc = await asyncio.gather(search_youtube(message.text,maxr),search_soundcloud(message.text,maxr),search_bandcamp(message.text,maxr))
            combined=[]
            for t in sc: combined.append({**t,'source':'soundcloud'})
            for t in bc: combined.append({**t,'source':'bandcamp'})
            for t in yt: combined.append({**t,'source':'youtube'})
            if not combined:
                await bot.edit_message_text("❌ ничего не нашел", chat_id=searching.chat.id, message_id=searching.message_id)
                return
            search_results[sid]=combined
            kb=create_tracks_keyboard(combined,0,sid)
            await bot.edit_message_text(f"🎵 найдено {len(combined)}", chat_id=searching.chat.id, message_id=searching.message_id, reply_markup=kb)
        except Exception as e:
            await bot.edit_message_text(f"❌ ошибка при поиске: {e}", chat_id=searching.chat.id, message_id=searching.message_id)
        return

async def handle_url_download(message: types.Message, url: str):
    reply = message.reply if message.chat.type!='private' else message.answer
    status = await reply(f"⏳ пытаюсь скачать медиа по ссылке {url[:50]}...", disable_web_page_preview=True)
    await download_media_from_url(url, message, status)

async def handle_group_search(message: types.Message, query: str):
    status = await message.reply("🔍 ищу музыку...")
    sid = str(uuid.uuid4())
    try:
        maxr=MAX_TRACKS//3
        yt,sc,bc = await asyncio.gather(search_youtube(query,maxr),search_soundcloud(query,maxr),search_bandcamp(query,maxr))
        combined=[]
        for t in sc: combined.append({**t,'source':'soundcloud'})
        for t in bc: combined.append({**t,'source':'bandcamp'})
        for t in yt: combined.append({**t,'source':'youtube'})
        if not combined:
            await bot.edit_message_text("❌ ничего не нашел", chat_id=status.chat.id, message_id=status.message_id)
            return
        search_results[sid]=combined
        kb=create_tracks_keyboard(combined,0,sid)
        await bot.edit_message_text(f"🎵 найдено {len(combined)} по '{query}'", chat_id=status.chat.id, message_id=status.message_id, reply_markup=kb)
    except Exception as e:
        await bot.edit_message_text(f"❌ ошибка: {e}", chat_id=status.chat.id, message_id=status.message_id) 