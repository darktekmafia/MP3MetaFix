"""Bridge existing RIFF INFO text tags to the shared editor; retain other chunks."""
import os
import shutil
import tempfile
from pathlib import Path
from backend.audio_formats import riff_chunks

INFO_FIELDS = {b"INAM": "title", b"IART": "artist", b"IPRD": "album", b"IGNR": "genre", b"ICRD": "year", b"ICMT": "comment", b"ITRK": "track_number", b"IMUS": "composer"}
MAX_INFO_BYTES = 1024 * 1024


def info_chunks(path):
    result = {}
    total = 0
    with open(path, "rb") as f:
        for kind, offset, length in riff_chunks(path):
            if kind != b"LIST" or length < 4:
                continue
            f.seek(offset)
            if f.read(4) != b"INFO":
                continue
            total += length
            if total > MAX_INFO_BYTES:
                raise ValueError("WAV INFO metadata is too large.")
            entries = []
            remaining = length - 4
            while remaining:
                if remaining < 8:
                    raise ValueError("Invalid WAV INFO tag.")
                header = f.read(8)
                size = int.from_bytes(header[4:], "little")
                padded = size + size % 2
                if padded + 8 > remaining:
                    raise ValueError("Truncated WAV INFO tag.")
                data = f.read(padded)
                entries.append((header[:4], data[:size]))
                remaining -= padded + 8
            result[offset] = entries
    return result


def read_info(path):
    values = {}
    for entries in info_chunks(path).values():
        for key, data in entries:
            if key in INFO_FIELDS:
                raw = data.rstrip(b"\x00")
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    text = raw.decode("cp1252", errors="replace")
                values.setdefault(INFO_FIELDS[key], text.strip())
    return values


def sync_existing_info(path, metadata):
    """Update mapped tags only in existing INFO lists; ID3 stores the full tag set."""
    lists = info_chunks(path)
    if not lists:
        return
    fd, name = tempfile.mkstemp(dir=Path(path).parent, suffix=".wav")
    try:
        with os.fdopen(fd, "wb") as out, open(path, "rb") as source:
            out.write(source.read(12))
            total_info = 0
            for kind, offset, length in riff_chunks(path):
                if offset in lists:
                    body = b"INFO"
                    for key, data in lists[offset]:
                        if key in INFO_FIELDS:
                            value = (getattr(metadata, INFO_FIELDS[key]) or "").strip()
                            if not value:
                                continue
                            data = value.encode("utf-8") + b"\x00"
                        entry = key + len(data).to_bytes(4, "little") + data + b"\x00" * (len(data) % 2)
                        if total_info + len(body) + len(entry) > MAX_INFO_BYTES:
                            raise ValueError("WAV INFO metadata is too large.")
                        body += entry
                    total_info += len(body)
                    out.write(b"LIST" + len(body).to_bytes(4, "little") + body)
                    out.write(b"\x00" * (len(body) % 2))
                else:
                    source.seek(offset - 8)
                    remaining = 8 + length + length % 2
                    while remaining:
                        chunk = source.read(min(remaining, 1024 * 1024))
                        if not chunk:
                            raise ValueError("Truncated WAV file.")
                        out.write(chunk)
                        remaining -= len(chunk)
            size = out.tell()
            out.seek(4)
            out.write((size - 8).to_bytes(4, "little"))
        shutil.copymode(path, name)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
