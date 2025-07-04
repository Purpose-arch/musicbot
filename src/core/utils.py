# utils.py
# Utility functions for title/artist extraction and MP3 metadata

def extract_title_and_artist(title):
    """Улучшенное извлечение названия трека и исполнителя"""
    prefixes = ['Official Video', 'Official Music Video', 'Official Audio', 'Lyric Video', 'Lyrics', 'Topic']
    for prefix in prefixes:
        if title.lower().endswith(f" - {prefix.lower()}"):
            title = title[:-len(prefix)-3]
    
    separators = [' - ', ' — ', ' – ', ' | ', ' ~ ']
    for sep in separators:
        if sep in title:
            parts = title.split(sep)
            if len(parts) == 2:
                if len(parts[0]) > len(parts[1]):
                    return parts[0].strip(), parts[1].strip()
                else:
                    return parts[1].strip(), parts[0].strip()
    
    if len(title) > 30:
        return title, "Unknown Artist"
    elif any(char in title for char in ['(', '[', '{']):
        return title, "Unknown Artist"
    else:
        return title, "Unknown Artist"


def set_mp3_metadata(file_path, title, artist):
    """Sets ID3 metadata TIT2 and TPE1 on MP3 file"""
    try:
        from mutagen.id3 import ID3, TIT2, TPE1
        try:
            audio = ID3(file_path)
        except Exception:
            audio = ID3()
        audio["TIT2"] = TIT2(encoding=3, text=title)
        audio["TPE1"] = TPE1(encoding=3, text=artist)
        audio.save(file_path)
        return True
    except Exception as e:
        print(f"ошибка при установке метаданных: {e}")
        return False 