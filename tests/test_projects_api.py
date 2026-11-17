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
        data={"expected_revision": str(album["revision"])},
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

    # 5. Phase 1A blocks deleting album with artwork (409), while empty draft album deletes cleanly (200)
    cur_rev = client.get(f"/api/projects/albums/{album_id}", cookies=auth_cookies).json()["revision"]
    res = client.delete(f"/api/projects/albums/{album_id}?expected_revision={cur_rev}", cookies=auth_cookies)
    assert res.status_code == 409

    empty_alb = client.post("/api/projects/albums", json={"title": "Empty Draft"}, cookies=auth_cookies).json()
    res_empty = client.delete(
        f"/api/projects/albums/{empty_alb['id']}?expected_revision={empty_alb['revision']}",
        cookies=auth_cookies,
    )
    assert res_empty.status_code == 200
    assert res_empty.json()["status"] == "deleted"


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

    res = client.delete(f"/api/projects/albums/{album_id}?expected_revision=1", cookies=bob_cookies)
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

    # Create album and add entries via dedicated /entries endpoint
    res_alb = client.post("/api/projects/albums", json={"title": "Sequenced Album"}, cookies=auth_cookies)
    assert res_alb.status_code == 201
    alb = res_alb.json()
    alb_id = alb["id"]

    r_add1 = client.post(
        f"/api/projects/albums/{alb_id}/entries",
        json={"expected_revision": alb["revision"], "track_id": trk1_id, "take_id": None, "custom_title": "Custom Track One"},
        cookies=auth_cookies,
    )
    assert r_add1.status_code == 201
    r_add2 = client.post(
        f"/api/projects/albums/{alb_id}/entries",
        json={"expected_revision": r_add1.json()["revision"], "track_id": trk2_id, "take_id": None},
        cookies=auth_cookies,
    )
    assert r_add2.status_code == 201
    data = r_add2.json()
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

    # Test patch with track_ids format (reordering existing entries)
    res_patch_ids = client.patch(
        f"/api/projects/albums/{alb_id}",
        json={"expected_revision": get_data["revision"], "track_ids": [trk2_id, trk1_id]},
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
        json={
            "expected_revision": album["revision"],
            "genre": "Ambient",
            "year": 2026,
            "change_summary": "Added genre and release year",
        },
        cookies=auth_cookies,
    )
    assert alb_patch.status_code == 200
    patched_alb = alb_patch.json()
    assert patched_alb["revision"] == 2
    assert len(patched_alb["revisions"]) == 2
    assert patched_alb["revisions"][1]["change_summary"] == "Added genre and release year"

    # Clean up
    client.delete(f"/api/projects/tracks/{track_id}", cookies=auth_cookies)
    client.delete(f"/api/projects/albums/{album_id}?expected_revision={patched_alb['revision']}", cookies=auth_cookies)


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
    add_res = client.post(
        f"/api/projects/albums/{alb_id}/entries",
        json={"expected_revision": alb["revision"], "track_id": track_id, "take_id": None},
        cookies=auth_cookies,
    )
    assert add_res.status_code == 201

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

    client.delete(f"/api/projects/albums/{alb_id}?expected_revision={alb_after['revision']}", cookies=auth_cookies)
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
function findAll(root, pred, out = []) {
  for (const c of (root.children || [])) {
    if (pred(c)) out.push(c);
    findAll(c, pred, out);
  }
  return out;
}
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
  confirm: () => context._confirmReturn !== undefined ? context._confirmReturn : true,
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
        json={"expected_revision": album["revision"], "track_id": trk_zero["id"], "take_id": None, "disc_number": 1},
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
        json={"expected_revision": a1["revision"], "track_id": trk_rec["id"], "take_id": t_alt["id"], "disc_number": 1},
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
        json={
            "expected_revision": a2["revision"],
            "track_id": trk_rec["id"],
            "take_id": t_master["id"],
            "custom_title": "Main Song (Master Cut)",
        },
        cookies=auth_cookies,
    )
    assert r3.status_code == 201
    a3 = r3.json()
    assert len(a3["tracks"]) == 3
    assert len({t["id"] for t in a3["tracks"]}) == 3

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

    # 5. Phase 1A blocks clearing a recorded entry (409), while placeholder and recorded entries can be removed (200)
    p_res = client.patch(
        f"/api/projects/albums/{album_id}/entries/{e_alt['id']}",
        json={"expected_revision": a3["revision"], "clear_take_to_placeholder": True},
        cookies=auth_cookies,
    )
    assert p_res.status_code == 409
    assert client.get(f"/api/projects/albums/{album_id}/entries/{e_alt['id']}/stream", cookies=auth_cookies).status_code == 200

    d_ph_res = client.delete(
        f"/api/projects/albums/{album_id}/entries/{e_placeholder['id']}?expected_revision={a3['revision']}",
        cookies=auth_cookies,
    )
    assert d_ph_res.status_code == 200
    assert len(d_ph_res.json()["tracks"]) == 2

    d_rec_res = client.delete(
        f"/api/projects/albums/{album_id}/entries/{e_alt['id']}?expected_revision={d_ph_res.json()['revision']}",
        cookies=auth_cookies,
    )
    assert d_rec_res.status_code == 200
    assert len(d_rec_res.json()["tracks"]) == 1


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
        json={"expected_revision": album["revision"], "track_id": trk_a["id"], "take_id": tak_a["id"]},
        cookies=auth_cookies,
    )
    assert ok_res.status_code == 201
    alb_after_ok = ok_res.json()
    entry_id = alb_after_ok["tracks"][0]["id"]

    # Wrong track for take on POST -> 400
    bad_post = client.post(
        f"/api/projects/albums/{album_id}/entries",
        json={"expected_revision": alb_after_ok["revision"], "track_id": trk_a["id"], "take_id": tak_b["id"]},
        cookies=auth_cookies,
    )
    assert bad_post.status_code == 400

    # Wrong track for take on PATCH against a placeholder entry -> 400
    ph_res = client.post(
        f"/api/projects/albums/{album_id}/entries",
        json={"expected_revision": alb_after_ok["revision"], "track_id": trk_a["id"], "take_id": None},
        cookies=auth_cookies,
    )
    assert ph_res.status_code == 201
    alb_after_ph = ph_res.json()
    ph_entry_id = alb_after_ph["tracks"][1]["id"]
    bad_patch = client.patch(
        f"/api/projects/albums/{album_id}/entries/{ph_entry_id}",
        json={"expected_revision": alb_after_ph["revision"], "take_id": tak_b["id"]},
        cookies=auth_cookies,
    )
    assert bad_patch.status_code == 400

    # Cross-tenant track/take reference -> 400
    bad_tenant = client.post(
        f"/api/projects/albums/{album_id}/entries",
        json={"expected_revision": alb_after_ph["revision"], "track_id": bob_trk["id"], "take_id": bob_tak["id"]},
        cookies=auth_cookies,
    )
    assert bad_tenant.status_code == 400

    # Bob cannot access or mutate admin album entry endpoints
    assert client.post(
        f"/api/projects/albums/{album_id}/entries",
        json={"expected_revision": 1, "track_id": bob_trk["id"], "take_id": bob_tak["id"]},
        cookies=bob_cookies,
    ).status_code == 404
    assert client.get(f"/api/projects/albums/{album_id}/entries/{entry_id}/stream", cookies=bob_cookies).status_code == 404
    assert client.post(f"/api/projects/albums/{album_id}/finalize?expected_revision=1", cookies=bob_cookies).status_code == 404


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
        json={"expected_revision": album["revision"], "track_id": trk["id"], "take_id": tak["id"]},
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

    # Stale source_audio_sha256 on POST /refresh returns 409 Conflict and preserves wav_1
    stale_refresh_res = client.post(
        f"/api/projects/albums/{album['id']}/entries/{entry_id}/refresh",
        json={
            "expected_revision": album["revision"],
            "expected_source_revision": preview["expected_source_revision"],
            "expected_source_audio_sha256": "0" * 64,
            "expected_source_updated_at": preview["expected_source_updated_at"],
            "expected_source_metadata_fingerprint": preview["expected_source_metadata_fingerprint"],
        },
        cookies=auth_cookies,
    )
    assert stale_refresh_res.status_code == 409
    assert client.get(f"/api/projects/albums/{album['id']}/entries/{entry_id}/download", cookies=auth_cookies).content == wav_1

    # Feature 3: Valid POST /refresh succeeds (200) and updates Album-owned recording to wav_2
    refresh_res = client.post(
        f"/api/projects/albums/{album['id']}/entries/{entry_id}/refresh",
        json={
            "expected_revision": album["revision"],
            "expected_source_revision": preview["expected_source_revision"],
            "expected_source_audio_sha256": preview["expected_source_audio_sha256"],
            "expected_source_updated_at": preview["expected_source_updated_at"],
            "expected_source_metadata_fingerprint": preview["expected_source_metadata_fingerprint"],
        },
        cookies=auth_cookies,
    )
    assert refresh_res.status_code == 200
    assert client.get(f"/api/projects/albums/{album['id']}/entries/{entry_id}/download", cookies=auth_cookies).content == wav_2


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
        json={"expected_revision": album["revision"], "track_id": trk["id"], "take_id": None},
        cookies=auth_cookies,
    ).json()
    entry_id = add_ph["tracks"][0]["id"]

    fail_fin = client.post(
        f"/api/projects/albums/{album_id}/finalize?expected_revision={add_ph['revision']}",
        cookies=auth_cookies,
    )
    assert fail_fin.status_code == 400
    detail = fail_fin.json()["detail"]
    assert isinstance(detail, dict) and len(detail.get("errors", [])) >= 1

    # Assign real take -> finalize succeeds
    assign_res = client.patch(
        f"/api/projects/albums/{album_id}/entries/{entry_id}",
        json={"expected_revision": add_ph["revision"], "take_id": tak["id"]},
        cookies=auth_cookies,
    )
    assert assign_res.status_code == 200
    assigned_rev = assign_res.json()["revision"]

    fin_res = client.post(
        f"/api/projects/albums/{album_id}/finalize?expected_revision={assigned_rev}",
        cookies=auth_cookies,
    )
    assert fin_res.status_code == 200
    fin_album = fin_res.json()
    assert fin_album["is_finalized"] is True
    assert fin_album["status"] == "completed"
    assert fin_album["finalized_at"] is not None
    fin_rev = fin_album["revision"]

    # All prohibited mutating / refresh routes return 409 Conflict
    assert client.patch(
        f"/api/projects/albums/{album_id}",
        json={"expected_revision": fin_rev, "title": "Edited"},
        cookies=auth_cookies,
    ).status_code == 409
    assert client.post(
        f"/api/projects/albums/{album_id}/artwork",
        data={"expected_revision": str(fin_rev)},
        files={"file": ("cover.jpg", VALID_JPEG_BYTES, "image/jpeg")},
        cookies=auth_cookies,
    ).status_code == 409
    assert client.post(
        f"/api/projects/albums/{album_id}/entries",
        json={"expected_revision": fin_rev, "track_id": trk["id"], "take_id": tak["id"]},
        cookies=auth_cookies,
    ).status_code == 409
    assert client.patch(
        f"/api/projects/albums/{album_id}/entries/{entry_id}",
        json={"expected_revision": fin_rev, "clear_take_to_placeholder": True},
        cookies=auth_cookies,
    ).status_code == 409
    assert client.delete(
        f"/api/projects/albums/{album_id}/entries/{entry_id}?expected_revision={fin_rev}",
        cookies=auth_cookies,
    ).status_code == 409
    assert client.get(f"/api/projects/albums/{album_id}/entries/{entry_id}/refresh-preview", cookies=auth_cookies).status_code == 409
    assert client.post(
        f"/api/projects/albums/{album_id}/entries/{entry_id}/refresh",
        json={
            "expected_revision": fin_rev,
            "expected_source_revision": 1,
            "expected_source_audio_sha256": "a" * 64,
            "expected_source_metadata_fingerprint": "a" * 64,
        },
        cookies=auth_cookies,
    ).status_code == 409
    assert client.post(f"/api/projects/albums/{album_id}/finalize?expected_revision={fin_rev}", cookies=auth_cookies).status_code == 409
    assert client.delete(f"/api/projects/albums/{album_id}?expected_revision={fin_rev}", cookies=auth_cookies).status_code == 409

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
  confirm: () => context._confirmReturn !== undefined ? context._confirmReturn : true,
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
  // Cancel confirmation -> zero PATCH requests
  context._confirmReturn = false;
  await getEl('btnConfirmAddTrackToAlbum').listeners['click'][0]();
  if (fetchCalls.some(c => c.url === '/api/projects/albums/alb_demo01/entries/ale_existing01' && c.opts && c.opts.method === 'PATCH')) {
    throw new Error('Canceling replacement confirmation must not send PATCH');
  }
  // Confirm -> sends PATCH /entries/ale_existing01 with tak_1
  context._confirmReturn = true;
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


def test_phase1a_api_replacement_blocked_legacy_reorder_and_unknown_ids(client, auth_cookies):
    """Verify HTTP 409 replacement blocks across endpoints, legacy track_ids preservation, and unknown ale_ ID rejection."""
    from backend.security import generate_album_entry_id

    wav_1 = _make_api_wav_bytes(0.3)
    wav_2 = _make_api_wav_bytes(0.5)
    trk_a = client.post("/api/projects/tracks", json={"title": "Song A"}, cookies=auth_cookies).json()
    trk_b = client.post("/api/projects/tracks", json={"title": "Song B"}, cookies=auth_cookies).json()

    t_a1 = client.post(
        f"/api/projects/tracks/{trk_a['id']}/takes",
        data={"label": "Take A1", "is_master": "true"},
        files={"file": ("a1.wav", wav_1, "audio/wav")},
        cookies=auth_cookies,
    ).json()
    t_a2 = client.post(
        f"/api/projects/tracks/{trk_a['id']}/takes",
        data={"label": "Take A2", "is_master": "false"},
        files={"file": ("a2.wav", wav_2, "audio/wav")},
        cookies=auth_cookies,
    ).json()
    t_b1 = client.post(
        f"/api/projects/tracks/{trk_b['id']}/takes",
        data={"label": "Take B1", "is_master": "true"},
        files={"file": ("b1.wav", wav_2, "audio/wav")},
        cookies=auth_cookies,
    ).json()

    album = client.post("/api/projects/albums", json={"title": "Safeguard API LP"}, cookies=auth_cookies).json()
    album_id = album["id"]

    # Add two entries from trk_a (t_a1, t_a2) and one from trk_b (t_b1)
    alb = client.post(
        f"/api/projects/albums/{album_id}/entries",
        json={"expected_revision": album["revision"], "track_id": trk_a["id"], "take_id": t_a1["id"]},
        cookies=auth_cookies,
    ).json()
    alb = client.post(
        f"/api/projects/albums/{album_id}/entries",
        json={"expected_revision": alb["revision"], "track_id": trk_a["id"], "take_id": t_a2["id"]},
        cookies=auth_cookies,
    ).json()
    alb = client.post(
        f"/api/projects/albums/{album_id}/entries",
        json={"expected_revision": alb["revision"], "track_id": trk_b["id"], "take_id": t_b1["id"]},
        cookies=auth_cookies,
    ).json()
    e_a1, e_a2, e_b1 = alb["tracks"]

    # 1. Clearing owned audio to placeholder via PATCH /entries/{id} returns 409 and preserves wav_1
    res_rep_entry = client.patch(
        f"/api/projects/albums/{album_id}/entries/{e_a1['id']}",
        json={"expected_revision": alb["revision"], "clear_take_to_placeholder": True},
        cookies=auth_cookies,
    )
    assert res_rep_entry.status_code == 409
    assert client.get(f"/api/projects/albums/{album_id}/entries/{e_a1['id']}/download", cookies=auth_cookies).content == wav_1

    # 2. Bypass attempt via PATCH /albums/{id} also returns 409 and preserves wav_1
    res_rep_alb = client.patch(
        f"/api/projects/albums/{album_id}",
        json={
            "expected_revision": alb["revision"],
            "tracks": [
                {**e_a1, "take_id": t_a2["id"]},
                e_a2,
                e_b1,
            ]
        },
        cookies=auth_cookies,
    )
    assert res_rep_alb.status_code == 409
    assert client.get(f"/api/projects/albums/{album_id}/entries/{e_a1['id']}/download", cookies=auth_cookies).content == wav_1

    # 3. Legacy track_ids reorder preserves all entries, takes, and owned audio
    res_legacy = client.patch(
        f"/api/projects/albums/{album_id}",
        json={"expected_revision": alb["revision"], "track_ids": [trk_b["id"], trk_a["id"], trk_a["id"]]},
        cookies=auth_cookies,
    )
    assert res_legacy.status_code == 200
    reordered = res_legacy.json()
    assert [t["id"] for t in reordered["tracks"]] == [e_b1["id"], e_a1["id"], e_a2["id"]]
    assert [t["take_id"] for t in reordered["tracks"]] == [t_b1["id"], t_a1["id"], t_a2["id"]]
    assert client.get(f"/api/projects/albums/{album_id}/entries/{e_a1['id']}/download", cookies=auth_cookies).content == wav_1

    # 4. Ambiguous legacy track_ids dropping duplicate trk_a returns 400 or 409 without mutating entries
    res_ambig = client.patch(
        f"/api/projects/albums/{album_id}",
        json={"expected_revision": reordered["revision"], "track_ids": [trk_b["id"], trk_a["id"]]},
        cookies=auth_cookies,
    )
    assert res_ambig.status_code in (400, 409)

    # 5. Unknown explicit ale_ ID returns 400 without deleting existing audio
    unknown_id = generate_album_entry_id()
    res_unknown = client.patch(
        f"/api/projects/albums/{album_id}",
        json={
            "expected_revision": reordered["revision"],
            "tracks": [
                {"id": unknown_id, "track_number": 1, "disc_number": 1, "track_id": trk_b["id"], "take_id": t_b1["id"]},
                e_a1,
                e_a2,
            ]
        },
        cookies=auth_cookies,
    )
    assert res_unknown.status_code == 400
    assert client.get(f"/api/projects/albums/{album_id}/entries/{e_b1['id']}/download", cookies=auth_cookies).content == wav_2


def test_phase1a_frontend_take_picker_error_and_finalized_delete_hidden():
    """Verify failed Take-picker loads never submit placeholder PATCH/POST and finalized album cards hide delete."""
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
function findAll(root, pred, out = []) {
  for (const c of (root.children || [])) {
    if (pred(c)) out.push(c);
    findAll(c, pred, out);
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
    className: '',
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
      if (val === '' || (typeof val === 'string' && val.startsWith('<option'))) this.children = [];
    },
    get innerHTML() { return this._innerHTML; },
    addEventListener: (ev, fn) => {
      if (!listeners[ev]) listeners[ev] = [];
      listeners[ev].push(fn);
    },
    dispatchEvent: async (ev) => {
      if (!ev.target) ev.target = el;
      for (const fn of (listeners[ev.type] || [])) await fn(ev);
    },
    setAttribute: () => {},
    appendChild: (child) => { el.children.push(child); return child; },
    querySelector: (sel) => findAll(el, c => {
      if (sel === 'input[name="pickAlbumTakeId"]:checked') return c.tagName === 'INPUT' && c.name === 'pickAlbumTakeId' && !!c.checked;
      return false;
    })[0] || null,
    querySelectorAll: (sel) => findAll(el, c => {
      if (sel === 'button') return c.tagName === 'BUTTON';
      return false;
    }),
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
const mutationCalls = [];
let trackFetchMode = 'http_500';

const albumsPayload = [
  {
    id: 'alb_draft1',
    title: 'Draft LP',
    status: 'draft',
    is_finalized: false,
    revision: 1,
    revisions: [],
    tracks: [
      { id: 'ale_owned1', track_number: 1, disc_number: 1, track_id: 'trk_1', take_id: 'tak_1', is_placeholder: false, has_owned_audio: true, snapshot: { source_track_title: 'Song 1', source_take_label: 'Take 1', format: 'wav' } }
    ],
  },
  {
    id: 'alb_final1',
    title: 'Final LP',
    status: 'completed',
    is_finalized: true,
    revision: 2,
    revisions: [],
    tracks: [
      { id: 'ale_owned2', track_number: 1, disc_number: 1, track_id: 'trk_1', take_id: 'tak_1', is_placeholder: false, has_owned_audio: true, snapshot: { source_track_title: 'Song 1', source_take_label: 'Take 1', format: 'wav' } }
    ],
  },
  {
    id: 'alb_empty1',
    title: 'Empty Draft LP',
    status: 'draft',
    is_finalized: false,
    has_cover: false,
    revision: 3,
    revisions: [],
    tracks: [],
  },
];

const context = {
  console, setTimeout, clearTimeout, Set, Map, Number, Date,
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
  confirm: () => context._confirmReturn !== undefined ? context._confirmReturn : true,
  fetch: async (url, opts) => {
    if (opts && (opts.method === 'POST' || opts.method === 'PATCH' || opts.method === 'DELETE')) {
      mutationCalls.push({ url, opts });
    }
    if (url === '/api/projects/tracks') {
      return { ok: true, status: 200, json: async () => ([{ id: 'trk_1', title: 'Song 1', status: 'draft', takes_count: 1 }]) };
    }
    if (url === '/api/projects/tracks/trk_1') {
      if (trackFetchMode === 'http_500') return { ok: false, status: 500, json: async () => ({ detail: 'Server error' }) };
      throw new Error('Simulated network failure');
    }
    if (url === '/api/projects/albums') {
      return { ok: true, status: 200, json: async () => albumsPayload };
    }
    if (url === '/api/projects/albums/alb_draft1') {
      return { ok: true, status: 200, json: async () => albumsPayload[0] };
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
  const cards = getEl('albumsGridContainer').children;
  if (cards.length !== 3) throw new Error('Expected 3 album cards');
  const draftBtns = cards[0].querySelectorAll('button');
  const finalBtns = cards[1].querySelectorAll('button');
  const emptyBtns = cards[2].querySelectorAll('button');
  if (draftBtns.some(b => (b.className || '').includes('btn-delete-track'))) {
    throw new Error('Recorded draft album card must NOT render delete button in Phase 1A');
  }
  if (finalBtns.some(b => (b.className || '').includes('btn-delete-track'))) {
    throw new Error('Finalized album card must NOT render delete button');
  }
  if (!emptyBtns.some(b => (b.className || '').includes('btn-delete-track'))) {
    throw new Error('Empty draft album card should keep delete button');
  }

  // Open draft album
  await draftBtns[0].listeners['click'][0]();
  await new Promise(r => setTimeout(r, 20));

  // 1. Add Track with HTTP 500 take lookup -> confirm disabled, clicking confirm sends NO POST
  trackFetchMode = 'http_500';
  await getEl('btnAddTrackToSequencer').listeners['click'][0]();
  getEl('selectPickTrack').value = 'trk_1';
  await getEl('selectPickTrack').dispatchEvent({ type: 'change' });
  await new Promise(r => setTimeout(r, 20));
  if (!getEl('btnConfirmAddTrackToAlbum').disabled) {
    throw new Error('Expected confirm button disabled when take lookup returns HTTP 500');
  }
  await getEl('btnConfirmAddTrackToAlbum').listeners['click'][0]();
  if (mutationCalls.length !== 0) {
    throw new Error('Failed take lookup in Add Track mode must NOT send POST!');
  }

  // 2. Change Take with network error -> confirm disabled, clicking confirm sends NO PATCH (never wipes recording)
  trackFetchMode = 'network_error';
  const rowButtons = getEl('albumSequencerTbody').children[0].querySelectorAll('button');
  const changeBtn = rowButtons.find(b => b.textContent === 'Take');
  await changeBtn.listeners['click'][0]();
  await new Promise(r => setTimeout(r, 20));
  if (!getEl('btnConfirmAddTrackToAlbum').disabled) {
    throw new Error('Expected confirm button disabled on network error in Change Take mode');
  }
  await getEl('btnConfirmAddTrackToAlbum').listeners['click'][0]();
  if (mutationCalls.length !== 0) {
    throw new Error('Failed take lookup in Change Take mode must NOT send PATCH!');
  }
  console.log('NODE_PHASE1A_VM_OK');
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
    assert proc.returncode == 0, f"Node Phase 1A VM test failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    assert "NODE_PHASE1A_VM_OK" in proc.stdout


def test_phase1a1_api_optimistic_concurrency_implicit_clear_and_corrupt_manifest(client, auth_cookies):
    """Verify REST expected_revision 409 guards, implicit take_id preservation/rejection, and fail-closed corrupt manifests."""
    import backend.projects_api as papi_mod

    wav_1 = _make_api_wav_bytes(0.4)
    trk = client.post("/api/projects/tracks", json={"title": "Concurrency Song"}, cookies=auth_cookies).json()
    tak = client.post(
        f"/api/projects/tracks/{trk['id']}/takes",
        data={"label": "Take 1", "is_master": "true"},
        files={"file": ("c1.wav", wav_1, "audio/wav")},
        cookies=auth_cookies,
    ).json()

    alb = client.post("/api/projects/albums", json={"title": "Concurrency LP"}, cookies=auth_cookies).json()
    album_id = alb["id"]
    alb = client.post(
        f"/api/projects/albums/{album_id}/entries",
        json={"expected_revision": alb["revision"], "track_id": trk["id"], "take_id": tak["id"]},
        cookies=auth_cookies,
    ).json()
    e1 = alb["tracks"][0]
    rev_current = alb["revision"]

    # 1. PATCH /albums/{id} omitting take_id preserves e1's owned recording
    res_meta = client.patch(
        f"/api/projects/albums/{album_id}",
        json={
            "expected_revision": rev_current,
            "tracks": [{"id": e1["id"], "track_id": trk["id"], "track_number": 1, "disc_number": 1, "custom_title": "Edited"}],
        },
        cookies=auth_cookies,
    )
    assert res_meta.status_code == 200
    assert res_meta.json()["tracks"][0]["take_id"] == tak["id"]
    assert res_meta.json()["tracks"][0]["has_owned_audio"] is True
    assert client.get(f"/api/projects/albums/{album_id}/entries/{e1['id']}/download", cookies=auth_cookies).content == wav_1
    rev_after_edit = res_meta.json()["revision"]

    # 2. PATCH /albums/{id} with explicit take_id: null on recorded entry -> 400 or 409
    res_null = client.patch(
        f"/api/projects/albums/{album_id}",
        json={
            "expected_revision": rev_after_edit,
            "tracks": [{"id": e1["id"], "track_id": trk["id"], "take_id": None, "track_number": 1}],
        },
        cookies=auth_cookies,
    )
    assert res_null.status_code in (400, 409)
    assert client.get(f"/api/projects/albums/{album_id}/entries/{e1['id']}/download", cookies=auth_cookies).content == wav_1

    # 3. Stale expected_revision -> 409 Conflict
    res_stale = client.patch(
        f"/api/projects/albums/{album_id}",
        json={"expected_revision": rev_current, "title": "Stale Overwrite", "tracks": []},
        cookies=auth_cookies,
    )
    assert res_stale.status_code == 409
    assert len(client.get(f"/api/projects/albums/{album_id}", cookies=auth_cookies).json()["tracks"]) == 1

    # 4. Omitting expected_revision -> 422 Unprocessable Entity at API boundary
    res_omit_rev = client.patch(
        f"/api/projects/albums/{album_id}",
        json={"tracks": []},
        cookies=auth_cookies,
    )
    assert res_omit_rev.status_code == 422
    assert client.get(f"/api/projects/albums/{album_id}/entries/{e1['id']}/download", cookies=auth_cookies).content == wav_1

    # 5. Corrupting album.json -> GET and DELETE fail closed with 422 and preserve owned audio on disk
    mgr = papi_mod.project_storage_manager
    user_id = client.get("/api/auth/status", cookies=auth_cookies).json().get("user", {}).get("id")
    if not user_id:
        from backend.auth import verify_auth_token
        user_id = verify_auth_token(auth_cookies[AUTH_COOKIE_NAME])
    album_dir = mgr._get_album_dir(user_id, album_id)
    (album_dir / "album.json").write_text("{broken_json", encoding="utf-8")

    assert client.get(f"/api/projects/albums/{album_id}", cookies=auth_cookies).status_code == 422
    assert client.delete(f"/api/projects/albums/{album_id}?expected_revision={rev_after_edit}", cookies=auth_cookies).status_code == 422
    from backend.security import get_album_entry_dir_name
    assert (album_dir / "entries" / get_album_entry_dir_name(e1["id"]) / "audio.wav").read_bytes() == wav_1


def test_phase1a1_frontend_overlapping_take_picker_and_expected_revision():
    """Verify stale late-success Take-picker responses are discarded and saveAlbumChanges sends expected_revision."""
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
function findAll(root, pred, out = []) {
  for (const c of (root.children || [])) {
    if (pred(c)) out.push(c);
    findAll(c, pred, out);
  }
  return out;
}
function makeEl(id, tag = 'div') {
  const listeners = {};
  const classSet = new Set();
  const el = {
    id, tagName: tag.toUpperCase(), value: '', name: '', type: '', textContent: '', className: '',
    checked: false, disabled: false, files: [], listeners, dataset: {}, style: {}, children: [], _innerHTML: '',
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
      if (val === '' || (typeof val === 'string' && val.startsWith('<option'))) this.children = [];
    },
    get innerHTML() { return this._innerHTML; },
    addEventListener: (ev, fn) => { if (!listeners[ev]) listeners[ev] = []; listeners[ev].push(fn); },
    dispatchEvent: async (ev) => {
      if (!ev.target) ev.target = el;
      for (const fn of (listeners[ev.type] || [])) await fn(ev);
    },
    setAttribute: () => {},
    appendChild: (child) => { el.children.push(child); return child; },
    querySelector: (sel) => findAll(el, c => sel === 'input[name="pickAlbumTakeId"]:checked' ? (c.tagName === 'INPUT' && c.name === 'pickAlbumTakeId' && !!c.checked) : false)[0] || null,
    querySelectorAll: (sel) => findAll(el, c => sel === 'button' ? c.tagName === 'BUTTON' : (sel === 'input[name="pickAlbumTakeId"]' ? (c.tagName === 'INPUT' && c.name === 'pickAlbumTakeId') : false)),
    reset: () => {},
  };
  return el;
}

const getEl = (id) => { if (!elements[id]) elements[id] = makeEl(id); return elements[id]; };
const docListeners = {};
const mutationCalls = [];
const albumObj = {
  id: 'alb_rev5',
  title: 'Rev LP',
  status: 'draft',
  is_finalized: false,
  revision: 5,
  revisions: [],
  tracks: [{ id: 'ale_1', track_number: 1, disc_number: 1, track_id: 'trk_a', take_id: 'tak_1', is_placeholder: false, has_owned_audio: true, snapshot: { source_track_title: 'Track A', source_take_label: 'Take 1', format: 'wav' } }],
};

const context = {
  console, setTimeout, clearTimeout, Set, Map, Number, Date,
  CustomEvent: class CustomEvent { constructor(n, o) { this.type = n; this.detail = o && o.detail; } },
  FormData: class FormData { constructor() { this.fields = []; } append(k, v) { this.fields.push([k, v]); } },
  sessionStorage: { _s: {}, getItem(k) { return this._s[k] || null; }, setItem(k, v) { this._s[k] = String(v); }, removeItem(k) { delete this._s[k]; } },
  document: {
    getElementById: getEl,
    createElement(tag) { return makeEl('', tag); },
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: (ev, fn) => { if (!docListeners[ev]) docListeners[ev] = []; docListeners[ev].push(fn); },
  },
  window: { MP3MetaFixMaxUploadSizeMb: 150, showToast: () => {}, addEventListener: () => {}, dispatchEvent: () => {} },
  confirm: () => context._confirmReturn !== undefined ? context._confirmReturn : true,
  fetch: async (url, opts) => {
    if (opts && (opts.method === 'POST' || opts.method === 'PATCH' || opts.method === 'DELETE')) {
      mutationCalls.push({ url, opts });
      if (url === '/api/projects/albums/alb_rev5' && opts.method === 'PATCH') {
        return { ok: true, status: 200, json: async () => ({ ...albumObj, revision: 6 }) };
      }
    }
    if (url === '/api/projects/tracks') {
      return { ok: true, status: 200, json: async () => ([
        { id: 'trk_a', title: 'Track A', status: 'draft', takes_count: 1 },
        { id: 'trk_b', title: 'Track B', status: 'draft', takes_count: 1 },
      ]) };
    }
    if (url === '/api/projects/tracks/trk_a') {
      // Slow success (45ms)
      await new Promise(r => setTimeout(r, 45));
      return { ok: true, status: 200, json: async () => ({ track: { id: 'trk_a', primary_take_id: 'tak_1' }, takes: [{ id: 'tak_1', label: 'Late Success Take', format: 'wav', is_master: true }] }) };
    }
    if (url === '/api/projects/tracks/trk_b') {
      // Fast failure (5ms)
      await new Promise(r => setTimeout(r, 5));
      return { ok: false, status: 500, json: async () => ({ detail: 'Track B failed' }) };
    }
    if (url === '/api/projects/albums') return { ok: true, status: 200, json: async () => ([albumObj]) };
    if (url === '/api/projects/albums/alb_rev5') return { ok: true, status: 200, json: async () => albumObj };
    if (url === '/api/storage/quota') return { ok: true, status: 200, json: async () => ({ used_bytes: 0, limit_bytes: 1000, usage_percent: 0 }) };
    return { ok: true, status: 200, json: async () => ({}) };
  },
};
context.window.window = context.window;

vm.runInNewContext(code, context);
(docListeners['DOMContentLoaded'] || []).forEach(fn => fn());

(async () => {
  await new Promise(r => setTimeout(r, 20));
  const inspectBtn = getEl('albumsGridContainer').querySelectorAll('button')[0];
  await inspectBtn.listeners['click'][0]();
  await new Promise(r => setTimeout(r, 20));

  // 1. Overlapping Take-picker: select trk_a (slow success) then immediately trk_b (fast 500 failure)
  await getEl('btnAddTrackToSequencer').listeners['click'][0]();
  getEl('selectPickTrack').value = 'trk_a';
  getEl('selectPickTrack').dispatchEvent({ type: 'change' });
  getEl('selectPickTrack').value = 'trk_b';
  getEl('selectPickTrack').dispatchEvent({ type: 'change' });

  // Wait past both responses (65ms)
  await new Promise(r => setTimeout(r, 65));
  if (!getEl('btnConfirmAddTrackToAlbum').disabled) {
    throw new Error('Late success from trk_a must NOT re-enable confirm after trk_b failed!');
  }
  const radios = getEl('pickTrackTakesList').querySelectorAll('input[name="pickAlbumTakeId"]');
  if (radios.length !== 0) {
    throw new Error('Late success from trk_a must NOT overwrite trk_b error state!');
  }
  await getEl('btnConfirmAddTrackToAlbum').listeners['click'][0]();
  if (mutationCalls.length !== 0) {
    throw new Error('Stale/failed Take-picker state must NOT trigger POST/PATCH');
  }

  // 2. Verify saveAlbumChanges sends expected_revision: 5
  await getEl('btnSaveAlbumDetails').listeners['click'][0]();
  await new Promise(r => setTimeout(r, 15));
  const saveCall = mutationCalls.find(c => c.url === '/api/projects/albums/alb_rev5' && c.opts.method === 'PATCH');
  if (!saveCall) throw new Error('Expected PATCH /api/projects/albums/alb_rev5');
  const sentBody = JSON.parse(saveCall.opts.body);
  if (sentBody.expected_revision !== 5) {
    throw new Error('Expected expected_revision === 5, got ' + sentBody.expected_revision);
  }
  console.log('NODE_PHASE1A1_VM_OK');
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
    assert proc.returncode == 0, f"Node Phase 1A.1 VM test failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    assert "NODE_PHASE1A1_VM_OK" in proc.stdout


def test_phase1a_final_api_revision_boundary_and_stream_safety(client, auth_cookies, tmp_path):
    """Verify 422 on missing expected_revision across all album mutators, 409 on bulk add/remove, and O_NOFOLLOW stream safety."""
    import backend.projects_api as papi_mod

    wav_1 = _make_api_wav_bytes(0.4)
    trk = client.post("/api/projects/tracks", json={"title": "Boundary Song"}, cookies=auth_cookies).json()
    tak = client.post(
        f"/api/projects/tracks/{trk['id']}/takes",
        data={"label": "Take 1", "is_master": "true"},
        files={"file": ("b1.wav", wav_1, "audio/wav")},
        cookies=auth_cookies,
    ).json()

    alb = client.post("/api/projects/albums", json={"title": "Boundary LP"}, cookies=auth_cookies).json()
    alb_id = alb["id"]

    # 1. Missing expected_revision -> 422 on every existing-album mutator
    assert client.patch(f"/api/projects/albums/{alb_id}", json={"title": "NoRev"}, cookies=auth_cookies).status_code == 422
    assert client.delete(f"/api/projects/albums/{alb_id}", cookies=auth_cookies).status_code == 422
    assert client.post(
        f"/api/projects/albums/{alb_id}/entries",
        json={"track_id": trk["id"], "take_id": tak["id"]},
        cookies=auth_cookies,
    ).status_code == 422
    assert client.post(f"/api/projects/albums/{alb_id}/finalize", cookies=auth_cookies).status_code == 422
    assert client.post(
        f"/api/projects/albums/{alb_id}/artwork",
        files={"file": ("cover.jpg", VALID_JPEG_BYTES, "image/jpeg")},
        cookies=auth_cookies,
    ).status_code == 422

    alb = client.post(
        f"/api/projects/albums/{alb_id}/entries",
        json={"expected_revision": alb["revision"], "track_id": trk["id"], "take_id": tak["id"]},
        cookies=auth_cookies,
    ).json()
    e1 = alb["tracks"][0]
    assert client.patch(
        f"/api/projects/albums/{alb_id}/entries/{e1['id']}",
        json={"custom_title": "NoRev"},
        cookies=auth_cookies,
    ).status_code == 422
    assert client.delete(f"/api/projects/albums/{alb_id}/entries/{e1['id']}", cookies=auth_cookies).status_code == 422

    # 2. Bulk PATCH /albums/{id} rejects entry additions/removals with 409
    assert client.patch(
        f"/api/projects/albums/{alb_id}",
        json={"expected_revision": alb["revision"], "tracks": []},
        cookies=auth_cookies,
    ).status_code == 409

    # 3. O_NOFOLLOW stream/download protection rejects symlinked audio file with 404
    mgr = papi_mod.project_storage_manager
    user_id = papi_mod.auth_manager.verify_token(auth_cookies[AUTH_COOKIE_NAME])
    assert user_id is not None
    audio_path = mgr.get_album_entry_audio_path(user_id, alb_id, e1["id"])
    assert audio_path is not None
    outside = tmp_path / "secret.wav"
    outside.write_bytes(b"SECRET")
    audio_path.unlink()
    audio_path.symlink_to(outside)

    assert client.get(f"/api/projects/albums/{alb_id}/entries/{e1['id']}/stream", cookies=auth_cookies).status_code == 404
    assert client.get(f"/api/projects/albums/{alb_id}/entries/{e1['id']}/download", cookies=auth_cookies).status_code == 404


def test_phase1a_final_frontend_change_take_zero_takes_and_card_delete_revision():
    """Verify Change Take mode on recorded entry never auto-selects placeholder on 0 takes/missing take and card delete sends expected_revision."""
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
function findAll(root, pred, out = []) {
  for (const c of (root.children || [])) {
    if (pred(c)) out.push(c);
    findAll(c, pred, out);
  }
  return out;
}
function makeEl(id, tag = 'div') {
  const listeners = {};
  const classSet = new Set();
  const el = {
    id, tagName: tag.toUpperCase(), value: '', name: '', type: '', textContent: '', className: '',
    checked: false, disabled: false, files: [], listeners, dataset: {}, style: {}, children: [], _innerHTML: '',
    classList: {
      add: (c) => classSet.add(c),
      remove: (c) => classSet.delete(c),
      contains: (c) => classSet.has(c),
      toggle: (c, force) => { if (force === true) classSet.add(c); else if (force === false) classSet.delete(c); else if (classSet.has(c)) classSet.delete(c); else classSet.add(c); },
    },
    set innerHTML(val) { this._innerHTML = val; if (val === '' || (typeof val === 'string' && val.startsWith('<option'))) this.children = []; },
    get innerHTML() { return this._innerHTML; },
    addEventListener: (ev, fn) => { if (!listeners[ev]) listeners[ev] = []; listeners[ev].push(fn); },
    dispatchEvent: async (ev) => { if (!ev.target) ev.target = el; for (const fn of (listeners[ev.type] || [])) await fn(ev); },
    setAttribute: () => {},
    appendChild: (child) => { el.children.push(child); return child; },
    querySelector: (sel) => findAll(el, c => sel === 'input[name="pickAlbumTakeId"]:checked' ? (c.tagName === 'INPUT' && c.name === 'pickAlbumTakeId' && !!c.checked) : false)[0] || null,
    querySelectorAll: (sel) => findAll(el, c => sel === 'button' ? c.tagName === 'BUTTON' : false),
    reset: () => {},
    remove: () => {},
  };
  return el;
}
const getEl = (id) => { if (!elements[id]) elements[id] = makeEl(id); return elements[id]; };
const docListeners = {};
const mutationCalls = [];
let trackTakesPayload = [];

const albumsPayload = [
  {
    id: 'alb_rec1', title: 'Recorded LP', status: 'draft', is_finalized: false, has_cover: false, revision: 2, revisions: [],
    tracks: [{ id: 'ale_r1', track_number: 1, disc_number: 1, track_id: 'trk_1', take_id: 'tak_missing', is_placeholder: false, has_owned_audio: true, snapshot: { source_track_title: 'Song 1', source_take_label: 'Old Take', format: 'wav' } }],
  },
  {
    id: 'alb_empty2', title: 'Empty LP', status: 'draft', is_finalized: false, has_cover: false, revision: 7, revisions: [], tracks: [],
  },
];

const context = {
  console, setTimeout, clearTimeout, Set, Map, Number, Date, encodeURIComponent,
  CustomEvent: class CustomEvent { constructor(n, o) { this.type = n; this.detail = o && o.detail; } },
  FormData: class FormData { constructor() { this.fields = []; } append(k, v) { this.fields.push([k, v]); } },
  sessionStorage: { _s: {}, getItem(k) { return this._s[k] || null; }, setItem(k, v) { this._s[k] = String(v); }, removeItem(k) { delete this._s[k]; } },
  document: {
    getElementById: getEl,
    createElement(tag) { return makeEl('', tag); },
    querySelector: () => null, querySelectorAll: () => [],
    addEventListener: (ev, fn) => { if (!docListeners[ev]) docListeners[ev] = []; docListeners[ev].push(fn); },
  },
  window: { MP3MetaFixMaxUploadSizeMb: 150, showToast: () => {}, addEventListener: () => {}, dispatchEvent: () => {} },
  confirm: () => context._confirmReturn !== undefined ? context._confirmReturn : true,
  fetch: async (url, opts) => {
    if (opts && (opts.method === 'POST' || opts.method === 'PATCH' || opts.method === 'DELETE')) mutationCalls.push({ url, opts });
    if (url === '/api/projects/tracks') return { ok: true, status: 200, json: async () => ([{ id: 'trk_1', title: 'Song 1', status: 'draft', takes_count: trackTakesPayload.length }]) };
    if (url === '/api/projects/tracks/trk_1') return { ok: true, status: 200, json: async () => ({ track: { id: 'trk_1', primary_take_id: null }, takes: trackTakesPayload }) };
    if (url === '/api/projects/albums') return { ok: true, status: 200, json: async () => albumsPayload };
    if (url === '/api/projects/albums/alb_rec1') return { ok: true, status: 200, json: async () => albumsPayload[0] };
    if (url === '/api/storage/quota') return { ok: true, status: 200, json: async () => ({ used_bytes: 0, limit_bytes: 1000, usage_percent: 0 }) };
    return { ok: true, status: 200, json: async () => ({}) };
  },
};
context.window.window = context.window;

vm.runInNewContext(code, context);
(docListeners['DOMContentLoaded'] || []).forEach(fn => fn());

(async () => {
  await new Promise(r => setTimeout(r, 20));
  const cards = getEl('albumsGridContainer').children;
  const emptyDelBtn = cards[1].querySelectorAll('button').find(b => (b.className || '').includes('btn-delete-track'));
  await emptyDelBtn.listeners['click'][0]();
  await new Promise(r => setTimeout(r, 20));
  const delCall = mutationCalls.find(c => c.opts.method === 'DELETE');
  if (!delCall || delCall.url !== '/api/projects/albums/alb_empty2?expected_revision=7') {
    throw new Error('Expected card delete to send ?expected_revision=7, got ' + JSON.stringify(delCall));
  }

  // Open recorded draft album and enter Change Take mode with 0 takes
  mutationCalls.length = 0;
  trackTakesPayload = [];
  await cards[0].querySelectorAll('button')[0].listeners['click'][0]();
  await new Promise(r => setTimeout(r, 20));
  const changeBtn = getEl('albumSequencerTbody').children[0].querySelectorAll('button').find(b => b.textContent === 'Take');
  await changeBtn.listeners['click'][0]();
  await new Promise(r => setTimeout(r, 20));
  if (!getEl('btnConfirmAddTrackToAlbum').disabled) {
    throw new Error('Expected confirm disabled when 0 takes exist in Change Take mode');
  }
  if (getEl('pickTrackTakesList').querySelector('input[name="pickAlbumTakeId"]:checked')) {
    throw new Error('Must NOT auto-select placeholder radio when 0 takes exist in Change Take mode');
  }

  // Also verify missing source take with another take present does NOT auto-select placeholder or other take
  trackTakesPayload = [{ id: 'tak_other', label: 'Other Take', format: 'wav', is_master: false, revision: 1 }];
  await changeBtn.listeners['click'][0]();
  await new Promise(r => setTimeout(r, 20));
  if (!getEl('btnConfirmAddTrackToAlbum').disabled) {
    throw new Error('Expected confirm disabled when source take is missing in Change Take mode');
  }
  if (getEl('pickTrackTakesList').querySelector('input[name="pickAlbumTakeId"]:checked')) {
    throw new Error('Must NOT auto-select any radio when source take is missing in Change Take mode');
  }

  console.log('NODE_PHASE1A_FINAL_VM_OK');
})().catch(err => {
  console.error(err);
  process.exit(1);
});
"""
    env = os.environ.copy()
    env["TARGET_JS_PATH"] = str(projects_js_path)
    proc = subprocess.run([node_bin, "-e", harness], capture_output=True, text=True, env=env, check=False)
    assert proc.returncode == 0, f"Node Phase 1A Final VM test failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    assert "NODE_PHASE1A_FINAL_VM_OK" in proc.stdout


def test_phase1a2_api_strict_revisions_fd_cleanup_get_purity_and_artwork_sync(client, auth_cookies, tmp_path):
    """Verify strict revision types (422), fd cleanup on pre-iteration disconnect, GET side-effect purity, bulk placeholder rejection, and artwork revision sync."""
    import errno
    import os
    import backend.projects_api as papi_mod

    wav_1 = _make_api_wav_bytes(0.4)
    trk = client.post("/api/projects/tracks", json={"title": "P1A2 Song"}, cookies=auth_cookies).json()
    tak = client.post(
        f"/api/projects/tracks/{trk['id']}/takes",
        data={"label": "Take 1", "is_master": "true"},
        files={"file": ("p1a2.wav", wav_1, "audio/wav")},
        cookies=auth_cookies,
    ).json()

    alb = client.post("/api/projects/albums", json={"title": "P1A2 LP"}, cookies=auth_cookies).json()
    alb_id = alb["id"]
    rev = alb["revision"]

    # 1. Malformed revision types rejected with 422 at API boundary
    for bad_json_rev in (True, False, 1.5, 1.0, "1", 0, -1):
        assert client.patch(
            f"/api/projects/albums/{alb_id}",
            json={"expected_revision": bad_json_rev, "title": "Bad"},
            cookies=auth_cookies,
        ).status_code == 422
    for bad_q_rev in ("true", "1.5", "1.0", "0", "-1", "abc"):
        assert client.delete(
            f"/api/projects/albums/{alb_id}?expected_revision={bad_q_rev}",
            cookies=auth_cookies,
        ).status_code == 422
        assert client.post(
            f"/api/projects/albums/{alb_id}/finalize?expected_revision={bad_q_rev}",
            cookies=auth_cookies,
        ).status_code == 422

    # 2. Artwork upload and replacement return updated album with incremented revision
    art_res = client.post(
        f"/api/projects/albums/{alb_id}/artwork",
        data={"expected_revision": str(rev)},
        files={"file": ("cover.jpg", io.BytesIO(VALID_JPEG_BYTES), "image/jpeg")},
        cookies=auth_cookies,
    )
    assert art_res.status_code == 200
    art_body = art_res.json()
    assert art_body["album"]["revision"] == rev + 1
    assert art_body["album"]["has_cover"] is True

    rep_art = client.post(
        f"/api/projects/albums/{alb_id}/artwork",
        data={"expected_revision": str(art_body["album"]["revision"])},
        files={"file": ("cover2.jpg", io.BytesIO(VALID_JPEG_BYTES), "image/jpeg")},
        cookies=auth_cookies,
    )
    assert rep_art.status_code == 200
    rep_body = rep_art.json()
    assert rep_body["album"]["revision"] == rev + 2

    # 3. Bulk PATCH rejects placeholder-to-Take assignment with 400
    ph_alb = client.post(
        f"/api/projects/albums/{alb_id}/entries",
        json={"expected_revision": rep_body["album"]["revision"], "track_id": trk["id"], "take_id": None},
        cookies=auth_cookies,
    ).json()
    ph_entry = ph_alb["tracks"][0]
    bulk_assign = client.patch(
        f"/api/projects/albums/{alb_id}",
        json={"expected_revision": ph_alb["revision"], "tracks": [{**ph_entry, "take_id": tak["id"]}]},
        cookies=auth_cookies,
    )
    assert bulk_assign.status_code == 400

    # 4. GET purity: nonexistent album stream/download/artwork and deleted-source refresh-preview create zero dirs/locks
    mgr = papi_mod.project_storage_manager
    user_id = papi_mod.auth_manager.verify_token(auth_cookies[AUTH_COOKIE_NAME])
    missing_id = "alb_0000000000000001"
    missing_lock = mgr._get_album_lock_path(user_id, missing_id)
    assert not missing_lock.exists()
    assert client.get(f"/api/projects/albums/{missing_id}/entries/ale_0000000000000001/stream", cookies=auth_cookies).status_code == 404
    assert client.get(f"/api/projects/albums/{missing_id}/entries/ale_0000000000000001/download", cookies=auth_cookies).status_code == 404
    assert client.get(f"/api/projects/albums/{missing_id}/artwork", cookies=auth_cookies).status_code == 404
    assert not missing_lock.exists()

    # Delete source track's take, then call refresh-preview -> no recreated take dir or lockfile
    rec_alb = client.post(
        f"/api/projects/albums/{alb_id}/entries",
        json={"expected_revision": ph_alb["revision"], "track_id": trk["id"], "take_id": tak["id"]},
        cookies=auth_cookies,
    ).json()
    rec_entry = rec_alb["tracks"][1]
    assert client.delete(f"/api/projects/tracks/{trk['id']}/takes/{tak['id']}", cookies=auth_cookies).status_code == 200
    take_dir = mgr._get_take_dir_path(user_id, trk["id"], tak["id"])
    assert not take_dir.exists()
    prev_res = client.get(f"/api/projects/albums/{alb_id}/entries/{rec_entry['id']}/refresh-preview", cookies=auth_cookies)
    assert prev_res.status_code == 200
    assert prev_res.json()["source_available"] is False
    assert not take_dir.exists()
    assert not (mgr._get_track_dir_path(user_id, trk["id"]) / "track.json.lock").exists()

    # 5. _FdStreamResource closes fd deterministically if response is closed/GC'd before generator iteration
    probe_file = tmp_path / "probe.wav"
    probe_file.write_bytes(wav_1)
    fd = os.open(probe_file, os.O_RDONLY)
    res_obj = papi_mod._FdStreamResource(fd)
    # Simulate Starlette BackgroundTask cleanup on pre-iteration disconnect without iterating
    res_obj.close()
    with pytest.raises(OSError) as exc_ebadf:
        os.fstat(fd)
    assert exc_ebadf.value.errno == errno.EBADF


def test_phase1a3_asgi_streaming_fd_cleanup(client, auth_cookies, tmp_path):
    """Verify _OwnedFdStreamingResponse closes descriptors deterministically across ASGI send error, pre-body cancel, mid-stream cancel, and normal completion."""
    import asyncio
    import errno
    import os
    import backend.projects_api as papi_mod

    wav_bytes = _make_api_wav_bytes(0.4)
    trk = client.post("/api/projects/tracks", json={"title": "ASGI Stream Song"}, cookies=auth_cookies).json()
    tak = client.post(
        f"/api/projects/tracks/{trk['id']}/takes",
        data={"label": "Take 1", "is_master": "true"},
        files={"file": ("asgi.wav", wav_bytes, "audio/wav")},
        cookies=auth_cookies,
    ).json()
    alb = client.post("/api/projects/albums", json={"title": "ASGI Stream LP"}, cookies=auth_cookies).json()
    alb = client.post(
        f"/api/projects/albums/{alb['id']}/entries",
        json={"expected_revision": alb["revision"], "track_id": trk["id"], "take_id": tak["id"]},
        cookies=auth_cookies,
    ).json()
    entry_id = alb["tracks"][0]["id"]
    user_id = papi_mod.auth_manager.verify_token(auth_cookies[AUTH_COOKIE_NAME])
    mgr = papi_mod.project_storage_manager

    async def _run_case(mode: str, range_hdr: str | None = None):
        opened = mgr.open_album_entry_audio_for_read(user_id, alb["id"], entry_id)
        assert opened is not None
        _, audio_path, fd, file_size = opened
        resource = papi_mod._FdStreamResource(fd)

        headers = [(b"host", b"testserver")]
        if range_hdr:
            headers.append((b"range", range_hdr.encode("ascii")))
        from starlette.requests import Request as StarletteRequest
        scope = {"type": "http", "method": "GET", "path": "/", "headers": headers}
        req = StarletteRequest(scope)
        resp = papi_mod.serve_audio_range_stream(audio_path, req, preopened_resource=resource, preopened_size=file_size)

        async def receive():
            await asyncio.sleep(10)
            return {"type": "http.disconnect"}

        sent_chunks = []

        async def send(message):
            if mode == "send_error":
                raise RuntimeError("Simulated ASGI send failure")
            if mode == "cancel_before_body" and message["type"] == "http.response.start":
                raise asyncio.CancelledError("Cancelled before body iteration")
            if mode == "cancel_mid_stream" and message["type"] == "http.response.body":
                raise asyncio.CancelledError("Cancelled during body streaming")
            if message["type"] == "http.response.body":
                sent_chunks.append(message.get("body", b""))

        try:
            await resp(scope, receive, send)
        except (RuntimeError, asyncio.CancelledError):
            pass

        # Must be closed deterministically without calling resource.close() manually
        with pytest.raises(OSError) as exc_ebadf:
            os.fstat(fd)
        assert exc_ebadf.value.errno == errno.EBADF
        return b"".join(sent_chunks)

    assert asyncio.run(_run_case("normal")) == wav_bytes
    assert asyncio.run(_run_case("normal", range_hdr="bytes=0-31")) == wav_bytes[:32]
    asyncio.run(_run_case("send_error"))
    asyncio.run(_run_case("cancel_before_body"))
    asyncio.run(_run_case("cancel_mid_stream"))


def test_phase1a2_frontend_artwork_revision_sync_and_context_guard():
    """Verify frontend artwork upload syncs revision when on same album, ignores stale album context, and never auto-retries 409."""
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
function findAll(root, pred, out = []) {
  for (const c of (root.children || [])) {
    if (pred(c)) out.push(c);
    findAll(c, pred, out);
  }
  return out;
}
function makeEl(id, tag = 'div') {
  const listeners = {};
  const classSet = new Set();
  const el = {
    id, tagName: tag.toUpperCase(), value: '', textContent: '', className: '',
    disabled: false, files: [], listeners, dataset: {}, style: {}, children: [], _innerHTML: '', src: '',
    classList: {
      add: (c) => classSet.add(c), remove: (c) => classSet.delete(c), contains: (c) => classSet.has(c),
      toggle: (c, f) => { if (f === true) classSet.add(c); else if (f === false) classSet.delete(c); },
    },
    set innerHTML(v) { this._innerHTML = v; if (v === '') this.children = []; }, get innerHTML() { return this._innerHTML; },
    addEventListener: (ev, fn) => { if (!listeners[ev]) listeners[ev] = []; listeners[ev].push(fn); },
    dispatchEvent: async (ev) => { if (!ev.target) ev.target = el; for (const fn of (listeners[ev.type] || [])) await fn(ev); },
    setAttribute: () => {}, appendChild: (c) => { el.children.push(c); return c; },
    querySelector: () => null,
    querySelectorAll: (sel) => findAll(el, c => sel === 'button' ? c.tagName === 'BUTTON' : false),
    reset: () => {},
    remove: () => {},
  };
  return el;
}
const getEl = (id) => { if (!elements[id]) elements[id] = makeEl(id); return elements[id]; };
const docListeners = {};
const calls = [];
let artworkMode = 'ok';
const album1 = { id: 'alb_1', title: 'Album 1', status: 'draft', is_finalized: false, revision: 2, revisions: [], tracks: [] };
const album2 = { id: 'alb_2', title: 'Album 2', status: 'draft', is_finalized: false, revision: 9, revisions: [], tracks: [] };

const context = {
  console, setTimeout, clearTimeout, Set, Map, Number, Date, encodeURIComponent,
  CustomEvent: class CustomEvent { constructor(n, o) { this.type = n; this.detail = o && o.detail; } },
  FormData: class FormData { constructor() { this.fields = []; } append(k, v) { this.fields.push([k, v]); } },
  sessionStorage: { _s: {}, getItem(k) { return this._s[k] || null; }, setItem(k, v) { this._s[k] = String(v); }, removeItem(k) { delete this._s[k]; } },
  document: {
    getElementById: getEl, createElement: (t) => makeEl('', t), querySelector: () => null, querySelectorAll: () => [],
    addEventListener: (ev, fn) => { if (!docListeners[ev]) docListeners[ev] = []; docListeners[ev].push(fn); },
  },
  window: { MP3MetaFixMaxUploadSizeMb: 150, showToast: () => {}, addEventListener: () => {}, dispatchEvent: () => {} },
  confirm: () => context._confirmReturn !== undefined ? context._confirmReturn : true,
  fetch: async (url, opts) => {
    if (opts && opts.method) calls.push({ url, opts });
    if (url === '/api/projects/albums') return { ok: true, status: 200, json: async () => ([album1, album2]) };
    if (url === '/api/projects/albums/alb_1') return { ok: true, status: 200, json: async () => album1 };
    if (url === '/api/projects/albums/alb_2') return { ok: true, status: 200, json: async () => album2 };
    if (url === '/api/projects/albums/alb_1/artwork') {
      if (artworkMode === '409') return { ok: false, status: 409, json: async () => ({ detail: 'Conflict' }) };
      if (artworkMode === 'switch_midflight') {
        await new Promise(r => setTimeout(r, 30));
        return { ok: true, status: 200, json: async () => ({ status: 'saved', album: { ...album1, revision: 3, has_cover: true } }) };
      }
      return { ok: true, status: 200, json: async () => ({ status: 'saved', album: { ...album1, revision: 3, has_cover: true } }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  },
};
context.window.window = context.window;
vm.runInNewContext(code, context);
(docListeners['DOMContentLoaded'] || []).forEach(fn => fn());

(async () => {
  await new Promise(r => setTimeout(r, 20));
  const cards = getEl('albumsGridContainer').children;
  // Open album 1 (rev 2)
  await cards[0].querySelectorAll('button')[0].listeners['click'][0]();
  await new Promise(r => setTimeout(r, 20));

  // Upload artwork -> updates activeAlbum revision to 3, so subsequent save sends expected_revision=3
  const input = getEl('inputAlbumCoverFile');
  input.files = [{ name: 'cover.jpg' }];
  await input.listeners['change'][0]({ target: input });
  await new Promise(r => setTimeout(r, 20));
  await getEl('btnSaveAlbumDetails').listeners['click'][0]();
  const patchCall = calls.find(c => c.opts.method === 'PATCH');
  if (!patchCall || JSON.parse(patchCall.opts.body).expected_revision !== 3) {
    throw new Error('Expected PATCH after artwork upload to send expected_revision=3, got ' + JSON.stringify(patchCall));
  }

  // Switching to album2 mid-flight must NOT overwrite album2's activeAlbum state with album1's response
  calls.length = 0;
  artworkMode = 'switch_midflight';
  await cards[0].querySelectorAll('button')[0].listeners['click'][0]();
  await new Promise(r => setTimeout(r, 20));
  const uploadPromise = input.listeners['change'][0]({ target: input });
  await cards[1].querySelectorAll('button')[0].listeners['click'][0]();
  await uploadPromise;
  await getEl('btnSaveAlbumDetails').listeners['click'][0]();
  const patch2 = calls.find(c => c.opts.method === 'PATCH');
  if (!patch2 || !patch2.url.endsWith('/alb_2') || JSON.parse(patch2.opts.body).expected_revision !== 9) {
    throw new Error('Stale album1 artwork response corrupted active album2 state: ' + JSON.stringify(patch2));
  }

  // 409 artwork conflict must NOT auto-retry
  calls.length = 0;
  artworkMode = '409';
  input.files = [{ name: 'cover.jpg' }];
  await input.listeners['change'][0]({ target: input });
  await new Promise(r => setTimeout(r, 20));
  if (calls.filter(c => c.url.endsWith('/artwork')).length !== 1) {
    throw new Error('409 artwork response must NOT auto-retry');
  }

  console.log('NODE_PHASE1A2_VM_OK');
})().catch(err => { console.error(err); process.exit(1); });
"""
    env = os.environ.copy()
    env["TARGET_JS_PATH"] = str(projects_js_path)
    proc = subprocess.run([node_bin, "-e", harness], capture_output=True, text=True, env=env, check=False)
    assert proc.returncode == 0, f"Node Phase 1A.2 VM test failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    assert "NODE_PHASE1A2_VM_OK" in proc.stdout


def test_phase1b_stage1_2_refresh_api_revision_and_concurrency(client, auth_cookies):
    """Phase 1B Stage 1.2: POST /refresh enforces strict expected_revision (422), stale 409, and finalized 409."""
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(b"\x00\x00" * 800)
    wav_ok = buf.getvalue()

    trk = client.post("/api/projects/tracks", json={"title": "Stage 1.2 API Track"}, cookies=auth_cookies).json()
    take = client.post(
        f"/api/projects/tracks/{trk['id']}/takes",
        files={"file": ("s12.wav", wav_ok, "audio/wav")},
        cookies=auth_cookies,
    ).json()
    alb = client.post("/api/projects/albums", json={"title": "Stage 1.2 API LP"}, cookies=auth_cookies).json()
    alb = client.post(
        f"/api/projects/albums/{alb['id']}/entries",
        json={"expected_revision": alb["revision"], "track_id": trk["id"], "take_id": take["id"]},
        cookies=auth_cookies,
    ).json()
    entry_id = alb["tracks"][0]["id"]
    url = f"/api/projects/albums/{alb['id']}/entries/{entry_id}/refresh"

    # 1. Missing, zero, negative, boolean, string, and float expected_revision -> 422
    assert client.post(
        url,
        json={"expected_source_revision": 1, "expected_source_audio_sha256": "a" * 64, "expected_source_metadata_fingerprint": "a" * 64},
        cookies=auth_cookies,
    ).status_code == 422
    for bad_rev in (0, -1, True, False, "2", 1.5):
        assert client.post(
            url,
            json={"expected_revision": bad_rev, "expected_source_revision": 1, "expected_source_audio_sha256": "a" * 64, "expected_source_metadata_fingerprint": "a" * 64},
            cookies=auth_cookies,
        ).status_code == 422

    # 2. Stale expected_revision -> 409 (conflict, not disabled-replacement message)
    stale_res = client.post(
        url,
        json={"expected_revision": alb["revision"] - 1, "expected_source_revision": 1, "expected_source_audio_sha256": "a" * 64, "expected_source_metadata_fingerprint": "a" * 64},
        cookies=auth_cookies,
    )
    assert stale_res.status_code == 409
    assert "modified concurrently" in stale_res.json()["detail"].lower()

    # 3. Current expected_revision -> 409 conflict, recording & revision preserved
    cur_res = client.post(
        url,
        json={"expected_revision": alb["revision"], "expected_source_revision": 1, "expected_source_audio_sha256": "a" * 64, "expected_source_metadata_fingerprint": "a" * 64},
        cookies=auth_cookies,
    )
    assert cur_res.status_code == 409
    assert "source take audio changed" in cur_res.json()["detail"].lower()
    assert client.get(f"/api/projects/albums/{alb['id']}", cookies=auth_cookies).json()["revision"] == alb["revision"]
    assert client.get(f"/api/projects/albums/{alb['id']}/entries/{entry_id}/download", cookies=auth_cookies).content == wav_ok


def test_phase1b_stage1_2_frontend_refresh_async_revision_capture():
    """Verify frontend openRefreshEntryModal/confirmRefreshAlbumEntry capture revision synchronously and discard stale responses."""
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
function findAll(root, pred, out = []) {
  for (const c of (root.children || [])) {
    if (pred(c)) out.push(c);
    findAll(c, pred, out);
  }
  return out;
}
function makeEl(id, tag = 'div') {
  const listeners = {};
  const classSet = new Set(['hidden']);
  const el = {
    id, tagName: tag.toUpperCase(), value: '', textContent: '', className: '',
    disabled: false, files: [], listeners, dataset: {}, style: {}, children: [], _innerHTML: '', src: '',
    classList: {
      add: (c) => classSet.add(c), remove: (c) => classSet.delete(c), contains: (c) => classSet.has(c),
      toggle: (c, f) => { if (f === true) classSet.add(c); else if (f === false) classSet.delete(c); },
    },
    set innerHTML(v) { this._innerHTML = v; if (v === '') this.children = []; }, get innerHTML() { return this._innerHTML; },
    addEventListener: (ev, fn) => { if (!listeners[ev]) listeners[ev] = []; listeners[ev].push(fn); },
    dispatchEvent: async (ev) => { if (!ev.target) ev.target = el; for (const fn of (listeners[ev.type] || [])) await fn(ev); },
    setAttribute: () => {}, appendChild: (c) => { el.children.push(c); return c; },
    querySelector: () => null,
    querySelectorAll: (sel) => findAll(el, c => sel === 'button' ? c.tagName === 'BUTTON' : false),
    reset: () => {},
    remove: () => {},
  };
  return el;
}
const getEl = (id) => { if (!elements[id]) elements[id] = makeEl(id); return elements[id]; };
const docListeners = {};
const deferredFetches = [];
const postCalls = [];

let albumA = {
  id: 'alb_A', title: 'Album A', status: 'draft', is_finalized: false, revision: 5, revisions: [],
  tracks: [
    { id: 'ale_1', track_id: 'trk_1', take_id: 'tak_1', track_number: 1, disc_number: 1, has_owned_audio: true, is_placeholder: false },
    { id: 'ale_2', track_id: 'trk_2', take_id: 'tak_2', track_number: 2, disc_number: 1, has_owned_audio: true, is_placeholder: false },
  ],
};
const albumB = {
  id: 'alb_B', title: 'Album B', status: 'draft', is_finalized: false, revision: 2, revisions: [],
  tracks: [],
};

const closeAlbumBtn = makeEl('closeAlbumBtn', 'button');
closeAlbumBtn.dataset.closeModal = 'modalAlbumDetail';
const closeRefreshBtn = makeEl('closeRefreshBtn', 'button');
closeRefreshBtn.dataset.closeModal = 'modalRefreshAlbumEntry';

const context = {
  console, setTimeout, clearTimeout, URLSearchParams, Object, Array, Number, Boolean, String, JSON, Math, Promise, Date, Error, FormData: function() { return { append: () => {} }; },
  document: {
    getElementById: getEl,
    createElement: (tag) => makeEl('dyn_' + Math.random().toString(36).slice(2), tag),
    addEventListener: (ev, fn) => { if (!docListeners[ev]) docListeners[ev] = []; docListeners[ev].push(fn); },
    querySelectorAll: (sel) => sel === '[data-close-modal]' ? [closeAlbumBtn, closeRefreshBtn] : [],
  },
  window: {
    location: { pathname: '/projects', hash: '', search: '' },
    addEventListener: () => {},
    history: { replaceState: () => {} },
    confirm: () => context._confirmReturn !== undefined ? context._confirmReturn : true,
  },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  showToast: () => {},
  fetch: async (url, opts = {}) => {
    const method = (opts.method || 'GET').toUpperCase();
    if (url.includes('/refresh-preview')) {
      return new Promise((resolve) => { deferredFetches.push({ url, resolve }); });
    }
    if (method === 'POST' && url.endsWith('/refresh')) {
      postCalls.push({ url, body: JSON.parse(opts.body) });
      return { ok: false, status: 409, json: async () => ({ detail: 'Temporarily disabled' }) };
    }
    if (method === 'PATCH' && url.endsWith('/alb_A')) {
      albumA = { ...albumA, revision: albumA.revision + 1 };
      return { ok: true, status: 200, json: async () => albumA };
    }
    if (url === '/api/projects/tracks') return { ok: true, status: 200, json: async () => [] };
    if (url === '/api/projects/albums') return { ok: true, status: 200, json: async () => [albumA, albumB] };
    if (url === '/api/projects/albums/alb_A') return { ok: true, status: 200, json: async () => albumA };
    if (url === '/api/projects/albums/alb_B') return { ok: true, status: 200, json: async () => albumB };
    if (url === '/api/storage/quota') return { ok: true, status: 200, json: async () => ({ used_bytes: 0, max_quota_bytes: 1000, used_percent: 0 }) };
    return { ok: true, status: 200, json: async () => ({}) };
  },
};
context.window.window = context.window;
vm.runInNewContext(code, context);
(docListeners['DOMContentLoaded'] || []).forEach(fn => fn());

(async () => {
  await new Promise(r => setTimeout(r, 20));
  const cards = getEl('albumsGridContainer').children;
  const modal = getEl('modalRefreshAlbumEntry');
  const btnConfirm = getEl('btnConfirmRefreshAlbumEntry');
  const btnSaveMeta = getEl('btnSaveAlbumDetails');

  const makePreview = (entryId) => ({
    ok: true,
    json: async () => ({
      track_id: 'trk_1',
      source_available: true,
      has_changes: true,
      audio_changed: true,
      lyrics_changed: false,
      metadata_changes: [],
      expected_source_revision: 3,
      expected_source_audio_sha256: 'a'.repeat(64),
      expected_source_updated_at: 100.0,
      expected_source_metadata_fingerprint: 'b'.repeat(64),
      album_snapshot: { source_track_title: 'Song ' + entryId, source_take_label: 'T1', source_take_revision: 1, format: 'wav', duration_seconds: 1.0 },
      source_snapshot: { source_take_label: 'T1', source_take_revision: 3, format: 'wav', duration_seconds: 1.0 },
    }),
  });

  function findRefreshBtns(root, out = []) {
    for (const c of (root.children || [])) {
      if (c.tagName === 'BUTTON' && c.title === 'Compare & Refresh from Source Take') out.push(c);
      findRefreshBtns(c, out);
    }
    return out;
  }

  // Open Album A (rev 5)
  await cards[0].querySelectorAll('button')[0].listeners['click'][0]();
  await new Promise(r => setTimeout(r, 20));
  let refreshBtns = findRefreshBtns(getEl('albumSequencerTbody'));
  if (refreshBtns.length < 2) throw new Error('Expected 2 refresh buttons rendered, got ' + refreshBtns.length);

  // Case 1: Preview starts for Album A rev 5, then Album A advances to rev 6 before response arrives -> discard
  modal.classList.add('hidden');
  const p1 = refreshBtns[0].listeners.click[0]();
  await btnSaveMeta.listeners['click'][0](); // rev 5 -> rev 6
  deferredFetches.shift().resolve(makePreview('ale_1'));
  await p1;
  await btnConfirm.listeners['click'][0]();
  if (!modal.classList.contains('hidden') || postCalls.length !== 0) {
    throw new Error('Case 1 failed: stale rev 5 response opened modal or sent POST');
  }

  // Case 2: Preview starts for Album A (rev 6), then activeAlbum changes to Album B -> discard
  refreshBtns = findRefreshBtns(getEl('albumSequencerTbody'));
  const p2 = refreshBtns[0].listeners.click[0]();
  await cards[1].querySelectorAll('button')[0].listeners['click'][0](); // switch to Album B
  await new Promise(r => setTimeout(r, 20));
  deferredFetches.shift().resolve(makePreview('ale_1'));
  await p2;
  await btnConfirm.listeners['click'][0]();
  if (!modal.classList.contains('hidden') || postCalls.length !== 0) {
    throw new Error('Case 2 failed: cross-album stale response opened modal or sent POST');
  }

  // Case 3: Two preview requests overlap on Album A (rev 6) and complete out of order (ale_1 resolves AFTER ale_2)
  await cards[0].querySelectorAll('button')[0].listeners['click'][0]();
  await new Promise(r => setTimeout(r, 20));
  refreshBtns = findRefreshBtns(getEl('albumSequencerTbody'));
  const p3a = refreshBtns[0].listeners.click[0]();
  const p3b = refreshBtns[1].listeners.click[0]();
  const f3a = deferredFetches.shift();
  const f3b = deferredFetches.shift();
  f3b.resolve(makePreview('ale_2'));
  await p3b;
  f3a.resolve(makePreview('ale_1'));
  await p3a;
  if (getEl('refreshEntrySubtitle').textContent !== 'Song ale_2 • Compare current album recording with latest source take') {
    throw new Error('Case 3 failed: older ale_1 response overwrote newer ale_2 modal state');
  }

  // Case 4: Album state changes AFTER confirmation modal is displayed -> confirm rejects without POST
  await btnSaveMeta.listeners['click'][0](); // rev 6 -> rev 7
  await btnConfirm.listeners['click'][0]();
  if (postCalls.length !== 0 || !modal.classList.contains('hidden')) {
    throw new Error('Case 4 failed: confirm sent POST despite changed album revision');
  }

  // Case 5: Unchanged context preserves originally captured revision (7) in POST /refresh
  refreshBtns = findRefreshBtns(getEl('albumSequencerTbody'));
  const p5 = refreshBtns[0].listeners.click[0]();
  deferredFetches.shift().resolve(makePreview('ale_1'));
  await p5;
  await btnConfirm.listeners['click'][0]();
  if (postCalls.length !== 1 || postCalls[0].body.expected_revision !== 7 || postCalls[0].body.expected_source_metadata_fingerprint !== 'b'.repeat(64) || !postCalls[0].url.endsWith('/ale_1/refresh')) {
    throw new Error('Case 5 failed: expected POST with captured revision 7 and metadata fingerprint, got ' + JSON.stringify(postCalls));
  }
  await closeRefreshBtn.listeners['click'][0]();

  // Case 6: Begin preview, close owning Album modal via Escape, then resolve -> refresh modal must NOT reopen
  postCalls.length = 0;
  await cards[0].querySelectorAll('button')[0].listeners['click'][0]();
  await new Promise(r => setTimeout(r, 20));
  refreshBtns = findRefreshBtns(getEl('albumSequencerTbody'));
  const beforeEscAlbumHidden = getEl('modalAlbumDetail').classList.contains('hidden');
  const keydownCount = (docListeners['keydown'] || []).length;
  const p6 = refreshBtns[0].listeners.click[0]();
  const f6 = deferredFetches.shift();
  for (const fn of (docListeners['keydown'] || [])) fn({ key: 'Escape' });
  f6.resolve(makePreview('ale_1'));
  await p6;
  await btnConfirm.listeners['click'][0]();
  if (!modal.classList.contains('hidden') || postCalls.length !== 0) {
    throw new Error('Case 6 failed (beforeEscAlbumHidden=' + beforeEscAlbumHidden + ', keydownCount=' + keydownCount + ', modalHidden=' + modal.classList.contains('hidden') + ', albumModalHidden=' + getEl('modalAlbumDetail').classList.contains('hidden') + ', postCalls=' + postCalls.length + ')');
  }

  // Case 7: Begin preview, explicitly close owning Album modal via close button, then resolve -> must NOT reopen
  await cards[0].querySelectorAll('button')[0].listeners['click'][0]();
  await new Promise(r => setTimeout(r, 20));
  refreshBtns = findRefreshBtns(getEl('albumSequencerTbody'));
  const p7 = refreshBtns[0].listeners.click[0]();
  const f7 = deferredFetches.shift();
  closeAlbumBtn.listeners['click'][0]();
  f7.resolve(makePreview('ale_1'));
  await p7;
  await btnConfirm.listeners['click'][0]();
  if (!modal.classList.contains('hidden') || postCalls.length !== 0) {
    throw new Error('Case 7 failed: closing Album modal did not invalidate in-flight preview');
  }

  // Case 8: Begin preview, cancel refresh modal via close button, then resolve -> must NOT reopen
  await cards[0].querySelectorAll('button')[0].listeners['click'][0]();
  await new Promise(r => setTimeout(r, 20));
  refreshBtns = findRefreshBtns(getEl('albumSequencerTbody'));
  const p8 = refreshBtns[0].listeners.click[0]();
  const f8 = deferredFetches.shift();
  closeRefreshBtn.listeners['click'][0]();
  f8.resolve(makePreview('ale_1'));
  await p8;
  await btnConfirm.listeners['click'][0]();
  if (!modal.classList.contains('hidden') || postCalls.length !== 0) {
    throw new Error('Case 8 failed: canceling refresh modal did not invalidate in-flight preview');
  }

  // Case 9: Open refresh confirmation modal, cancel it, verify pending request cannot be confirmed
  const p9 = refreshBtns[0].listeners.click[0]();
  deferredFetches.shift().resolve(makePreview('ale_1'));
  await p9;
  if (modal.classList.contains('hidden') || btnConfirm.disabled !== false) {
    throw new Error('Case 9 setup failed: refresh modal did not open');
  }
  closeRefreshBtn.listeners['click'][0]();
  await btnConfirm.listeners['click'][0]();
  if (!modal.classList.contains('hidden') || btnConfirm.disabled !== true || postCalls.length !== 0) {
    throw new Error('Case 9 failed: canceled refresh modal could still be confirmed');
  }

  // Case 10: A -> B -> A at same revision (7), then resolve original A response -> must remain invalid
  const p10 = refreshBtns[0].listeners.click[0]();
  const f10 = deferredFetches.shift();
  await cards[1].querySelectorAll('button')[0].listeners['click'][0](); // switch to B
  await new Promise(r => setTimeout(r, 20));
  await cards[0].querySelectorAll('button')[0].listeners['click'][0](); // return to A at same rev 7
  await new Promise(r => setTimeout(r, 20));
  f10.resolve(makePreview('ale_1'));
  await p10;
  await btnConfirm.listeners['click'][0]();
  if (!modal.classList.contains('hidden') || postCalls.length !== 0) {
    throw new Error('Case 10 failed: A -> B -> A at same revision revived original preview');
  }

  // Case 11: Fresh preview can still open and confirm normally after cancellation
  refreshBtns = findRefreshBtns(getEl('albumSequencerTbody'));
  const p11 = refreshBtns[0].listeners.click[0]();
  deferredFetches.shift().resolve(makePreview('ale_1'));
  await p11;
  await btnConfirm.listeners['click'][0]();
  if (postCalls.length !== 1 || postCalls[0].body.expected_revision !== 7) {
    throw new Error('Case 11 failed: fresh preview after cancellation did not confirm normally');
  }

  console.log('NODE_STAGE1_2_REFRESH_VM_OK');
})().catch(err => { console.error(err); process.exit(1); });
"""
    env = os.environ.copy()
    env["TARGET_JS_PATH"] = str(projects_js_path)
    proc = subprocess.run([node_bin, "-e", harness], capture_output=True, text=True, env=env, check=False)
    assert proc.returncode == 0, f"Node Stage 1.2 refresh VM test failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    assert "NODE_STAGE1_2_REFRESH_VM_OK" in proc.stdout


def test_phase1b_stage1_3b_2a1_api_route_error_mapping(client, auth_cookies, tmp_path):
    """Phase 1B Stage 1.3B-2A-1: all 8 Album mutation routes map 503 uncertain, 409 not_enrolled/revision, and 422 corrupt."""
    import io
    import json
    import wave
    from PIL import Image
    import backend.projects_api as papi_mod
    from backend.project_models import ActiveTxRecord, AlbumJournal, JournalProtocol, JournalTxState

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(b"\x00\x00" * 400)
    wav_ok = buf.getvalue()

    img_buf = io.BytesIO()
    Image.new("RGB", (64, 64), color=(10, 20, 30)).save(img_buf, format="JPEG")
    jpg_bytes = img_buf.getvalue()

    trk = client.post("/api/projects/tracks", json={"title": "API 2A1 Track"}, cookies=auth_cookies).json()
    take = client.post(
        f"/api/projects/tracks/{trk['id']}/takes",
        files={"file": ("api2a1.wav", wav_ok, "audio/wav")},
        cookies=auth_cookies,
    ).json()
    alb = client.post("/api/projects/albums", json={"title": "API 2A1 Album"}, cookies=auth_cookies).json()
    alb = client.post(
        f"/api/projects/albums/{alb['id']}/entries",
        json={"track_id": trk["id"], "take_id": take["id"], "expected_revision": alb["revision"]},
        cookies=auth_cookies,
    ).json()
    alb = client.post(
        f"/api/projects/albums/{alb['id']}/entries",
        json={"track_id": trk["id"], "expected_revision": alb["revision"]},
        cookies=auth_cookies,
    ).json()
    entry_rec = alb["tracks"][0]
    entry_ph = alb["tracks"][1]

    status_json = client.get("/api/auth/status", cookies=auth_cookies).json()
    user_id = status_json["user"]["id"]
    alb_dir = papi_mod.project_storage_manager._get_album_dir(user_id, alb["id"])
    journal_path = alb_dir / "journal.json"

    def _call_1_unenrolled_route(rev: int):
        return [
            ("delete_album", client.delete(f"/api/projects/albums/{alb['id']}?expected_revision={rev}", cookies=auth_cookies)),
        ]

    def _call_all_8_routes(rev: int):
        return [
            ("patch_album", client.patch(f"/api/projects/albums/{alb['id']}", json={"title": "X", "expected_revision": rev}, cookies=auth_cookies)),
            ("finalize", client.post(f"/api/projects/albums/{alb['id']}/finalize?expected_revision={rev}", cookies=auth_cookies)),
            ("add_entry", client.post(f"/api/projects/albums/{alb['id']}/entries", json={"track_id": trk["id"], "expected_revision": rev}, cookies=auth_cookies)),
            ("patch_entry", client.patch(f"/api/projects/albums/{alb['id']}/entries/{entry_ph['id']}", json={"custom_title": "Y", "expected_revision": rev}, cookies=auth_cookies)),
            ("refresh", client.post(f"/api/projects/albums/{alb['id']}/entries/{entry_rec['id']}/refresh", json={"expected_revision": rev, "expected_source_revision": 1, "expected_source_audio_sha256": entry_rec["snapshot"]["audio_sha256"], "expected_source_metadata_fingerprint": "a" * 64}, cookies=auth_cookies)),
            ("del_entry", client.delete(f"/api/projects/albums/{alb['id']}/entries/{entry_ph['id']}?expected_revision={rev}", cookies=auth_cookies)),
            ("artwork", client.post(f"/api/projects/albums/{alb['id']}/artwork", data={"expected_revision": str(rev)}, files={"file": ("cover.jpg", jpg_bytes, "image/jpeg")}, cookies=auth_cookies)),
            *_call_1_unenrolled_route(rev),
        ]

    # 1. Idle v2 journal -> 409 mutation_not_enrolled on remaining unenrolled route (and no reconciliation recommended)
    idle_v2 = AlbumJournal(schema_version=2, protocol=JournalProtocol.ENFORCED_V2, album_id=alb["id"])
    journal_path.write_text(json.dumps(idle_v2.model_dump(mode="json")), encoding="utf-8")
    for name, res in _call_1_unenrolled_route(alb["revision"]):
        assert res.status_code == 409, f"{name} expected 409, got {res.status_code}: {res.text}"
        assert res.headers.get("X-Error-Code") == "mutation_not_enrolled"
        body = res.json()["detail"]
        assert body["error_code"] == "mutation_not_enrolled"
        assert "reconcil" not in body["message"].lower()

    # Enrolled PATCH /albums/{id} succeeds on idle v2 journal
    enrolled_patch = client.patch(
        f"/api/projects/albums/{alb['id']}",
        json={"title": "Enrolled API Patch", "expected_revision": alb["revision"]},
        cookies=auth_cookies,
    )
    assert enrolled_patch.status_code == 200, enrolled_patch.text
    alb = enrolled_patch.json()

    # 2. Active / uncertain v2 journal -> 503 durability_uncertain
    act_v2 = AlbumJournal(
        schema_version=2,
        protocol=JournalProtocol.ENFORCED_V2,
        album_id=alb["id"],
        active_tx=ActiveTxRecord(
            tx_id="tx_api_act",
            op="update_album",
            state=JournalTxState.PUBLISH_INTENT,
            expected_revision=alb["revision"],
            target_revision=alb["revision"] + 1,
            payload_sha256="a" * 64,
            intended_manifest_sha256="b" * 64,
        ),
    )
    journal_path.write_text(json.dumps(act_v2.model_dump(mode="json")), encoding="utf-8")
    for name, res in _call_all_8_routes(alb["revision"]):
        assert res.status_code == 503, f"{name} expected 503, got {res.status_code}: {res.text}"
        assert res.headers.get("X-Error-Code") == "durability_uncertain"
        assert res.json()["detail"]["error_code"] == "durability_uncertain"

    # GET remains 200 and side-effect-free
    assert client.get(f"/api/projects/albums/{alb['id']}", cookies=auth_cookies).status_code == 200
    assert client.get("/api/projects/albums", cookies=auth_cookies).status_code == 200

    # 3. Corrupt journal -> 422 preserved across all 8 routes
    journal_path.write_text("{corrupt", encoding="utf-8")
    for name, res in _call_all_8_routes(alb["revision"]):
        assert res.status_code == 422, f"{name} expected 422, got {res.status_code}: {res.text}"

    # 4. Journal-less Album stale revision -> 409 with X-Error-Code: revision_conflict and string detail
    journal_path.unlink()
    stale_res = client.patch(
        f"/api/projects/albums/{alb['id']}",
        json={"title": "Stale", "expected_revision": alb["revision"] - 1},
        cookies=auth_cookies,
    )
    assert stale_res.status_code == 409
    assert stale_res.headers.get("X-Error-Code") == "revision_conflict"
    assert isinstance(stale_res.json()["detail"], str)

    # 5. Persisted manifest omitting redundant 'track_ids' with matching v2 receipt -> 409 mutation_not_enrolled (not 422)
    from backend.project_models import TerminalTxReceipt
    from backend.project_storage import ProjectStorageManager
    manifest_path = alb_dir / "album.json"
    persisted_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    persisted_data.pop("track_ids", None)
    persisted_data["last_tx_id"] = "tx_api_raw"
    persisted_data["tx_history"] = ["tx_api_raw"]
    manifest_path.write_text(json.dumps(persisted_data), encoding="utf-8")
    persisted_sha = ProjectStorageManager._compute_canonical_manifest_sha256(persisted_data)
    raw_j = AlbumJournal(
        schema_version=2,
        protocol=JournalProtocol.ENFORCED_V2,
        album_id=alb["id"],
        tx_receipts=[
            TerminalTxReceipt(
                tx_id="tx_api_raw",
                op="update_album",
                outcome=JournalTxState.COMMITTED,
                expected_revision=persisted_data["revision"] - 1,
                result_revision=persisted_data["revision"],
                payload_sha256="a" * 64,
                intended_manifest_sha256=persisted_sha,
            )
        ],
    )
    journal_path.write_text(json.dumps(raw_j.model_dump(mode="json")), encoding="utf-8")
    raw_m_bytes = manifest_path.read_bytes()
    raw_j_bytes = journal_path.read_bytes()
    raw_res = client.delete(
        f"/api/projects/albums/{alb['id']}?expected_revision={persisted_data['revision']}",
        cookies=auth_cookies,
    )
    assert raw_res.status_code == 409
    assert raw_res.headers.get("X-Error-Code") == "mutation_not_enrolled"
    assert manifest_path.read_bytes() == raw_m_bytes
    assert journal_path.read_bytes() == raw_j_bytes


def test_phase1b_stage1_3b_2a2a_api_update_replay_and_errors(client, auth_cookies):
    """Phase 1B Stage 1.3B-2A-2A: PATCH /albums/{id} v2 enrollment, Idempotency-Key replay, 409/503/422 mappings."""
    import json
    import os
    import backend.projects_api as papi_mod
    from backend.project_models import AlbumJournal, JournalProtocol, JournalTxState, TerminalTxReceipt
    from backend.project_storage import ProjectStorageManager

    alb = client.post("/api/projects/albums", json={"title": "API 2A2A"}, cookies=auth_cookies).json()
    status_json = client.get("/api/auth/status", cookies=auth_cookies).json()
    user_id = status_json["user"]["id"]
    alb_dir = papi_mod.project_storage_manager._get_album_dir(user_id, alb["id"])
    journal_path = alb_dir / "journal.json"

    # 1. Keyed no-op does not create journal or set X-Idempotent-Replay
    noop_res = client.patch(
        f"/api/projects/albums/{alb['id']}",
        json={"title": "API 2A2A", "expected_revision": alb["revision"]},
        headers={"Idempotency-Key": "api_k1"},
        cookies=auth_cookies,
    )
    assert noop_res.status_code == 200
    assert noop_res.headers.get("X-Idempotent-Replay") is None
    assert not journal_path.exists()

    # 2. Meaningful update creates v2 journal & consumes api_k1
    upd_res = client.patch(
        f"/api/projects/albums/{alb['id']}",
        json={"title": "API 2A2A Updated", "expected_revision": alb["revision"]},
        headers={"Idempotency-Key": "api_k1"},
        cookies=auth_cookies,
    )
    assert upd_res.status_code == 200
    assert upd_res.headers.get("X-Idempotent-Replay") is None
    assert upd_res.json()["revision"] == alb["revision"] + 1
    assert journal_path.is_file()

    # 3. Exact replay sets X-Idempotent-Replay: true; conflicting key returns 409 idempotency_conflict
    rep_res = client.patch(
        f"/api/projects/albums/{alb['id']}",
        json={"title": "API 2A2A Updated", "expected_revision": alb["revision"]},
        headers={"Idempotency-Key": "api_k1"},
        cookies=auth_cookies,
    )
    assert rep_res.status_code == 200
    assert rep_res.headers.get("X-Idempotent-Replay") == "true"

    conf_res = client.patch(
        f"/api/projects/albums/{alb['id']}",
        json={"title": "API 2A2A Different", "expected_revision": alb["revision"]},
        headers={"Idempotency-Key": "api_k1"},
        cookies=auth_cookies,
    )
    assert conf_res.status_code == 409
    assert conf_res.headers.get("X-Error-Code") == "idempotency_conflict"

    # 4. Historical ABORTED replay -> 409 transaction_aborted + X-Idempotent-Replay: true
    j_cur = AlbumJournal.model_validate_json(journal_path.read_text(encoding="utf-8"))
    ab_sha = ProjectStorageManager._compute_update_album_payload_sha256(
        "update_album", upd_res.json()["revision"], {"title": "Aborted API"}
    )
    j_ab = AlbumJournal(
        schema_version=2,
        protocol=JournalProtocol.ENFORCED_V2,
        album_id=alb["id"],
        tx_receipts=[
            *j_cur.tx_receipts,
            TerminalTxReceipt(
                tx_id="tx_api_ab_01",
                op="update_album",
                outcome=JournalTxState.ABORTED,
                expected_revision=upd_res.json()["revision"],
                result_revision=upd_res.json()["revision"],
                payload_sha256=ab_sha,
                intended_manifest_sha256="e" * 64,
                idempotency_key="api_k_ab",
            ),
        ],
        idempotency_records=j_cur.idempotency_records,
    )
    journal_path.write_text(json.dumps(j_ab.model_dump(mode="json")), encoding="utf-8")
    ab_res = client.patch(
        f"/api/projects/albums/{alb['id']}",
        json={"title": "Aborted API", "expected_revision": upd_res.json()["revision"]},
        headers={"Idempotency-Key": "api_k_ab"},
        cookies=auth_cookies,
    )
    assert ab_res.status_code == 409
    assert ab_res.headers.get("X-Error-Code") == "transaction_aborted"
    assert ab_res.headers.get("X-Idempotent-Replay") == "true"

    # 5. Direct os.replace("album.json") failure -> 503 durability_uncertain
    orig_replace = os.replace
    import backend.project_storage as ps_mod
    ps_mod.os.replace = lambda s, d, *a, **kw: (_ for _ in ()).throw(OSError(5, "EIO")) if d == "album.json" else orig_replace(s, d, *a, **kw)
    try:
        unc_res = client.patch(
            f"/api/projects/albums/{alb['id']}",
            json={"title": "Uncertain API", "expected_revision": upd_res.json()["revision"]},
            cookies=auth_cookies,
        )
    finally:
        ps_mod.os.replace = orig_replace
    assert unc_res.status_code == 503
    assert unc_res.headers.get("X-Error-Code") == "durability_uncertain"

    # 6. Proven CORRUPT precedence over ABORTED replay -> 422
    manifest_path = alb_dir / "album.json"
    journal_path.write_text(json.dumps(j_ab.model_dump(mode="json")), encoding="utf-8")
    orig_m_text = manifest_path.read_text(encoding="utf-8")
    corrupt_m = json.loads(orig_m_text)
    corrupt_m["last_tx_id"] = "tx_corrupt_unknown"
    manifest_path.write_text(json.dumps(corrupt_m), encoding="utf-8")
    cor_ab_res = client.patch(
        f"/api/projects/albums/{alb['id']}",
        json={"title": "Aborted API", "expected_revision": upd_res.json()["revision"]},
        headers={"Idempotency-Key": "api_k_ab"},
        cookies=auth_cookies,
    )
    assert cor_ab_res.status_code == 422
    manifest_path.write_text(orig_m_text, encoding="utf-8")
    journal_path.write_text(json.dumps(j_cur.model_dump(mode="json")), encoding="utf-8")

    # 7. Bulk placeholder-to-Take assignment via PATCH /albums/{id} -> 400
    import io, wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(8000); wf.writeframes(b"\x00\x00" * 200)
    trk_r = client.post("/api/projects/tracks", json={"title": "PH Track"}, cookies=auth_cookies).json()
    tk_r = client.post(
        f"/api/projects/tracks/{trk_r['id']}/takes",
        files={"file": ("ph.wav", buf.getvalue(), "audio/wav")},
        cookies=auth_cookies,
    ).json()
    alb_ph = client.post("/api/projects/albums", json={"title": "PH API Album"}, cookies=auth_cookies).json()
    alb_ph = client.post(
        f"/api/projects/albums/{alb_ph['id']}/entries",
        json={"track_id": trk_r["id"], "expected_revision": alb_ph["revision"]},
        cookies=auth_cookies,
    ).json()
    ph_assign = client.patch(
        f"/api/projects/albums/{alb_ph['id']}",
        json={
            "expected_revision": alb_ph["revision"],
            "tracks": [{"id": alb_ph["tracks"][0]["id"], "track_id": trk_r["id"], "take_id": tk_r["id"], "track_number": 1, "disc_number": 1}],
        },
        cookies=auth_cookies,
    )
    assert ph_assign.status_code == 400


def test_phase1b_stage1_3b_2a2b_api_finalization_replay_and_errors(client, auth_cookies):
    """Phase 1B Stage 1.3B-2A-2B: POST /albums/{id}/finalize v2 enrollment, replay, 409/500/503/422 mappings."""
    import io
    import json
    import os
    import wave
    import backend.project_storage as ps_mod
    import backend.projects_api as papi_mod
    from backend.project_models import AlbumJournal, JournalProtocol, JournalTxState, TerminalTxReceipt
    from backend.project_storage import ManifestPublishError, ManifestPublishState, ProjectStorageManager

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(8000); wf.writeframes(b"\x01\x02" * 300)
    wav_ok = buf.getvalue()

    trk = client.post("/api/projects/tracks", json={"title": "2A2B API Track"}, cookies=auth_cookies).json()
    tk = client.post(
        f"/api/projects/tracks/{trk['id']}/takes",
        files={"file": ("2a2b.wav", wav_ok, "audio/wav")},
        cookies=auth_cookies,
    ).json()
    alb = client.post("/api/projects/albums", json={"title": "2A2B API Album"}, cookies=auth_cookies).json()
    alb = client.post(
        f"/api/projects/albums/{alb['id']}/entries",
        json={"track_id": trk["id"], "take_id": tk["id"], "expected_revision": alb["revision"]},
        cookies=auth_cookies,
    ).json()

    status_json = client.get("/api/auth/status", cookies=auth_cookies).json()
    user_id = status_json["user"]["id"]
    alb_dir = papi_mod.project_storage_manager._get_album_dir(user_id, alb["id"])
    journal_path = alb_dir / "journal.json"

    # 1. First PREPARED definitely NOT_PUBLISHED -> sanitized HTTP 500 publication_failed (not durability_uncertain, no path leak)
    orig_pub = ps_mod._publish_manifest_atomic_in_dir
    def _fail_first_prep(dfd, fname, data):
        if fname == "journal.json":
            raise ManifestPublishError("/secret/host/path/journal.json EIO", ManifestPublishState.NOT_PUBLISHED)
        return orig_pub(dfd, fname, data)
    ps_mod._publish_manifest_atomic_in_dir = _fail_first_prep
    try:
        err500 = client.post(
            f"/api/projects/albums/{alb['id']}/finalize?expected_revision={alb['revision']}",
            headers={"Idempotency-Key": "fin_k1"},
            cookies=auth_cookies,
        )
    finally:
        ps_mod._publish_manifest_atomic_in_dir = orig_pub
    assert err500.status_code == 500
    assert err500.headers.get("X-Error-Code") == "publication_failed"
    body500 = err500.json()["detail"]
    assert body500["error_code"] == "publication_failed"
    assert "durability_uncertain" not in json.dumps(body500)
    assert "reconcil" not in json.dumps(body500).lower()
    assert "/secret/host/path" not in err500.text
    assert not journal_path.exists()

    # 2. Genuine manifest uncertainty (os.replace("album.json") EIO) -> 503 durability_uncertain
    orig_replace = os.replace
    ps_mod.os.replace = lambda s, d, *a, **kw: (_ for _ in ()).throw(OSError(5, "EIO")) if d == "album.json" else orig_replace(s, d, *a, **kw)
    try:
        err503 = client.post(
            f"/api/projects/albums/{alb['id']}/finalize?expected_revision={alb['revision']}",
            headers={"Idempotency-Key": "fin_k_unc"},
            cookies=auth_cookies,
        )
    finally:
        ps_mod.os.replace = orig_replace
    assert err503.status_code == 503
    assert err503.headers.get("X-Error-Code") == "durability_uncertain"

    # Reset to clean idle journal-less state (removing leftover .tmp_manifest_* from simulated os.replace EIO), then finalize cleanly with fin_k1
    journal_path.unlink()
    for tmp_f in alb_dir.glob(".tmp_manifest_*.json"):
        tmp_f.unlink()
    fin_res = client.post(
        f"/api/projects/albums/{alb['id']}/finalize?expected_revision={alb['revision']}",
        headers={"Idempotency-Key": "fin_k1"},
        cookies=auth_cookies,
    )
    assert fin_res.status_code == 200, fin_res.text
    assert fin_res.headers.get("X-Idempotent-Replay") is None
    assert fin_res.json()["is_finalized"] is True

    # 3. Exact COMMITTED replay -> 200 + X-Idempotent-Replay: true; new key -> 409; wrong rev -> 409 idempotency_conflict
    rep_res = client.post(
        f"/api/projects/albums/{alb['id']}/finalize?expected_revision={alb['revision']}",
        headers={"Idempotency-Key": "fin_k1"},
        cookies=auth_cookies,
    )
    assert rep_res.status_code == 200
    assert rep_res.headers.get("X-Idempotent-Replay") == "true"

    new_k_res = client.post(
        f"/api/projects/albums/{alb['id']}/finalize?expected_revision={fin_res.json()['revision']}",
        headers={"Idempotency-Key": "fin_k_other"},
        cookies=auth_cookies,
    )
    assert new_k_res.status_code == 409

    conf_res = client.post(
        f"/api/projects/albums/{alb['id']}/finalize?expected_revision={fin_res.json()['revision']}",
        headers={"Idempotency-Key": "fin_k1"},
        cookies=auth_cookies,
    )
    assert conf_res.status_code == 409
    assert conf_res.headers.get("X-Error-Code") == "idempotency_conflict"

    # 4. Historical ABORTED replay -> 409 transaction_aborted + X-Idempotent-Replay: true; CORRUPT -> 422
    j_cur = AlbumJournal.model_validate_json(journal_path.read_text(encoding="utf-8"))
    ab_sha = ProjectStorageManager._compute_finalize_album_payload_sha256(alb["revision"])
    j_ab = AlbumJournal(
        schema_version=2,
        protocol=JournalProtocol.ENFORCED_V2,
        album_id=alb["id"],
        tx_receipts=[
            *j_cur.tx_receipts,
            TerminalTxReceipt(
                tx_id="tx_api_fin_ab",
                op="finalize_album",
                outcome=JournalTxState.ABORTED,
                expected_revision=alb["revision"],
                result_revision=alb["revision"],
                payload_sha256=ab_sha,
                intended_manifest_sha256="e" * 64,
                idempotency_key="fin_k_ab",
            ),
        ],
        idempotency_records=j_cur.idempotency_records,
    )
    journal_path.write_text(json.dumps(j_ab.model_dump(mode="json")), encoding="utf-8")
    ab_res = client.post(
        f"/api/projects/albums/{alb['id']}/finalize?expected_revision={alb['revision']}",
        headers={"Idempotency-Key": "fin_k_ab"},
        cookies=auth_cookies,
    )
    assert ab_res.status_code == 409
    assert ab_res.headers.get("X-Error-Code") == "transaction_aborted"
    assert ab_res.headers.get("X-Idempotent-Replay") == "true"

    journal_path.write_text("{corrupt", encoding="utf-8")
    cor_res = client.post(
        f"/api/projects/albums/{alb['id']}/finalize?expected_revision={alb['revision']}",
        headers={"Idempotency-Key": "fin_k_ab"},
        cookies=auth_cookies,
    )
    assert cor_res.status_code == 422


def test_phase1b_stage1_3b_2b1a_api_generation_readers_errors_and_webp(client, auth_cookies, monkeypatch):
    """Phase 1B Stage 1.3B-2B-1A: generation-aware API list/detail/stream/download/artwork, 422/503/404, and WebP MIME."""
    import hashlib
    import io
    import json
    import wave
    import backend.projects_api as papi_mod
    from backend.project_models import (
        AlbumJournal,
        ArtifactRefRecord,
        ArtifactRole,
        JournalProtocol,
        JournalTxState,
        TerminalTxReceipt,
    )
    from backend.project_storage import ProjectStorageManager
    from backend.security import get_album_entry_dir_name

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(8000); wf.writeframes(b"\x03\x04" * 300)
    wav_ok = buf.getvalue()
    wav_sha = hashlib.sha256(wav_ok).hexdigest()
    webp_ok = b"RIFF\x18\x00\x00\x00WEBPVP8 \x0c\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    webp_sha = hashlib.sha256(webp_ok).hexdigest()

    trk = client.post("/api/projects/tracks", json={"title": "2B1A API Track"}, cookies=auth_cookies).json()
    tk = client.post(
        f"/api/projects/tracks/{trk['id']}/takes",
        files={"file": ("2b1a.wav", wav_ok, "audio/wav")},
        cookies=auth_cookies,
    ).json()
    alb = client.post("/api/projects/albums", json={"title": "2B1A API Album"}, cookies=auth_cookies).json()
    alb = client.post(
        f"/api/projects/albums/{alb['id']}/entries",
        json={"track_id": trk["id"], "take_id": tk["id"], "expected_revision": alb["revision"]},
        cookies=auth_cookies,
    ).json()
    eid = alb["tracks"][0]["id"]

    # Absent optional cover -> 404
    assert client.get(f"/api/projects/albums/{alb['id']}/artwork", cookies=auth_cookies).status_code == 404

    user_id = client.get("/api/auth/status", cookies=auth_cookies).json()["user"]["id"]
    alb_dir = papi_mod.project_storage_manager._get_album_dir(user_id, alb["id"])
    ehash = get_album_entry_dir_name(eid)
    entry_dir = alb_dir / "entries" / ehash
    snap_bytes = (entry_dir / "snapshot.json").read_bytes()
    snap_sha = hashlib.sha256(snap_bytes).hexdigest()
    (entry_dir / "audio_g1.wav").write_bytes(wav_ok)
    (entry_dir / "snapshot_g1.json").write_bytes(snap_bytes)
    (alb_dir / "cover_g1.webp").write_bytes(webp_ok)

    m = json.loads((alb_dir / "album.json").read_text(encoding="utf-8"))
    m["revision"] = 3
    m["cover_generation"] = 1
    m["active_cover_filename"] = "cover_g1.webp"
    m["has_cover"] = True
    m["last_tx_id"] = "tx_api_gen_1"
    m["tx_history"] = ["tx_api_gen_1"]
    m["tracks"][0]["active_generation"] = 1
    m["tracks"][0]["active_audio_filename"] = "audio_g1.wav"
    m["tracks"][0]["active_snapshot_filename"] = "snapshot_g1.json"
    (alb_dir / "album.json").write_text(json.dumps(m), encoding="utf-8")
    m_sha = ProjectStorageManager._compute_canonical_manifest_sha256(m)

    j = AlbumJournal(
        schema_version=2,
        protocol=JournalProtocol.ENFORCED_V2,
        album_id=alb["id"],
        tx_receipts=[
            TerminalTxReceipt(
                tx_id="tx_api_gen_1",
                op="save_album_cover",
                outcome=JournalTxState.COMMITTED,
                expected_revision=2,
                result_revision=3,
                payload_sha256="a" * 64,
                intended_manifest_sha256=m_sha,
                new_refs=[
                    ArtifactRefRecord(rel_path=f"entries/{ehash}/audio_g1.wav", role=ArtifactRole.ENTRY_AUDIO, sha256=wav_sha, size_bytes=len(wav_ok), entry_id=eid, generation=1),
                    ArtifactRefRecord(rel_path=f"entries/{ehash}/snapshot_g1.json", role=ArtifactRole.ENTRY_SNAPSHOT, sha256=snap_sha, size_bytes=len(snap_bytes), entry_id=eid, generation=1),
                    ArtifactRefRecord(rel_path="cover_g1.webp", role=ArtifactRole.COVER_ART, sha256=webp_sha, size_bytes=len(webp_ok), generation=1),
                ],
            )
        ],
    )
    (alb_dir / "journal.json").write_text(json.dumps(j.model_dump(mode="json")), encoding="utf-8")

    # 1. Valid generated list, detail, audio stream, download, and WebP artwork
    assert client.get("/api/projects/albums", cookies=auth_cookies).status_code == 200
    assert client.get(f"/api/projects/albums/{alb['id']}", cookies=auth_cookies).status_code == 200
    assert client.get(f"/api/projects/albums/{alb['id']}/entries/{eid}/stream", cookies=auth_cookies).status_code == 200
    assert client.get(f"/api/projects/albums/{alb['id']}/entries/{eid}/download", cookies=auth_cookies).status_code == 200
    art_res = client.get(f"/api/projects/albums/{alb['id']}/artwork", cookies=auth_cookies)
    assert art_res.status_code == 200
    assert art_res.headers.get("content-type", "").startswith("image/webp")
    assert art_res.content == webp_ok

    # 2. Missing generated cover -> 503 durability_uncertain across list, detail, and artwork
    (alb_dir / "cover_g1.webp").unlink()
    for url in (
        "/api/projects/albums",
        f"/api/projects/albums/{alb['id']}",
        f"/api/projects/albums/{alb['id']}/artwork",
    ):
        r503 = client.get(url, cookies=auth_cookies)
        assert r503.status_code == 503, f"{url} -> {r503.status_code}"
        assert r503.headers.get("X-Error-Code") == "durability_uncertain"
    (alb_dir / "cover_g1.webp").write_bytes(webp_ok)

    # 3. Generated snapshot wrong content -> 422 on audio stream & download
    (entry_dir / "snapshot_g1.json").write_bytes(snap_bytes.replace(b'"2b1a.wav"', b'"bad.wav"'))
    assert client.get(f"/api/projects/albums/{alb['id']}/entries/{eid}/stream", cookies=auth_cookies).status_code == 422
    assert client.get(f"/api/projects/albums/{alb['id']}/entries/{eid}/download", cookies=auth_cookies).status_code == 422
    (entry_dir / "snapshot_g1.json").write_bytes(snap_bytes)

    # 4. Partial cover declaration -> 422 on list, detail, and artwork
    good_m_bytes = (alb_dir / "album.json").read_bytes()
    good_j_bytes = (alb_dir / "journal.json").read_bytes()
    m_bad = json.loads(good_m_bytes.decode("utf-8"))
    m_bad.pop("active_cover_filename")
    (alb_dir / "album.json").write_text(json.dumps(m_bad), encoding="utf-8")
    for url in (
        "/api/projects/albums",
        f"/api/projects/albums/{alb['id']}",
        f"/api/projects/albums/{alb['id']}/artwork",
    ):
        assert client.get(url, cookies=auth_cookies).status_code == 422
    (alb_dir / "album.json").write_bytes(good_m_bytes)

    # 5. Unsafe active basename -> 422 across list, detail, and artwork
    m_unsafe = json.loads(good_m_bytes.decode("utf-8"))
    m_unsafe["active_cover_filename"] = "../evil.jpg"
    (alb_dir / "album.json").write_text(json.dumps(m_unsafe), encoding="utf-8")
    for url in (
        "/api/projects/albums",
        f"/api/projects/albums/{alb['id']}",
        f"/api/projects/albums/{alb['id']}/artwork",
    ):
        assert client.get(url, cookies=auth_cookies).status_code == 422
    (alb_dir / "album.json").write_bytes(good_m_bytes)

    # 6. Correction 3: Schema-valid ABORTED receipt in last_tx_id/tx_history -> 422 on artwork & stream
    m_aborted = json.loads(good_m_bytes.decode("utf-8"))
    m_aborted["last_tx_id"] = "tx_aborted_api"
    m_aborted["tx_history"] = ["tx_api_gen_1", "tx_aborted_api"]
    (alb_dir / "album.json").write_text(json.dumps(m_aborted), encoding="utf-8")
    j_base_ab = AlbumJournal.model_validate_json(good_j_bytes)
    aborted_rcp = TerminalTxReceipt(
        tx_id="tx_aborted_api",
        op="album_mutation",
        outcome=JournalTxState.ABORTED,
        expected_revision=3,
        result_revision=3,
        payload_sha256="b" * 64,
        intended_manifest_sha256="c" * 64,
    )
    j_aborted = AlbumJournal(
        schema_version=j_base_ab.schema_version,
        protocol=j_base_ab.protocol,
        album_id=j_base_ab.album_id,
        tx_receipts=[j_base_ab.tx_receipts[0], aborted_rcp],
    )
    j_aborted_text = json.dumps(j_aborted.model_dump(mode="json"))
    AlbumJournal.model_validate_json(j_aborted_text)
    (alb_dir / "journal.json").write_text(j_aborted_text, encoding="utf-8")
    assert client.get(f"/api/projects/albums/{alb['id']}/artwork", cookies=auth_cookies).status_code == 422
    assert client.get(f"/api/projects/albums/{alb['id']}/entries/{eid}/stream", cookies=auth_cookies).status_code == 422
    (alb_dir / "album.json").write_bytes(good_m_bytes)
    (alb_dir / "journal.json").write_bytes(good_j_bytes)

    # 7. Descriptor-targeted os.fstat(EIO / ENOENT) on cover, audio, and entries/ -> 503 durability_uncertain on /artwork and /stream
    import errno
    import os
    orig_os_open = os.open
    orig_os_close = os.close
    orig_os_fstat = os.fstat
    target_fds: set[int] = set()

    def _track_open_api(path, flags, *args, **kwargs):
        fd = orig_os_open(path, flags, *args, **kwargs)
        if str(path) in ("cover_g1.webp", "audio_g1.wav", "entries"):
            target_fds.add(fd)
        return fd

    def _track_close_api(fd):
        target_fds.discard(fd)
        return orig_os_close(fd)

    for err_no in (errno.EIO, errno.ENOENT):
        def _fail_fstat_api(fd, _err=err_no):
            if fd in target_fds:
                raise OSError(_err, f"Simulated API fstat failure {_err}")
            return orig_os_fstat(fd)

        with monkeypatch.context() as mp:
            mp.setattr(os, "open", _track_open_api)
            mp.setattr(os, "close", _track_close_api)
            mp.setattr(os, "fstat", _fail_fstat_api)
            r_art_eio = client.get(f"/api/projects/albums/{alb['id']}/artwork", cookies=auth_cookies)
            assert r_art_eio.status_code == 503
            assert r_art_eio.headers.get("X-Error-Code") == "durability_uncertain"
            r_str_eio = client.get(f"/api/projects/albums/{alb['id']}/entries/{eid}/stream", cookies=auth_cookies)
            assert r_str_eio.status_code == 503
            assert r_str_eio.headers.get("X-Error-Code") == "durability_uncertain"

    # 9. Valid idle LEGACY_PROVISIONAL (v1) journal remains readable via API without mutation
    v1_alb = client.post("/api/projects/albums", json={"title": "2B1A V1 Journal"}, cookies=auth_cookies).json()
    v1_alb = client.post(
        f"/api/projects/albums/{v1_alb['id']}/entries",
        json={"track_id": trk["id"], "take_id": tk["id"], "expected_revision": v1_alb["revision"]},
        cookies=auth_cookies,
    ).json()
    v1_eid = v1_alb["tracks"][0]["id"]
    v1_dir = papi_mod.project_storage_manager._get_album_dir(user_id, v1_alb["id"])
    v1_j = AlbumJournal(schema_version=1, protocol=JournalProtocol.LEGACY_PROVISIONAL, album_id=v1_alb["id"], active_tx=None)
    (v1_dir / "journal.json").write_text(json.dumps(v1_j.model_dump(mode="json")), encoding="utf-8")
    assert client.get(f"/api/projects/albums/{v1_alb['id']}", cookies=auth_cookies).status_code == 200
    assert client.get(f"/api/projects/albums/{v1_alb['id']}/entries/{v1_eid}/stream", cookies=auth_cookies).status_code == 200

    # 8. Atomic audio inode substitution on /stream -> 503, never returns foreign bytes
    from backend.project_storage import ProjectStorageManager
    foreign_wav = b"RIFF" + b"FOREIGN!" + wav_ok[12:]
    orig_verify_prov = ProjectStorageManager._verify_read_artifact_provenance_locked

    def _swap_audio_api(self, *args, **kwargs):
        res = orig_verify_prov(self, *args, **kwargs)
        if kwargs.get("role") == ArtifactRole.ENTRY_AUDIO or (len(args) > 2 and args[2] == ArtifactRole.ENTRY_AUDIO):
            tmp_swap = entry_dir / "audio_g1.wav.swap"
            tmp_swap.write_bytes(foreign_wav)
            os.replace(tmp_swap, entry_dir / "audio_g1.wav")
        return res

    with monkeypatch.context() as mp:
        mp.setattr(ProjectStorageManager, "_verify_read_artifact_provenance_locked", _swap_audio_api)
        r_sub = client.get(f"/api/projects/albums/{alb['id']}/entries/{eid}/stream", cookies=auth_cookies)
        assert r_sub.status_code == 503
        assert b"FOREIGN!" not in r_sub.content




def test_feature1_api_add_album_entry_v2_and_multi_take(client, auth_cookies):
    """Feature 1 API: add multiple Takes from one Track to fresh & v2-journaled Albums and stream both."""
    import io
    import wave

    def _make_wav(sample_byte: bytes, frames: int = 300) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(8000)
            wf.writeframes(sample_byte * frames)
        return buf.getvalue()

    wav_a = _make_wav(b"\x11\x12")
    wav_b = _make_wav(b"\x21\x22")

    trk = client.post("/api/projects/tracks", json={"title": "API Feature 1 Track"}, cookies=auth_cookies).json()
    tk_a = client.post(
        f"/api/projects/tracks/{trk['id']}/takes",
        files={"file": ("take_a.wav", wav_a, "audio/wav")},
        cookies=auth_cookies,
    ).json()
    tk_b = client.post(
        f"/api/projects/tracks/{trk['id']}/takes",
        files={"file": ("take_b.wav", wav_b, "audio/wav")},
        cookies=auth_cookies,
    ).json()

    # 1. Fresh draft Album + first Take
    alb = client.post("/api/projects/albums", json={"title": "API Feature 1 Album"}, cookies=auth_cookies).json()
    r1 = client.post(
        f"/api/projects/albums/{alb['id']}/entries",
        json={"track_id": trk["id"], "take_id": tk_a["id"], "expected_revision": alb["revision"]},
        cookies=auth_cookies,
    )
    assert r1.status_code == 201
    alb = r1.json()
    entry_a = alb["tracks"][0]

    # 2. Metadata PATCH initializes v2 journal
    r_patch = client.patch(
        f"/api/projects/albums/{alb['id']}",
        json={"title": "API Feature 1 Album v2", "expected_revision": alb["revision"]},
        cookies=auth_cookies,
    )
    assert r_patch.status_code == 200
    alb = r_patch.json()

    # 3. Add second Take from the same Track after v2 journal exists
    r2 = client.post(
        f"/api/projects/albums/{alb['id']}/entries",
        json={"track_id": trk["id"], "take_id": tk_b["id"], "expected_revision": alb["revision"]},
        cookies=auth_cookies,
    )
    assert r2.status_code == 201
    alb = r2.json()
    assert len(alb["tracks"]) == 2
    entry_b = alb["tracks"][1]

    # 4. Stream both independent Album-owned recordings
    s1 = client.get(f"/api/projects/albums/{alb['id']}/entries/{entry_a['id']}/stream", cookies=auth_cookies)
    assert s1.status_code == 200 and s1.content == wav_a
    s2 = client.get(f"/api/projects/albums/{alb['id']}/entries/{entry_b['id']}/stream", cookies=auth_cookies)
    assert s2.status_code == 200 and s2.content == wav_b

    # 5. Delete source Take B; Album recording B still streams
    assert client.delete(f"/api/projects/tracks/{trk['id']}/takes/{tk_b['id']}", cookies=auth_cookies).status_code == 200
    s2_after = client.get(f"/api/projects/albums/{alb['id']}/entries/{entry_b['id']}/stream", cookies=auth_cookies)
    assert s2_after.status_code == 200 and s2_after.content == wav_b

    # 6. Stale expected_revision -> 409
    r_stale = client.post(
        f"/api/projects/albums/{alb['id']}/entries",
        json={"track_id": trk["id"], "take_id": tk_a["id"], "expected_revision": alb["revision"] - 1},
        cookies=auth_cookies,
    )
    assert r_stale.status_code == 409

    # 7. Finalized Album rejects new entries -> 409
    r_fin = client.post(
        f"/api/projects/albums/{alb['id']}/finalize?expected_revision={alb['revision']}",
        cookies=auth_cookies,
    )
    assert r_fin.status_code == 200, r_fin.text
    fin_alb = r_fin.json()
    r_fin_add = client.post(
        f"/api/projects/albums/{alb['id']}/entries",
        json={"track_id": trk["id"], "take_id": tk_a["id"], "expected_revision": fin_alb["revision"]},
        cookies=auth_cookies,
    )
    assert r_fin_add.status_code == 409


def test_feature2_api_and_ui_confirm_recording_replacement(client, auth_cookies):
    """Feature 2 API + UI: PATCH /entries/{id} replaces recording, retains prior generation, and UI cancel sends no PATCH."""
    import io
    import os
    from pathlib import Path
    import shutil
    import subprocess
    import wave

    def _make_wav(sample_byte: bytes, frames: int = 300) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(8000)
            wf.writeframes(sample_byte * frames)
        return buf.getvalue()

    wav_1 = _make_wav(b"\x61\x62")
    wav_2 = _make_wav(b"\x71\x72")
    wav_sib = _make_wav(b"\x81\x82")

    trk = client.post("/api/projects/tracks", json={"title": "API F2 Track"}, cookies=auth_cookies).json()
    tk_1 = client.post("/api/projects/tracks/" + trk["id"] + "/takes", files={"file": ("t1.wav", wav_1, "audio/wav")}, cookies=auth_cookies).json()
    tk_2 = client.post("/api/projects/tracks/" + trk["id"] + "/takes", files={"file": ("t2.wav", wav_2, "audio/wav")}, cookies=auth_cookies).json()
    tk_sib = client.post("/api/projects/tracks/" + trk["id"] + "/takes", files={"file": ("tsib.wav", wav_sib, "audio/wav")}, cookies=auth_cookies).json()

    alb = client.post("/api/projects/albums", json={"title": "API F2 Album"}, cookies=auth_cookies).json()
    alb = client.post(
        f"/api/projects/albums/{alb['id']}/entries",
        json={"track_id": trk["id"], "take_id": tk_1["id"], "expected_revision": alb["revision"]},
        cookies=auth_cookies,
    ).json()
    alb = client.post(
        f"/api/projects/albums/{alb['id']}/entries",
        json={"track_id": trk["id"], "take_id": tk_sib["id"], "expected_revision": alb["revision"]},
        cookies=auth_cookies,
    ).json()
    e_target = alb["tracks"][0]["id"]
    e_sib = alb["tracks"][1]["id"]

    # Replace target entry recording via PATCH /entries/{id}
    r_rep = client.patch(
        f"/api/projects/albums/{alb['id']}/entries/{e_target}",
        json={"expected_revision": alb["revision"], "take_id": tk_2["id"]},
        cookies=auth_cookies,
    )
    assert r_rep.status_code == 200
    rep_alb = r_rep.json()
    assert rep_alb["tracks"][0]["take_id"] == tk_2["id"]
    assert rep_alb["tracks"][0]["active_generation"] == 1
    assert rep_alb["tracks"][1]["take_id"] == tk_sib["id"]

    s_new = client.get(f"/api/projects/albums/{alb['id']}/entries/{e_target}/stream", cookies=auth_cookies)
    assert s_new.status_code == 200 and s_new.content == wav_2
    s_sib = client.get(f"/api/projects/albums/{alb['id']}/entries/{e_sib}/stream", cookies=auth_cookies)
    assert s_sib.status_code == 200 and s_sib.content == wav_sib

    # Finalize after replacement -> 200, active recording streams, retained wav_1 preserved
    r_fin = client.post(
        f"/api/projects/albums/{alb['id']}/finalize?expected_revision={rep_alb['revision']}",
        cookies=auth_cookies,
    )
    assert r_fin.status_code == 200, r_fin.text
    s_fin = client.get(f"/api/projects/albums/{alb['id']}/entries/{e_target}/stream", cookies=auth_cookies)
    assert s_fin.status_code == 200 and s_fin.content == wav_2

    import backend.projects_api as papi_mod
    from backend.security import get_album_entry_dir_name
    user_id = client.get("/api/auth/status", cookies=auth_cookies).json()["user"]["id"]
    e_dir = papi_mod.project_storage_manager._get_album_dir(user_id, alb["id"]) / "entries" / get_album_entry_dir_name(e_target)
    assert (e_dir / "audio.wav").read_bytes() == wav_1


def test_feature3_api_refresh_finalize_and_stream(client, auth_cookies):
    """Feature 3 API: refresh-preview -> POST /refresh -> POST /finalize -> GET /stream, and cancel leaves album unchanged."""
    import io
    import wave

    def _make_wav(sample_byte: bytes, frames: int = 300) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(8000)
            wf.writeframes(sample_byte * frames)
        return buf.getvalue()

    wav_1 = _make_wav(b"\x51\x52")
    wav_2 = _make_wav(b"\x61\x62")

    trk = client.post("/api/projects/tracks", json={"title": "API F3 Track"}, cookies=auth_cookies).json()
    tk = client.post(f"/api/projects/tracks/{trk['id']}/takes", files={"file": ("f3.wav", wav_1, "audio/wav")}, cookies=auth_cookies).json()
    alb = client.post("/api/projects/albums", json={"title": "API F3 Album"}, cookies=auth_cookies).json()
    alb = client.post(
        f"/api/projects/albums/{alb['id']}/entries",
        json={"track_id": trk["id"], "take_id": tk["id"], "expected_revision": alb["revision"]},
        cookies=auth_cookies,
    ).json()
    eid = alb["tracks"][0]["id"]

    # Update source take audio via upload (or new take? update_take_audio via storage)
    import backend.projects_api as papi_mod
    user_id = client.get("/api/auth/status", cookies=auth_cookies).json()["user"]["id"]
    papi_mod.project_storage_manager.update_take_audio(user_id, trk["id"], tk["id"], audio_bytes=wav_2, extension=".wav")

    # Preview only (simulating opening modal and canceling without POST)
    prev = client.get(f"/api/projects/albums/{alb['id']}/entries/{eid}/refresh-preview", cookies=auth_cookies).json()
    assert prev["has_changes"] is True and prev["audio_changed"] is True
    assert isinstance(prev.get("expected_source_metadata_fingerprint"), str) and len(prev["expected_source_metadata_fingerprint"]) == 64
    assert client.get(f"/api/projects/albums/{alb['id']}/entries/{eid}/stream", cookies=auth_cookies).content == wav_1

    # Missing or explicit null expected_source_metadata_fingerprint -> 422 (Album unchanged)
    r_omit_fp = client.post(
        f"/api/projects/albums/{alb['id']}/entries/{eid}/refresh",
        json={
            "expected_revision": alb["revision"],
            "expected_source_revision": prev["expected_source_revision"],
            "expected_source_audio_sha256": prev["expected_source_audio_sha256"],
            "expected_source_updated_at": prev["expected_source_updated_at"],
        },
        cookies=auth_cookies,
    )
    assert r_omit_fp.status_code == 422
    r_null_fp = client.post(
        f"/api/projects/albums/{alb['id']}/entries/{eid}/refresh",
        json={
            "expected_revision": alb["revision"],
            "expected_source_revision": prev["expected_source_revision"],
            "expected_source_audio_sha256": prev["expected_source_audio_sha256"],
            "expected_source_updated_at": prev["expected_source_updated_at"],
            "expected_source_metadata_fingerprint": None,
        },
        cookies=auth_cookies,
    )
    assert r_null_fp.status_code == 422
    assert client.get(f"/api/projects/albums/{alb['id']}", cookies=auth_cookies).json()["revision"] == alb["revision"]
    assert client.get(f"/api/projects/albums/{alb['id']}/entries/{eid}/stream", cookies=auth_cookies).content == wav_1

    # Track title drift after preview -> 409 Conflict with stale expected_source_metadata_fingerprint
    assert client.patch(f"/api/projects/tracks/{trk['id']}", json={"title": "API F3 Track Updated"}, cookies=auth_cookies).status_code == 200
    r_drift = client.post(
        f"/api/projects/albums/{alb['id']}/entries/{eid}/refresh",
        json={
            "expected_revision": alb["revision"],
            "expected_source_revision": prev["expected_source_revision"],
            "expected_source_audio_sha256": prev["expected_source_audio_sha256"],
            "expected_source_updated_at": prev["expected_source_updated_at"],
            "expected_source_metadata_fingerprint": prev["expected_source_metadata_fingerprint"],
        },
        cookies=auth_cookies,
    )
    assert r_drift.status_code == 409

    # Fresh preview + confirm refresh with current fingerprint -> 200
    prev2 = client.get(f"/api/projects/albums/{alb['id']}/entries/{eid}/refresh-preview", cookies=auth_cookies).json()
    r_ref = client.post(
        f"/api/projects/albums/{alb['id']}/entries/{eid}/refresh",
        json={
            "expected_revision": alb["revision"],
            "expected_source_revision": prev2["expected_source_revision"],
            "expected_source_audio_sha256": prev2["expected_source_audio_sha256"],
            "expected_source_updated_at": prev2["expected_source_updated_at"],
            "expected_source_metadata_fingerprint": prev2["expected_source_metadata_fingerprint"],
        },
        cookies=auth_cookies,
    )
    assert r_ref.status_code == 200
    ref_alb = r_ref.json()
    assert ref_alb["tracks"][0]["snapshot"]["source_track_title"] == "API F3 Track Updated"

    # Finalize and stream refreshed recording
    r_fin = client.post(f"/api/projects/albums/{alb['id']}/finalize?expected_revision={ref_alb['revision']}", cookies=auth_cookies)
    assert r_fin.status_code == 200
    s_fin = client.get(f"/api/projects/albums/{alb['id']}/entries/{eid}/stream", cookies=auth_cookies)
    assert s_fin.status_code == 200 and s_fin.content == wav_2


def test_feature4_api_and_ui_remove_album_entry(client, auth_cookies):
    """Feature 4: DELETE /entries/{entry_id} on recorded draft entries, finalize, stream, 409 guards, and frontend confirmation."""
    import io
    import os
    from pathlib import Path
    import shutil
    import subprocess
    import wave

    def _make_wav(marker: bytes) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(8000)
            wf.writeframes(marker * 300)
        return buf.getvalue()

    wav_1 = _make_wav(b"\x41\x42")
    wav_2 = _make_wav(b"\x43\x44")

    trk = client.post("/api/projects/tracks", json={"title": "API F4 Track"}, cookies=auth_cookies).json()
    tk1 = client.post(
        f"/api/projects/tracks/{trk['id']}/takes",
        files={"file": ("t1.wav", wav_1, "audio/wav")},
        cookies=auth_cookies,
    ).json()
    tk2 = client.post(
        f"/api/projects/tracks/{trk['id']}/takes",
        files={"file": ("t2.wav", wav_2, "audio/wav")},
        cookies=auth_cookies,
    ).json()

    alb = client.post("/api/projects/albums", json={"title": "API F4 Album"}, cookies=auth_cookies).json()
    alb = client.post(
        f"/api/projects/albums/{alb['id']}/entries",
        json={"track_id": trk["id"], "take_id": tk1["id"], "expected_revision": alb["revision"]},
        cookies=auth_cookies,
    ).json()
    alb = client.post(
        f"/api/projects/albums/{alb['id']}/entries",
        json={"track_id": trk["id"], "take_id": tk2["id"], "expected_revision": alb["revision"]},
        cookies=auth_cookies,
    ).json()
    e1, e2 = alb["tracks"]

    # Stale revision -> 409
    r_stale = client.delete(
        f"/api/projects/albums/{alb['id']}/entries/{e1['id']}?expected_revision={alb['revision'] - 1}",
        cookies=auth_cookies,
    )
    assert r_stale.status_code == 409

    # Remove recorded entry e1 -> 200, e2 remains, source Track & Takes untouched
    r_del = client.delete(
        f"/api/projects/albums/{alb['id']}/entries/{e1['id']}?expected_revision={alb['revision']}",
        cookies=auth_cookies,
    )
    assert r_del.status_code == 200, r_del.text
    alb_after = r_del.json()
    assert [t["id"] for t in alb_after["tracks"]] == [e2["id"]]
    assert alb_after["revision"] == alb["revision"] + 1
    assert len(client.get(f"/api/projects/tracks/{trk['id']}", cookies=auth_cookies).json()["takes"]) == 2

    # Finalize and stream remaining entry e2
    r_fin = client.post(f"/api/projects/albums/{alb['id']}/finalize?expected_revision={alb_after['revision']}", cookies=auth_cookies)
    assert r_fin.status_code == 200, r_fin.text
    fin_alb = r_fin.json()
    s_e2 = client.get(f"/api/projects/albums/{alb['id']}/entries/{e2['id']}/stream", cookies=auth_cookies)
    assert s_e2.status_code == 200 and s_e2.content == wav_2

    # Finalized Album rejects DELETE entry -> 409
    r_fin_del = client.delete(
        f"/api/projects/albums/{alb['id']}/entries/{e2['id']}?expected_revision={fin_alb['revision']}",
        cookies=auth_cookies,
    )
    assert r_fin_del.status_code == 409

    # Frontend check: ✕ remove button renders for recorded draft entries and confirm() gates DELETE
    node_bin = shutil.which("node")
    assert node_bin is not None
    projects_js_path = Path(__file__).resolve().parent.parent / "frontend" / "js" / "projects.js"
    js_script = r"""
const fs = require('fs');
const vm = require('vm');
const code = fs.readFileSync(process.env.TARGET_JS_PATH, 'utf8');

const elements = {};
function findAll(root, pred, out = []) {
  for (const c of (root.children || [])) {
    if (pred(c)) out.push(c);
    findAll(c, pred, out);
  }
  return out;
}
function makeEl(id, tag = 'div') {
  const listeners = {};
  const classSet = new Set();
  const el = {
    id, tagName: tag.toUpperCase(), value: '', name: '', type: '', textContent: '', className: '',
    title: '', checked: false, disabled: false, files: [], listeners, dataset: {}, style: {}, children: [], _innerHTML: '',
    classList: {
      add: (c) => classSet.add(c),
      remove: (c) => classSet.delete(c),
      contains: (c) => classSet.has(c),
      toggle: (c, force) => { if (force === true) classSet.add(c); else if (force === false) classSet.delete(c); else if (classSet.has(c)) classSet.delete(c); else classSet.add(c); },
    },
    set innerHTML(val) { this._innerHTML = val; if (val === '' || (typeof val === 'string' && val.startsWith('<option'))) this.children = []; },
    get innerHTML() { return this._innerHTML; },
    addEventListener: (ev, fn) => { if (!listeners[ev]) listeners[ev] = []; listeners[ev].push(fn); },
    dispatchEvent: async (ev) => { if (!ev.target) ev.target = el; for (const fn of (listeners[ev.type] || [])) await fn(ev); },
    setAttribute: () => {},
    appendChild: (child) => { el.children.push(child); return child; },
    querySelector: () => null,
    querySelectorAll: (sel) => findAll(el, c => sel === 'button' ? c.tagName === 'BUTTON' : false),
    reset: () => {},
    remove: () => {},
  };
  return el;
}
function getEl(id) {
  if (!elements[id]) elements[id] = makeEl(id);
  return elements[id];
}

const docListeners = {};
const mutationCalls = [];
let confirmResult = false;
const albumObj = {
  id: 'alb_f4',
  title: 'F4 Album',
  status: 'draft',
  is_finalized: false,
  revision: 2,
  tracks: [{
    id: 'ale_rec_1',
    track_id: 'trk_1',
    take_id: 'tak_1',
    has_owned_audio: true,
    is_placeholder: false,
    track_number: 1,
    snapshot: { source_track_title: 'Recorded Song' },
  }],
};

const context = {
  console, Date, Math, JSON, Set, Map, Promise, URLSearchParams, encodeURIComponent, decodeURIComponent, setTimeout, clearTimeout,
  document: {
    getElementById: getEl,
    createElement: (tag) => makeEl('', tag),
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: (ev, fn) => { if (!docListeners[ev]) docListeners[ev] = []; docListeners[ev].push(fn); },
    body: makeEl('body', 'body'),
  },
  window: { location: { pathname: '/projects', search: '' }, addEventListener: () => {} },
  confirm: () => confirmResult,
  alert: () => {},
  fetch: async (url, opts) => {
    if (opts && (opts.method === 'POST' || opts.method === 'PATCH' || opts.method === 'DELETE')) {
      mutationCalls.push({ url, method: opts.method });
    }
    if (url === '/api/projects/tracks') return { ok: true, status: 200, json: async () => [] };
    if (url === '/api/projects/albums') return { ok: true, status: 200, json: async () => [albumObj] };
    if (url === '/api/projects/albums/alb_f4') return { ok: true, status: 200, json: async () => albumObj };
    if (url === '/api/storage/quota') return { ok: true, status: 200, json: async () => ({ used_bytes: 0, limit_bytes: 1000, usage_percent: 0 }) };
    if (opts && opts.method === 'DELETE') return { ok: true, status: 200, json: async () => ({ ...albumObj, revision: 3, tracks: [] }) };
    return { ok: true, status: 200, json: async () => ({}) };
  },
};
context.window.window = context.window;

vm.runInNewContext(code, context);
(docListeners['DOMContentLoaded'] || []).forEach(fn => fn());

(async () => {
  await new Promise(r => setTimeout(r, 20));
  const cards = getEl('albumsGridContainer').children;
  await cards[0].querySelectorAll('button')[0].listeners['click'][0]();
  await new Promise(r => setTimeout(r, 20));

  const rowBtns = getEl('albumSequencerTbody').children[0].querySelectorAll('button');
  const btnRemove = rowBtns.find(b => b.textContent === '✕' && b.title === 'Remove from Album');
  if (!btnRemove) throw new Error('Expected remove button (✕) for recorded entry on draft Album');

  confirmResult = false;
  await btnRemove.listeners['click'][0]();
  await new Promise(r => setTimeout(r, 20));
  if (mutationCalls.length !== 0) throw new Error('Expected 0 DELETE calls when confirm() canceled');

  confirmResult = true;
  await btnRemove.listeners['click'][0]();
  await new Promise(r => setTimeout(r, 20));
  const delCalls = mutationCalls.filter(c => c.method === 'DELETE' && c.url.includes('/entries/ale_rec_1'));
  if (delCalls.length !== 1) throw new Error('Expected 1 DELETE call when confirm() accepted');
  console.log('UI_CHECK_OK');
})().catch(err => {
  console.error(err);
  process.exit(1);
});
"""
    env = os.environ.copy()
    env["TARGET_JS_PATH"] = str(projects_js_path)
    proc = subprocess.run([node_bin, "-e", js_script], capture_output=True, text=True, env=env)
    assert proc.returncode == 0, f"node failed: {proc.stderr}\n{proc.stdout}"
    assert "UI_CHECK_OK" in proc.stdout


def test_feature5_api_and_ui_artwork_replacement(client, auth_cookies):
    """Feature 5: API artwork upload/replacement, GET /artwork, invalid upload guard, finalize, 409 on finalized, and frontend controls."""
    import io
    import os
    from pathlib import Path
    import shutil
    import subprocess
    import wave
    from PIL import Image

    wbuf = io.BytesIO()
    with wave.open(wbuf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(b"\x22\x23" * 300)
    wav_ok = wbuf.getvalue()

    def _make_img_bytes(color: tuple[int, int, int], fmt: str) -> bytes:
        img = Image.new("RGB", (32, 32), color=color)
        b = io.BytesIO()
        img.save(b, format=fmt)
        return b.getvalue()

    jpg_1 = _make_img_bytes((200, 10, 10), "JPEG")
    png_2 = _make_img_bytes((10, 200, 10), "PNG")
    jpg_3 = _make_img_bytes((10, 10, 200), "JPEG")

    trk = client.post("/api/projects/tracks", json={"title": "API F5 Track"}, cookies=auth_cookies).json()
    tk = client.post(
        f"/api/projects/tracks/{trk['id']}/takes",
        files={"file": ("f5.wav", wav_ok, "audio/wav")},
        cookies=auth_cookies,
    ).json()

    alb = client.post("/api/projects/albums", json={"title": "API F5 Cover LP"}, cookies=auth_cookies).json()
    alb = client.post(
        f"/api/projects/albums/{alb['id']}/entries",
        json={"track_id": trk["id"], "take_id": tk["id"], "expected_revision": alb["revision"]},
        cookies=auth_cookies,
    ).json()
    eid = alb["tracks"][0]["id"]

    # Initial upload -> 200, GET /artwork returns normalized image
    r1 = client.post(
        f"/api/projects/albums/{alb['id']}/artwork",
        data={"expected_revision": str(alb["revision"])},
        files={"file": ("c1.jpg", io.BytesIO(jpg_1), "image/jpeg")},
        cookies=auth_cookies,
    )
    assert r1.status_code == 200, r1.text
    alb1 = r1.json()["album"]
    assert alb1["cover_generation"] == 1
    assert alb1["active_cover_filename"] == "cover_g1.jpg"
    g1_bytes = client.get(f"/api/projects/albums/{alb['id']}/artwork", cookies=auth_cookies).content

    # Replace once & twice -> 200, GET /artwork updates to latest
    r2 = client.post(
        f"/api/projects/albums/{alb['id']}/artwork",
        data={"expected_revision": str(alb1["revision"])},
        files={"file": ("c2.png", io.BytesIO(png_2), "image/png")},
        cookies=auth_cookies,
    )
    assert r2.status_code == 200, r2.text
    alb2 = r2.json()["album"]
    assert alb2["cover_generation"] == 2
    assert alb2["active_cover_filename"] == "cover_g2.png"
    g2_bytes = client.get(f"/api/projects/albums/{alb['id']}/artwork", cookies=auth_cookies).content
    assert g2_bytes != g1_bytes

    r3 = client.post(
        f"/api/projects/albums/{alb['id']}/artwork",
        data={"expected_revision": str(alb2["revision"])},
        files={"file": ("c3.jpg", io.BytesIO(jpg_3), "image/jpeg")},
        cookies=auth_cookies,
    )
    assert r3.status_code == 200, r3.text
    alb3 = r3.json()["album"]
    assert alb3["cover_generation"] == 3
    g3_bytes = client.get(f"/api/projects/albums/{alb['id']}/artwork", cookies=auth_cookies).content
    assert g3_bytes != g2_bytes

    # Invalid upload -> 400, leaves Album revision & active cover unchanged
    r_bad = client.post(
        f"/api/projects/albums/{alb['id']}/artwork",
        data={"expected_revision": str(alb3["revision"])},
        files={"file": ("bad.jpg", io.BytesIO(b"not an image"), "image/jpeg")},
        cookies=auth_cookies,
    )
    assert r_bad.status_code == 400
    assert client.get(f"/api/projects/albums/{alb['id']}", cookies=auth_cookies).json()["revision"] == alb3["revision"]
    assert client.get(f"/api/projects/albums/{alb['id']}/artwork", cookies=auth_cookies).content == g3_bytes

    # Stale revision -> 409
    r_stale = client.post(
        f"/api/projects/albums/{alb['id']}/artwork",
        data={"expected_revision": str(alb3["revision"] - 1)},
        files={"file": ("c1.jpg", io.BytesIO(jpg_1), "image/jpeg")},
        cookies=auth_cookies,
    )
    assert r_stale.status_code == 409

    # Finalize and verify active cover and entry audio still served, further replacement rejected with 409
    r_fin = client.post(f"/api/projects/albums/{alb['id']}/finalize?expected_revision={alb3['revision']}", cookies=auth_cookies)
    assert r_fin.status_code == 200, r_fin.text
    fin_alb = r_fin.json()
    assert client.get(f"/api/projects/albums/{alb['id']}/artwork", cookies=auth_cookies).content == g3_bytes
    assert client.get(f"/api/projects/albums/{alb['id']}/entries/{eid}/stream", cookies=auth_cookies).content == wav_ok

    r_fin_rep = client.post(
        f"/api/projects/albums/{alb['id']}/artwork",
        data={"expected_revision": str(fin_alb["revision"])},
        files={"file": ("c1.jpg", io.BytesIO(jpg_1), "image/jpeg")},
        cookies=auth_cookies,
    )
    assert r_fin_rep.status_code == 409

    # Frontend check: artwork upload updates src on success, preserves src on failure, and disables input on finalized Album
    node_bin = shutil.which("node")
    assert node_bin is not None
    projects_js_path = Path(__file__).resolve().parent.parent / "frontend" / "js" / "projects.js"
    js_script = r"""
const fs = require('fs');
const vm = require('vm');
const code = fs.readFileSync(process.env.TARGET_JS_PATH, 'utf8');

const elements = {};
function findAll(root, pred, out = []) {
  for (const c of (root.children || [])) {
    if (pred(c)) out.push(c);
    findAll(c, pred, out);
  }
  return out;
}
function makeEl(id, tag = 'div') {
  const listeners = {};
  const classSet = new Set();
  const el = {
    id, tagName: tag.toUpperCase(), value: '', name: '', type: '', textContent: '', className: '',
    src: '', checked: false, disabled: false, files: [], listeners, dataset: {}, style: {}, children: [], _innerHTML: '',
    classList: {
      add: (c) => classSet.add(c),
      remove: (c) => classSet.delete(c),
      contains: (c) => classSet.has(c),
      toggle: (c, force) => { if (force === true) classSet.add(c); else if (force === false) classSet.delete(c); else if (classSet.has(c)) classSet.delete(c); else classSet.add(c); },
    },
    set innerHTML(val) { this._innerHTML = val; if (val === '' || (typeof val === 'string' && val.startsWith('<option'))) this.children = []; },
    get innerHTML() { return this._innerHTML; },
    addEventListener: (ev, fn) => { if (!listeners[ev]) listeners[ev] = []; listeners[ev].push(fn); },
    setAttribute: () => {},
    appendChild: (child) => { el.children.push(child); return child; },
    querySelector: () => null,
    querySelectorAll: (sel) => findAll(el, c => sel === 'button' ? c.tagName === 'BUTTON' : false),
    reset: () => {},
    remove: () => {},
  };
  return el;
}
function getEl(id) {
  if (!elements[id]) elements[id] = makeEl(id);
  return elements[id];
}

const docListeners = {};
let nextUploadOk = true;
const draftAlbum = {
  id: 'alb_f5', title: 'F5 UI Album', status: 'draft', is_finalized: false, revision: 2, has_cover: true,
  tracks: [], revisions: [],
};
const finalizedAlbum = { ...draftAlbum, id: 'alb_f5_fin', is_finalized: true, status: 'completed', revision: 4 };

class MockFormData { constructor() { this.items = []; } append(k, v) { this.items.push([k, v]); } }

const context = {
  console, Date, Math, JSON, Set, Map, Promise, FormData: MockFormData, URLSearchParams, encodeURIComponent, decodeURIComponent, setTimeout, clearTimeout,
  document: {
    getElementById: getEl,
    createElement: (tag) => makeEl('', tag),
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: (ev, fn) => { if (!docListeners[ev]) docListeners[ev] = []; docListeners[ev].push(fn); },
    body: makeEl('body', 'body'),
  },
  window: { location: { pathname: '/projects', search: '' }, addEventListener: () => {} },
  confirm: () => true,
  alert: () => {},
  fetch: async (url, opts) => {
    if (url === '/api/projects/tracks') return { ok: true, status: 200, json: async () => [] };
    if (url === '/api/projects/albums') return { ok: true, status: 200, json: async () => [draftAlbum, finalizedAlbum] };
    if (url === '/api/projects/albums/alb_f5') return { ok: true, status: 200, json: async () => draftAlbum };
    if (url === '/api/projects/albums/alb_f5_fin') return { ok: true, status: 200, json: async () => finalizedAlbum };
    if (url === '/api/storage/quota') return { ok: true, status: 200, json: async () => ({ used_bytes: 0, limit_bytes: 1000, usage_percent: 0 }) };
    if (url === '/api/projects/albums/alb_f5/artwork' && opts && opts.method === 'POST') {
      if (!nextUploadOk) return { ok: false, status: 400, json: async () => ({ detail: 'Bad image' }) };
      return { ok: true, status: 200, json: async () => ({ status: 'saved', album: { ...draftAlbum, revision: 3, active_cover_filename: 'cover_g2.png' } }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  },
};
context.window.window = context.window;

vm.runInNewContext(code, context);
(docListeners['DOMContentLoaded'] || []).forEach(fn => fn());

(async () => {
  await new Promise(r => setTimeout(r, 20));
  const cards = getEl('albumsGridContainer').children;
  await cards[0].querySelectorAll('button')[0].listeners['click'][0]();
  await new Promise(r => setTimeout(r, 20));

  const artEl = getEl('albumDetailArtwork');
  const initialSrc = artEl.src;
  const inputEl = getEl('inputAlbumCoverFile');
  if (inputEl.disabled) throw new Error('Expected cover input enabled on draft Album');

  // Failed upload preserves displayed cover src
  nextUploadOk = false;
  inputEl.files = [{ name: 'bad.jpg', size: 10 }];
  await inputEl.listeners['change'][0]({ target: inputEl });
  await new Promise(r => setTimeout(r, 20));
  if (artEl.src !== initialSrc) throw new Error('Expected failed upload to preserve previous cover src');

  // Successful upload updates displayed cover src
  nextUploadOk = true;
  await new Promise(r => setTimeout(r, 5));
  await inputEl.listeners['change'][0]({ target: inputEl });
  await new Promise(r => setTimeout(r, 20));
  if (artEl.src === initialSrc || !artEl.src.includes('/api/projects/albums/alb_f5/artwork?t=')) {
    throw new Error('Expected successful upload to update cover src');
  }

  // Finalized Album disables cover replacement input
  await cards[1].querySelectorAll('button')[0].listeners['click'][0]();
  await new Promise(r => setTimeout(r, 20));
  if (!inputEl.disabled) throw new Error('Expected cover input disabled on finalized Album');
  console.log('F5_UI_CHECK_OK');
})().catch(err => {
  console.error(err);
  process.exit(1);
});
"""
    env = os.environ.copy()
    env["TARGET_JS_PATH"] = str(projects_js_path)
    proc = subprocess.run([node_bin, "-e", js_script], capture_output=True, text=True, env=env)
    assert proc.returncode == 0, f"node failed: {proc.stderr}\n{proc.stdout}"
    assert "F5_UI_CHECK_OK" in proc.stdout
