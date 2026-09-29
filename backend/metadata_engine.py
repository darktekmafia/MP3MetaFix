"""Shared MP3, M4A, and WAV metadata engine for every workspace."""

import os
import shutil
import tempfile
import base64
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from mutagen.mp4 import MP4, MP4Cover
from mutagen.wave import WAVE
from backend.audio_formats import audio_format, validate_audio_container
from backend.wave_metadata import read_info, sync_existing_info
from backend.security import validate_and_normalize_image
from backend.config import MAX_ARTWORK_SIZE_BYTES
from fastapi import HTTPException
from mutagen.mp3 import MP3, HeaderNotFoundError
from mutagen.id3 import (
    ID3,
    ID3NoHeaderError,
    TIT2,
    TPE1,
    TALB,
    TPE2,
    TCOM,
    TCON,
    TDRC,
    TYER,
    TRCK,
    TPOS,
    COMM,
    USLT,
    SYLT,
    TBPM,
    APIC,
    PictureType,
    Encoding,
)
import re
from pydantic import BaseModel, Field

class MetadataModel(BaseModel):
    title: Optional[str] = Field(default="", max_length=500, description="Track title")
    artist: Optional[str] = Field(default="", max_length=500, description="Artist name")
    album: Optional[str] = Field(default="", max_length=500, description="Album name")
    album_artist: Optional[str] = Field(default="", max_length=500, description="Album artist")
    composer: Optional[str] = Field(default="", max_length=500, description="Composer")
    genre: Optional[str] = Field(default="", max_length=500, description="Music genre")
    year: Optional[str] = Field(default="", max_length=100, description="Release year or date")
    track_number: Optional[str] = Field(default="", max_length=50, description="Track number")
    total_tracks: Optional[str] = Field(default="", max_length=50, description="Total tracks")
    disc_number: Optional[str] = Field(default="", max_length=50, description="Disc number")
    total_discs: Optional[str] = Field(default="", max_length=50, description="Total discs")
    bpm: Optional[str] = Field(default="", max_length=50, description="Beats per minute")
    comment: Optional[str] = Field(default="", max_length=10000, description="User comment")
    lyrics: Optional[str] = Field(default="", max_length=65536, description="Unsynchronized lyrics (max 64KB)")
    synced_lyrics: Optional[list] = Field(default=None, description="Synchronized lyrics entries list")
    custom_filename: Optional[str] = Field(default=None, max_length=255, description="Requested output filename")
    remove_artwork: Optional[bool] = Field(default=False, description="Flag to remove cover art")


def parse_split_slash(value: Optional[str]) -> Tuple[str, str]:
    """Parse 'X/Y' formatted strings (track/total or disc/total)."""
    if not value:
        return "", ""
    parts = str(value).split("/")
    first = parts[0].strip()
    second = parts[1].strip() if len(parts) > 1 else ""
    return first, second


def format_slash_field(first: Optional[str], second: Optional[str]) -> str:
    """Format first/second into 'X/Y' or 'X' string."""
    f = (first or "").strip()
    s = (second or "").strip()
    if f and s:
        return f"{f}/{s}"
    return f or s


def extract_metadata_and_artwork(file_path: Path) -> Dict[str, Any]:
    """Inspect a supported audio file and return normalized tags, stream info, and artwork."""
    descriptor = audio_format(file_path)
    validate_audio_container(file_path)
    if descriptor["format"] == "m4a":
        return _extract_m4a(file_path)
    try:
        audio = WAVE(file_path) if descriptor["format"] == "wav" else MP3(file_path)
    except HeaderNotFoundError:
        # Fallback to pure ID3 if MPEG header is slightly skewed
        try:
            tags = ID3(file_path)
            audio = None
        except Exception as e:
            raise ValueError(f"Could not parse MP3 file: {str(e)}")
    except Exception as e:
        raise ValueError(f"Invalid MP3 audio: {str(e)}")

    if descriptor["format"] == "wav" and (
        audio.info.audio_format not in (1, 3, 65534)
        or audio.info.channels <= 0 or audio.info.sample_rate <= 0
        or audio.info.bits_per_sample <= 0 or audio.info.length <= 0
    ):
        raise ValueError("Unsupported or invalid WAV audio stream.")
    tags = audio.tags if audio is not None and audio.tags is not None else None
    if tags is None and descriptor["format"] == "mp3":
        try:
            tags = ID3(file_path)
        except ID3NoHeaderError:
            tags = {}
        except Exception:
            tags = {}

    # Helper for text tags
    def get_text(frame_name: str, default: str = "") -> str:
        if tags and frame_name in tags:
            frame = tags[frame_name]
            if hasattr(frame, "text") and frame.text:
                return str(frame.text[0]).strip()
        return default

    # Track and Disc parsing
    raw_track = get_text("TRCK")
    track_num, total_tracks = parse_split_slash(raw_track)

    raw_disc = get_text("TPOS")
    disc_num, total_discs = parse_split_slash(raw_disc)

    # Year (check TDRC first, then TYER)
    year = get_text("TDRC")
    if not year:
        year = get_text("TYER")

    # Comment (find COMM frame)
    comment = ""
    if tags:
        for k, v in tags.items():
            if k.startswith("COMM"):
                if hasattr(v, "text") and v.text:
                    comment = str(v.text[0]).strip()
                    break

    # Lyrics (find USLT frame)
    lyrics = ""
    if tags:
        for k, v in tags.items():
            if k.startswith("USLT"):
                if hasattr(v, "text") and v.text:
                    lyrics = str(v.text).strip()
                    break

    # Synced lyrics (find SYLT frame)
    synced_lyrics = []
    if tags:
        for k, v in tags.items():
            if k.startswith("SYLT"):
                if hasattr(v, "text") and v.text:
                    for item in v.text:
                        if isinstance(item, (list, tuple)) and len(item) >= 2:
                            synced_lyrics.append({"text": str(item[0]), "time_ms": int(item[1])})
                    break

    # Audio stream properties
    duration = 0.0
    bitrate = 0
    sample_rate = 0
    channels = 0
    if audio is not None and audio.info is not None:
        duration = round(getattr(audio.info, "length", 0.0), 2)
        bitrate = int(getattr(audio.info, "bitrate", 0) / 1000)
        sample_rate = getattr(audio.info, "sample_rate", 0)
        channels = getattr(audio.info, "channels", 0)

    # Cover Art extraction
    has_artwork = False
    artwork_mime = None
    artwork_base64 = None
    artwork_size_bytes = 0

    art = get_embedded_artwork_binary(file_path)
    if art:
        img_data, artwork_mime = art
        has_artwork = True
        artwork_size_bytes = len(img_data)
        artwork_base64 = f"data:{artwork_mime};base64,{base64.b64encode(img_data).decode('ascii')}"

    result = {
        "metadata": {
            "title": get_text("TIT2"),
            "artist": get_text("TPE1"),
            "album": get_text("TALB"),
            "album_artist": get_text("TPE2"),
            "composer": get_text("TCOM"),
            "genre": get_text("TCON"),
            "year": year,
            "track_number": track_num,
            "total_tracks": total_tracks,
            "disc_number": disc_num,
            "total_discs": total_discs,
            "bpm": get_text("TBPM"),
            "comment": comment,
            "lyrics": lyrics,
            "synced_lyrics": synced_lyrics,
        },
        "audio_info": {
            **descriptor,
            "duration": duration,
            "bitrate_kbps": bitrate,
            "sample_rate_hz": sample_rate,
            "channels": channels,
            "file_size_bytes": file_path.stat().st_size if file_path.exists() else 0,
        },
        "artwork": {
            "has_artwork": has_artwork,
            "mime_type": artwork_mime,
            "size_bytes": artwork_size_bytes,
            "preview_data_url": artwork_base64,
        }
    }

    if descriptor["format"] == "wav":
        info_tags = read_info(file_path)
        # INFO is a fallback only: explicit ID3 values take precedence.
        for key, value in info_tags.items():
            if not result["metadata"].get(key):
                result["metadata"][key] = value
    return result


def get_embedded_artwork_binary(file_path: Path) -> Optional[Tuple[bytes, str]]:
    """Return normalized raster artwork with a verified MIME type."""
    try:
        descriptor = audio_format(file_path)
        if descriptor["format"] == "m4a":
            return _m4a_artwork(MP4(file_path).tags or {})
        tags = WAVE(file_path).tags if descriptor["format"] == "wav" else ID3(file_path)
    except Exception:
        return None
    if not tags:
        return None

    for k, v in tags.items():
        if isinstance(v, APIC) or k.startswith("APIC"):
            return _safe_artwork(v.data)
    return None


def _write_id3(
    file_path: Path,
    meta: MetadataModel,
    new_artwork_bytes: Optional[bytes] = None,
    new_artwork_mime: Optional[str] = None,
    id3_version: int = 4,  # ID3v2.4 default (UTF-8)
) -> None:
    """Write updated ID3 tags and cover art to an MP3 or WAV file."""
    if audio_format(file_path)["format"] == "wav":
        audio = WAVE(file_path)
        if audio.tags is None:
            audio.add_tags()
        tags = audio.tags
    else:
        try:
            tags = ID3(file_path)
        except ID3NoHeaderError:
            tags = ID3()

    # Text Encoding: UTF-8 for v2.4 / v2.3
    enc = Encoding.UTF8

    # Helper to set or remove text frame
    def set_frame(frame_cls, value: Optional[str]):
        val = (value or "").strip()
        frame_name = frame_cls.__name__
        if val:
            tags.setall(frame_name, [frame_cls(encoding=enc, text=[val])])
        else:
            tags.delall(frame_name)

    set_frame(TIT2, meta.title)
    set_frame(TPE1, meta.artist)
    set_frame(TALB, meta.album)
    set_frame(TPE2, meta.album_artist)
    set_frame(TCOM, meta.composer)
    set_frame(TCON, meta.genre)
    set_frame(TBPM, meta.bpm)

    # Year (TDRC / TYER)
    year_val = (meta.year or "").strip()
    if year_val:
        tags.setall("TDRC", [TDRC(encoding=enc, text=[year_val])])
        tags.setall("TYER", [TYER(encoding=enc, text=[year_val])])
    else:
        tags.delall("TDRC")
        tags.delall("TYER")

    # Track / Total
    trck_val = format_slash_field(meta.track_number, meta.total_tracks)
    if trck_val:
        tags.setall("TRCK", [TRCK(encoding=enc, text=[trck_val])])
    else:
        tags.delall("TRCK")

    # Disc / Total
    tpos_val = format_slash_field(meta.disc_number, meta.total_discs)
    if tpos_val:
        tags.setall("TPOS", [TPOS(encoding=enc, text=[tpos_val])])
    else:
        tags.delall("TPOS")

    # Comment
    comm_val = (meta.comment or "").strip()
    tags.delall("COMM")
    if comm_val:
        tags.add(COMM(encoding=enc, lang="eng", desc="", text=[comm_val]))

    # Lyrics
    lyrics_val = (meta.lyrics or "").strip()
    tags.delall("USLT")
    if lyrics_val:
        tags.add(USLT(encoding=enc, lang="eng", desc="", text=lyrics_val))

    # Synced Lyrics (SYLT)
    if meta.synced_lyrics is not None:
        tags.delall("SYLT")
        if meta.synced_lyrics:
            sylt_entries = []
            for entry in meta.synced_lyrics:
                if isinstance(entry, dict):
                    txt = str(entry.get("text", "")).strip()
                    t_ms = int(entry.get("time_ms", 0))
                elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                    txt = str(entry[0]).strip()
                    t_ms = int(entry[1])
                else:
                    continue
                if txt or t_ms >= 0:
                    sylt_entries.append((txt, t_ms))
            if sylt_entries:
                tags.add(SYLT(encoding=enc, lang="eng", format=2, type=1, desc="", text=sylt_entries))

    # Artwork Handling
    if meta.remove_artwork:
        tags.delall("APIC")
    elif new_artwork_bytes:
        tags.delall("APIC")
        tags.add(
            APIC(
                encoding=Encoding.LATIN1,
                mime=new_artwork_mime or "image/jpeg",
                type=PictureType.COVER_FRONT,
                desc="Cover",
                data=new_artwork_bytes,
            )
        )

    # Save to file
    tags.save(file_path, v2_version=id3_version)
    if audio_format(file_path)["format"] == "wav":
        sync_existing_info(file_path, meta)


M4A_TEXT_FIELDS = {
    "title": "\xa9nam", "artist": "\xa9ART", "album": "\xa9alb",
    "album_artist": "aART", "composer": "\xa9wrt", "genre": "\xa9gen",
    "year": "\xa9day", "comment": "\xa9cmt", "lyrics": "\xa9lyr",
}


class MetadataValidationError(ValueError):
    """A user field cannot be represented by the selected audio format."""


def _m4a_number(value, field):
    text = (value or "").strip()
    if not text:
        return 0
    if not text.isascii() or not text.isdecimal() or not 0 <= int(text) <= 65535:
        raise MetadataValidationError(f"M4A {field} must be a whole number from 0 to 65535, or blank.")
    return int(text)


def _safe_artwork(data):
    """Never trust embedded MIME or emit active content; preserve original tags on disk."""
    try:
        return validate_and_normalize_image(data, MAX_ARTWORK_SIZE_BYTES)
    except HTTPException:
        return None


def _m4a_artwork(tags):
    covers = tags.get("covr", [])
    if not covers:
        return None
    cover = covers[0]
    mime = "image/png" if cover.imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
    return _safe_artwork(bytes(cover))


def _extract_m4a(file_path):
    audio = MP4(file_path)
    codec = audio.info.codec or ""
    if not (codec.startswith("mp4a") or codec in ("alac", "Opus")):
        raise ValueError("Unsupported M4A audio codec; expected AAC, ALAC, or Opus.")
    tags = audio.tags or {}
    metadata = {}
    for field, atom in M4A_TEXT_FIELDS.items():
        values = tags.get(atom, [])
        metadata[field] = str(values[0]).strip() if values else ""
    for atom, first, total in [("trkn", "track_number", "total_tracks"), ("disk", "disc_number", "total_discs")]:
        values = tags.get(atom, [(0, 0)])[0]
        metadata[first], metadata[total] = (str(value) if value else "" for value in values)
    bpm = tags.get("tmpo", [0])[0]
    metadata["bpm"] = str(bpm) if bpm else ""
    metadata["synced_lyrics"] = []
    art = _m4a_artwork(tags)
    return {
        "metadata": metadata,
        "audio_info": {
            **audio_format(file_path),
            "duration": round(audio.info.length, 2),
            "bitrate_kbps": int(audio.info.bitrate / 1000),
            "sample_rate_hz": audio.info.sample_rate,
            "channels": audio.info.channels,
            "file_size_bytes": file_path.stat().st_size,
        },
        "artwork": {
            "has_artwork": art is not None,
            "mime_type": art[1] if art else None,
            "size_bytes": len(art[0]) if art else 0,
            "preview_data_url": f"data:{art[1]};base64,{base64.b64encode(art[0]).decode('ascii')}" if art else None,
        },
    }


def _write_m4a(file_path, meta, artwork, mime):
    # Validate every integer before changing any tags.
    numeric = {field: _m4a_number(getattr(meta, field), field.replace("_", " "))
               for field in ("track_number", "total_tracks", "disc_number", "total_discs", "bpm")}
    audio = MP4(file_path)
    if audio.tags is None:
        audio.add_tags()
    for field, atom in M4A_TEXT_FIELDS.items():
        value = (getattr(meta, field) or "").strip()
        if value:
            audio.tags[atom] = [value]
        else:
            audio.tags.pop(atom, None)
    for atom, first, total in [("trkn", "track_number", "total_tracks"), ("disk", "disc_number", "total_discs")]:
        if numeric[first] or numeric[total]:
            audio.tags[atom] = [(numeric[first], numeric[total])]
        else:
            audio.tags.pop(atom, None)
    if numeric["bpm"]:
        audio.tags["tmpo"] = [numeric["bpm"]]
    else:
        audio.tags.pop("tmpo", None)
    if meta.remove_artwork:
        audio.tags.pop("covr", None)
    elif artwork:
        if mime not in ("image/jpeg", "image/png"):
            raise MetadataValidationError("M4A artwork must be JPEG or PNG.")
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        audio.tags["covr"] = [MP4Cover(artwork, imageformat=fmt)]
    audio.save()


def write_metadata_and_artwork(file_path: Path, meta: MetadataModel,
                               new_artwork_bytes=None, new_artwork_mime=None,
                               id3_version=4):
    """Save tags atomically in the original format without re-encoding audio."""
    descriptor = audio_format(file_path)
    fd, name = tempfile.mkstemp(dir=file_path.parent, prefix=".save-", suffix=descriptor["extension"])
    os.close(fd)
    temporary = Path(name)
    try:
        shutil.copy2(file_path, temporary)
        if descriptor["format"] == "m4a":
            _write_m4a(temporary, meta, new_artwork_bytes, new_artwork_mime)
        else:
            _write_id3(temporary, meta, new_artwork_bytes, new_artwork_mime, id3_version)
        validate_audio_container(temporary)
        os.replace(temporary, file_path)
    finally:
        temporary.unlink(missing_ok=True)


def sylt_to_lrc(entries: list, artist: str = "", title: str = "", album: str = "") -> str:
    """Format SYLT list of (text, time_ms) entries into standard LRC format."""
    lines = []
    if title:
        lines.append(f"[ti:{title}]")
    if artist:
        lines.append(f"[ar:{artist}]")
    if album:
        lines.append(f"[al:{album}]")
    lines.append("[by:MP3MetaFix]")

    for item in entries:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            txt, t_ms = str(item[0]), int(item[1])
        elif isinstance(item, dict):
            txt, t_ms = str(item.get("text", "")), int(item.get("time_ms", 0))
        else:
            continue

        minutes = int(t_ms // 60000)
        seconds = int((t_ms % 60000) // 1000)
        hundredths = int((t_ms % 1000) // 10)
        lines.append(f"[{minutes:02d}:{seconds:02d}.{hundredths:02d}]{txt}")

    return "\n".join(lines)


def lrc_to_sylt(lrc_text: str) -> list:
    """Parse standard LRC formatted text into list of (text, time_ms) entries."""
    entries = []
    time_regex = re.compile(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]")
    for raw_line in lrc_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^\[[a-zA-Z]+:.*\]$", line):
            continue

        matches = list(time_regex.finditer(line))
        if not matches:
            continue

        clean_text = time_regex.sub("", line).strip()
        for m in matches:
            mins = int(m.group(1))
            secs = int(m.group(2))
            fraction_str = m.group(3) or "0"
            if len(fraction_str) == 1:
                ms = int(fraction_str) * 100
            elif len(fraction_str) == 2:
                ms = int(fraction_str) * 10
            else:
                ms = int(fraction_str[:3])

            total_ms = (mins * 60 + secs) * 1000 + ms
            entries.append({"text": clean_text, "time_ms": total_ms})

    entries.sort(key=lambda x: x["time_ms"])
    return entries
