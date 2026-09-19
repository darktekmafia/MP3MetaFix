"""Unit and Integration tests for MP3MetaFix backend and security layer."""

import io
import time
import pytest
from pathlib import Path
from PIL import Image
from fastapi.testclient import TestClient
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, TRCK, APIC, PictureType, Encoding

from backend.main import app
from backend.config import VERSION, TEMP_DIR, SESSION_COOKIE_NAME
from backend.security import (
    sanitize_filename,
    validate_mp3_magic_bytes,
    validate_and_normalize_image,
    create_signed_session_token,
    verify_signed_session_token,
    get_storage_dir_name,
)
from backend.metadata_engine import (
    MetadataModel,
    extract_metadata_and_artwork,
    write_metadata_and_artwork,
    get_embedded_artwork_binary,
)
from backend.storage import SessionManager


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def sample_mp3_bytes() -> bytes:
    """Generate a minimal valid MP3 byte stream with ID3v2 header."""
    id3_header = b"ID3\x04\x00\x00\x00\x00\x00\x00"
    mpeg_frame = b"\xff\xfb\x90\x44" + (b"\x00" * 413)
    return id3_header + (mpeg_frame * 5)


@pytest.fixture
def sample_image_bytes() -> bytes:
    """Generate a valid 100x100 RGB JPEG image."""
    img = Image.new("RGB", (100, 100), color=(50, 100, 200))
    out = io.BytesIO()
    img.save(out, format="JPEG")
    return out.getvalue()


# --- Security & Sanitization Tests ---

def test_sanitize_filename():
    assert sanitize_filename("../../../etc/passwd") == "passwd.mp3"
    assert sanitize_filename("song: test*?.mp3") == "song test.mp3"
    assert sanitize_filename("Queen - Bohemian Rhapsody") == "Queen - Bohemian Rhapsody.mp3"
    assert sanitize_filename("   .hidden.mp3   ") == "hidden.mp3"
    assert sanitize_filename("") == "track.mp3"


def test_validate_mp3_magic_bytes(sample_mp3_bytes):
    assert validate_mp3_magic_bytes(sample_mp3_bytes) is True
    assert validate_mp3_magic_bytes(b"MZ\x90\x00") is False  # Windows PE exe
    assert validate_mp3_magic_bytes(b"\x7fELF") is False  # Linux ELF
    assert validate_mp3_magic_bytes(b"<?php echo 'hi'; ?>") is False


def test_validate_and_normalize_image(sample_image_bytes):
    clean_bytes, mime = validate_and_normalize_image(sample_image_bytes, 10 * 1024 * 1024)
    assert mime == "image/jpeg"
    assert len(clean_bytes) > 0

    # Test PNG converted / kept
    png_img = Image.new("RGBA", (50, 50), color=(255, 0, 0, 128))
    png_out = io.BytesIO()
    png_img.save(png_out, format="PNG")
    clean_png, png_mime = validate_and_normalize_image(png_out.getvalue(), 10 * 1024 * 1024)
    assert png_mime == "image/png"

    # Test non-image rejection
    with pytest.raises(Exception):
        validate_and_normalize_image(b"not an image", 10 * 1024 * 1024)


def test_cryptographic_session_tokens():
    import uuid
    valid_id = str(uuid.uuid4())
    token = create_signed_session_token(valid_id)
    assert "." in token

    # Verification passes for legitimate token
    verified = verify_signed_session_token(token)
    assert verified == valid_id

    # Tampered signature fails
    tampered_token = f"{valid_id}.0000000000000000000000000000000000000000000000000000000000000000"
    assert verify_signed_session_token(tampered_token) is None

    # Invalid UUID format fails
    invalid_token = create_signed_session_token("not-a-valid-uuid")
    assert verify_signed_session_token(invalid_token) is None

    # Blank / corrupted token fails
    assert verify_signed_session_token("") is None
    assert verify_signed_session_token("malformed") is None


def test_storage_dir_hashing():
    import uuid
    id1 = str(uuid.uuid4())
    id2 = str(uuid.uuid4())
    hash1 = get_storage_dir_name(id1)
    hash2 = get_storage_dir_name(id2)

    assert len(hash1) == 32
    assert hash1 != id1
    assert hash1 != hash2


# --- Metadata Engine Tests ---

def test_metadata_write_and_read(tmp_path: Path, sample_mp3_bytes, sample_image_bytes):
    test_file = tmp_path / "test_track.mp3"
    test_file.write_bytes(sample_mp3_bytes)

    meta = MetadataModel(
        title="Test Song",
        artist="Test Artist",
        album="Test Album",
        year="2026",
        track_number="3",
        total_tracks="10",
        genre="Synthwave",
        comment="Antigravity Test",
    )

    write_metadata_and_artwork(
        file_path=test_file,
        meta=meta,
        new_artwork_bytes=sample_image_bytes,
        new_artwork_mime="image/jpeg",
    )

    result = extract_metadata_and_artwork(test_file)
    extracted_meta = result["metadata"]

    assert extracted_meta["title"] == "Test Song"
    assert extracted_meta["artist"] == "Test Artist"
    assert extracted_meta["album"] == "Test Album"
    assert extracted_meta["year"] == "2026"
    assert extracted_meta["track_number"] == "3"
    assert extracted_meta["total_tracks"] == "10"
    assert extracted_meta["genre"] == "Synthwave"
    assert extracted_meta["comment"] == "Antigravity Test"

    assert result["artwork"]["has_artwork"] is True
    assert result["artwork"]["mime_type"] == "image/jpeg"


# --- API Endpoint Integration Tests ---

def test_api_health_and_version(client):
    res_health = client.get("/api/health")
    assert res_health.status_code == 200
    assert res_health.json()["status"] == "ok"

    res_ver = client.get("/api/version")
    assert res_ver.status_code == 200
    assert res_ver.json()["version"] == VERSION


def test_api_upload_invalid_file(client):
    response = client.post(
        "/api/upload",
        files={"file": ("malicious.exe", b"MZ\x90\x00ThisIsNotAnMP3", "audio/mpeg")},
    )
    assert response.status_code == 400
    assert "not a valid MP3" in response.json()["detail"]


def test_api_unauthenticated_access_rejected(client):
    """Verify protected endpoints return 401 without session cookie."""
    assert client.get("/api/artwork").status_code == 401
    assert client.post("/api/save", json={"title": "Test"}).status_code == 401
    assert client.get("/api/stream").status_code == 401
    assert client.get("/api/download").status_code == 401
    assert client.delete("/api/session").status_code == 401


def test_api_cookie_authenticated_full_workflow(client, sample_mp3_bytes, sample_image_bytes):
    # 1. Upload valid MP3 (sets HttpOnly cookie)
    upload_res = client.post(
        "/api/upload",
        files={"file": ("my_audio.mp3", sample_mp3_bytes, "audio/mpeg")},
    )
    assert upload_res.status_code == 200
    assert SESSION_COOKIE_NAME in upload_res.cookies
    data = upload_res.json()
    assert "session_id" not in data  # Ensure raw session_id is NOT leaked
    assert data["success"] is True

    # 2. Upload cover art (cookie sent automatically)
    art_res = client.post(
        "/api/artwork",
        files={"image": ("cover.jpg", sample_image_bytes, "image/jpeg")},
    )
    assert art_res.status_code == 200
    assert art_res.json()["success"] is True

    # 3. Save modified metadata
    save_payload = {
        "title": "Cosmic Drift",
        "artist": "SynthMaster",
        "album": "Neon Galaxy",
        "year": "2026",
        "genre": "Synthwave",
        "custom_filename": "SynthMaster - Cosmic Drift.mp3",
    }
    save_res = client.post("/api/save", json=save_payload)
    assert save_res.status_code == 200
    save_data = save_res.json()
    assert save_data["target_filename"] == "SynthMaster - Cosmic Drift.mp3"
    assert save_data["metadata"]["title"] == "Cosmic Drift"
    assert save_data["artwork"]["has_artwork"] is True

    # 4. Stream audio
    stream_res = client.get("/api/stream")
    assert stream_res.status_code in (200, 206)

    # 5. Download modified MP3 (both plain and named download paths)
    download_res = client.get("/api/download")
    assert download_res.status_code == 200
    assert "attachment; filename=" in download_res.headers.get("content-disposition", "")
    assert len(download_res.content) > 0

    download_named_res = client.get("/api/download/SynthMaster%20-%20Cosmic%20Drift.mp3")
    assert download_named_res.status_code == 200
    assert "SynthMaster - Cosmic Drift.mp3" in download_named_res.headers.get("content-disposition", "")
    assert len(download_named_res.content) > 0

    # 6. Delete session (clears cookie)
    del_res = client.delete("/api/session")
    assert del_res.status_code == 200
    assert del_res.json()["success"] is True

    # Subsequent access without active session should fail
    assert client.get("/api/stream").status_code in (401, 404)


def test_storage_ttl_cleanup(tmp_path: Path):
    mgr = SessionManager(temp_dir=tmp_path, ttl_minutes=1)
    session_id, audio_path = mgr.create_session("old_file.mp3")
    audio_path.write_bytes(b"dummy")

    sdir = mgr.get_session_dir(session_id)
    assert sdir is not None

    # Manually backdate the session
    meta_file = sdir / "session.json"
    meta_file.write_text('{"last_accessed_at": 1000, "created_at": 1000}')

    # Prune
    pruned_count = mgr.prune_expired_sessions()
    assert pruned_count == 1
    assert mgr.get_session_dir(session_id) is None

