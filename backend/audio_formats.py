"""Shared audio container descriptors and bounded structural validation."""
from pathlib import Path

AUDIO_FORMATS = {
    ".mp3": {"format": "mp3", "extension": ".mp3", "mime_type": "audio/mpeg"},
    ".m4a": {"format": "m4a", "extension": ".m4a", "mime_type": "audio/mp4"},
    ".wav": {"format": "wav", "extension": ".wav", "mime_type": "audio/wav"},
}


def audio_format(path):
    try:
        return AUDIO_FORMATS[Path(path).suffix.lower()]
    except KeyError:
        raise ValueError("Supported audio formats are MP3, M4A, and WAV.") from None


def matches_audio_header(header: bytes, extension: str) -> bool:
    if extension == ".wav":
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE"
    if extension == ".m4a":
        if len(header) < 16 or header[4:8] != b"ftyp":
            return False
        size = int.from_bytes(header[:4], "big")
        return 16 <= size <= len(header) and size % 4 == 0 and header[8:12] in {
            b"M4A ", b"isom", b"iso2", b"mp41", b"mp42", b"qt  ",
        }
    if extension == ".mp3":
        # Do not let MP3's legacy sync-word scan mistake another container for MP3.
        if header[:4] == b"RIFF" or header[4:8] == b"ftyp":
            return False
        from backend.security import validate_mp3_magic_bytes
        return validate_mp3_magic_bytes(header)
    return False


def riff_chunks(path):
    """Return bounded top-level RIFF offsets; reject truncated or oversized chunks."""
    size = Path(path).stat().st_size
    with open(path, "rb") as f:
        header = f.read(12)
        if not matches_audio_header(header, ".wav"):
            raise ValueError("Invalid WAV container.")
        end = int.from_bytes(header[4:8], "little") + 8
        if end != size:
            raise ValueError("Invalid WAV container length.")
        chunks = []
        offset = 12
        while offset < end:
            if len(chunks) >= 10000 or offset + 8 > end:
                raise ValueError("Invalid WAV chunk layout.")
            f.seek(offset)
            chunk = f.read(8)
            length = int.from_bytes(chunk[4:], "little")
            next_offset = offset + 8 + length + length % 2
            if next_offset > end:
                raise ValueError("Truncated WAV chunk.")
            if chunk[:4] == b"fmt " and not 16 <= length <= 4096:
                raise ValueError("Invalid WAV format chunk.")
            chunks.append((chunk[:4], offset + 8, length))
            offset = next_offset
        if not any(k == b"fmt " and n >= 16 for k, _, n in chunks) or not any(k == b"data" and n > 0 for k, _, n in chunks):
            raise ValueError("WAV audio data is missing.")
        return chunks


def validate_audio_container(path):
    """Check container bounds before handing metadata to Mutagen."""
    extension = audio_format(path)["extension"]
    if extension == ".wav":
        riff_chunks(path)
    elif extension == ".m4a":
        size = Path(path).stat().st_size
        budget = 10000
        handlers = []
        top_types = []
        with open(path, "rb") as f:
            def boxes(start, end, level=0):
                nonlocal budget
                offset = start
                while offset < end:
                    budget -= 1
                    if budget < 0 or offset + 8 > end:
                        raise ValueError("Invalid M4A box layout.")
                    f.seek(offset)
                    header = f.read(8)
                    length = int.from_bytes(header[:4], "big")
                    kind = header[4:]
                    header_size = 8
                    if length == 1:
                        if offset + 16 > end:
                            raise ValueError("Truncated M4A box.")
                        length = int.from_bytes(f.read(8), "big")
                        header_size = 16
                    elif length == 0:
                        length = end - offset
                    if length < header_size or offset + length > end:
                        raise ValueError("Invalid M4A box size.")
                    if level == 0:
                        top_types.append(kind)
                    if (level, kind) in {(0, b"moov"), (1, b"trak"), (2, b"mdia")}:
                        boxes(offset + header_size, offset + length, level + 1)
                    elif level == 3 and kind == b"hdlr":
                        if length < header_size + 12:
                            raise ValueError("Invalid M4A track handler.")
                        f.seek(offset + header_size + 8)
                        handlers.append(f.read(4))
                    offset += length
            boxes(0, size)
        if b"moov" not in top_types or b"mdat" not in top_types or b"soun" not in handlers or b"vide" in handlers:
            raise ValueError("Expected an audio-only M4A file.")
