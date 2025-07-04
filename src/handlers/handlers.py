# handlers.py
import uuid
import asyncio
import os
import json
import base64
import re
import logging
import tempfile
import yt_dlp
from aiogram.types import FSInputFile

from aiogram import F, types
from aiogram.filters import Command

from src.core.bot_instance import dp, bot, ADMIN_ID
from src.core.config import TRACKS_PER_PAGE, MAX_TRACKS, GROUP_TRACKS_PER_PAGE, GROUP_MAX_TRACKS, MAX_PARALLEL_DOWNLOADS, YDL_AUDIO_OPTS, LOG_GROUP_ID
from src.core.state import search_results, download_tasks, download_queues, playlist_downloads
from src.search.search import search_soundcloud, search_vk
from src.handlers.keyboard import create_tracks_keyboard
from src.download.track_downloader import download_track, _blocking_download_and_convert
from src.download.media_downloader import download_media_from_url
from src.download.download_queue import process_download_queue
from src.recognition.music_recognition import shazam, search_genius, search_yandex_music, search_musicxmatch, search_lyrics_parallel
from src.core.utils import set_mp3_metadata
from src.recognition.transcription import process_voice_or_video
from src.logger.group_logger import send_log_message

logger = logging.getLogger(__name__)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    # Notify admin about start action
    await send_log_message(
        bot,
        LOG_GROUP_ID,
        f'👤 <a href="tg://user?id={message.from_user.id}">{message.from_user.full_name}</a>\n➤ /start',
        parse_mode="HTML"
    )
    await message.answer(
        "🐈‍⬛ приветик я\n\n"
        "✅ персональный\n"
        "✅ иксперементальный\n"
        "✅ скачивающий\n"
        "✅ юный\n"
        "✅ новобранец\n\n"
        "🎵 ищу музыку по названию\n"
        "🔗 скачиваю треки и плейлисты по ссылке (soundcloud), а также видео (тикток)\n\n"
        "👥 также можно добавить меня в группу и использовать команду\n"
        "«музыка/найти/трек/песня (запрос)»\n"
        "либо отправить ссылку там"
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    # Notify admin about help action
    await send_log_message(
        bot,
        LOG_GROUP_ID,
        f'👤 <a href="tg://user?id={message.from_user.id}">{message.from_user.full_name}</a>\n➤ /help',
        parse_mode="HTML"
    )
    help_text = """*как пользоваться ботом* 

1️⃣ **поиск музыки** 
просто напиши название трека или исполнителя я поищу на soundcloud и покажу список

2️⃣ **скачивание по ссылке** 
отправь мне прямую ссылку на трек или плейлист soundcloud я попытаюсь скачать
(плейлисты отправляются целиком после загрузки всех треков)

*команды*
/start - показать приветственное сообщение
/help - показать это сообщение
/cancel - отменить активные загрузки и очистить очередь"""
    await message.answer(help_text, parse_mode="Markdown")

@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message):
    # Notify admin about cancel action
    await send_log_message(
        bot,
        LOG_GROUP_ID,
        f'👤 <a href="tg://user?id={message.from_user.id}">{message.from_user.full_name}</a>\n➤ /cancel',
        parse_mode="HTML"
    )
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
        logger.info(f"User {callback.from_user.username} direct_download: {data['url']}")
        # Определяем тип чата
        is_group = callback.message.chat.type in ('group', 'supergroup')
        # Notify admin
        await send_log_message(
            bot,
            LOG_GROUP_ID,
            f'👤 <a href="tg://user?id={callback.from_user.id}">{callback.from_user.full_name}</a>\n➤ прямое скачивание: <a href="{data["url"]}">ссылка</a>',
            parse_mode="HTML"
        )
        if data['url'] in download_tasks.get(user, {}):
            await callback.answer("этот трек уже качается или в очереди", show_alert=True); return
        if any(item[0]['url']==data['url'] for item in download_queues.get(user, [])):
            await callback.answer("этот трек уже качается или в очереди", show_alert=True); return
        active = sum(1 for t in download_tasks.get(user, {}).values() if not t.done())
        if active >= MAX_PARALLEL_DOWNLOADS:
            await callback.answer(f"❌ слишком много загрузок ({active}/{MAX_PARALLEL_DOWNLOADS})", show_alert=True)
        else:
            status = await callback.message.answer(f"⏳ скачиваю...")
            download_tasks.setdefault(user, {})
            task = asyncio.create_task(download_track(user, data, callback.message, status, original_message_context=callback.message))
            download_tasks[user][data['url']] = task
            await callback.answer("начал скачивание")
            if is_group:
                try:
                    await bot.delete_message(chat_id=callback.message.chat.id, message_id=callback.message.message_id)
                except Exception as e:
                    print(f"Warning: Could not delete message: {e}")
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
        logger.info(f"User {callback.from_user.username} track_download: {data['title']} url {data['url']}")
        # Определяем тип чата
        is_group = callback.message.chat.type in ('group', 'supergroup')
        # Notify admin
        await send_log_message(
            bot,
            LOG_GROUP_ID,
            f'👤 <a href="tg://user?id={callback.from_user.id}">{callback.from_user.full_name}</a>\n➤ скачивание трека: <a href="{data["url"]}">{data["title"]}</a>',
            parse_mode="HTML"
        )
        user = callback.from_user.id
        if data['url'] in download_tasks.get(user, {}) or any(item[0]['url']==data['url'] for item in download_queues.get(user, [])):
            await callback.answer("этот трек уже качается или в очереди", show_alert=True); return
        active = sum(1 for t in download_tasks.get(user, {}).values() if not t.done())
        if active >= MAX_PARALLEL_DOWNLOADS:
            await callback.answer(f"❌ слишком много загрузок ({active}/{MAX_PARALLEL_DOWNLOADS})", show_alert=True)
        else:
            status = await callback.message.answer(f"⏳ скачиваю...")
            download_tasks.setdefault(user, {})
            task = asyncio.create_task(download_track(user, data, callback.message, status, original_message_context=callback.message))
            download_tasks[user][data['url']] = task
            await callback.answer("начал скачивание")
            if is_group:
                try:
                    await bot.delete_message(chat_id=callback.message.chat.id, message_id=callback.message.message_id)
                except Exception as e:
                    print(f"Warning: Could not delete message: {e}")
    except Exception as e:
        await callback.answer(f"❌ ошибка: {e}", show_alert=True)

@dp.callback_query(F.data.startswith("page_"))
async def process_page_callback(callback: types.CallbackQuery):
    try:
        _, p, sid = callback.data.split('_',2)
        page = int(p)
        if sid not in search_results:
            await callback.answer("❌ результаты устарели", show_alert=True); return
        # Определяем тип чата
        is_group = callback.message.chat.type in ('group', 'supergroup')
        # Передаем параметр is_group при создании клавиатуры
        kb = create_tracks_keyboard(search_results[sid], page, sid, is_group)
        await callback.message.edit_reply_markup(reply_markup=kb)
        await callback.answer()
    except:
        await callback.answer("❌ ошибка при переключении страницы", show_alert=True)

@dp.callback_query(F.data=="info")
async def process_info_callback(callback: types.CallbackQuery):
    await callback.answer()

@dp.message((F.voice | F.audio | F.video_note))
async def handle_media_recognition(message: types.Message):
    """
    Handles voice, audio, and video notes for music recognition,
    search, download, and sending the first result with lyrics.
    """
    user_id = message.from_user.id
    chat_id = message.chat.id
    message_id = message.message_id
    is_group = message.chat.type in ('group', 'supergroup')

    # Notify admin
    media_type = "voice" if message.voice else ("audio" if message.audio else "video note")
    await send_log_message(
        bot,
        LOG_GROUP_ID,
        f'👤 <a href="tg://user?id={user_id}">{message.from_user.full_name}</a>\n➤ распознавание {media_type}',
        parse_mode="HTML"
    )

    status_message = await message.reply("⏳ обрабатываю файл...")

    original_media_path = None
    downloaded_track_path = None
    converted_media_path = None
    temp_dir = None

    try:
        # 1. Create temporary directory
        temp_dir_obj = tempfile.TemporaryDirectory()
        temp_dir = temp_dir_obj.name

        # 2. Download the media file for Shazam processing
        media_file = message.voice or message.audio or message.video_note
        if not media_file:
            raise ValueError("Сообщение не содержит voice/audio/video_note")

        # Determine file extension based on media type
        if message.voice:
            file_extension = "ogg"
        elif message.audio:
            file_extension = "mp3" # Assuming common audio type
        elif message.video_note:
            file_extension = "mp4" # Assuming common video note type
        else:
             file_extension = "file" # Fallback

        destination_path = os.path.join(temp_dir, f"{media_file.file_unique_id}.{file_extension}")
        
        # Download using bot.download
        await bot.download(media_file, destination=destination_path)
        original_media_path = destination_path
        
        if not os.path.exists(original_media_path):
            raise ValueError("Не удалось скачать медиафайл с помощью bot.download.")
            
        logger.info(f"Media downloaded to: {original_media_path}")
        
        # 3. Convert audio file for better recognition
        converted_media_path = os.path.join(temp_dir, f"converted_{media_file.file_unique_id}.mp3")
        try:
            # Using ffmpeg for conversion with normalization
            proc = await asyncio.create_subprocess_exec(
                'ffmpeg', '-y', '-i', original_media_path,
                '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11',
                '-ar', '44100', '-ac', '2',
                '-codec:a', 'libmp3lame', '-q:a', '2',
                converted_media_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            
            if os.path.exists(converted_media_path) and os.path.getsize(converted_media_path) > 0:
                logger.info(f"Successfully converted media to: {converted_media_path}")
                recognition_path = converted_media_path
            else:
                logger.warning("Conversion failed, using original file for recognition")
                recognition_path = original_media_path
        except Exception as e:
            logger.error(f"Error converting media file: {e}")
            recognition_path = original_media_path

        # 4. First try: Recognize using Shazam
        await status_message.edit_text("🔎 распознаю трек...")
        result = await shazam.recognize(recognition_path)
        track_info = result.get("track", {})
        rec_title = track_info.get("title") or track_info.get("heading", "Unknown Title")
        rec_artist = track_info.get("subtitle", "Unknown Artist")

        # Check if Shazam recognition was successful
        if rec_title != "Unknown Title" and rec_artist != "Unknown Artist":
            # Shazam recognition successful
            await status_message.edit_text(f"✅ распознано: {rec_artist} - {rec_title}\n🔍 ищу трек для скачивания...")
            logger.info(f"Recognized: {rec_artist} - {rec_title}")

            # 5. Search for the track
            search_query = f"{rec_artist} {rec_title}"
            max_results = 10
            
            # Асинхронный поиск в обоих источниках - VK и SoundCloud
            sc_task = asyncio.create_task(search_soundcloud(search_query, max_results))
            vk_task = asyncio.create_task(search_vk(search_query, max_results))
            sc_results, vk_results = await asyncio.gather(sc_task, vk_task)
            
            # Комбинируем результаты с приоритетом VK
            combined_results = []
            for t in vk_results: 
                combined_results.append({**t, 'source': 'vk'})
            for t in sc_results: 
                combined_results.append({**t, 'source': 'soundcloud'})
                
            search_results_list = combined_results

            first_valid_result = None
            for res in search_results_list:
                if res and res.get('url') and res.get('title') and res.get('channel'):
                    first_valid_result = res
                    break

            if not first_valid_result:
                await status_message.delete()
                if original_media_path and os.path.exists(original_media_path):
                    os.remove(original_media_path)
                temp_dir_obj.cleanup()
                return

            download_url = first_valid_result['url']
            logger.info(f"Found track to download: {first_valid_result['title']} from {download_url}")
            
            # В группах сокращаем сообщение
            await status_message.edit_text(f"⏳ скачиваю трек...")

            # 6. Download the first result
            loop = asyncio.get_running_loop()
            safe_title = ''.join(c if c.isalnum() or c in ('_','-') else '_' for c in rec_title).strip('_.-')[:60]
            if not safe_title: safe_title = f"audio_{uuid.uuid4()}"
            base_temp_path = os.path.join(temp_dir, f"recognized_{safe_title}")
            
            # Ensure no conflicting file exists
            if os.path.exists(base_temp_path + '.mp3'):
                os.remove(base_temp_path + '.mp3')

            download_opts = {
                **YDL_AUDIO_OPTS,
                'outtmpl': base_temp_path + '.%(ext)s',
                'quiet': True, 'verbose': False, 'no_warnings': True,
                'prefer_ffmpeg': True, 'nocheckcertificate': True, 'ignoreerrors': True,
                'extract_flat': False, 'ffmpeg_location': '/usr/bin/ffmpeg'
            }
            expected_mp3_path = base_temp_path + '.mp3'

            await loop.run_in_executor(None, _blocking_download_and_convert, download_url, download_opts)

            if not os.path.exists(expected_mp3_path) or os.path.getsize(expected_mp3_path) == 0:
                raise ValueError("Скачанный файл не найден или пуст.")
            
            downloaded_track_path = expected_mp3_path
            logger.info(f"Track downloaded to: {downloaded_track_path}")

            # 7. Set metadata (using recognized title/artist)
            set_mp3_metadata(downloaded_track_path, rec_title, rec_artist)

            # 8. Fetch lyrics (using recognized title/artist)
            try:
                lyrics = await search_lyrics_parallel(rec_artist, rec_title, timeout=10.0)
                if lyrics:
                    logger.info(f"Lyrics found for {rec_artist} - {rec_title}")
            except Exception as e:
                logger.error(f"Error fetching lyrics: {e}")
                lyrics = None

            # 9. Send Audio and Lyrics
            await status_message.edit_text("📤 отправляю...")
            
            audio_msg = await bot.send_audio(
                chat_id,
                FSInputFile(downloaded_track_path),
                title=rec_title,
                performer=rec_artist,
                reply_to_message_id=message_id
            )

            if lyrics:
                await bot.send_message(
                    chat_id,
                    f"<blockquote expandable>{lyrics}</blockquote>",
                    reply_to_message_id=audio_msg.message_id,
                    parse_mode="HTML"
                )
            
            # Delete status message after success
            await status_message.delete()
        else:
            # Shazam recognition failed, try transcription
            logger.info("Shazam recognition failed, trying transcription")
            if not is_group:
                await status_message.edit_text("🔍 распознавание не удалось, пробую транскрипцию...")
            
            # Get deepgram API key from config
            from src.core.config import DEEPGRAM_API_KEY
            
            # Process voice or video with transcription
            transcription = await process_voice_or_video(
                message=message,
                sender_id=user_id,
                chat_id=chat_id,
                message_id=message_id,
                client=bot,
                api_key=DEEPGRAM_API_KEY
            )
            
            # If transcription is successful, show it to the user
            if transcription:
                logger.info(f"Transcription successful: {transcription}")
                
                # Send transcription text
                await status_message.delete()
                
                await bot.send_message(
                    chat_id,
                    f"<blockquote expandable>{transcription}</blockquote>",
                    reply_to_message_id=message_id,
                    parse_mode="HTML"
                )
            else:
                # Both Shazam and transcription failed
                await status_message.delete()
                if original_media_path and os.path.exists(original_media_path):
                    os.remove(original_media_path)
                temp_dir_obj.cleanup()
                return

    except Exception as e:
        logger.error(f"Error in handle_media_recognition: {e}", exc_info=True)
        try:
            # В группах не показываем ошибку
            if is_group:
                await status_message.delete()
            else:
                await status_message.edit_text(f"❌ Ошибка обработки: {e}")
        except Exception: # Handle case where status message might already be deleted or inaccessible
            logger.warning("Could not edit status message during error handling.")
            # Только в личных чатах отправляем новое сообщение
            if not is_group:
                await message.reply(f"❌ Ошибка обработки: {e}") # Send a new message if status edit fails

    finally:
        # Cleanup temporary files and directory
        if original_media_path and os.path.exists(original_media_path):
            try: os.remove(original_media_path)
            except Exception as e: logger.warning(f"Could not remove original media file {original_media_path}: {e}")
        if downloaded_track_path and os.path.exists(downloaded_track_path):
            try: os.remove(downloaded_track_path)
            except Exception as e: logger.warning(f"Could not remove downloaded track file {downloaded_track_path}: {e}")
        if converted_media_path and os.path.exists(converted_media_path):
            try: os.remove(converted_media_path)
            except Exception as e: logger.warning(f"Could not remove converted media file {converted_media_path}: {e}")
        if temp_dir and os.path.exists(temp_dir):
             try: temp_dir_obj.cleanup()
             except Exception as e: logger.warning(f"Could not cleanup temporary directory {temp_dir}: {e}")

@dp.message(F.caption)
async def handle_telethon_agent_file(message: types.Message):
    """Handles files received from the Telethon agent and forwards them to the original user."""
    # Check if the caption contains the metadata from the Telethon agent
    if message.caption and message.caption.startswith('{') and message.caption.endswith('}'):
        try:
            metadata = json.loads(message.caption)
            if metadata.get("source_type") == "telethon_agent":
                logger.info(f"Received file from Telethon agent. Metadata: {metadata}")

                original_chat_id = metadata.get("original_chat_id")
                original_message_id = metadata.get("original_message_id")
                status_message_id = metadata.get("status_message_id")
                file_type = metadata.get("file_type")
                title = metadata.get("title")
                performer = metadata.get("performer")
                duration = metadata.get("duration", 0)

                if not (original_chat_id and file_type):
                    logger.error(f"Missing required metadata from Telethon agent: {metadata}")
                    return

                file_id = None
                if message.audio:
                    file_id = message.audio.file_id
                elif message.video:
                    file_id = message.video.file_id
                elif message.photo:
                    file_id = message.photo[-1].file_id # Get the largest photo
                elif message.document:
                    file_id = message.document.file_id

                if not file_id:
                    logger.error(f"Could not get file_id from message from Telethon agent. Message: {message}")
                    return

                # Delete the original status message from download_media_from_url
                try:
                    if status_message_id and original_chat_id: # original_chat_id here is the chat where the status message was sent
                        await bot.delete_message(chat_id=original_chat_id, message_id=status_message_id)
                except Exception as e:
                    logger.warning(f"Could not delete status message {status_message_id} in chat {original_chat_id}: {e}")

                # Send the file to the original user
                try:
                    if file_type == "audio":
                        await bot.send_audio(
                            chat_id=original_chat_id,
                            audio=file_id,
                            title=title,
                            performer=performer,
                            duration=duration,
                            reply_to_message_id=original_message_id if original_message_id else None
                        )
                    elif file_type == "video":
                        await bot.send_video(
                            chat_id=original_chat_id,
                            video=file_id,
                            caption=title, # Video caption
                            duration=duration, # NEW: Pass duration for video files
                            reply_to_message_id=original_message_id if original_message_id else None
                        )
                    elif file_type == "photo":
                        await bot.send_photo(
                            chat_id=original_chat_id,
                            photo=file_id,
                            caption=title, # Photo caption
                            reply_to_message_id=original_message_id if original_message_id else None
                        )
                    elif file_type == "document":
                        await bot.send_document(
                            chat_id=original_chat_id,
                            document=file_id,
                            caption=title, # Document caption
                            reply_to_message_id=original_message_id if original_message_id else None
                        )
                    logger.info(f"Successfully resent file from agent to chat {original_chat_id}.")

                    # Delete the agent's message after successful re-sending to keep bot's chat clean
                    try:
                        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
                    except Exception as e:
                        logger.warning(f"Could not delete agent's message {message.message_id} in chat {message.chat.id}: {e}")

                except Exception as e:
                    logger.error(f"Error re-sending file from Telethon agent to {original_chat_id}: {e}", exc_info=True)
                    try:
                        # Notify the original user about the error
                        await bot.send_message(original_chat_id, f"❌ Ошибка при отправке файла: {e}")
                    except: pass # If cannot even send error message, just log it.

        except json.JSONDecodeError:
            # Not a JSON caption from our agent, ignore.
            pass
        except Exception as e:
            logger.error(f"Unexpected error in handle_telethon_agent_file: {e}", exc_info=True)

@dp.message(F.text)
async def handle_text(message: types.Message):
    if message.text.startswith('/'):
        return
    txt = message.text.lower().strip()
    ctype = message.chat.type
    if ctype in ('group','supergroup'):
        # Список префиксов для поиска музыки
        prefixes = ["музыка ", "найти ", "трек ", "песня "]
        prefix_used = None
        
        # Проверяем, есть ли в сообщении один из префиксов
        for prefix in prefixes:
            if txt.startswith(prefix):
                prefix_used = prefix
                break
        
        if prefix_used:
            q = message.text.strip()[len(prefix_used):].strip()
            if q: await handle_group_search(message, q)
            else: await message.reply(f"❌ после '{prefix_used.strip()}' нужен запрос")
            return
            
        m = re.search(r'https?://[^\s]+',message.text)
        if m: await handle_url_download(message,m.group(0)); return
        return
    elif ctype=='private':
        if message.text.strip().startswith(('http://','https://')):
            await handle_url_download(message,message.text.strip()); return
        # Notify admin about private search
        await send_log_message(
            bot,
            LOG_GROUP_ID,
            f'👤 <a href="tg://user?id={message.from_user.id}">{message.from_user.full_name}</a>\n➤ поиск в личке: {message.text.strip()}',
            parse_mode="HTML"
        )
        # treat as search
        searching = await message.answer("🔍 ищу музыку...")
        sid = str(uuid.uuid4())
        try:
            maxr = MAX_TRACKS
            sc_task = asyncio.create_task(search_soundcloud(message.text, maxr))
            vk_task = asyncio.create_task(search_vk(message.text, maxr))
            sc, vk = await asyncio.gather(sc_task, vk_task)
            combined = []
            # Сначала добавляем результаты из VK (приоритет)
            for t in vk: combined.append({**t, 'source': 'vk'})
            # Затем добавляем результаты из SoundCloud
            for t in sc: combined.append({**t, 'source': 'soundcloud'})
            if not combined:
                await bot.edit_message_text("❌ ничего не нашел", chat_id=searching.chat.id, message_id=searching.message_id)
                return
            search_results[sid] = combined
            kb = create_tracks_keyboard(combined, 0, sid)
            await bot.edit_message_text(f"🎵 найдено {len(combined)}", chat_id=searching.chat.id, message_id=searching.message_id, reply_markup=kb)
        except Exception as e:
            await bot.edit_message_text(f"❌ ошибка при поиске: {e}", chat_id=searching.chat.id, message_id=searching.message_id)
        return

async def handle_url_download(message: types.Message, url: str):
    logger.info(f"User {message.from_user.username} download_url: {url}")
    is_group = message.chat.type in ('group', 'supergroup')
    # Notify admin
    await send_log_message(
        bot,
        LOG_GROUP_ID,
        f'👤 <a href="tg://user?id={message.from_user.id}">{message.from_user.full_name}</a>\n➤ загрузка по ссылке: <a href="{url}">ссылка</a>',
        parse_mode="HTML"
    )
    reply = message.reply if message.chat.type!='private' else message.answer
    status = await reply("⏳ скачиваю...", disable_web_page_preview=True)
    
    await download_media_from_url(url, message, status)

async def handle_group_search(message: types.Message, query: str):
    logger.info(f"User {message.from_user.username} group_search: {query}")
    # Notify admin
    await send_log_message(
        bot,
        LOG_GROUP_ID,
        f'👤 <a href="tg://user?id={message.from_user.id}">{message.from_user.full_name}</a>\n➤ поиск в группе: {query}',
        parse_mode="HTML"
    )
    status = await message.reply("🔍 ищу музыку...")
    sid = str(uuid.uuid4())
    try:
        # Используем GROUP_MAX_TRACKS для групповых чатов
        maxr = GROUP_MAX_TRACKS
        sc_task = asyncio.create_task(search_soundcloud(query, maxr))
        vk_task = asyncio.create_task(search_vk(query, maxr))
        sc, vk = await asyncio.gather(sc_task, vk_task)
        combined = []
        # Сначала добавляем результаты из VK (приоритет)
        for t in vk: combined.append({**t, 'source': 'vk'})
        # Затем добавляем результаты из SoundCloud
        for t in sc: combined.append({**t, 'source': 'soundcloud'})
        if not combined:
            await bot.edit_message_text("❌ ничего не нашел", chat_id=status.chat.id, message_id=status.message_id)
            return
        search_results[sid] = combined
        # Передаем флаг is_group=True
        kb = create_tracks_keyboard(combined, 0, sid, is_group=True)
        # Сокращаем текст сообщения для группы
        await bot.edit_message_text(f"🎵 найдено {len(combined)}", chat_id=status.chat.id, message_id=status.message_id, reply_markup=kb)
    except Exception as e:
        await bot.edit_message_text(f"❌ ошибка: {e}", chat_id=status.chat.id, message_id=status.message_id) 