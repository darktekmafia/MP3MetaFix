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
    """Verify that updating a take from the editor increments both the take and track revisions."""
    # Create Track
    res = client.post("/api/projects/tracks", json={"title": "Ingest Rev Track"}, cookies=auth_cookies)
    assert res.status_code == 201
    track_id = res.json()["id"]

    # Upload Take
    files = {"file": ("song.mp3", io.BytesIO(VALID_MP3_HEADER), "audio/mpeg")}
    res = client.post(
        f"/api/projects/tracks/{track_id}/takes",
        files=files,
        data={"label": "Take 1"},
        cookies=auth_cookies,
    )
    take_id = res.json()["id"]

    # Load take into session
    res_load = client.post(
        f"/api/projects/tracks/{track_id}/takes/{take_id}/load-session",
        cookies=auth_cookies,
    )
    session_cookie = res_load.cookies[SESSION_COOKIE_NAME]

    # Ingest session with Update Take mode and revision notes
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

    # Verify that the parent track's revision was also incremented
    res_track = client.get(f"/api/projects/tracks/{track_id}", cookies=auth_cookies)
    assert res_track.status_code == 200
    track_data = res_track.json()["track"]
    assert track_data["revision"] == 2
    assert len(track_data["revisions"]) == 2
    assert track_data["revisions"][-1]["change_summary"] == "Added track and disc information"

    # Clean up
    client.delete(f"/api/projects/tracks/{track_id}", cookies=auth_cookies)


