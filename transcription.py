import os
import logging
import asyncio
import tempfile
import json
import subprocess
from typing import Optional

from deepgram import DeepgramClient, PrerecordedOptions

# --- Deepgram Speech SDK Setup ---
async def recognize_from_wav(wav_file_path: str, api_key: str) -> Optional[str]:
    """
    Transcribes speech from a WAV file using Deepgram API.
    
    Args:
        wav_file_path: Path to the WAV file
        api_key: Deepgram API key
        
    Returns:
        Transcribed text or None if failed
    """
    try:
        # Use httpx for async HTTP requests
        import httpx
        
        # Read the WAV file
        with open(wav_file_path, 'rb') as audio:
            audio_data = audio.read()
        
        # Prepare headers
        headers = {
            'Authorization': f'Token {api_key}',
            'Content-Type': 'audio/wav'
        }
        
        # Prepare parameters for the API call
        params = {
            'smart_format': 'true',
            'punctuate': 'true',
            'model': 'nova-2',
            'language': 'ru'  # Set language to Russian
        }
        
        # Make the API call
        logging.info(f"Sending request to Deepgram API for file: {wav_file_path}")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                'https://api.deepgram.com/v1/listen',
                headers=headers,
                params=params,
                content=audio_data
            )
            
            # Log response status
            logging.info(f"Deepgram API response status: {response.status_code}")
            
            # Check if the request was successful
            if response.status_code == 200:
                result = response.json()
                
                # Log summary of result
                alternatives = result.get('results', {}).get('channels', [{}])[0].get('alternatives', [])
                if alternatives:
                    confidence = alternatives[0].get('confidence', 0)
                    transcript = alternatives[0].get('transcript', '')
                    logging.info(f"Transcription successful with confidence: {confidence:.2f}, length: {len(transcript)} chars")
                    
                    # Return the transcript if not empty
                    if transcript.strip():
                        return transcript
                    else:
                        logging.warning("Empty transcript returned from Deepgram")
                else:
                    logging.warning("No alternatives in Deepgram response")
            else:
                # Log error details
                logging.error(f"Deepgram API error: {response.status_code} - {response.text}")
                
                # Try with fallback model if the request failed
                logging.info("Trying with fallback model...")
                fallback_params = {
                    'smart_format': 'true',
                    'model': 'general',  # Use general model as fallback
                    'language': 'ru'
                }
                
                response = await client.post(
                    'https://api.deepgram.com/v1/listen',
                    headers=headers,
                    params=fallback_params,
                    content=audio_data
                )
                
                if response.status_code == 200:
                    result = response.json()
                    alternatives = result.get('results', {}).get('channels', [{}])[0].get('alternatives', [])
                    if alternatives:
                        transcript = alternatives[0].get('transcript', '')
                        logging.info(f"Fallback transcription successful, length: {len(transcript)} chars")
                        if transcript.strip():
                            return transcript
                
                logging.error("Both primary and fallback transcription attempts failed")
    except Exception as e:
        logging.error(f"Error during speech recognition: {e}")
    
    return None

# --- FFmpeg Helper ---
async def convert_to_wav(input_file: str, output_file: str) -> bool:
    """
    Converts input media (OGG voice or MP4 video) to WAV format using FFmpeg.
    
    Args:
        input_file: Path to the input media file
        output_file: Path to save the output WAV file
        
    Returns:
        True if conversion was successful, False otherwise
    """
    try:
        # Construct the FFmpeg command
        # -y: Overwrite output file if it exists
        # -i: Input file
        # -ar 16000: Set audio sample rate to 16kHz (required by Deepgram)
        # -ac 1: Set to mono audio (1 channel)
        # -c:a pcm_s16le: Use 16-bit PCM audio codec
        command = [
            'ffmpeg',
            '-y',
            '-i', input_file,
            '-ar', '16000',
            '-ac', '1',
            '-c:a', 'pcm_s16le',
            output_file
        ]
        
        # Execute the command
        logging.info(f"Running conversion command: {' '.join(command)}")
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        # Check if conversion was successful
        if process.returncode == 0:
            logging.info(f"Conversion successful: {input_file} -> {output_file}")
            return True
        else:
            logging.error(f"FFmpeg conversion failed with code {process.returncode}: {stderr.decode()}")
            return False
    except Exception as e:
        logging.error(f"Error during media conversion: {e}")
        return False

# --- Main Processing Function ---
async def process_voice_or_video(message, sender_id, chat_id, message_id, client, api_key):
    """
    Processes voice or video messages: downloads the media, converts to WAV, and transcribes it.
    
    Args:
        message: The message object containing media (aiogram)
        sender_id: ID of the sender
        chat_id: ID of the chat
        message_id: ID of the message
        client: aiogram Bot instance
        api_key: Deepgram API key
        
    Returns:
        Transcribed text or None if processing failed
    """
    try:
        # Create temporary directory
        with tempfile.TemporaryDirectory() as temp_dir:
            # Define file paths
            media_file = message.voice or message.audio or message.video_note
            if not media_file:
                logging.error("Message does not contain voice/audio/video_note")
                return None
                
            # Получаем file_id из медиа-объекта
            file_id = media_file.file_id
            
            # В aiogram 3.x используем просто client.download()
            file_extension = "ogg" if message.voice else ("mp3" if message.audio else "mp4")
            downloaded_path = os.path.join(temp_dir, f"media_{message_id}.{file_extension}")
            
            # Скачиваем файл
            logging.info(f"Downloading media file ID {file_id} from message {message_id}")
            await client.download(
                media_file,
                destination=downloaded_path
            )
            
            if not os.path.exists(downloaded_path):
                logging.error(f"Failed to download media from message {message_id}")
                return None
            
            logging.info(f"Media downloaded to {downloaded_path}")
            
            # Check if the media is a video note
            is_video_note = hasattr(message, 'video_note') and message.video_note
            media_type = "video note" if is_video_note else "voice message" if message.voice else "audio"
            logging.info(f"Processing {media_type}")
            
            # Путь для WAV файла
            wav_path = os.path.join(temp_dir, f"audio_{message_id}.wav")
            
            # Convert the media to WAV format
            if await convert_to_wav(downloaded_path, wav_path):
                # Transcribe the audio
                logging.info(f"Sending {wav_path} for transcription")
                transcription = await recognize_from_wav(wav_path, api_key)
                
                if transcription:
                    return transcription
                else:
                    logging.warning(f"No transcription returned for message {message_id}")
                    return None
            else:
                logging.error(f"Failed to convert media to WAV format for message {message_id}")
                return None
    except Exception as e:
        logging.error(f"Error processing media message {message_id}: {e}")
        return None