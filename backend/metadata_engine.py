"""Mutagen-based ID3 metadata and cover art manipulation engine."""

import io
import base64
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
import mutagen
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
    TBPM,
    APIC,
    PictureType,
    Encoding,
)
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
    """Inspect an MP3 file and extract all ID3 tags, stream info, and cover art."""
    try:
        audio = MP3(file_path)
    except HeaderNotFoundError:
        # Fallback to pure ID3 if MPEG header is slightly skewed
        try:
            tags = ID3(file_path)
            audio = None
        except Exception as e:
            raise ValueError(f"Could not parse MP3 file: {str(e)}")
    except Exception as e:
        raise ValueError(f"Invalid MP3 audio: {str(e)}")

    tags = audio.tags if audio and audio.tags else None
    if tags is None:
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

    # Audio stream properties
    duration = 0.0
    bitrate = 0
    sample_rate = 0
    channels = 0
    if audio and audio.info:
        duration = round(getattr(audio.info, "length", 0.0), 2)
        bitrate = int(getattr(audio.info, "bitrate", 0) / 1000)
        sample_rate = getattr(audio.info, "sample_rate", 0)
        channels = getattr(audio.info, "channels", 0)

    # Cover Art extraction
    has_artwork = False
    artwork_mime = None
    artwork_base64 = None
    artwork_size_bytes = 0

    if tags:
        for k, v in tags.items():
            if isinstance(v, APIC) or k.startswith("APIC"):
                has_artwork = True
                artwork_mime = v.mime if hasattr(v, "mime") and v.mime else "image/jpeg"
                img_data = v.data
                artwork_size_bytes = len(img_data)
                # Generate base64 thumbnail for frontend preview
                artwork_base64 = f"data:{artwork_mime};base64,{base64.b64encode(img_data).decode('ascii')}"
                break

    return {
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
        },
        "audio_info": {
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


def get_embedded_artwork_binary(file_path: Path) -> Optional[Tuple[bytes, str]]:
    """Retrieve raw artwork bytes and mime type from MP3 file."""
    try:
        tags = ID3(file_path)
    except Exception:
        return None

    for k, v in tags.items():
        if isinstance(v, APIC) or k.startswith("APIC"):
            mime = v.mime if hasattr(v, "mime") and v.mime else "image/jpeg"
            return v.data, mime
    return None


def write_metadata_and_artwork(
    file_path: Path,
    meta: MetadataModel,
    new_artwork_bytes: Optional[bytes] = None,
    new_artwork_mime: Optional[str] = None,
    id3_version: int = 4,  # ID3v2.4 default (UTF-8)
) -> None:
    """Write updated ID3 tags and cover art to the MP3 file."""
    try:
        tags = ID3(file_path)
    except ID3NoHeaderError:
        tags = ID3()
    except Exception as e:
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
