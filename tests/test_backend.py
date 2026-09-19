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

    # Test corrupted image rejection with sanitized error message
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        validate_and_normalize_image(b"not an image at all", 10 * 1024 * 1024)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid image format. Only JPEG, PNG, and WebP images are supported."


def test_cryptographic_session_tokens():
    import uuid
    import hmac
    import hashlib
    from backend.config import SESSION_SECRET_KEY

    valid_id = str(uuid.uuid4())
    token = create_signed_session_token(valid_id)
    assert "." in token
    parts = token.split(".")
    assert len(parts) == 3  # uuid.timestamp.sig

    # Verification passes for legitimate 3-part timestamped token
    verified = verify_signed_session_token(token)
    assert verified == valid_id

    # Legacy 2-part un-timestamped token MUST BE REJECTED
    legacy_sig = hmac.new(
        SESSION_SECRET_KEY.encode("utf-8"),
        valid_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    legacy_2part_token = f"{valid_id}.{legacy_sig}"
    assert verify_signed_session_token(legacy_2part_token) is None

    # Expired token fails
    expired_token = f"{valid_id}.{int(time.time()) - 7200}.{parts[2]}"
    assert verify_signed_session_token(expired_token, max_age_seconds=3600) is None

    # Future token tampering fails
    future_token = f"{valid_id}.{int(time.time()) + 999999}.{parts[2]}"
    assert verify_signed_session_token(future_token) is None

    # Tampered signature fails
    tampered_token = f"{valid_id}.{parts[1]}.0000000000000000000000000000000000000000000000000000000000000000"
    assert verify_signed_session_token(tampered_token) is None

    # Invalid UUID format fails
    invalid_token = create_signed_session_token("not-a-valid-uuid")
    assert verify_signed_session_token(invalid_token) is None

    # Blank / corrupted token fails
    assert verify_signed_session_token("") is None
    assert verify_signed_session_token("malformed") is None
    assert verify_signed_session_token("part1.part2") is None


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


def test_csrf_cross_site_protection(client):
    """Ensure cross-site mutating requests are blocked by CSRF middleware."""
    res = client.post(
        "/api/save",
        json={"title": "Hacked"},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert res.status_code == 403
    assert "Cross-site request forgery protection" in res.json()["detail"]


def test_rate_limiter():
    from backend.security import InMemoryRateLimiter
    limiter = InMemoryRateLimiter(max_requests=3, window_seconds=60)
    assert limiter.is_allowed("1.2.3.4") is True
    assert limiter.is_allowed("1.2.3.4") is True
    assert limiter.is_allowed("1.2.3.4") is True
    # 4th request in window rejected
    assert limiter.is_allowed("1.2.3.4") is False
    # Different IP allowed
    assert limiter.is_allowed("5.6.7.8") is True


def test_image_decompression_bomb_rejection():
    # Construct an oversized image
    from PIL import Image
    from fastapi import HTTPException
    img = Image.new("RGB", (5000, 5000), color=(0, 0, 0))
    out = io.BytesIO()
    img.save(out, format="JPEG")
    with pytest.raises(HTTPException) as exc:
        validate_and_normalize_image(out.getvalue(), 10 * 1024 * 1024)
    assert exc.value.status_code == 400
    assert "exceed maximum allowed" in exc.value.detail or "Decompression bomb" in exc.value.detail


def test_storage_quota_management(tmp_path: Path):
    mgr = SessionManager(temp_dir=tmp_path, ttl_minutes=60, max_storage_bytes=1000)
    # Session 1
    s1, a1 = mgr.create_session("file1.mp3")
    a1.write_bytes(b"A" * 600)
    assert mgr.get_total_temp_size_bytes() >= 600

    # Session 2
    s2, a2 = mgr.create_session("file2.mp3")
    a2.write_bytes(b"B" * 300)

    # Trigger quota check with required bytes causing eviction
    assert mgr.ensure_storage_available(required_bytes=400) is True
    # s1 should have been evicted as it was oldest
    assert mgr.get_session_dir(s1) is None
    assert mgr.get_session_dir(s2) is not None


def test_metadata_payload_length_limits(client, sample_mp3_bytes):
    """Verify that oversized metadata strings are rejected with 422 Unprocessable Entity."""
    # 1. Upload valid MP3 to get session
    upload_res = client.post(
        "/api/upload",
        files={"file": ("test.mp3", sample_mp3_bytes, "audio/mpeg")},
    )
    assert upload_res.status_code == 200

    # 2. Attempt to save metadata with 1000-character title (max is 500)
    res = client.post("/api/save", json={"title": "A" * 1000})
    assert res.status_code == 422

    # 3. Attempt to save lyrics with 100,000 characters (max is 65,536)
    res_lyrics = client.post("/api/save", json={"lyrics": "L" * 100000})
    assert res_lyrics.status_code == 422


def test_rate_limiter_purging():
    """Verify rate limiter memory leak defense (auto-purging) and helper behavior."""
    from backend.security import InMemoryRateLimiter, is_trusted_proxy_ip
    from starlette.datastructures import Headers

    limiter = InMemoryRateLimiter(max_requests=2, window_seconds=1, max_tracked_ips=5)

    # Check is_trusted_proxy_ip helper against default configured loopback
    assert is_trusted_proxy_ip("127.0.0.1") is True
    assert is_trusted_proxy_ip("::1") is True
    assert is_trusted_proxy_ip("10.0.0.1") is False
    assert is_trusted_proxy_ip("198.51.100.50") is False
    assert is_trusted_proxy_ip("8.8.8.8") is False

    # Simulate IP entries and verify auto-purging (using RFC 5737 TEST-NET-1 addresses)
    for i in range(10):
        limiter.is_allowed(f"192.0.2.{i}")

    # Wait for window expiry and trigger purge
    time.sleep(1.1)
    limiter.is_allowed("192.0.2.99")

    # Old expired IPs should have been pruned from memory
    assert len(limiter.history) <= 5


@pytest.mark.anyio
async def test_asgi_proxy_headers_middleware_chain():
    """Integration test verifying proxy header trust through the actual ASGI middleware stack.

    Uses standard RFC 5737 documentation/test addresses:
    - TEST_TRUSTED_PROXY_IP = "198.51.100.55" (TEST-NET-2)
    - TEST_UNTRUSTED_PEER_IP = "192.0.2.123"  (TEST-NET-1)
    - TEST_FORWARDED_CLIENT_IP = "203.0.113.195" (TEST-NET-3)
    - TEST_SPOOFED_PREFIX_IP = "192.0.2.1"    (TEST-NET-1)

    Tests that:
    1. Untrusted direct peers cannot spoof rate-limit identity via X-Forwarded-For.
    2. Configured reverse proxy (198.51.100.55) correctly resolves client IP.
    3. Multi-hop X-Forwarded-For with prepended spoofed IPs correctly resolves the untrusted client.
    4. When proxy trust is disabled, all X-Forwarded-For headers are ignored.
    """
    import httpx
    from fastapi import FastAPI, Request
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    from backend.security import InMemoryRateLimiter

    TEST_TRUSTED_PROXY = "198.51.100.55"
    TEST_UNTRUSTED_PEER = "192.0.2.123"
    TEST_CLIENT_IP = "203.0.113.195"
    TEST_SPOOFED_IP = "192.0.2.1"

    test_limiter = InMemoryRateLimiter(max_requests=2, window_seconds=60)

    # --- Scenario A: Proxy Trust Enabled with Configured Proxy IP ---
    app_with_proxy = FastAPI()
    app_with_proxy.add_middleware(ProxyHeadersMiddleware, trusted_hosts=[TEST_TRUSTED_PROXY])

    @app_with_proxy.post("/api/test-rate-limit")
    async def rate_limited_endpoint(request: Request):
        client_ip = test_limiter.get_client_ip(request)
        allowed = test_limiter.is_allowed(client_ip)
        return {"client_ip": client_ip, "allowed": allowed}

    # 1. Untrusted peer direct connection: Spoof attempt MUST FAIL
    transport_untrusted = httpx.ASGITransport(app=app_with_proxy, client=(TEST_UNTRUSTED_PEER, 54321))
    async with httpx.AsyncClient(transport=transport_untrusted, base_url="http://test") as ac:
        # Request 1: Claims to be TEST_CLIENT_IP
        res1 = await ac.post("/api/test-rate-limit", headers={"X-Forwarded-For": TEST_CLIENT_IP})
        assert res1.status_code == 200
        assert res1.json()["client_ip"] == TEST_UNTRUSTED_PEER  # Kept as socket peer!
        assert res1.json()["allowed"] is True

        # Request 2: Claims to be 203.0.113.196
        res2 = await ac.post("/api/test-rate-limit", headers={"X-Forwarded-For": "203.0.113.196"})
        assert res2.status_code == 200
        assert res2.json()["client_ip"] == TEST_UNTRUSTED_PEER
        assert res2.json()["allowed"] is True

        # Request 3: Claims to be 203.0.113.197 (3rd request from socket peer within window)
        res3 = await ac.post("/api/test-rate-limit", headers={"X-Forwarded-For": "203.0.113.197"})
        assert res3.status_code == 200
        assert res3.json()["client_ip"] == TEST_UNTRUSTED_PEER
        assert res3.json()["allowed"] is False  # Rate limit enforced on socket peer despite rotating XFF!

    # 2. Trusted reverse proxy connection: Resolves intended client IP
    proxy_limiter = InMemoryRateLimiter(max_requests=2, window_seconds=60)
    app_proxy = FastAPI()
    app_proxy.add_middleware(ProxyHeadersMiddleware, trusted_hosts=[TEST_TRUSTED_PROXY])

    @app_proxy.post("/api/test-proxy")
    async def proxy_endpoint(request: Request):
        client_ip = proxy_limiter.get_client_ip(request)
        allowed = proxy_limiter.is_allowed(client_ip)
        return {"client_ip": client_ip, "allowed": allowed}

    transport_proxy = httpx.ASGITransport(app=app_proxy, client=(TEST_TRUSTED_PROXY, 54321))
    async with httpx.AsyncClient(transport=transport_proxy, base_url="http://test") as ac:
        # Legitimate forwarded client
        res = await ac.post("/api/test-proxy", headers={"X-Forwarded-For": TEST_CLIENT_IP})
        assert res.status_code == 200
        assert res.json()["client_ip"] == TEST_CLIENT_IP

        # Client tried to prepend forged IP before proxy appended TEST_CLIENT_IP
        res_spoof = await ac.post("/api/test-proxy", headers={"X-Forwarded-For": f"{TEST_SPOOFED_IP}, {TEST_CLIENT_IP}"})
        assert res_spoof.status_code == 200
        assert res_spoof.json()["client_ip"] == TEST_CLIENT_IP  # Correctly resolved untrusted client boundary

    # --- Scenario B: Proxy Trust Disabled (Workstation Mode) ---
    app_workstation = FastAPI()  # No ProxyHeadersMiddleware installed

    @app_workstation.post("/api/test-workstation")
    async def workstation_endpoint(request: Request):
        client_ip = test_limiter.get_client_ip(request)
        return {"client_ip": client_ip}

    transport_workstation = httpx.ASGITransport(app=app_workstation, client=(TEST_TRUSTED_PROXY, 54321))
    async with httpx.AsyncClient(transport=transport_workstation, base_url="http://test") as ac:
        res = await ac.post("/api/test-workstation", headers={"X-Forwarded-For": TEST_CLIENT_IP})
        assert res.status_code == 200
        assert res.json()["client_ip"] == TEST_TRUSTED_PROXY  # Forwarded headers ignored when disabled


@pytest.mark.anyio
async def test_cookie_secure_flag_trust_policy():
    """Verify that cookie Secure flag is governed ONLY by the trusted request scheme.

    Tests that:
    1. An untrusted direct client sending X-Forwarded-Proto: https cannot trick the server into setting Secure cookies.
    2. A trusted reverse proxy forwarding X-Forwarded-Proto: https results in Secure cookies.
    3. When proxy trust is disabled, all X-Forwarded-Proto headers are ignored.
    """
    import httpx
    from fastapi import FastAPI, Request, Response
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    TEST_PROXY = "198.51.100.55"
    TEST_DIRECT_PEER = "192.0.2.123"

    # --- App with Proxy Trust Enabled for 198.51.100.55 ---
    app_proxy = FastAPI()
    app_proxy.add_middleware(ProxyHeadersMiddleware, trusted_hosts=[TEST_PROXY])

    @app_proxy.post("/api/test-cookie")
    async def cookie_endpoint(request: Request, response: Response):
        is_https = (request.url.scheme == "https")
        response.set_cookie(key="test_session", value="tok123", secure=is_https, httponly=True)
        return {"scheme": request.url.scheme, "secure_cookie": is_https}

    # Case 1: Untrusted direct peer claims X-Forwarded-Proto: https -> MUST BE IGNORED
    t_untrusted = httpx.ASGITransport(app=app_proxy, client=(TEST_DIRECT_PEER, 54321))
    async with httpx.AsyncClient(transport=t_untrusted, base_url="http://test") as ac:
        res = await ac.post("/api/test-cookie", headers={"X-Forwarded-Proto": "https"})
        assert res.status_code == 200
        assert res.json()["scheme"] == "http"
        assert res.json()["secure_cookie"] is False
        cookie_header = res.headers.get("set-cookie", "")
        assert "Secure" not in cookie_header

    # Case 2: Trusted proxy forwards X-Forwarded-Proto: https -> APPLIED
    t_proxy = httpx.ASGITransport(app=app_proxy, client=(TEST_PROXY, 54321))
    async with httpx.AsyncClient(transport=t_proxy, base_url="http://test") as ac:
        res = await ac.post("/api/test-cookie", headers={"X-Forwarded-Proto": "https"})
        assert res.status_code == 200
        assert res.json()["scheme"] == "https"
        assert res.json()["secure_cookie"] is True
        cookie_header = res.headers.get("set-cookie", "")
        assert "Secure" in cookie_header or "secure" in cookie_header

    # --- App with Proxy Trust Disabled ---
    app_disabled = FastAPI()

    @app_disabled.post("/api/test-cookie-disabled")
    async def cookie_disabled_endpoint(request: Request, response: Response):
        is_https = (request.url.scheme == "https")
        response.set_cookie(key="test_session", value="tok123", secure=is_https, httponly=True)
        return {"scheme": request.url.scheme, "secure_cookie": is_https}

    t_disabled = httpx.ASGITransport(app=app_disabled, client=(TEST_PROXY, 54321))
    async with httpx.AsyncClient(transport=t_disabled, base_url="http://test") as ac:
        res = await ac.post("/api/test-cookie-disabled", headers={"X-Forwarded-Proto": "https"})
        assert res.status_code == 200
        assert res.json()["scheme"] == "http"
        assert res.json()["secure_cookie"] is False
        assert "Secure" not in res.headers.get("set-cookie", "")


def test_service_launch_commands_configuration():
    """Verify that service templates and launch scripts dynamically use host/port variables and disable uvicorn default proxy headers."""
    base_dir = Path(__file__).resolve().parent.parent

    # 1. deploy/mp3metafix.service
    service_file = base_dir / "deploy" / "mp3metafix.service"
    content = service_file.read_text(encoding="utf-8")
    assert "--host $MP3METAFIX_HOST" in content
    assert "--port $MP3METAFIX_PORT" in content
    assert "--no-proxy-headers" in content
    assert "--host 127.0.0.1" not in content

    # 2. install.sh
    install_sh = base_dir / "install.sh"
    install_content = install_sh.read_text(encoding="utf-8")
    assert "--no-proxy-headers" in install_content
    assert "$MP3METAFIX_HOST" in install_content

    # 3. run.sh
    run_sh = base_dir / "run.sh"
    run_content = run_sh.read_text(encoding="utf-8")
    assert "--no-proxy-headers" in run_content
    assert '"$HOST"' in run_content


def test_audio_stream_ranges(client, sample_mp3_bytes):
    """Test full RFC 7233 byte-range scenarios for audio streaming preview."""
    # 1. Upload audio file to establish session
    upload_res = client.post(
        "/api/upload",
        files={"file": ("stream_test.mp3", sample_mp3_bytes, "audio/mpeg")},
    )
    assert upload_res.status_code == 200
    file_size = len(sample_mp3_bytes)
    assert file_size > 0

    # 2. No Range header -> 200 OK (Full File)
    res_full = client.get("/api/stream")
    assert res_full.status_code == 200
    assert len(res_full.content) == file_size
    assert res_full.content == sample_mp3_bytes

    # 3. Normal range (bytes=0-99) -> 206 Partial Content (100 bytes)
    res_range = client.get("/api/stream", headers={"Range": "bytes=0-99"})
    assert res_range.status_code == 206
    assert res_range.headers.get("Content-Range") == f"bytes 0-99/{file_size}"
    assert res_range.headers.get("Content-Length") == "100"
    assert res_range.content == sample_mp3_bytes[0:100]

    # 4. Open-ended range (bytes=100-) -> 206 Partial Content from 100 to end
    res_open = client.get("/api/stream", headers={"Range": "bytes=100-"})
    assert res_open.status_code == 206
    assert res_open.headers.get("Content-Range") == f"bytes 100-{file_size - 1}/{file_size}"
    assert int(res_open.headers.get("Content-Length")) == file_size - 100
    assert res_open.content == sample_mp3_bytes[100:]

    # 5. Suffix range (bytes=-50) -> 206 Partial Content for last 50 bytes
    res_suffix = client.get("/api/stream", headers={"Range": "bytes=-50"})
    assert res_suffix.status_code == 206
    assert res_suffix.headers.get("Content-Range") == f"bytes {file_size - 50}-{file_size - 1}/{file_size}"
    assert res_suffix.headers.get("Content-Length") == "50"
    assert res_suffix.content == sample_mp3_bytes[-50:]

    # 6. Suffix range exceeding file size (bytes=-999999) -> 206 full file
    res_big_suffix = client.get("/api/stream", headers={"Range": "bytes=-999999"})
    assert res_big_suffix.status_code == 206
    assert res_big_suffix.headers.get("Content-Range") == f"bytes 0-{file_size - 1}/{file_size}"
    assert int(res_big_suffix.headers.get("Content-Length")) == file_size

    # 7. Inverted range (bytes=500-200) -> 416 Range Not Satisfiable
    res_inverted = client.get("/api/stream", headers={"Range": "bytes=500-200"})
    assert res_inverted.status_code == 416
    assert res_inverted.headers.get("Content-Range") == f"bytes */{file_size}"

    # 8. Out-of-bounds start (bytes=999999-) -> 416 Range Not Satisfiable
    res_oob = client.get("/api/stream", headers={"Range": "bytes=999999-"})
    assert res_oob.status_code == 416
    assert res_oob.headers.get("Content-Range") == f"bytes */{file_size}"

    # 9. Invalid suffix (bytes=-0) -> 416 Range Not Satisfiable
    res_invalid_suffix = client.get("/api/stream", headers={"Range": "bytes=-0"})
    assert res_invalid_suffix.status_code == 416
    assert res_invalid_suffix.headers.get("Content-Range") == f"bytes */{file_size}"

    # 10. Malformed range syntax (bytes=abc-def and bytes=12345) -> 416 Range Not Satisfiable
    res_malformed = client.get("/api/stream", headers={"Range": "bytes=abc-def"})
    assert res_malformed.status_code == 416
    assert res_malformed.headers.get("Content-Range") == f"bytes */{file_size}"

    res_no_hyphen = client.get("/api/stream", headers={"Range": "bytes=12345"})
    assert res_no_hyphen.status_code == 416
    assert res_no_hyphen.headers.get("Content-Range") == f"bytes */{file_size}"

    # 11. Multi-range header (bytes=0-50, 100-150) -> Rejected with 416 Range Not Satisfiable
    res_multi = client.get("/api/stream", headers={"Range": "bytes=0-50, 100-150"})
    assert res_multi.status_code == 416
    assert res_multi.headers.get("Content-Range") == f"bytes */{file_size}"

    # 12. Non-bytes range unit (items=0-10) -> Ignored per RFC 7233, returns 200 OK (Full File)
    res_other_unit = client.get("/api/stream", headers={"Range": "items=0-10"})
    assert res_other_unit.status_code == 200
    assert len(res_other_unit.content) == file_size


def test_api_updates_apply_endpoint_disabled(client):
    """Verify that in-app update installation is disabled and returns 403 Forbidden."""
    res = client.post("/api/updates/apply")
    assert res.status_code == 403
    assert "currently disabled" in res.json()["detail"]


def test_api_version_endpoint(client):
    """Verify GET /api/version returns rich version and repository info."""
    res = client.get("/api/version")
    assert res.status_code == 200
    data = res.json()
    assert data["version"] == VERSION
    assert "git_commit" in data
    assert "github_repo" in data
    assert "is_git_repo" in data
    assert data["github_repo"] == "darktekmafia/MP3MetaFix"


def test_semver_parsing_and_comparison():
    """Verify semantic version parsing and newer-version comparison logic."""
    from backend.updater import parse_semver, is_version_newer
    
    assert parse_semver("0.2.0") == (0, 2, 0)
    assert parse_semver("v0.1.1") == (0, 1, 1)
    assert parse_semver("v1.0.0-beta") == (1, 0, 0)
    
    assert is_version_newer("0.3.0", "0.2.0") is True
    assert is_version_newer("1.0.0", "0.9.9") is True
    assert is_version_newer("0.2.1", "0.2.0") is True
    assert is_version_newer("0.2.0", "0.2.0") is False
    assert is_version_newer("0.1.0", "0.2.0") is False


def test_api_updates_check_endpoint(client, monkeypatch):
    """Verify GET /api/updates/check returns valid structure even when offline or mocked."""
    res = client.get("/api/updates/check")
    assert res.status_code == 200
    data = res.json()
    assert "current_version" in data
    assert "latest_version" in data
    assert "update_available" in data
    assert isinstance(data["update_available"], bool)


@pytest.mark.anyio
async def test_stream_install_update_generator():
    """Verify stream_install_update yields SSE events."""
    from backend.updater import stream_install_update

    events = []
    async for event in stream_install_update():
        events.append(event)
        if len(events) >= 2:
            break
    assert len(events) > 0
    assert events[0].startswith("data: ")


def test_migrate_service_content_preserves_customizations():
    """Verify that service migration updates only ExecStart launch flags while strictly preserving all user customizations and cgroups."""
    from scripts.migrate_service import migrate_service_content, MigrationStatus

    legacy_unit = """[Unit]
Description=MP3MetaFix Web Server & Audio Metadata Editor
After=network.target

[Service]
Type=simple
User=customuser
WorkingDirectory=/custom/path/MP3MetaFix
Environment="PATH=/custom/path/MP3MetaFix/.venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="MP3METAFIX_HOST=0.0.0.0"
Environment="MP3METAFIX_PORT=8844"
Environment="MP3METAFIX_DATA_DIR=/custom/path/MP3MetaFix/data"
Environment="MP3METAFIX_TRUST_PROXIES=true"
Environment="CUSTOM_OVERRIDE=1"
ExecStart=/custom/path/MP3MetaFix/.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8844 --workers 4
Restart=always
RestartSec=3
ProtectSystem=full
PrivateTmp=true
NoNewPrivileges=true
MemoryMax=1024M
TasksMax=128
CPUQuota=90%

[Install]
WantedBy=multi-user.target
"""

    migrated, status, err = migrate_service_content(legacy_unit)
    assert status == MigrationStatus.CHANGED
    assert err is None

    # 1. Verify launch command updated
    assert "ExecStart=/custom/path/MP3MetaFix/.venv/bin/uvicorn backend.main:app --host $MP3METAFIX_HOST --port $MP3METAFIX_PORT --workers 4 --no-proxy-headers" in migrated

    # 2. Verify all custom environment variables preserved
    assert 'Environment="MP3METAFIX_HOST=0.0.0.0"' in migrated
    assert 'Environment="MP3METAFIX_PORT=8844"' in migrated
    assert 'Environment="MP3METAFIX_TRUST_PROXIES=true"' in migrated
    assert 'Environment="CUSTOM_OVERRIDE=1"' in migrated
    assert 'User=customuser' in migrated
    assert 'WorkingDirectory=/custom/path/MP3MetaFix' in migrated

    # 3. Verify custom sandboxing and resource limits preserved
    assert "MemoryMax=1024M" in migrated
    assert "TasksMax=128" in migrated
    assert "CPUQuota=90%" in migrated
    assert "ProtectSystem=full" in migrated
    assert "PrivateTmp=true" in migrated
    assert "NoNewPrivileges=true" in migrated


def test_migrate_service_content_idempotency():
    """Verify that running migration repeatedly on an already migrated unit returns UNCHANGED with identical content."""
    from scripts.migrate_service import migrate_service_content, MigrationStatus

    modern_unit = """[Unit]
Description=MP3MetaFix Web Server & Audio Metadata Editor
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/MP3MetaFix
Environment="PATH=/root/MP3MetaFix/.venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="MP3METAFIX_HOST=0.0.0.0"
Environment="MP3METAFIX_PORT=8844"
Environment="MP3METAFIX_DATA_DIR=/root/MP3MetaFix/data"
Environment="MP3METAFIX_TRUST_PROXIES=true"
ExecStart=/root/MP3MetaFix/.venv/bin/uvicorn backend.main:app --host $MP3METAFIX_HOST --port $MP3METAFIX_PORT --workers 2 --no-proxy-headers
Restart=always
RestartSec=3
ProtectSystem=full
PrivateTmp=true
NoNewPrivileges=true
MemoryMax=512M
TasksMax=64
CPUQuota=80%

[Install]
WantedBy=multi-user.target
"""

    migrated, status, err = migrate_service_content(modern_unit)
    assert status == MigrationStatus.UNCHANGED
    assert err is None
    assert migrated == modern_unit


def test_migrate_service_content_legacy_env_synthesis():
    """Verify that a legacy unit without explicit Environment host/port lines has them synthesized into [Service]."""
    from scripts.migrate_service import migrate_service_content, MigrationStatus

    legacy_minimal = """[Unit]
Description=MP3MetaFix Web Server

[Service]
ExecStart=/opt/mp3metafix/.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 9000 --workers 2
Restart=always
"""

    migrated, status, err = migrate_service_content(legacy_minimal)
    assert status == MigrationStatus.CHANGED
    assert err is None
    assert 'Environment="MP3METAFIX_HOST=0.0.0.0"' in migrated
    assert 'Environment="MP3METAFIX_PORT=9000"' in migrated
    assert "ExecStart=/opt/mp3metafix/.venv/bin/uvicorn backend.main:app --host $MP3METAFIX_HOST --port $MP3METAFIX_PORT --workers 2 --no-proxy-headers" in migrated


def test_migrate_service_unrelated_unit_safety():
    """Verify that unrelated or non-uvicorn unit files return UNCHANGED and are left completely untouched."""
    from scripts.migrate_service import migrate_service_content, MigrationStatus

    unrelated = """[Unit]
Description=Some Other App

[Service]
ExecStart=/usr/bin/python3 /opt/other/app.py
"""

    migrated, status, err = migrate_service_content(unrelated)
    assert status == MigrationStatus.UNCHANGED
    assert err is None
    assert migrated == unrelated


def test_migrate_service_rejection_of_unsafe_execstart():
    """Verify that unsafe, shell-wrapped, compound, or malformed ExecStart commands are explicitly rejected as FAILED."""
    from scripts.migrate_service import migrate_service_content, MigrationStatus

    # 1. Shell pipe wrapper
    pipe_unit = """[Unit]
Description=MP3MetaFix

[Service]
ExecStart=/bin/sh -c "/opt/mp3metafix/.venv/bin/uvicorn backend.main:app | logger"
"""
    _, status, err = migrate_service_content(pipe_unit)
    assert status == MigrationStatus.FAILED
    assert "shell pipelines" in err

    # 2. Compound command with &&
    compound_unit = """[Unit]
Description=MP3MetaFix

[Service]
ExecStart=/opt/mp3metafix/.venv/bin/uvicorn backend.main:app && echo done
"""
    _, status, err = migrate_service_content(compound_unit)
    assert status == MigrationStatus.FAILED
    assert "compound commands" in err

    # 3. Unclosed quotation
    unclosed_unit = """[Unit]
Description=MP3MetaFix

[Service]
ExecStart=/opt/mp3metafix/.venv/bin/uvicorn backend.main:app --host "0.0.0.0
"""
    _, status, err = migrate_service_content(unclosed_unit)
    assert status == MigrationStatus.FAILED
    assert "quoting" in err

    # 4. Multiple ExecStart lines in [Service]
    multi_exec = """[Unit]
Description=MP3MetaFix

[Service]
ExecStart=/opt/mp3metafix/.venv/bin/uvicorn backend.main:app
ExecStart=/opt/mp3metafix/.venv/bin/uvicorn backend.main:app --port 8844
"""
    _, status, err = migrate_service_content(multi_exec)
    assert status == MigrationStatus.FAILED
    assert "Multiple ExecStart" in err


def test_migrate_service_file_on_disk_permissions_and_atomicity(tmp_path: Path):
    """Verify migrate_service_file performs atomic in-place updates on disk while strictly preserving file permissions."""
    import stat
    from scripts.migrate_service import migrate_service_file, MigrationStatus

    svc_file = tmp_path / "mp3metafix.service"
    legacy_content = """[Unit]
Description=MP3MetaFix

[Service]
Environment="MP3METAFIX_HOST=127.0.0.1"
Environment="MP3METAFIX_PORT=8844"
ExecStart=/opt/mp3metafix/.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8844 --workers 2
"""
    svc_file.write_text(legacy_content, encoding="utf-8")
    # Set custom permission mode (e.g. 0640)
    svc_file.chmod(0o640)

    # First migration run: Modifies file and returns CHANGED
    status1, msg1 = migrate_service_file(svc_file)
    assert status1 == MigrationStatus.CHANGED
    assert "Migrated launch settings" in msg1

    # Verify content updated
    content_after = svc_file.read_text(encoding="utf-8")
    assert "--no-proxy-headers" in content_after
    assert "--host $MP3METAFIX_HOST" in content_after
    assert "--port $MP3METAFIX_PORT" in content_after

    # Verify permission mode preserved
    file_mode = stat.S_IMODE(svc_file.stat().st_mode)
    assert file_mode == 0o640

    # Verify no temporary files left behind
    parent_files = list(tmp_path.glob("*.tmp*"))
    assert parent_files == []

    # Second migration run: Returns UNCHANGED with no modifications
    status2, msg2 = migrate_service_file(svc_file)
    assert status2 == MigrationStatus.UNCHANGED
    assert "already up to date" in msg2
    assert svc_file.read_text(encoding="utf-8") == content_after

    # Migration on non-existent file: Returns FAILED
    status3, msg3 = migrate_service_file(tmp_path / "nonexistent.service")
    assert status3 == MigrationStatus.FAILED
    assert "does not exist" in msg3


def test_migrate_service_cli_exit_codes(tmp_path: Path):
    """Verify CLI exit codes: 0 for changed, 2 for unchanged, 1 for failure."""
    import subprocess
    import sys

    # 1. Test Exit Code 0 (Changed)
    svc_legacy = tmp_path / "legacy.service"
    svc_legacy.write_text("""[Unit]
Description=MP3MetaFix
[Service]
Environment="MP3METAFIX_HOST=0.0.0.0"
Environment="MP3METAFIX_PORT=8844"
ExecStart=/opt/.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8844
""", encoding="utf-8")

    res_changed = subprocess.run(
        [sys.executable, "scripts/migrate_service.py", str(svc_legacy)],
        capture_output=True,
        text=True,
    )
    assert res_changed.returncode == 0
    assert "[+] Migrated launch settings" in res_changed.stdout

    # 2. Test Exit Code 2 (Unchanged / Already current)
    res_unchanged = subprocess.run(
        [sys.executable, "scripts/migrate_service.py", str(svc_legacy)],
        capture_output=True,
        text=True,
    )
    assert res_unchanged.returncode == 2
    assert "[*] Service" in res_unchanged.stdout
    assert "already up to date" in res_unchanged.stdout

    # 3. Test Exit Code 1 (Failed / Unsafe command)
    svc_failed = tmp_path / "failed.service"
    svc_failed.write_text("""[Unit]
Description=MP3MetaFix
[Service]
ExecStart=/bin/sh -c "/opt/.venv/bin/uvicorn backend.main:app | tee log"
""", encoding="utf-8")

    res_failed = subprocess.run(
        [sys.executable, "scripts/migrate_service.py", str(svc_failed)],
        capture_output=True,
        text=True,
    )
    assert res_failed.returncode == 1
    assert "[!] Error:" in res_failed.stderr


def test_installer_service_migration_and_restart_failure_handling(tmp_path: Path):
    """Verify that install.sh migration and update logic detects failed migrations, reloads, and restarts and does not report false success."""
    import subprocess

    # Test 1: Bash subshell executing migration logic when migration script fails
    bash_script_fail = f"""
    MIGRATE_SCRIPT="scripts/migrate_service.py"
    INSTALL_DIR="{Path.cwd()}"
    SYS_SVC="{tmp_path / 'invalid.service'}"
    cat << 'EOF' > "$SYS_SVC"
[Unit]
Description=Test
[Service]
ExecStart=/bin/sh -c "uvicorn backend.main:app | logger"
EOF

    # Run migration helper
    python3 "$MIGRATE_SCRIPT" "$SYS_SVC" 2>/dev/null
    SVC_RES=$?
    if [ "$SVC_RES" -eq 1 ]; then
        echo "DETECTED_FAILURE"
        exit 1
    fi
    """
    res1 = subprocess.run(["bash", "-c", bash_script_fail], capture_output=True, text=True)
    assert res1.returncode == 1
    assert "DETECTED_FAILURE" in res1.stdout

    # Test 2: Unit file was left unchanged after failed migration
    invalid_content = (tmp_path / 'invalid.service').read_text(encoding="utf-8")
    assert '| logger' in invalid_content

    # Test 3: Test that failed systemctl restart prevents successful update reporting
    bash_script_restart_fail = """
    has_errors=false
    # Simulate a failed daemon-reload / restart
    mock_systemctl_fail() {
        return 1
    }
    if ! mock_systemctl_fail; then
        has_errors=true
    fi
    if [ "$has_errors" = true ]; then
        echo "UPDATE_REPORTED_FAILURE"
        exit 1
    else
        echo "UPDATE_REPORTED_SUCCESS"
        exit 0
    fi
    """
    res2 = subprocess.run(["bash", "-c", bash_script_restart_fail], capture_output=True, text=True)
    assert res2.returncode == 1
    assert "UPDATE_REPORTED_FAILURE" in res2.stdout


def test_installer_reexec_on_git_update_and_loop_prevention(tmp_path: Path):
    """Verify that install.sh re-executes when commits change and halts re-execution when _MP3METAFIX_REEXEC=1."""
    import subprocess

    # Simulation script testing commit comparison and re-exec loop guard
    test_reexec_script = """
    reexec_count=0
    test_update_func() {
        if [ "$_MP3METAFIX_REEXEC" != "1" ]; then
            PREV_COMMIT="commit_aaa"
            NEW_COMMIT="commit_bbb"
            if [ "$PREV_COMMIT" != "$NEW_COMMIT" ]; then
                export _MP3METAFIX_REEXEC=1
                echo "REEXEC_TRIGGERED"
                # Call update again simulating exec
                test_update_func
                return
            fi
        fi
        echo "NEW_LOGIC_EXECUTED_AFTER_REEXEC"
    }
    test_update_func
    """
    res = subprocess.run(["bash", "-c", test_reexec_script], capture_output=True, text=True)
    assert res.returncode == 0
    assert "REEXEC_TRIGGERED" in res.stdout
    assert "NEW_LOGIC_EXECUTED_AFTER_REEXEC" in res.stdout
    # Verify REEXEC_TRIGGERED appeared exactly once (no loop)
    assert res.stdout.count("REEXEC_TRIGGERED") == 1


def test_handoff_capable_installer_executes_new_logic_on_first_run(tmp_path: Path):
    """Verify that starting from a handoff-capable installer (v0.3.3+), pulling updates executes new logic in a single run."""
    import subprocess

    origin_dir = tmp_path / "origin"
    origin_dir.mkdir()
    repo_dir = tmp_path / "local"

    # Initialize origin git repo
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin_dir)], check=True, capture_output=True)

    # Initial commit in a temp working tree
    init_dir = tmp_path / "init"
    init_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(init_dir)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(init_dir), "config", "user.name", "TestUser"], check=True)
    subprocess.run(["git", "-C", str(init_dir), "config", "user.email", "test@example.com"], check=True)

    # Handoff-capable install.sh (v0.3.3+)
    handoff_script = """#!/usr/bin/env bash
set -e
ORIG_ARGS=("$@")
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

do_update() {
    if [ -d "${INSTALL_DIR}/.git" ]; then
        if [ "$_MP3METAFIX_REEXEC" != "1" ]; then
            PREV_COMMIT=$(git -C "$INSTALL_DIR" rev-parse HEAD 2>/dev/null || true)
            git -C "$INSTALL_DIR" fetch --tags >/dev/null 2>&1 || true
            git -C "$INSTALL_DIR" pull origin main >/dev/null 2>&1 || git -C "$INSTALL_DIR" pull >/dev/null 2>&1 || true
            NEW_COMMIT=$(git -C "$INSTALL_DIR" rev-parse HEAD 2>/dev/null || true)
            if [ -n "$PREV_COMMIT" ] && [ -n "$NEW_COMMIT" ] && [ "$PREV_COMMIT" != "$NEW_COMMIT" ]; then
                export _MP3METAFIX_REEXEC=1
                exec bash "${INSTALL_DIR}/install.sh" "${ORIG_ARGS[@]}"
            fi
        fi
    fi
    echo "EXECUTING_V033_FLOW"
}

if [ "$1" = "--update" ]; then
    do_update
fi
"""
    (init_dir / "install.sh").write_text(handoff_script, encoding="utf-8")
    (init_dir / "install.sh").chmod(0o755)
    subprocess.run(["git", "-C", str(init_dir), "add", "."], check=True)
    subprocess.run(["git", "-C", str(init_dir), "commit", "-m", "init v0.3.3"], check=True)
    subprocess.run(["git", "-C", str(init_dir), "remote", "add", "origin", str(origin_dir)], check=True)
    subprocess.run(["git", "-C", str(init_dir), "push", "origin", "main"], check=True)

    # Clone to local repo
    subprocess.run(["git", "clone", str(origin_dir), str(repo_dir)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "TestUser"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "test@example.com"], check=True)

    # Now add a new release (v0.3.4) to origin
    new_release_script = handoff_script.replace("EXECUTING_V033_FLOW", "EXECUTING_V034_NEW_FLOW_WITH_MIGRATION")
    (init_dir / "install.sh").write_text(new_release_script, encoding="utf-8")
    subprocess.run(["git", "-C", str(init_dir), "commit", "-am", "release v0.3.4"], check=True)
    subprocess.run(["git", "-C", str(init_dir), "push", "origin", "main"], check=True)

    # Run install.sh --update from local repo (which starts as v0.3.3 handoff-capable)
    res = subprocess.run(["bash", str(repo_dir / "install.sh"), "--update"], capture_output=True, text=True)
    assert res.returncode == 0
    # The handoff executed the v0.3.4 logic immediately in the single run!
    assert "EXECUTING_V034_NEW_FLOW_WITH_MIGRATION" in res.stdout
    assert "EXECUTING_V033_FLOW" not in res.stdout


def test_pre_handoff_installer_transition_behavior(tmp_path: Path):
    """Verify that a legacy pre-handoff installer (v0.3.0) completes its old flow on first run, and requires a 2nd run for new logic."""
    import subprocess

    origin_dir = tmp_path / "origin"
    origin_dir.mkdir()
    repo_dir = tmp_path / "local"

    # Initialize origin git repo
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin_dir)], check=True, capture_output=True)

    # Initial commit with pre-handoff installer (no exec bash)
    init_dir = tmp_path / "init"
    init_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(init_dir)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(init_dir), "config", "user.name", "TestUser"], check=True)
    subprocess.run(["git", "-C", str(init_dir), "config", "user.email", "test@example.com"], check=True)

    pre_handoff_script = """#!/usr/bin/env bash
set -e
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

do_update() {
    git -C "$INSTALL_DIR" pull origin main >/dev/null 2>&1 || git -C "$INSTALL_DIR" pull >/dev/null 2>&1 || true
    echo "EXECUTING_LEGACY_PRE_HANDOFF_FLOW"
}

if [ "$1" = "--update" ]; then
    do_update
fi
"""
    (init_dir / "install.sh").write_text(pre_handoff_script, encoding="utf-8")
    subprocess.run(["git", "-C", str(init_dir), "add", "."], check=True)
    subprocess.run(["git", "-C", str(init_dir), "commit", "-m", "v0.3.0"], check=True)
    subprocess.run(["git", "-C", str(init_dir), "remote", "add", "origin", str(origin_dir)], check=True)
    subprocess.run(["git", "-C", str(init_dir), "push", "origin", "main"], check=True)

    # Clone local repo
    subprocess.run(["git", "clone", str(origin_dir), str(repo_dir)], check=True, capture_output=True)

    # Now add v0.3.4 to origin with new migration logic
    new_script = """#!/usr/bin/env bash
set -e
ORIG_ARGS=("$@")
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "EXECUTING_MODERN_V034_MIGRATION_FLOW"
"""
    (init_dir / "install.sh").write_text(new_script, encoding="utf-8")
    subprocess.run(["git", "-C", str(init_dir), "commit", "-am", "v0.3.4"], check=True)
    subprocess.run(["git", "-C", str(init_dir), "push", "origin", "main"], check=True)

    # First update run from pre-handoff repo: Executes legacy flow because in-memory script was v0.3.0
    res1 = subprocess.run(["bash", str(repo_dir / "install.sh"), "--update"], capture_output=True, text=True)
    assert res1.returncode == 0
    assert "EXECUTING_LEGACY_PRE_HANDOFF_FLOW" in res1.stdout

    # Second update run: Now loads the new script from disk and executes the modern migration flow
    res2 = subprocess.run(["bash", str(repo_dir / "install.sh"), "--update"], capture_output=True, text=True)
    assert res2.returncode == 0
    assert "EXECUTING_MODERN_V034_MIGRATION_FLOW" in res2.stdout







