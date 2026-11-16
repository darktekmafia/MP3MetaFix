"""Integration tests for MP3Projects and File Storage REST API endpoints."""

import io
import zipfile
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.auth import auth_manager, create_auth_token, AUTH_COOKIE_NAME
from backend.config import SESSION_COOKIE_NAME
from backend.security import create_signed_session_token
from backend.project_storage import project_storage_manager
from backend.storage import storage_manager

# Minimal synthetic MP3 header: ID3v2 container header
VALID_MP3_HEADER = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x44" + (b"\x00" * 100)
# Minimal synthetic WAV header: RIFF WAVE fmt
VALID_WAV_HEADER = b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
# Minimal 1x1 JPEG
VALID_JPEG_BYTES = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
    b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
    b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342"
    b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
    b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
)


@pytest.fixture(autouse=True)
def setup_auth_environment(tmp_path, monkeypatch):
    """Ensure a clean, isolated auth state and project storage for each test."""
    from backend.auth import AuthManager, login_rate_limiter
    from backend.project_storage import ProjectStorageManager
    auth_dir = tmp_path / "auth"
    test_auth_mgr = AuthManager(auth_dir=auth_dir)
    monkeypatch.setattr("backend.main.auth_manager", test_auth_mgr)
    monkeypatch.setattr("backend.auth.auth_manager", test_auth_mgr)
    monkeypatch.setattr("backend.projects_api.auth_manager", test_auth_mgr)
    
    test_storage = ProjectStorageManager(root_dir=tmp_path / "tenants")
    monkeypatch.setattr("backend.projects_api.project_storage_manager", test_storage)

    test_temp_dir = tmp_path / "temp"
    test_temp_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(storage_manager, "temp_dir", test_temp_dir)

    from backend.security import upload_rate_limiter
    login_rate_limiter.failed_attempts.clear()
    login_rate_limiter.blocked_until.clear()
    upload_rate_limiter.history.clear()
    admin_user = test_auth_mgr.create_initial_admin("admin", "AdminPass123!")
    return {"manager": test_auth_mgr, "admin_user": admin_user}


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_cookies(setup_auth_environment):
    """Generate cookies for an authenticated admin user."""
    mgr = setup_auth_environment["manager"]
    admin = setup_auth_environment["admin_user"]
    token = mgr.issue_token(admin["id"])
    return {AUTH_COOKIE_NAME: token}


def test_unauthenticated_access_denied(client):
    """Verify persistent storage routes reject unauthenticated requests with 401."""
    res = client.get("/api/projects/tracks")
    assert res.status_code == 401

    res = client.post("/api/projects/tracks", json={"title": "Unauthorized"})
    assert res.status_code == 401

    res = client.get("/api/projects/albums")
    assert res.status_code == 401

    res = client.get("/api/storage/quota")
    assert res.status_code == 401


def test_track_workspace_api_crud(client, auth_cookies):
    """Test full Track Workspace CRUD via REST API."""
    # 1. Create track
    payload = {
        "title": "Neon Horizon",
        "artist": "Synthwave Boy",
        "bpm": 124,
        "musical_key": "A minor",
        "notes": "Work in progress take tree",
        "master_lyrics": "Driving under neon signs",
    }
    res = client.post("/api/projects/tracks", json=payload, cookies=auth_cookies)
    assert res.status_code == 201
    track = res.json()
    track_id = track["id"]
    assert track["title"] == "Neon Horizon"
    assert track["artist"] == "Synthwave Boy"
    assert track["bpm"] == 124
    assert track["status"] == "draft"

    # 2. Get track detail
    res = client.get(f"/api/projects/tracks/{track_id}", cookies=auth_cookies)
    assert res.status_code == 200
    detail = res.json()
    assert detail["track"]["id"] == track_id
    assert detail["takes"] == []

    # 3. List tracks
    res = client.get("/api/projects/tracks", cookies=auth_cookies)
    assert res.status_code == 200
    tracks = res.json()
    assert any(t["id"] == track_id for t in tracks)

    # 4. Patch track
    res = client.patch(
        f"/api/projects/tracks/{track_id}",
        json={"status": "in_progress", "bpm": 128},
        cookies=auth_cookies,
    )
    assert res.status_code == 200
    updated = res.json()
    assert updated["status"] == "in_progress"
    assert updated["bpm"] == 128

    # 5. Delete track
    res = client.delete(f"/api/projects/tracks/{track_id}", cookies=auth_cookies)
    assert res.status_code == 200
    assert res.json()["status"] == "deleted"

    # Verify 404 after deletion
    res = client.get(f"/api/projects/tracks/{track_id}", cookies=auth_cookies)
    assert res.status_code == 404


def test_take_upload_and_audio_streaming(client, auth_cookies):
    """Test take creation, audio upload, and HTTP 206 Partial Content range streaming."""
    # Create track
    res = client.post("/api/projects/tracks", json={"title": "Streaming Track"}, cookies=auth_cookies)
    track_id = res.json()["id"]

    # Upload Take
    files = {"file": ("demo_take.mp3", io.BytesIO(VALID_MP3_HEADER), "audio/mpeg")}
    data = {
        "label": "First Vocal Take",
        "prompt": "Cyberpunk synth vocals",
        "notes": "Good take, needs mixing",
        "is_master": "true",
    }
    res = client.post(
        f"/api/projects/tracks/{track_id}/takes",
        files=files,
        data=data,
        cookies=auth_cookies,
    )
    assert res.status_code == 201
    take = res.json()
    take_id = take["id"]
    assert take["label"] == "First Vocal Take"
    assert take["is_master"] is True

    # Check track detail contains take
    res = client.get(f"/api/projects/tracks/{track_id}", cookies=auth_cookies)
    detail = res.json()
    assert len(detail["takes"]) == 1
    assert detail["track"]["primary_take_id"] == take_id

    # Test HTTP 200 full stream
    res = client.get(f"/api/projects/tracks/{track_id}/takes/{take_id}/stream", cookies=auth_cookies)
    assert res.status_code == 200
    assert res.headers["accept-ranges"] == "bytes"
    assert int(res.headers["content-length"]) == len(VALID_MP3_HEADER)
    assert res.content == VALID_MP3_HEADER

    # Test HTTP 206 Partial Content range stream
    headers = {"Range": "bytes=0-9"}
    res = client.get(
        f"/api/projects/tracks/{track_id}/takes/{take_id}/stream",
        headers=headers,
        cookies=auth_cookies,
    )
    assert res.status_code == 206
    assert res.headers["content-range"] == f"bytes 0-9/{len(VALID_MP3_HEADER)}"
    assert res.content == VALID_MP3_HEADER[:10]

    # Test Download
    res = client.get(f"/api/projects/tracks/{track_id}/takes/{take_id}/download", cookies=auth_cookies)
    assert res.status_code == 200
    assert "attachment" in res.headers["content-disposition"]
    assert res.content == VALID_MP3_HEADER

    # Clean up track
    client.delete(f"/api/projects/tracks/{track_id}", cookies=auth_cookies)


def test_stem_upload_and_management(client, auth_cookies):
    """Test separated stem upload, role assignment, and stem deletion."""
    res = client.post("/api/projects/tracks", json={"title": "Stem Studio Song"}, cookies=auth_cookies)
    track_id = res.json()["id"]

    res = client.post(
        f"/api/projects/tracks/{track_id}/takes",
        files={"file": ("mix.wav", io.BytesIO(VALID_WAV_HEADER), "audio/wav")},
        data={"label": "Full Mix"},
        cookies=auth_cookies,
    )
    take_id = res.json()["id"]

    # Upload Vocals Stem
    res = client.post(
        f"/api/projects/tracks/{track_id}/takes/{take_id}/stems",
        files={"file": ("vocals_isolated.wav", io.BytesIO(VALID_WAV_HEADER), "audio/wav")},
        data={"role": "vocals"},
        cookies=auth_cookies,
    )
    assert res.status_code == 201
    stem = res.json()
    stem_id = stem["id"]
    assert stem["role"] == "vocals"

    # Upload Guitar Stem
    res_guitar = client.post(
        f"/api/projects/tracks/{track_id}/takes/{take_id}/stems",
        files={"file": ("lead_guitar.wav", io.BytesIO(VALID_WAV_HEADER), "audio/wav")},
        data={"role": "guitar"},
        cookies=auth_cookies,
    )
    assert res_guitar.status_code == 201
    assert res_guitar.json()["role"] == "guitar"

    # Stream stem
    res = client.get(
        f"/api/projects/tracks/{track_id}/takes/{take_id}/stems/{stem_id}/stream",
        cookies=auth_cookies,
    )
    assert res.status_code == 200
    assert res.content == VALID_WAV_HEADER

    # Delete stem
    res = client.delete(
        f"/api/projects/tracks/{track_id}/takes/{take_id}/stems/{stem_id}",
        cookies=auth_cookies,
    )
    assert res.status_code == 200
    assert res.json()["status"] == "deleted"

    # Clean up track
    client.delete(f"/api/projects/tracks/{track_id}", cookies=auth_cookies)


def test_stem_downloads_and_zip_pack(client, auth_cookies):
    """Test individual stem download and full take stem pack ZIP generation."""
    res = client.post("/api/projects/tracks", json={"title": "Zip Test Track"}, cookies=auth_cookies)
    track_id = res.json()["id"]

    res = client.post(
        f"/api/projects/tracks/{track_id}/takes",
        files={"file": ("main_mix.wav", io.BytesIO(VALID_WAV_HEADER), "audio/wav")},
        data={"label": "Take 1"},
        cookies=auth_cookies,
    )
    take_id = res.json()["id"]

    # Upload Vocals stem
    res_vox = client.post(
        f"/api/projects/tracks/{track_id}/takes/{take_id}/stems",
        files={"file": ("lead_vox.wav", io.BytesIO(VALID_WAV_HEADER), "audio/wav")},
        data={"role": "lead_vocals"},
        cookies=auth_cookies,
    )
    assert res_vox.status_code == 201
    vox_id = res_vox.json()["id"]

    # Upload Bass stem
    res_bass = client.post(
        f"/api/projects/tracks/{track_id}/takes/{take_id}/stems",
        files={"file": ("electric_bass.wav", io.BytesIO(VALID_WAV_HEADER), "audio/wav")},
        data={"role": "bass"},
        cookies=auth_cookies,
    )
    assert res_bass.status_code == 201
    bass_id = res_bass.json()["id"]

    # Test single stem download
    res_dl = client.get(
        f"/api/projects/tracks/{track_id}/takes/{take_id}/stems/{vox_id}/download",
        cookies=auth_cookies,
    )
    assert res_dl.status_code == 200
    assert "attachment" in res_dl.headers["content-disposition"]
    assert res_dl.content == VALID_WAV_HEADER

    # Test stem pack ZIP download
    res_zip = client.get(
        f"/api/projects/tracks/{track_id}/takes/{take_id}/stems/download-pack",
        cookies=auth_cookies,
    )
    assert res_zip.status_code == 200
    assert res_zip.headers["content-type"] == "application/zip"
    assert "stems.zip" in res_zip.headers["content-disposition"]

    # Open and verify ZIP archive contents
    with zipfile.ZipFile(io.BytesIO(res_zip.content), "r") as zf:
        namelist = zf.namelist()
        assert len(namelist) == 2
        # Check files exist in zip
        assert any("lead_vocals" in name or "lead_vox" in name for name in namelist)
        assert any("bass" in name for name in namelist)
        # Verify content of extracted member
        for name in namelist:
            assert zf.read(name) == VALID_WAV_HEADER

    # Clean up track
    client.delete(f"/api/projects/tracks/{track_id}", cookies=auth_cookies)


def test_all_stem_roles_supported(client, auth_cookies):
    """Test that all StemRole enum variants are accepted upon upload."""
    from backend.project_models import StemRole

    res = client.post(
        "/api/projects/tracks",
        json={"title": "Multi-Stem Suite Track"},
        cookies=auth_cookies,
    )
    assert res.status_code == 201
    track_id = res.json()["id"]

    res = client.post(
        f"/api/projects/tracks/{track_id}/takes",
        files={"file": ("mix.wav", io.BytesIO(VALID_WAV_HEADER), "audio/wav")},
        data={"label": "Take 1"},
        cookies=auth_cookies,
    )
    assert res.status_code == 201
    take_id = res.json()["id"]

    for role in StemRole:
        stem_res = client.post(
            f"/api/projects/tracks/{track_id}/takes/{take_id}/stems",
            files={"file": (f"{role.value}.wav", io.BytesIO(VALID_WAV_HEADER), "audio/wav")},
            data={"role": role.value},
            cookies=auth_cookies,
        )
        assert stem_res.status_code == 201
        assert stem_res.json()["role"] == role.value

    # Verify all stems are present in take
    track_res = client.get(f"/api/projects/tracks/{track_id}", cookies=auth_cookies)
    assert track_res.status_code == 200
    stems = track_res.json()["takes"][0]["stems"]
    assert len(stems) == len(StemRole)

    client.delete(f"/api/projects/tracks/{track_id}", cookies=auth_cookies)


def test_lyrics_saving_and_retrieval(client, auth_cookies):
    """Test saving and retrieving synchronized lyrics for a take."""
    res = client.post("/api/projects/tracks", json={"title": "Lyrics Test Song"}, cookies=auth_cookies)
    track_id = res.json()["id"]

    res = client.post(
        f"/api/projects/tracks/{track_id}/takes",
        files={"file": ("mix.mp3", io.BytesIO(VALID_MP3_HEADER), "audio/mpeg")},
        data={"label": "Take 1"},
        cookies=auth_cookies,
    )
    take_id = res.json()["id"]

    lrc_text = "[00:05.00]Line 1\n[00:10.00]Line 2"
    res = client.post(
        f"/api/projects/tracks/{track_id}/takes/{take_id}/lyrics",
        json={"lyrics": lrc_text, "format": "lrc"},
        cookies=auth_cookies,
    )
    assert res.status_code == 200
    assert res.json()["status"] == "saved"

    # Retrieve lyrics
    res = client.get(
        f"/api/projects/tracks/{track_id}/takes/{take_id}/lyrics",
        cookies=auth_cookies,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["format"] == "lrc"
    assert data["lyrics"] == lrc_text

    client.delete(f"/api/projects/tracks/{track_id}", cookies=auth_cookies)


def test_album_workspace_api(client, auth_cookies):
    """Test Album Workspace CRUD, sequencing, and cover artwork management."""
    # 1. Create Album
    res = client.post(
        "/api/projects/albums",
        json={"title": "Retrowave 2026", "album_artist": "CyberArtist", "year": 2026, "genre": "Synthwave"},
        cookies=auth_cookies,
    )
    assert res.status_code == 201
    album = res.json()
    album_id = album["id"]
    assert album["title"] == "Retrowave 2026"
    assert album["year"] == 2026

    # 2. Upload Artwork
    files = {"file": ("cover.jpg", io.BytesIO(VALID_JPEG_BYTES), "image/jpeg")}
    res = client.post(
        f"/api/projects/albums/{album_id}/artwork",
        files=files,
        cookies=auth_cookies,
    )
    assert res.status_code == 200
    assert res.json()["status"] == "saved"

    # 3. Retrieve Artwork
    res = client.get(f"/api/projects/albums/{album_id}/artwork", cookies=auth_cookies)
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/jpeg"

    # 4. List albums
    res = client.get("/api/projects/albums", cookies=auth_cookies)
    assert res.status_code == 200
    albums = res.json()
    assert any(a["id"] == album_id for a in albums)

    # 5. Delete album
    res = client.delete(f"/api/projects/albums/{album_id}", cookies=auth_cookies)
    assert res.status_code == 200
    assert res.json()["status"] == "deleted"


def test_storage_quota_telemetry(client, auth_cookies):
    """Verify quota statistics endpoint returns accurate metrics."""
    res = client.get("/api/storage/quota", cookies=auth_cookies)
    assert res.status_code == 200
    stats = res.json()
    assert "used_bytes" in stats
    assert "max_quota_bytes" in stats
    assert "tracks_count" in stats
    assert "takes_count" in stats
    assert "albums_count" in stats


def test_ingest_session_into_project_take(client, auth_cookies):
    """Test promoting an active ephemeral /app session into a permanent track take."""
    # 1. Create a dummy ephemeral session in storage_manager
    session_id, audio_path = storage_manager.create_session("session_track.mp3", ".mp3")
    with open(audio_path, "wb") as f:
        f.write(VALID_MP3_HEADER)

    session_token = create_signed_session_token(session_id)
    combined_cookies = {
        AUTH_COOKIE_NAME: auth_cookies[AUTH_COOKIE_NAME],
        SESSION_COOKIE_NAME: session_token,
    }

    # 2. Ingest into new track workspace
    res = client.post(
        "/api/projects/ingest-session",
        json={"new_track_title": "Imported Hit", "take_label": "Master from /app"},
        cookies=combined_cookies,
    )
    assert res.status_code == 201
    take = res.json()
    assert take["label"] == "Master from /app"
    assert take["filename"] == "session_track.mp3"

    # Clean up ephemeral session
    storage_manager.cleanup_session(session_id)


def test_ingest_session_update_existing_take(client, auth_cookies):
    """Test updating an existing project take in place from an active editing session."""
    # 1. Create a project track and initial take
    res = client.post("/api/projects/tracks", json={"title": "Original Track"}, cookies=auth_cookies)
    assert res.status_code == 201
    track = res.json()
    track_id = track["id"]

    res = client.post(
        f"/api/projects/tracks/{track_id}/takes",
        data={"label": "Take 1", "notes": "Initial draft"},
        files={"file": ("v1.mp3", VALID_MP3_HEADER, "audio/mpeg")},
        cookies=auth_cookies,
    )
    assert res.status_code == 201
    take = res.json()
    take_id = take["id"]
    assert take["label"] == "Take 1"

    # 2. Create an ephemeral session representing updated edits
    session_id, audio_path = storage_manager.create_session("v1_updated.mp3", ".mp3")
    with open(audio_path, "wb") as f:
        f.write(VALID_MP3_HEADER + b"\x00" * 64)

    session_token = create_signed_session_token(session_id)
    combined_cookies = {
        AUTH_COOKIE_NAME: auth_cookies[AUTH_COOKIE_NAME],
        SESSION_COOKIE_NAME: session_token,
    }

    # 3. Ingest into existing track & take (Update mode)
    res = client.post(
        "/api/projects/ingest-session",
        json={
            "track_id": track_id,
            "take_id": take_id,
            "take_label": "Take 1 (Updated)",
            "notes": "Fixed metadata and levels",
        },
        cookies=combined_cookies,
    )
    assert res.status_code in (200, 201)
    updated_take = res.json()
    assert updated_take["id"] == take_id
    assert updated_take["label"] == "Take 1 (Updated)"
    assert updated_take["notes"] == "Fixed metadata and levels"

    # Verify track takes count is still 1 (not duplicated)
    res = client.get(f"/api/projects/tracks/{track_id}", cookies=auth_cookies)
    assert res.status_code == 200
    track_detail = res.json()
    assert len(track_detail["takes"]) == 1
    assert track_detail["takes"][0]["id"] == take_id
    assert track_detail["takes"][0]["label"] == "Take 1 (Updated)"

    storage_manager.cleanup_session(session_id)


def test_api_cross_tenant_isolation(client, setup_auth_environment, auth_cookies):
    """Verify User B cannot access, modify, or delete User A's tracks or albums via API."""
    import uuid
    mgr = setup_auth_environment["manager"]
    
    # Create second user with valid UUID
    bob_id = str(uuid.uuid4())
    mgr._save_users(mgr._load_users() + [{
        "id": bob_id,
        "username": "bob",
        "password_hash": "pbkdf2_sha256$600000$00000000000000000000000000000000$0000000000000000000000000000000000000000000000000000000000000000",
        "role": "user",
        "revocation_epoch": 0,
    }])
    bob_token = mgr.issue_token(bob_id)
    bob_cookies = {AUTH_COOKIE_NAME: bob_token}

    # Admin creates track and album
    res = client.post("/api/projects/tracks", json={"title": "Admin Confidential"}, cookies=auth_cookies)
    track_id = res.json()["id"]

    res = client.post("/api/projects/albums", json={"title": "Admin Album"}, cookies=auth_cookies)
    album_id = res.json()["id"]

    # Bob attempts to get Admin's track -> 404
    res = client.get(f"/api/projects/tracks/{track_id}", cookies=bob_cookies)
    assert res.status_code == 404

    # Bob attempts to patch Admin's track -> 404
    res = client.patch(f"/api/projects/tracks/{track_id}", json={"title": "Hacked"}, cookies=bob_cookies)
    assert res.status_code == 404

    # Bob attempts to delete Admin's track -> 404
    res = client.delete(f"/api/projects/tracks/{track_id}", cookies=bob_cookies)
    assert res.status_code == 404

    # Bob attempts to get/delete Admin's album -> 404
    res = client.get(f"/api/projects/albums/{album_id}", cookies=bob_cookies)
    assert res.status_code == 404

    res = client.delete(f"/api/projects/albums/{album_id}", cookies=bob_cookies)
    assert res.status_code == 404

    # Bob's track list is empty
    res = client.get("/api/projects/tracks", cookies=bob_cookies)
    assert res.status_code == 200
    assert len(res.json()) == 0


def test_api_quota_rejection(client, auth_cookies, monkeypatch):
    """Verify API returns 413 when upload exceeds remaining storage quota."""
    # Set tiny quota of 1 KB
    monkeypatch.setattr("backend.project_storage.get_runtime_global_storage_bytes", lambda: 1024)

    res = client.post("/api/projects/tracks", json={"title": "Quota Song"}, cookies=auth_cookies)
    track_id = res.json()["id"]

    # Ingest 2 KB audio file
    large_dummy_audio = VALID_MP3_HEADER + (b"\x00" * 2048)
    files = {"file": ("big.mp3", io.BytesIO(large_dummy_audio), "audio/mpeg")}
    res = client.post(
        f"/api/projects/tracks/{track_id}/takes",
        files=files,
        cookies=auth_cookies,
    )
    assert res.status_code == 413
    assert "Storage quota exceeded" in res.json()["detail"]


def test_load_take_into_session(client, auth_cookies):
    """Verify loading a persistent take into an active session sets cookie and copies audio."""
    res = client.post("/api/projects/tracks", json={"title": "Load Test Track"}, cookies=auth_cookies)
    track_id = res.json()["id"]

    files = {"file": ("song.mp3", io.BytesIO(VALID_MP3_HEADER), "audio/mpeg")}
    res = client.post(
        f"/api/projects/tracks/{track_id}/takes",
        files=files,
        cookies=auth_cookies,
    )
    take_id = res.json()["id"]

    # Load take into session
    res = client.post(
        f"/api/projects/tracks/{track_id}/takes/{take_id}/load-session",
        cookies=auth_cookies,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "loaded"
    assert "session_id" in data
    assert SESSION_COOKIE_NAME in res.cookies

    # Verify that calling /api/session with the returned cookie works
    session_cookie = res.cookies[SESSION_COOKIE_NAME]
    res_session = client.get("/api/session", cookies={SESSION_COOKIE_NAME: session_cookie, **auth_cookies})
    assert res_session.status_code == 200
    assert res_session.json()["active"] is True


def test_take_upload_exceeding_256kb_allowed_by_middleware(client, auth_cookies):
    """Verify uploads >256KB are not rejected by default body limit in RequestLimitsMiddleware."""
    res = client.post("/api/projects/tracks", json={"title": "Large File Track"}, cookies=auth_cookies)
    track_id = res.json()["id"]

    # 512 KB synthetic MP3
    large_audio = VALID_MP3_HEADER + (b"\x00" * (512 * 1024))
    files = {"file": ("large.mp3", io.BytesIO(large_audio), "audio/mpeg")}
    res = client.post(
        f"/api/projects/tracks/{track_id}/takes",
        files=files,
        cookies=auth_cookies,
    )
    assert res.status_code == 201
    assert res.json()["size_bytes"] > 500000


def test_storage_quota_response_fields(client, auth_cookies):
    """Verify /api/storage/quota response provides all expected telemetry fields."""
    res = client.get("/api/storage/quota", cookies=auth_cookies)
    assert res.status_code == 200
    data = res.json()
    assert "used_bytes" in data
    assert "used_mb" in data
    assert "max_quota_bytes" in data
    assert "max_quota_mb" in data
    assert "used_percent" in data
    assert "limit_bytes" in data
    assert "percent_used" in data
    assert data["limit_bytes"] == data["max_quota_bytes"]
    assert data["percent_used"] == data["used_percent"]


def test_album_tracklist_sequencing_and_synchronization(client, auth_cookies):
    """Verify adding, sequencing, and saving tracks to an album persists across API lookups."""
    # Create two tracks
    res1 = client.post("/api/projects/tracks", json={"title": "Track One"}, cookies=auth_cookies)
    trk1_id = res1.json()["id"]
    res2 = client.post("/api/projects/tracks", json={"title": "Track Two"}, cookies=auth_cookies)
    trk2_id = res2.json()["id"]

    # Create album
    res_alb = client.post("/api/projects/albums", json={"title": "Sequenced Album"}, cookies=auth_cookies)
    assert res_alb.status_code == 201
    alb_id = res_alb.json()["id"]

    # Update album with sequenced tracks list
    tracks_payload = [
        {"track_number": 1, "disc_number": 1, "track_id": trk1_id, "take_id": None, "custom_title": "Custom Track One"},
        {"track_number": 2, "disc_number": 1, "track_id": trk2_id, "take_id": None, "custom_title": None},
    ]
    res_patch = client.patch(
        f"/api/projects/albums/{alb_id}",
        json={"tracks": tracks_payload},
        cookies=auth_cookies,
    )
    assert res_patch.status_code == 200
    data = res_patch.json()
    assert len(data["tracks"]) == 2
    assert data["tracks"][0]["track_id"] == trk1_id
    assert data["tracks"][0]["custom_title"] == "Custom Track One"
    assert data["tracks"][1]["track_id"] == trk2_id
    assert data["track_ids"] == [trk1_id, trk2_id]

    # Verify GET /albums/{id}
    res_get = client.get(f"/api/projects/albums/{alb_id}", cookies=auth_cookies)
    assert res_get.status_code == 200
    get_data = res_get.json()
    assert len(get_data["tracks"]) == 2
    assert get_data["track_ids"] == [trk1_id, trk2_id]

    # Test patch with track_ids format
    res_patch_ids = client.patch(
        f"/api/projects/albums/{alb_id}",
        json={"track_ids": [trk2_id, trk1_id]},
        cookies=auth_cookies,
    )
    assert res_patch_ids.status_code == 200
    ids_data = res_patch_ids.json()
    assert len(ids_data["tracks"]) == 2
    assert ids_data["tracks"][0]["track_id"] == trk2_id
    assert ids_data["tracks"][0]["track_number"] == 1
    assert ids_data["tracks"][1]["track_id"] == trk1_id
    assert ids_data["tracks"][1]["track_number"] == 2


def test_revision_tracking_api(client, auth_cookies):
    """Test revision incrementing and revision history over REST endpoints."""
    # 1. Create Track
    res = client.post("/api/projects/tracks", json={"title": "Rev Track API"}, cookies=auth_cookies)
    assert res.status_code == 201
    track = res.json()
    track_id = track["id"]
    assert track["revision"] == 1
    assert len(track["revisions"]) == 1

    # 2. Patch Track with change summary
    patch_res = client.patch(
        f"/api/projects/tracks/{track_id}",
        json={"title": "Rev Track API v2", "bpm": 128, "change_summary": "Adjusted tempo and title"},
        cookies=auth_cookies,
    )
    assert patch_res.status_code == 200
    patched_track = patch_res.json()
    assert patched_track["revision"] == 2
    assert len(patched_track["revisions"]) == 2
    assert patched_track["revisions"][1]["change_summary"] == "Adjusted tempo and title"
    assert "bpm" in patched_track["revisions"][1]["changed_fields"]

    # 3. Create Album and test revision
    alb_res = client.post("/api/projects/albums", json={"title": "Rev Album API"}, cookies=auth_cookies)
    assert alb_res.status_code == 201
    album = alb_res.json()
    album_id = album["id"]
    assert album["revision"] == 1

    alb_patch = client.patch(
        f"/api/projects/albums/{album_id}",
        json={"genre": "Ambient", "year": 2026, "change_summary": "Added genre and release year"},
        cookies=auth_cookies,
    )
    assert alb_patch.status_code == 200
    patched_alb = alb_patch.json()
    assert patched_alb["revision"] == 2
    assert len(patched_alb["revisions"]) == 2
    assert patched_alb["revisions"][1]["change_summary"] == "Added genre and release year"

    # Clean up
    client.delete(f"/api/projects/tracks/{track_id}", cookies=auth_cookies)
    client.delete(f"/api/projects/albums/{album_id}", cookies=auth_cookies)


def test_ingest_session_updates_track_revision(client, auth_cookies):
    """Verify revision semantics on ingest-session without overwriting parent track metadata."""
    # Create Track with populated notes
    res = client.post(
        "/api/projects/tracks",
        json={"title": "Ingest Rev Track", "artist": "Original Artist", "notes": "Original workspace notes"},
        cookies=auth_cookies,
    )
    assert res.status_code == 201
    track_id = res.json()["id"]
    assert res.json()["revision"] == 1

    # Upload Take 1 (first take becomes master and bumps track revision to 2)
    files = {"file": ("song.mp3", io.BytesIO(VALID_MP3_HEADER), "audio/mpeg")}
    res = client.post(
        f"/api/projects/tracks/{track_id}/takes",
        files=files,
        data={"label": "Take 1"},
        cookies=auth_cookies,
    )
    assert res.status_code == 201
    take_id = res.json()["id"]
    assert res.json()["is_master"] is True

    # Load take into session
    res_load = client.post(
        f"/api/projects/tracks/{track_id}/takes/{take_id}/load-session",
        cookies=auth_cookies,
    )
    assert res_load.status_code == 200
    session_cookie = res_load.cookies[SESSION_COOKIE_NAME]

    # Ingest session with Update Take mode when Take 1 is already master
    res_ingest = client.post(
        "/api/projects/ingest-session",
        json={
            "track_id": track_id,
            "take_id": take_id,
            "take_label": "Take 1 (Revised)",
            "notes": "Added track and disc information",
            "is_master": True,
        },
        cookies={SESSION_COOKIE_NAME: session_cookie, **auth_cookies},
    )
    assert res_ingest.status_code == 201
    updated_take = res_ingest.json()
    assert updated_take["revision"] == 2
    assert updated_take["revisions"][-1]["change_summary"] == "Added track and disc information"
    assert updated_take["notes"] == "Added track and disc information"

    # Routine update of the existing master take preserves parent track notes and does not add redundant track revision
    res_track = client.get(f"/api/projects/tracks/{track_id}", cookies=auth_cookies)
    assert res_track.status_code == 200
    track_data = res_track.json()["track"]
    assert track_data["notes"] == "Original workspace notes"
    assert track_data["revision"] == 2

    # Adding a second non-master take via ingest-session bumps parent track revision to 3 with ["takes"]
    res_ingest_2 = client.post(
        "/api/projects/ingest-session",
        json={
            "track_id": track_id,
            "take_label": "Take 2 (Alt)",
            "notes": "Alternate mix balance",
            "is_master": False,
        },
        cookies={SESSION_COOKIE_NAME: session_cookie, **auth_cookies},
    )
    assert res_ingest_2.status_code == 201
    take_2 = res_ingest_2.json()
    assert take_2["is_master"] is False

    res_track_after = client.get(f"/api/projects/tracks/{track_id}", cookies=auth_cookies)
    assert res_track_after.status_code == 200
    track_after = res_track_after.json()["track"]
    assert track_after["revision"] == 3
    assert track_after["revisions"][-1]["changed_fields"] == ["takes"]
    assert track_after["notes"] == "Original workspace notes"
    assert track_after["primary_take_id"] == take_id

    # Clean up
    client.delete("/api/session", cookies={SESSION_COOKIE_NAME: session_cookie, **auth_cookies})
    client.delete(f"/api/projects/tracks/{track_id}", cookies=auth_cookies)


def _build_tagged_mp3_bytes(title: str, artist: str, bpm: str, key: str, lyrics: str) -> bytes:
    """Create valid synthetic MP3 bytes with embedded ID3 metadata frames."""
    from mutagen.id3 import ID3, TIT2, TPE1, TBPM, TKEY, USLT
    id3_header = b"ID3\x04\x00\x00\x00\x00\x00\x00"
    mpeg_frame = b"\xff\xfb\x90\x44" + (b"\x00" * 413)
    raw = id3_header + (mpeg_frame * 5)
    bio = io.BytesIO(raw)
    tags = ID3()
    tags.add(TIT2(encoding=3, text=title))
    tags.add(TPE1(encoding=3, text=artist))
    tags.add(TBPM(encoding=3, text=bpm))
    tags.add(TKEY(encoding=3, text=key))
    tags.add(USLT(encoding=3, lang="eng", desc="", text=lyrics))
    tags.save(bio, v2_version=4)
    bio.seek(0)
    return bio.read() + (mpeg_frame * 5)


def test_load_take_session_provenance_cleared_on_unrelated_upload_and_delete(client, auth_cookies):
    """Verify loaded_project_take provenance is set on load-session and cleared on new upload or session reset."""
    res_trk = client.post("/api/projects/tracks", json={"title": "Provenance Track"}, cookies=auth_cookies)
    assert res_trk.status_code == 201
    track_id = res_trk.json()["id"]

    res_take = client.post(
        f"/api/projects/tracks/{track_id}/takes",
        data={"label": "Take Alpha"},
        files={"file": ("alpha.mp3", VALID_MP3_HEADER, "audio/mpeg")},
        cookies=auth_cookies,
    )
    assert res_take.status_code == 201
    take_id = res_take.json()["id"]

    # 1. Load project take into session -> /api/session exposes loaded_project_take
    res_load = client.post(
        f"/api/projects/tracks/{track_id}/takes/{take_id}/load-session",
        cookies=auth_cookies,
    )
    assert res_load.status_code == 200
    load_data = res_load.json()
    assert load_data["loaded_project_take"]["track_id"] == track_id
    assert load_data["loaded_project_take"]["take_id"] == take_id
    proj_session_cookie = res_load.cookies[SESSION_COOKIE_NAME]

    res_sess_1 = client.get("/api/session", cookies={SESSION_COOKIE_NAME: proj_session_cookie, **auth_cookies})
    assert res_sess_1.status_code == 200
    assert res_sess_1.json()["active"] is True
    assert res_sess_1.json()["loaded_project_take"]["track_id"] == track_id
    assert res_sess_1.json()["loaded_project_take"]["take_id"] == take_id

    # Legitimate update of the loaded take works cleanly
    res_legit = client.post(
        "/api/projects/ingest-session",
        json={"track_id": track_id, "take_id": take_id, "take_label": "Take Alpha Edited", "is_master": True},
        cookies={SESSION_COOKIE_NAME: proj_session_cookie, **auth_cookies},
    )
    assert res_legit.status_code == 201
    assert res_legit.json()["id"] == take_id
    assert res_legit.json()["label"] == "Take Alpha Edited"

    # 2. Upload an unrelated audio file via /api/upload -> clears loaded_project_take
    sample_mp3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + (b"\xff\xfb\x90\x44" + b"\x00" * 413) * 5
    res_upload = client.post(
        "/api/upload",
        files={"file": ("unrelated_song.mp3", sample_mp3, "audio/mpeg")},
        cookies={SESSION_COOKIE_NAME: proj_session_cookie, **auth_cookies},
    )
    assert res_upload.status_code == 200
    assert res_upload.json()["loaded_project_take"] is None
    new_session_cookie = res_upload.cookies[SESSION_COOKIE_NAME]

    res_sess_2 = client.get("/api/session", cookies={SESSION_COOKIE_NAME: new_session_cookie, **auth_cookies})
    assert res_sess_2.status_code == 200
    assert res_sess_2.json()["active"] is True
    assert res_sess_2.json()["loaded_project_take"] is None

    # 3. Reset session via DELETE /api/session -> session inactive, no loaded_project_take
    res_del = client.delete("/api/session", cookies={SESSION_COOKIE_NAME: new_session_cookie, **auth_cookies})
    assert res_del.status_code == 200
    res_sess_3 = client.get("/api/session", cookies=auth_cookies)
    assert res_sess_3.status_code == 200
    assert res_sess_3.json()["active"] is False
    assert "loaded_project_take" not in res_sess_3.json()

    # Clean up remaining sessions & track
    client.delete("/api/session", cookies={SESSION_COOKIE_NAME: proj_session_cookie, **auth_cookies})
    client.delete(f"/api/projects/tracks/{track_id}", cookies=auth_cookies)


@pytest.mark.parametrize("is_master_flag", [False, True])
def test_ingest_session_protects_existing_parent_track_metadata(client, auth_cookies, is_master_flag):
    """Verify ingest-session never overwrites populated or blank parent track fields when adding/updating takes."""
    # Track 1: Populated parent metadata
    res_trk = client.post(
        "/api/projects/tracks",
        json={
            "title": "Workspace Title",
            "artist": "Workspace Artist",
            "bpm": 120,
            "musical_key": "C minor",
            "master_lyrics": "Original workspace lyrics line",
            "notes": "Original workspace notes",
        },
        cookies=auth_cookies,
    )
    assert res_trk.status_code == 201
    track_id = res_trk.json()["id"]

    res_t1 = client.post(
        f"/api/projects/tracks/{track_id}/takes",
        data={"label": "Take 1"},
        files={"file": ("t1.mp3", VALID_MP3_HEADER, "audio/mpeg")},
        cookies=auth_cookies,
    )
    take1_id = res_t1.json()["id"]

    # Track 2: Intentionally blank optional parent fields
    res_blank_trk = client.post(
        "/api/projects/tracks",
        json={"title": "Blank Fields Workspace"},
        cookies=auth_cookies,
    )
    assert res_blank_trk.status_code == 201
    blank_track_id = res_blank_trk.json()["id"]

    # Ephemeral session containing conflicting embedded ID3 metadata
    tagged_bytes = _build_tagged_mp3_bytes(
        title="Rogue Song Title",
        artist="Rogue Artist",
        bpm="145",
        key="F#m",
        lyrics="Completely different song lyrics!",
    )
    session_id, audio_path = storage_manager.create_session("different_song.mp3", ".mp3")
    with open(audio_path, "wb") as f:
        f.write(tagged_bytes)
    session_token = create_signed_session_token(session_id)
    cookies = {AUTH_COOKIE_NAME: auth_cookies[AUTH_COOKIE_NAME], SESSION_COOKIE_NAME: session_token}

    # Case A: Add new take to existing populated track
    res_add = client.post(
        "/api/projects/ingest-session",
        json={
            "track_id": track_id,
            "take_label": "Take 2 Rogue",
            "notes": "Take 2 specific notes",
            "is_master": is_master_flag,
        },
        cookies=cookies,
    )
    assert res_add.status_code == 201
    assert res_add.json()["notes"] == "Take 2 specific notes"

    trk_detail = client.get(f"/api/projects/tracks/{track_id}", cookies=auth_cookies).json()["track"]
    assert trk_detail["title"] == "Workspace Title"
    assert trk_detail["artist"] == "Workspace Artist"
    assert trk_detail["bpm"] == 120
    assert trk_detail["musical_key"] == "C minor"
    assert trk_detail["master_lyrics"] == "Original workspace lyrics line"
    assert trk_detail["notes"] == "Original workspace notes"

    # Case B: Update existing take on populated track
    res_upd = client.post(
        "/api/projects/ingest-session",
        json={
            "track_id": track_id,
            "take_id": take1_id,
            "take_label": "Take 1 Edited",
            "notes": "Take 1 revision note only",
            "is_master": is_master_flag,
        },
        cookies=cookies,
    )
    assert res_upd.status_code == 201
    assert res_upd.json()["notes"] == "Take 1 revision note only"

    trk_detail_after_upd = client.get(f"/api/projects/tracks/{track_id}", cookies=auth_cookies).json()["track"]
    assert trk_detail_after_upd["title"] == "Workspace Title"
    assert trk_detail_after_upd["artist"] == "Workspace Artist"
    assert trk_detail_after_upd["bpm"] == 120
    assert trk_detail_after_upd["musical_key"] == "C minor"
    assert trk_detail_after_upd["master_lyrics"] == "Original workspace lyrics line"
    assert trk_detail_after_upd["notes"] == "Original workspace notes"

    # Case C: Add new take to track with blank optional fields -> still does NOT auto-populate parent fields
    res_add_blank = client.post(
        "/api/projects/ingest-session",
        json={
            "track_id": blank_track_id,
            "take_label": "Demo Take",
            "notes": "Demo take notes only",
            "is_master": is_master_flag,
        },
        cookies=cookies,
    )
    assert res_add_blank.status_code == 201
    blank_detail = client.get(f"/api/projects/tracks/{blank_track_id}", cookies=auth_cookies).json()["track"]
    assert blank_detail["title"] == "Blank Fields Workspace"
    assert blank_detail["artist"] is None
    assert blank_detail["bpm"] is None
    assert blank_detail["musical_key"] is None
    assert blank_detail["master_lyrics"] is None
    assert blank_detail["notes"] is None

    storage_manager.cleanup_session(session_id)
    client.delete(f"/api/projects/tracks/{track_id}", cookies=auth_cookies)
    client.delete(f"/api/projects/tracks/{blank_track_id}", cookies=auth_cookies)


def test_frontend_stale_take_invalidation_wiring():
    """Verify app.js, projects.js, and storage_manager.js clear stale loadedProjectTake references consistently."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    app_js = (root / "frontend" / "js" / "app.js").read_text(encoding="utf-8")
    projects_js = (root / "frontend" / "js" / "projects.js").read_text(encoding="utf-8")
    storage_js = (root / "frontend" / "js" / "storage_manager.js").read_text(encoding="utf-8")

    # app.js clears loaded project take on new file upload, new file button, non-project loadSession, and inactive restore
    assert "function clearLoadedProjectTakeState()" in app_js
    assert "sessionStorage.removeItem('mp3metafix_loaded_project_track')" in app_js
    assert "window.dispatchEvent(new CustomEvent('mp3metafix:loaded-take-cleared'))" in app_js
    assert app_js.count("clearLoadedProjectTakeState();") >= 4

    # projects.js and storage_manager.js both expose global hooks and validate sessionStorage + active track list
    for src in (projects_js, storage_js):
        assert "window.clearLoadedProjectTake = clearLoadedProjectTakeRef;" in src
        assert "window.setLoadedProjectTake = setLoadedProjectTakeRef;" in src
        assert "window.addEventListener('mp3metafix:loaded-take-cleared'" in src
        assert ".some(t => t.id === loadedTake.track_id)" in src


def _build_pcm_wav_bytes(duration_sec: float = 0.5, sample_rate: int = 44100) -> bytes:
    """Create valid PCM WAV audio bytes with exact duration."""
    import wave
    import struct
    num_frames = int(duration_sec * sample_rate)
    out = io.BytesIO()
    with wave.open(out, "wb") as wf:
        wf.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
        wf.writeframes(struct.pack("<h", 0) * num_frames)
    return out.getvalue()


def test_direct_take_upload_preserves_existing_audio_detects_duration_and_master_behavior(client, auth_cookies):
    """Verify direct take upload creates independent audio, probes duration, and respects is_master."""
    res_trk = client.post("/api/projects/tracks", json={"title": "Direct Upload Song"}, cookies=auth_cookies)
    assert res_trk.status_code == 201
    track_id = res_trk.json()["id"]

    wav_1 = _build_pcm_wav_bytes(0.5)
    wav_2 = _build_pcm_wav_bytes(1.0)
    wav_3 = _build_pcm_wav_bytes(0.25)

    # 1. First direct take upload (even with is_master=false) auto-masters and detects duration
    res_1 = client.post(
        f"/api/projects/tracks/{track_id}/takes",
        data={"label": "Take 1", "is_master": "false"},
        files={"file": ("take1.wav", io.BytesIO(wav_1), "audio/wav")},
        cookies=auth_cookies,
    )
    assert res_1.status_code == 201
    t1 = res_1.json()
    assert t1["is_master"] is True
    assert t1["duration_seconds"] == pytest.approx(0.5, abs=0.02)

    # 2. Second upload with is_master=false keeps Take 1 as master and preserves Take 1 audio
    res_2 = client.post(
        f"/api/projects/tracks/{track_id}/takes",
        data={"label": "Take 2", "is_master": "false"},
        files={"file": ("take2.wav", io.BytesIO(wav_2), "audio/wav")},
        cookies=auth_cookies,
    )
    assert res_2.status_code == 201
    t2 = res_2.json()
    assert t2["is_master"] is False
    assert t2["duration_seconds"] == pytest.approx(1.0, abs=0.02)

    detail = client.get(f"/api/projects/tracks/{track_id}", cookies=auth_cookies).json()
    assert detail["track"]["primary_take_id"] == t1["id"]
    assert client.get(f"/api/projects/tracks/{track_id}/takes/{t1['id']}/download", cookies=auth_cookies).content == wav_1
    assert client.get(f"/api/projects/tracks/{track_id}/takes/{t2['id']}/download", cookies=auth_cookies).content == wav_2

    # 3. Third upload with is_master=true switches master to Take 3 and clears Take 1 is_master
    res_3 = client.post(
        f"/api/projects/tracks/{track_id}/takes",
        data={"label": "Take 3 Master", "is_master": "true"},
        files={"file": ("take3.wav", io.BytesIO(wav_3), "audio/wav")},
        cookies=auth_cookies,
    )
    assert res_3.status_code == 201
    t3 = res_3.json()
    assert t3["is_master"] is True
    assert t3["duration_seconds"] == pytest.approx(0.25, abs=0.02)

    detail_after = client.get(f"/api/projects/tracks/{track_id}", cookies=auth_cookies).json()
    assert detail_after["track"]["primary_take_id"] == t3["id"]
    by_id = {tk["id"]: tk for tk in detail_after["takes"]}
    assert [tid for tid, tk in by_id.items() if tk["is_master"]] == [t3["id"]]
    assert client.get(f"/api/projects/tracks/{track_id}/takes/{t1['id']}/download", cookies=auth_cookies).content == wav_1

    client.delete(f"/api/projects/tracks/{track_id}", cookies=auth_cookies)


def test_direct_take_upload_rejects_invalid_audio_cleanly(client, auth_cookies):
    """Verify invalid extensions, tiny files, and corrupt headers return 400 without affecting existing takes."""
    res_trk = client.post("/api/projects/tracks", json={"title": "Validation Track"}, cookies=auth_cookies)
    track_id = res_trk.json()["id"]

    res_ok = client.post(
        f"/api/projects/tracks/{track_id}/takes",
        data={"label": "Valid Take"},
        files={"file": ("valid.mp3", VALID_MP3_HEADER, "audio/mpeg")},
        cookies=auth_cookies,
    )
    assert res_ok.status_code == 201
    valid_id = res_ok.json()["id"]

    # Unsupported extension
    res_bad_ext = client.post(
        f"/api/projects/tracks/{track_id}/takes",
        data={"label": "Bad Ext"},
        files={"file": ("notes.txt", b"not audio at all" * 5, "text/plain")},
        cookies=auth_cookies,
    )
    assert res_bad_ext.status_code == 400
    assert "Unsupported audio format" in res_bad_ext.json()["detail"]

    # Empty / too small
    res_tiny = client.post(
        f"/api/projects/tracks/{track_id}/takes",
        data={"label": "Tiny"},
        files={"file": ("tiny.mp3", b"ID3short", "audio/mpeg")},
        cookies=auth_cookies,
    )
    assert res_tiny.status_code == 400
    assert "empty or too short" in res_tiny.json()["detail"]

    # Invalid magic bytes
    res_bad_magic = client.post(
        f"/api/projects/tracks/{track_id}/takes",
        data={"label": "Bad Magic"},
        files={"file": ("fake.mp3", b"RIFF" + b"\x00" * 100, "audio/mpeg")},
        cookies=auth_cookies,
    )
    assert res_bad_magic.status_code == 400
    assert "Invalid audio file header" in res_bad_magic.json()["detail"]

    # Existing valid take remains untouched
    takes = client.get(f"/api/projects/tracks/{track_id}", cookies=auth_cookies).json()["takes"]
    assert [t["id"] for t in takes] == [valid_id]
    assert client.get(f"/api/projects/tracks/{track_id}/takes/{valid_id}/download", cookies=auth_cookies).content == VALID_MP3_HEADER

    client.delete(f"/api/projects/tracks/{track_id}", cookies=auth_cookies)


def test_direct_take_upload_and_master_switch_album_release_compatibility(client, auth_cookies):
    """Verify Album Releases remain compatible when takes are uploaded directly and master selection changes."""
    res_trk = client.post("/api/projects/tracks", json={"title": "Album Track 1"}, cookies=auth_cookies)
    track_id = res_trk.json()["id"]

    t1 = client.post(
        f"/api/projects/tracks/{track_id}/takes",
        data={"label": "Take 1"},
        files={"file": ("v1.wav", _build_pcm_wav_bytes(0.25), "audio/wav")},
        cookies=auth_cookies,
    ).json()

    alb = client.post("/api/projects/albums", json={"title": "Compat EP"}, cookies=auth_cookies).json()
    alb_id = alb["id"]
    client.patch(
        f"/api/projects/albums/{alb_id}",
        json={"tracks": [{"track_number": 1, "disc_number": 1, "track_id": track_id, "take_id": None}]},
        cookies=auth_cookies,
    )

    # Direct upload of a new master take updates track primary_take_id
    t2 = client.post(
        f"/api/projects/tracks/{track_id}/takes",
        data={"label": "Take 2 Master", "is_master": "true"},
        files={"file": ("v2.wav", _build_pcm_wav_bytes(0.5), "audio/wav")},
        cookies=auth_cookies,
    ).json()
    trk_detail = client.get(f"/api/projects/tracks/{track_id}", cookies=auth_cookies).json()
    assert trk_detail["track"]["primary_take_id"] == t2["id"]

    # Switch master back to t1
    res_patch = client.patch(
        f"/api/projects/tracks/{track_id}/takes/{t1['id']}",
        json={"is_master": True},
        cookies=auth_cookies,
    )
    assert res_patch.status_code == 200
    trk_detail_2 = client.get(f"/api/projects/tracks/{track_id}", cookies=auth_cookies).json()
    assert trk_detail_2["track"]["primary_take_id"] == t1["id"]

    # Album track entry stays valid and intact
    alb_after = client.get(f"/api/projects/albums/{alb_id}", cookies=auth_cookies).json()
    assert alb_after["track_ids"] == [track_id]
    assert alb_after["tracks"][0]["track_id"] == track_id

    client.delete(f"/api/projects/albums/{alb_id}", cookies=auth_cookies)
    client.delete(f"/api/projects/tracks/{track_id}", cookies=auth_cookies)


def test_frontend_direct_take_upload_form_submission_behavior():
    """Execute projects.js upload take flow in Node to verify submit binding, validation, error handling, and drawer preservation."""
    import os
    import shutil
    import subprocess
    from pathlib import Path

    node_bin = shutil.which("node")
    assert node_bin is not None, "node is required to execute frontend behavior tests"

    projects_js_path = Path(__file__).resolve().parent.parent / "frontend" / "js" / "projects.js"
    storage_js_path = Path(__file__).resolve().parent.parent / "frontend" / "js" / "storage_manager.js"
    storage_js = storage_js_path.read_text(encoding="utf-8")
    assert "if (dom.formUploadTake)" in storage_js
    assert "handleUploadTakeSubmit" in storage_js

    # Run projects.js inside a lightweight Node DOM/fetch harness
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const code = fs.readFileSync(process.env.TARGET_JS_PATH, 'utf8');

const elements = {};
function makeEl(id, tag = 'div') {
  const listeners = {};
  const classSet = new Set();
  return {
    id,
    tagName: tag.toUpperCase(),
    value: '',
    textContent: '',
    innerHTML: '',
    checked: false,
    disabled: false,
    files: [],
    listeners,
    dataset: {},
    style: {},
    classList: {
      add: (c) => classSet.add(c),
      remove: (c) => classSet.delete(c),
      contains: (c) => classSet.has(c),
    },
    addEventListener: (ev, fn) => {
      if (!listeners[ev]) listeners[ev] = [];
      listeners[ev].push(fn);
    },
    setAttribute: () => {},
    appendChild: () => {},
    querySelector: (sel) => sel === 'button[type="submit"]' ? submitBtn : null,
    querySelectorAll: () => [],
    reset: () => {},
    _classSet: classSet,
  };
}
const submitBtn = makeEl('submitUploadTake', 'button');
submitBtn.textContent = 'Upload Take';

const docListeners = {};
const toasts = [];
const fetchCalls = [];
let nextFetchResponse = { ok: true, status: 201, json: async () => ({ id: 'tak_new' }) };

const getEl = (id) => {
  if (!elements[id]) elements[id] = makeEl(id);
  return elements[id];
};

const context = {
  console,
  Set,
  Map,
  Number,
  CustomEvent: class CustomEvent { constructor(n, o) { this.type = n; this.detail = o && o.detail; } },
  FormData: class FormData {
    constructor() { this.fields = []; }
    append(k, v) { this.fields.push([k, v]); }
  },
  sessionStorage: {
    _s: {},
    getItem(k) { return this._s[k] || null; },
    setItem(k, v) { this._s[k] = String(v); },
    removeItem(k) { delete this._s[k]; },
  },
  document: {
    getElementById: getEl,
    createElement(tag) { return makeEl('', tag); },
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: (ev, fn) => {
      if (!docListeners[ev]) docListeners[ev] = [];
      docListeners[ev].push(fn);
    },
  },
  window: {
    MP3MetaFixMaxUploadSizeMb: 150,
    showToast: (msg, type) => toasts.push({ msg, type }),
    addEventListener: () => {},
    dispatchEvent: () => {},
  },
  fetch: async (url, opts) => {
    fetchCalls.push({ url, opts });
    if (url === '/api/projects/tracks') {
      return { ok: true, status: 200, json: async () => ([{ id: 'trk_1234567890abcdef', title: 'Song', status: 'draft', takes_count: 1 }]) };
    }
    if (url.startsWith('/api/projects/tracks/trk_1234567890abcdef') && (!opts || opts.method !== 'POST')) {
      return { ok: true, status: 200, json: async () => ({ track: { id: 'trk_1234567890abcdef' }, takes: [{ id: 'tak_1', label: 'Take 1', format: 'wav', size_bytes: 1024, is_master: true }] }) };
    }
    if (url === '/api/projects/albums' || url === '/api/storage/quota') {
      return { ok: true, status: 200, json: async () => ({}) };
    }
    return nextFetchResponse;
  },
  confirm: () => true,
};
context.window.window = context.window;

vm.runInNewContext(code, context);
(docListeners['DOMContentLoaded'] || []).forEach(fn => fn());

(async () => {
  const form = getEl('formUploadTake');
  if (!form || !form.listeners['submit'] || form.listeners['submit'].length === 0) {
    throw new Error('formUploadTake submit handler was NOT registered!');
  }
  const submitHandler = form.listeners['submit'][0];

  // 1. Unsupported extension rejected cleanly without POST upload
  getEl('uploadTakeTrackId').value = 'trk_1234567890abcdef';
  getEl('uploadTakeFileInput').files = [{ name: 'bad.txt', size: 128 }];
  let prevented = false;
  await submitHandler({ preventDefault: () => { prevented = true; } });
  if (!prevented) throw new Error('e.preventDefault() not called');
  if (fetchCalls.some(c => c.opts && c.opts.method === 'POST')) {
    throw new Error('Invalid file triggered fetch POST!');
  }
  if (!toasts.some(t => t.type === 'error' && t.msg.includes('Unsupported file format'))) {
    throw new Error('Expected unsupported format toast');
  }

  // 2. Non-JSON 500 response reports clear HTTP error without throwing
  getEl('uploadTakeFileInput').files = [{ name: 'valid.wav', size: 4096 }];
  nextFetchResponse = { ok: false, status: 502, json: async () => { throw new SyntaxError('Unexpected token <'); } };
  await submitHandler({ preventDefault: () => {} });
  if (!toasts.some(t => t.type === 'error' && t.msg.includes('HTTP 502'))) {
    throw new Error('Expected non-JSON HTTP 502 error toast');
  }
  if (submitBtn.disabled) throw new Error('Submit button remained disabled after error');

  // 3. Valid upload succeeds, hits POST /takes, closes modal, and expands/keeps drawer visible
  getEl('modalUploadTake').classList.remove('hidden');
  getEl('uploadTakeLabel').value = 'Alt Take';
  nextFetchResponse = { ok: true, status: 201, json: async () => ({ id: 'tak_2' }) };
  await submitHandler({ preventDefault: () => {} });
  const uploadPost = fetchCalls.find(c => c.url === '/api/projects/tracks/trk_1234567890abcdef/takes' && c.opts && c.opts.method === 'POST');
  if (!uploadPost) throw new Error('Expected POST /api/projects/tracks/{id}/takes call');
  if (!getEl('modalUploadTake').classList.contains('hidden')) {
    throw new Error('Expected modalUploadTake to be hidden after successful upload');
  }
  const trackDetailFetch = fetchCalls.filter(c => c.url === '/api/projects/tracks/trk_1234567890abcdef' && (!c.opts || c.opts.method !== 'POST'));
  if (trackDetailFetch.length === 0) {
    throw new Error('Expected expanded Takes Drawer to automatically load updated takes after upload');
  }
  console.log('NODE_HARNESS_OK');
})().catch(err => {
  console.error(err);
  process.exit(1);
});
"""
    for js_path in (projects_js_path, storage_js_path):
        env = os.environ.copy()
        env["TARGET_JS_PATH"] = str(js_path)
        proc = subprocess.run(
            [node_bin, "-e", harness],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert proc.returncode == 0, f"Node harness failed for {js_path.name}:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
        assert "NODE_HARNESS_OK" in proc.stdout


def _make_api_wav_bytes(duration_sec: float = 0.4, sample_rate: int = 44100) -> bytes:
    """Build valid uncompressed mono PCM WAV bytes of the requested duration."""
    import struct
    import wave

    num_frames = int(duration_sec * sample_rate)
    out = io.BytesIO()
    with wave.open(out, "wb") as wf:
        wf.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
        wf.writeframes(struct.pack("<h", 0) * num_frames)
    return out.getvalue()


def test_album_entries_take_selection_streaming_and_isolation(client, auth_cookies):
    """Verify REST album entry CRUD, explicit take vs placeholder, multi-entry from same track, range streaming, and isolation."""
    wav_master = _make_api_wav_bytes(0.4)
    wav_alt = _make_api_wav_bytes(0.8)
    wav_updated = _make_api_wav_bytes(1.1)

    trk_zero = client.post("/api/projects/tracks", json={"title": "Empty Sketch"}, cookies=auth_cookies).json()
    trk_rec = client.post("/api/projects/tracks", json={"title": "Main Song", "artist": "Synth"}, cookies=auth_cookies).json()

    t_master = client.post(
        f"/api/projects/tracks/{trk_rec['id']}/takes",
        data={"label": "Studio Master", "is_master": "true"},
        files={"file": ("master.wav", wav_master, "audio/wav")},
        cookies=auth_cookies,
    ).json()
    t_alt = client.post(
        f"/api/projects/tracks/{trk_rec['id']}/takes",
        data={"label": "Unplugged Alt", "is_master": "false"},
        files={"file": ("alt.wav", wav_alt, "audio/wav")},
        cookies=auth_cookies,
    ).json()

    album = client.post("/api/projects/albums", json={"title": "Studio Collection"}, cookies=auth_cookies).json()
    album_id = album["id"]

    # 1. Add 0-take track as placeholder
    r1 = client.post(
        f"/api/projects/albums/{album_id}/entries",
        json={"track_id": trk_zero["id"], "take_id": None, "disc_number": 1},
        cookies=auth_cookies,
    )
    assert r1.status_code == 201
    a1 = r1.json()
    e_placeholder = a1["tracks"][0]
    assert e_placeholder["id"].startswith("ale_")
    assert e_placeholder["is_placeholder"] is True
    assert e_placeholder["has_owned_audio"] is False

    # 2. Add explicit non-master take -> does NOT alter track master
    r2 = client.post(
        f"/api/projects/albums/{album_id}/entries",
        json={"track_id": trk_rec["id"], "take_id": t_alt["id"], "disc_number": 1},
        cookies=auth_cookies,
    )
    assert r2.status_code == 201
    a2 = r2.json()
    e_alt = a2["tracks"][1]
    assert e_alt["take_id"] == t_alt["id"]
    assert e_alt["is_placeholder"] is False
    assert e_alt["has_owned_audio"] is True
    assert e_alt["snapshot"]["source_take_label"] == "Unplugged Alt"

    trk_check = client.get(f"/api/projects/tracks/{trk_rec['id']}", cookies=auth_cookies).json()
    assert trk_check["track"]["primary_take_id"] == t_master["id"]

    # 3. Add same track again using master take with custom_title
    r3 = client.post(
        f"/api/projects/albums/{album_id}/entries",
        json={"track_id": trk_rec["id"], "take_id": t_master["id"], "custom_title": "Main Song (Master Cut)"},
        cookies=auth_cookies,
    )
    assert r3.status_code == 201
    assert len(r3.json()["tracks"]) == 3
    assert len({t["id"] for t in r3.json()["tracks"]}) == 3

    # 4. Mutate & delete source t_alt -> album stream, HTTP 206 range, and download still serve wav_alt
    ingest_edit_sess = storage_manager.create_session("alt_changed.wav", ".wav")
    with open(ingest_edit_sess[1], "wb") as f:
        f.write(wav_updated)
    from backend.security import create_signed_session_token
    ingest_res = client.post(
        "/api/projects/ingest-session",
        json={"track_id": trk_rec["id"], "take_id": t_alt["id"]},
        cookies={**auth_cookies, SESSION_COOKIE_NAME: create_signed_session_token(ingest_edit_sess[0])},
    )
    assert ingest_res.status_code in (200, 201)
    storage_manager.cleanup_session(ingest_edit_sess[0])

    del_take_res = client.delete(f"/api/projects/tracks/{trk_rec['id']}/takes/{t_alt['id']}", cookies=auth_cookies)
    assert del_take_res.status_code == 200

    full_stream = client.get(f"/api/projects/albums/{album_id}/entries/{e_alt['id']}/stream", cookies=auth_cookies)
    assert full_stream.status_code == 200
    assert full_stream.content == wav_alt

    range_stream = client.get(
        f"/api/projects/albums/{album_id}/entries/{e_alt['id']}/stream",
        headers={"Range": "bytes=0-63"},
        cookies=auth_cookies,
    )
    assert range_stream.status_code == 206
    assert range_stream.content == wav_alt[:64]

    dl_res = client.get(f"/api/projects/albums/{album_id}/entries/{e_alt['id']}/download", cookies=auth_cookies)
    assert dl_res.status_code == 200
    assert dl_res.content == wav_alt

    # 5. Clear e_alt to placeholder via PATCH, then delete it via DELETE
    p_res = client.patch(
        f"/api/projects/albums/{album_id}/entries/{e_alt['id']}",
        json={"clear_take_to_placeholder": True},
        cookies=auth_cookies,
    )
    assert p_res.status_code == 200
    cleared_entry = next(t for t in p_res.json()["tracks"] if t["id"] == e_alt["id"])
    assert cleared_entry["is_placeholder"] is True
    assert client.get(f"/api/projects/albums/{album_id}/entries/{e_alt['id']}/stream", cookies=auth_cookies).status_code == 404

    d_res = client.delete(f"/api/projects/albums/{album_id}/entries/{e_alt['id']}", cookies=auth_cookies)
    assert d_res.status_code == 200
    assert len(d_res.json()["tracks"]) == 2


def test_album_entries_cross_track_and_cross_tenant_rejected(client, setup_auth_environment, auth_cookies):
    """Reject cross-track take mismatches and cross-tenant track/take/album entry requests."""
    import uuid

    mgr = setup_auth_environment["manager"]
    bob_id = str(uuid.uuid4())
    mgr._save_users(mgr._load_users() + [{
        "id": bob_id,
        "username": "bob_album",
        "password_hash": "pbkdf2_sha256$600000$" + ("0" * 32) + "$" + ("0" * 64),
        "role": "user",
        "revocation_epoch": 0,
    }])
    bob_cookies = {AUTH_COOKIE_NAME: mgr.issue_token(bob_id)}

    wav_a = _make_api_wav_bytes(0.3)
    trk_a = client.post("/api/projects/tracks", json={"title": "Track A"}, cookies=auth_cookies).json()
    trk_b = client.post("/api/projects/tracks", json={"title": "Track B"}, cookies=auth_cookies).json()
    tak_a = client.post(
        f"/api/projects/tracks/{trk_a['id']}/takes",
        data={"label": "Take A"},
        files={"file": ("a.wav", wav_a, "audio/wav")},
        cookies=auth_cookies,
    ).json()
    tak_b = client.post(
        f"/api/projects/tracks/{trk_b['id']}/takes",
        data={"label": "Take B"},
        files={"file": ("b.wav", wav_a, "audio/wav")},
        cookies=auth_cookies,
    ).json()

    bob_trk = client.post("/api/projects/tracks", json={"title": "Bob Track"}, cookies=bob_cookies).json()
    bob_tak = client.post(
        f"/api/projects/tracks/{bob_trk['id']}/takes",
        data={"label": "Bob Take"},
        files={"file": ("bob.wav", wav_a, "audio/wav")},
        cookies=bob_cookies,
    ).json()

    album = client.post("/api/projects/albums", json={"title": "Admin Isolated LP"}, cookies=auth_cookies).json()
    album_id = album["id"]

    # Valid entry on Track A
    ok_res = client.post(
        f"/api/projects/albums/{album_id}/entries",
        json={"track_id": trk_a["id"], "take_id": tak_a["id"]},
        cookies=auth_cookies,
    )
    assert ok_res.status_code == 201
    entry_id = ok_res.json()["tracks"][0]["id"]

    # Wrong track for take on POST -> 400
    bad_post = client.post(
        f"/api/projects/albums/{album_id}/entries",
        json={"track_id": trk_a["id"], "take_id": tak_b["id"]},
        cookies=auth_cookies,
    )
    assert bad_post.status_code == 400

    # Wrong track for take on PATCH -> 400
    bad_patch = client.patch(
        f"/api/projects/albums/{album_id}/entries/{entry_id}",
        json={"take_id": tak_b["id"]},
        cookies=auth_cookies,
    )
    assert bad_patch.status_code == 400

    # Cross-tenant track/take reference -> 400
    bad_tenant = client.post(
        f"/api/projects/albums/{album_id}/entries",
        json={"track_id": bob_trk["id"], "take_id": bob_tak["id"]},
        cookies=auth_cookies,
    )
    assert bad_tenant.status_code == 400

    # Bob cannot access or mutate admin album entry endpoints
    assert client.post(f"/api/projects/albums/{album_id}/entries", json={"track_id": bob_trk["id"], "take_id": bob_tak["id"]}, cookies=bob_cookies).status_code == 404
    assert client.get(f"/api/projects/albums/{album_id}/entries/{entry_id}/stream", cookies=bob_cookies).status_code == 404
    assert client.post(f"/api/projects/albums/{album_id}/finalize", cookies=bob_cookies).status_code == 404


def test_album_entry_refresh_api_preview_confirm_and_conflict(client, auth_cookies):
    """Verify REST source refresh preview diff, 409 conflict on concurrent source change, and confirmed refresh."""
    wav_1 = _make_api_wav_bytes(0.4)
    wav_2 = _make_api_wav_bytes(0.7)
    wav_3 = _make_api_wav_bytes(1.0)

    trk = client.post("/api/projects/tracks", json={"title": "Refresh API Song"}, cookies=auth_cookies).json()
    tak = client.post(
        f"/api/projects/tracks/{trk['id']}/takes",
        data={"label": "Take 1", "notes": "Rough mix"},
        files={"file": ("v1.wav", wav_1, "audio/wav")},
        cookies=auth_cookies,
    ).json()
    client.post(
        f"/api/projects/tracks/{trk['id']}/takes/{tak['id']}/lyrics",
        json={"lyrics": "Verse A\nOld Chorus", "format": "lrc"},
        cookies=auth_cookies,
    )

    album = client.post("/api/projects/albums", json={"title": "Refresh API LP"}, cookies=auth_cookies).json()
    album = client.post(
        f"/api/projects/albums/{album['id']}/entries",
        json={"track_id": trk["id"], "take_id": tak["id"]},
        cookies=auth_cookies,
    ).json()
    entry_id = album["tracks"][0]["id"]

    # Mutate source take audio & lyrics
    sess_id, sess_path = storage_manager.create_session("v2.wav", ".wav")
    with open(sess_path, "wb") as f:
        f.write(wav_2)
    client.post(
        "/api/projects/ingest-session",
        json={"track_id": trk["id"], "take_id": tak["id"], "notes": "Refined mix"},
        cookies={**auth_cookies, SESSION_COOKIE_NAME: create_signed_session_token(sess_id)},
    )
    storage_manager.cleanup_session(sess_id)
    client.post(
        f"/api/projects/tracks/{trk['id']}/takes/{tak['id']}/lyrics",
        json={"lyrics": "Verse A\nNew Chorus", "format": "lrc"},
        cookies=auth_cookies,
    )

    prev_res = client.get(f"/api/projects/albums/{album['id']}/entries/{entry_id}/refresh-preview", cookies=auth_cookies)
    assert prev_res.status_code == 200
    preview = prev_res.json()
    assert preview["source_available"] is True
    assert preview["has_changes"] is True
    assert preview["audio_changed"] is True
    assert preview["lyrics_changed"] is True
    assert "+New Chorus" in preview["lyrics_diff"]
    assert preview["album_audio_stream_url"].endswith(f"/entries/{entry_id}/stream")
    assert preview["source_audio_stream_url"].endswith(f"/takes/{tak['id']}/stream")

    # Mutate source again after preview -> 409 Conflict
    sess2_id, sess2_path = storage_manager.create_session("v3.wav", ".wav")
    with open(sess2_path, "wb") as f:
        f.write(wav_3)
    client.post(
        "/api/projects/ingest-session",
        json={"track_id": trk["id"], "take_id": tak["id"]},
        cookies={**auth_cookies, SESSION_COOKIE_NAME: create_signed_session_token(sess2_id)},
    )
    storage_manager.cleanup_session(sess2_id)

    conflict_res = client.post(
        f"/api/projects/albums/{album['id']}/entries/{entry_id}/refresh",
        json={
            "expected_source_revision": preview["expected_source_revision"],
            "expected_source_audio_sha256": preview["expected_source_audio_sha256"],
            "expected_source_updated_at": preview["expected_source_updated_at"],
        },
        cookies=auth_cookies,
    )
    assert conflict_res.status_code == 409
    assert client.get(f"/api/projects/albums/{album['id']}/entries/{entry_id}/download", cookies=auth_cookies).content == wav_1

    # Fresh preview & confirm succeeds
    fresh = client.get(f"/api/projects/albums/{album['id']}/entries/{entry_id}/refresh-preview", cookies=auth_cookies).json()
    ok_refresh = client.post(
        f"/api/projects/albums/{album['id']}/entries/{entry_id}/refresh",
        json={
            "expected_source_revision": fresh["expected_source_revision"],
            "expected_source_audio_sha256": fresh["expected_source_audio_sha256"],
            "expected_source_updated_at": fresh["expected_source_updated_at"],
        },
        cookies=auth_cookies,
    )
    assert ok_refresh.status_code == 200
    assert client.get(f"/api/projects/albums/{album['id']}/entries/{entry_id}/download", cookies=auth_cookies).content == wav_3


def test_album_finalization_api_validation_and_lock_enforcement(client, auth_cookies):
    """Verify REST finalization validation errors and strict 409 immutability enforcement."""
    # Creating an album directly as completed is rejected
    bad_create = client.post(
        "/api/projects/albums",
        json={"title": "Premature Release", "status": "completed"},
        cookies=auth_cookies,
    )
    assert bad_create.status_code == 400

    wav_ok = _make_api_wav_bytes(0.5)
    trk = client.post("/api/projects/tracks", json={"title": "Release Track"}, cookies=auth_cookies).json()
    tak = client.post(
        f"/api/projects/tracks/{trk['id']}/takes",
        data={"label": "Master Wav"},
        files={"file": ("rel.wav", wav_ok, "audio/wav")},
        cookies=auth_cookies,
    ).json()

    album = client.post("/api/projects/albums", json={"title": "Final LP"}, cookies=auth_cookies).json()
    album_id = album["id"]

    # Add placeholder -> finalize fails 400 with structured error list
    add_ph = client.post(
        f"/api/projects/albums/{album_id}/entries",
        json={"track_id": trk["id"], "take_id": None},
        cookies=auth_cookies,
    ).json()
    entry_id = add_ph["tracks"][0]["id"]

    fail_fin = client.post(f"/api/projects/albums/{album_id}/finalize", cookies=auth_cookies)
    assert fail_fin.status_code == 400
    detail = fail_fin.json()["detail"]
    assert isinstance(detail, dict) and len(detail.get("errors", [])) >= 1

    # Assign real take -> finalize succeeds
    assign_res = client.patch(
        f"/api/projects/albums/{album_id}/entries/{entry_id}",
        json={"take_id": tak["id"]},
        cookies=auth_cookies,
    )
    assert assign_res.status_code == 200

    fin_res = client.post(f"/api/projects/albums/{album_id}/finalize", cookies=auth_cookies)
    assert fin_res.status_code == 200
    fin_album = fin_res.json()
    assert fin_album["is_finalized"] is True
    assert fin_album["status"] == "completed"
    assert fin_album["finalized_at"] is not None

    # All prohibited mutating / refresh routes return 409 Conflict
    assert client.patch(f"/api/projects/albums/{album_id}", json={"title": "Edited"}, cookies=auth_cookies).status_code == 409
    assert client.post(
        f"/api/projects/albums/{album_id}/artwork",
        files={"file": ("cover.jpg", VALID_JPEG_BYTES, "image/jpeg")},
        cookies=auth_cookies,
    ).status_code == 409
    assert client.post(
        f"/api/projects/albums/{album_id}/entries",
        json={"track_id": trk["id"], "take_id": tak["id"]},
        cookies=auth_cookies,
    ).status_code == 409
    assert client.patch(
        f"/api/projects/albums/{album_id}/entries/{entry_id}",
        json={"clear_take_to_placeholder": True},
        cookies=auth_cookies,
    ).status_code == 409
    assert client.delete(f"/api/projects/albums/{album_id}/entries/{entry_id}", cookies=auth_cookies).status_code == 409
    assert client.get(f"/api/projects/albums/{album_id}/entries/{entry_id}/refresh-preview", cookies=auth_cookies).status_code == 409
    assert client.post(
        f"/api/projects/albums/{album_id}/entries/{entry_id}/refresh",
        json={"expected_source_revision": 1, "expected_source_audio_sha256": "a" * 64},
        cookies=auth_cookies,
    ).status_code == 409
    assert client.post(f"/api/projects/albums/{album_id}/finalize", cookies=auth_cookies).status_code == 409

    # Read-only download & stream still work
    assert client.get(f"/api/projects/albums/{album_id}/entries/{entry_id}/stream", cookies=auth_cookies).status_code == 200
    assert client.get(f"/api/projects/albums/{album_id}/entries/{entry_id}/download", cookies=auth_cookies).status_code == 200


def test_frontend_album_track_take_selection_and_sequencer_behavior():
    """Execute frontend/js/projects.js in Node VM to verify nested Track/Take selection, Change Take, and finalized locks."""
    import os
    import shutil
    import subprocess
    from pathlib import Path

    node_bin = shutil.which("node")
    assert node_bin is not None, "node is required to execute frontend behavior tests"

    projects_js_path = Path(__file__).resolve().parent.parent / "frontend" / "js" / "projects.js"

    harness = r"""
const fs = require('fs');
const vm = require('vm');
const code = fs.readFileSync(process.env.TARGET_JS_PATH, 'utf8');

const elements = {};
function collectText(el) {
  let out = el.textContent || '';
  if (Array.isArray(el.children)) {
    for (const c of el.children) out += ' ' + collectText(c);
  }
  return out;
}
function matchSimple(el, sel) {
  if (sel === 'input[name="pickAlbumTakeId"]:checked') {
    return el.tagName === 'INPUT' && el.name === 'pickAlbumTakeId' && !!el.checked;
  }
  if (sel === 'input[name="pickAlbumTakeId"]') {
    return el.tagName === 'INPUT' && el.name === 'pickAlbumTakeId';
  }
  if (sel === 'button') return el.tagName === 'BUTTON';
  if (sel === 'tr') return el.tagName === 'TR';
  if (sel === 'option') return el.tagName === 'OPTION';
  if (sel === 'a') return el.tagName === 'A';
  return false;
}
function findAll(root, sel, out = []) {
  for (const c of (root.children || [])) {
    if (matchSimple(c, sel)) out.push(c);
    findAll(c, sel, out);
  }
  return out;
}
function makeEl(id, tag = 'div') {
  const listeners = {};
  const classSet = new Set();
  const el = {
    id,
    tagName: tag.toUpperCase(),
    value: '',
    name: '',
    type: '',
    textContent: '',
    checked: false,
    disabled: false,
    files: [],
    listeners,
    dataset: {},
    style: {},
    children: [],
    _innerHTML: '',
    classList: {
      add: (c) => classSet.add(c),
      remove: (c) => classSet.delete(c),
      contains: (c) => classSet.has(c),
      toggle: (c, force) => {
        if (force === true) classSet.add(c);
        else if (force === false) classSet.delete(c);
        else if (classSet.has(c)) classSet.delete(c);
        else classSet.add(c);
      },
    },
    set innerHTML(val) {
      this._innerHTML = val;
      if (val === '' || (typeof val === 'string' && val.startsWith('<option'))) {
        this.children = [];
      }
    },
    get innerHTML() {
      return this._innerHTML;
    },
    addEventListener: (ev, fn) => {
      if (!listeners[ev]) listeners[ev] = [];
      listeners[ev].push(fn);
    },
    dispatchEvent: async (ev) => {
      if (!ev.target) ev.target = el;
      const fns = listeners[ev.type] || [];
      for (const fn of fns) {
        await fn(ev);
      }
    },
    setAttribute: () => {},
    appendChild: (child) => {
      el.children.push(child);
      if (tag.toUpperCase() === 'SELECT' && child.tagName === 'OPTION' && !el.value) {
        el.value = child.value;
      }
      return child;
    },
    querySelector: (sel) => findAll(el, sel)[0] || null,
    querySelectorAll: (sel) => findAll(el, sel),
    reset: () => {},
  };
  return el;
}

const getEl = (id) => {
  if (!elements[id]) elements[id] = makeEl(id);
  return elements[id];
};

const docListeners = {};
const toasts = [];
const fetchCalls = [];

let mockAlbum = {
  id: 'alb_demo01',
  title: 'Test LP',
  album_artist: 'Artist',
  status: 'draft',
  is_finalized: false,
  revision: 1,
  revisions: [],
  has_cover: false,
  tracks: [
    {
      id: 'ale_existing01',
      track_number: 1,
      disc_number: 1,
      track_id: 'trk_multi',
      take_id: 'tak_2',
      is_placeholder: false,
      has_owned_audio: true,
      snapshot: { source_track_title: 'Multi Song', source_take_label: 'Master Cut', format: 'wav', duration_seconds: 125, source_take_revision: 1 },
    },
  ],
};

const context = {
  console,
  setTimeout,
  clearTimeout,
  Set,
  Map,
  Number,
  Date,
  CustomEvent: class CustomEvent { constructor(n, o) { this.type = n; this.detail = o && o.detail; } },
  FormData: class FormData { constructor() { this.fields = []; } append(k, v) { this.fields.push([k, v]); } },
  sessionStorage: { _s: {}, getItem(k) { return this._s[k] || null; }, setItem(k, v) { this._s[k] = String(v); }, removeItem(k) { delete this._s[k]; } },
  document: {
    getElementById: getEl,
    createElement(tag) { return makeEl('', tag); },
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: (ev, fn) => {
      if (!docListeners[ev]) docListeners[ev] = [];
      docListeners[ev].push(fn);
    },
  },
  window: {
    MP3MetaFixMaxUploadSizeMb: 150,
    showToast: (msg, type) => toasts.push({ msg, type }),
    addEventListener: () => {},
    dispatchEvent: () => {},
  },
  confirm: () => true,
  fetch: async (url, opts) => {
    fetchCalls.push({ url, opts });
    if (url === '/api/projects/tracks') {
      return {
        ok: true,
        status: 200,
        json: async () => ([
          { id: 'trk_empty', title: 'Empty Idea', artist: 'Writer', status: 'draft', takes_count: 0 },
          { id: 'trk_multi', title: 'Multi Song', artist: 'Writer', status: 'draft', takes_count: 2 },
        ]),
      };
    }
    if (url === '/api/projects/tracks/trk_multi') {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          track: { id: 'trk_multi', title: 'Multi Song', primary_take_id: 'tak_2' },
          takes: [
            { id: 'tak_1', label: 'Acoustic Alt', format: 'wav', size_bytes: 2048, duration_seconds: 90, revision: 1, is_master: false },
            { id: 'tak_2', label: 'Master Cut', format: 'wav', size_bytes: 4096, duration_seconds: 125, revision: 2, is_master: true },
          ],
        }),
      };
    }
    if (url === '/api/projects/tracks/trk_empty') {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          track: { id: 'trk_empty', title: 'Empty Idea', primary_take_id: null },
          takes: [],
        }),
      };
    }
    if (url === '/api/projects/albums') {
      return { ok: true, status: 200, json: async () => ([mockAlbum]) };
    }
    if (url === '/api/projects/albums/alb_demo01' && (!opts || opts.method === 'GET' || !opts.method)) {
      return { ok: true, status: 200, json: async () => mockAlbum };
    }
    if (url === '/api/projects/albums/alb_demo01/entries' && opts && opts.method === 'POST') {
      const body = JSON.parse(opts.body);
      const nextEntry = {
        id: 'ale_added_' + (mockAlbum.tracks.length + 1),
        track_number: mockAlbum.tracks.length + 1,
        disc_number: 1,
        track_id: body.track_id,
        take_id: body.take_id,
        is_placeholder: body.take_id === null,
        has_owned_audio: body.take_id !== null,
        snapshot: body.take_id ? { source_track_title: 'Multi Song', source_take_label: 'Acoustic Alt', format: 'wav', source_take_revision: 1 } : null,
      };
      mockAlbum = { ...mockAlbum, revision: mockAlbum.revision + 1, tracks: [...mockAlbum.tracks, nextEntry] };
      return { ok: true, status: 201, json: async () => mockAlbum };
    }
    if (url === '/api/projects/albums/alb_demo01/entries/ale_existing01' && opts && opts.method === 'PATCH') {
      const body = JSON.parse(opts.body);
      mockAlbum = {
        ...mockAlbum,
        revision: mockAlbum.revision + 1,
        tracks: mockAlbum.tracks.map(t => t.id === 'ale_existing01' ? { ...t, take_id: body.take_id, is_placeholder: body.take_id === null } : t),
      };
      return { ok: true, status: 200, json: async () => mockAlbum };
    }
    if (url === '/api/storage/quota') {
      return { ok: true, status: 200, json: async () => ({ used_bytes: 0, limit_bytes: 1000, usage_percent: 0 }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  },
};
context.window.window = context.window;

vm.runInNewContext(code, context);
(docListeners['DOMContentLoaded'] || []).forEach(fn => fn());

(async () => {
  await new Promise(r => setTimeout(r, 20));
  // Open Album Detail modal via album card's inspect button
  const albumCardButtons = getEl('albumsGridContainer').querySelectorAll('button');
  if (albumCardButtons.length === 0) throw new Error('Album card inspect button missing');
  await albumCardButtons[0].listeners['click'][0]();
  await new Promise(r => setTimeout(r, 20));

  // Sequencer should display existing take badge & Change Take button
  const seqText = collectText(getEl('albumSequencerTbody'));
  if (!seqText.includes('Master Cut') || !seqText.includes('Owned Copy')) {
    throw new Error('Sequencer missing take pill details: ' + seqText);
  }

  // Click "+ Add Track"
  await getEl('btnAddTrackToSequencer').listeners['click'][0]();
  const trackOptions = getEl('selectPickTrack').querySelectorAll('option').map(o => o.value);
  if (!trackOptions.includes('trk_multi') || !trackOptions.includes('trk_empty')) {
    throw new Error('Pick track dropdown must include already-added and 0-take tracks: ' + JSON.stringify(trackOptions));
  }

  // Select trk_multi -> loads takes, preselects master (tak_2) with master badge
  getEl('selectPickTrack').value = 'trk_multi';
  await getEl('selectPickTrack').dispatchEvent({ type: 'change' });
  await new Promise(r => setTimeout(r, 20));
  const radios = getEl('pickTrackTakesList').querySelectorAll('input[name="pickAlbumTakeId"]');
  if (radios.length !== 3) throw new Error('Expected 2 takes + placeholder radio, got ' + radios.length);
  const checkedRadio = getEl('pickTrackTakesList').querySelector('input[name="pickAlbumTakeId"]:checked');
  if (!checkedRadio || checkedRadio.value !== 'tak_2') {
    throw new Error('Expected master tak_2 preselected by default');
  }
  if (!collectText(getEl('pickTrackTakesList')).includes('MASTER')) {
    throw new Error('Expected ★ MASTER badge in takes list');
  }

  // Switch selection to non-master tak_1 and confirm
  radios.forEach(r => { r.checked = (r.value === 'tak_1'); });
  await getEl('btnConfirmAddTrackToAlbum').listeners['click'][0]();
  const addNonMaster = fetchCalls.find(c => c.url === '/api/projects/albums/alb_demo01/entries' && c.opts && c.opts.method === 'POST' && JSON.parse(c.opts.body).take_id === 'tak_1');
  if (!addNonMaster) throw new Error('Expected POST /entries with non-master tak_1');
  if (fetchCalls.some(c => c.url.includes('set-master'))) {
    throw new Error('Selecting non-master take must NEVER mutate track workspace master!');
  }

  // Select 0-take trk_empty -> placeholder auto-checked and submitted as take_id: null
  await getEl('btnAddTrackToSequencer').listeners['click'][0]();
  await new Promise(r => setTimeout(r, 10));
  getEl('selectPickTrack').value = 'trk_empty';
  await getEl('selectPickTrack').dispatchEvent({ type: 'change' });
  await new Promise(r => setTimeout(r, 20));
  await getEl('btnConfirmAddTrackToAlbum').listeners['click'][0]();
  const addPlaceholder = fetchCalls.find(c => c.url === '/api/projects/albums/alb_demo01/entries' && c.opts && c.opts.method === 'POST' && JSON.parse(c.opts.body).track_id === 'trk_empty' && JSON.parse(c.opts.body).take_id === null);
  if (!addPlaceholder) throw new Error('Expected POST /entries with take_id: null for 0-take track');
  if (!collectText(getEl('albumSequencerTbody')).includes('Placeholder')) {
    throw new Error('Expected sequencer to render Placeholder badge');
  }

  // Click "Take" (Change Selected Take) on first row and change tak_2 -> tak_1
  const firstRowButtons = getEl('albumSequencerTbody').children[0].querySelectorAll('button');
  const changeBtn = firstRowButtons.find(b => b.textContent === 'Take');
  if (!changeBtn) throw new Error('Change Take button missing in sequencer row');
  await changeBtn.listeners['click'][0]();
  await new Promise(r => setTimeout(r, 20));
  if (!getEl('selectPickTrack').disabled) throw new Error('selectPickTrack should be disabled in Change Take mode');
  const editRadios = getEl('pickTrackTakesList').querySelectorAll('input[name="pickAlbumTakeId"]');
  editRadios.forEach(r => { r.checked = (r.value === 'tak_1'); });
  await getEl('btnConfirmAddTrackToAlbum').listeners['click'][0]();
  const patchEntry = fetchCalls.find(c => c.url === '/api/projects/albums/alb_demo01/entries/ale_existing01' && c.opts && c.opts.method === 'PATCH');
  if (!patchEntry || JSON.parse(patchEntry.opts.body).take_id !== 'tak_1') {
    throw new Error('Expected PATCH /entries/ale_existing01 with tak_1');
  }

  // Simulate opening a finalized album -> controls locked
  mockAlbum = { ...mockAlbum, is_finalized: true, status: 'completed' };
  await albumCardButtons[0].listeners['click'][0]();
  await new Promise(r => setTimeout(r, 20));
  if (!getEl('btnSaveAlbumDetails').disabled || !getEl('btnAddTrackToSequencer').disabled || !getEl('btnFinalizeAlbum').disabled) {
    throw new Error('Expected finalized album save/add/finalize buttons disabled');
  }
  if (getEl('albumDetailFinalizedBadge').classList.contains('hidden')) {
    throw new Error('Expected albumDetailFinalizedBadge visible once finalized');
  }
  const finalizedRowBtns = getEl('albumSequencerTbody').children[0].querySelectorAll('button');
  if (finalizedRowBtns.length > 0) {
    throw new Error('Finalized album sequencer must not render mutating buttons');
  }
  console.log('NODE_ALBUM_VM_OK');
})().catch(err => {
  console.error(err);
  process.exit(1);
});
"""
    env = os.environ.copy()
    env["TARGET_JS_PATH"] = str(projects_js_path)
    proc = subprocess.run(
        [node_bin, "-e", harness],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, f"Node Album VM test failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    assert "NODE_ALBUM_VM_OK" in proc.stdout





