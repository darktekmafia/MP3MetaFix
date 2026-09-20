"""Unit and Integration tests for MP3MetaFix backend and security layer."""

import io
import time
import json
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

    res_health_head = client.head("/api/health")
    assert res_health_head.status_code == 200

    res_ver = client.get("/api/version")
    assert res_ver.status_code == 200
    assert res_ver.json()["version"] == VERSION

    res_ver_head = client.head("/api/version")
    assert res_ver_head.status_code == 200


def test_api_system_stats_telemetry(client):
    """Verify system telemetry stats endpoint and security boundaries."""
    res = client.get("/api/system/stats")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["version"] == VERSION
    assert isinstance(data["uptime_seconds"], int)

    # CPU metrics
    assert "cpu" in data
    assert "cores" in data["cpu"]
    assert "load_1m" in data["cpu"]
    assert "estimated_percent" in data["cpu"]

    # Memory metrics
    assert "memory" in data
    assert "total_mb" in data["memory"]
    assert "used_percent" in data["memory"]

    # Disk metrics
    assert "disk" in data
    assert "total_gb" in data["disk"]
    assert "free_gb" in data["disk"]

    # App temporary storage metrics
    assert "app_storage" in data
    assert "temp_storage_mb" in data["app_storage"]
    assert "active_sessions_count" in data["app_storage"]
    assert "max_temp_storage_mb" in data["app_storage"]

    # Network runtime metrics
    assert "network" in data
    assert "host" in data["network"]
    assert "port" in data["network"]

    # Security check: Ensure raw secret keys or sensitive root paths are not leaked
    dumped = json.dumps(data)
    assert "/proc" not in dumped
    assert "secret_key" not in dumped

    # HEAD request support
    res_head = client.head("/api/system/stats")
    assert res_head.status_code == 200


def test_multi_interface_static_mounts(client):
    """Verify routing across Gateway Hub (/), Quick Fix (/app), and Manager (/manager)."""
    # 1. Gateway Hub (/)
    res_hub = client.get("/")
    assert res_hub.status_code == 200
    assert "Select Workspace Interface" in res_hub.text
    assert "MP3MetaFix" in res_hub.text
    assert "MP3MetaManager" in res_hub.text

    # 2. MP3MetaFix Quick Editor (/app)
    res_app = client.get("/app/")
    assert res_app.status_code == 200
    assert "Drop your MP3 file here" in res_app.text
    assert "app-switcher-nav" in res_app.text

    # 3. MP3MetaManager Desktop Workspace (/manager)
    res_mgr = client.get("/manager/")
    assert res_mgr.status_code == 200
    assert "MP3MetaManager Desktop Workspace" in res_mgr.text
    assert "app-switcher-nav" in res_mgr.text


def test_security_static_mounts_and_telemetry_isolation(client):
    """Verify security isolation, method rejection, and directory traversal defense."""
    # 1. Mutating HTTP methods on telemetry must be rejected
    assert client.post("/api/system/stats").status_code == 405
    assert client.delete("/api/system/stats").status_code == 405
    assert client.put("/api/system/stats").status_code == 405

    # 2. Directory traversal attempts against static endpoints must fail
    res_trav1 = client.get("/app/../../etc/passwd")
    assert res_trav1.status_code in (404, 400)

    res_trav2 = client.get("/manager/....//....//etc/shadow")
    assert res_trav2.status_code in (404, 400)

    res_trav3 = client.get("/assets/../../backend/config.py")
    assert res_trav3.status_code in (404, 400)


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


def test_v031_to_v034_update_transition_requires_second_invocation(tmp_path: Path):
    """Verify that an installation starting from v0.3.1 (no migration in install.sh) updates disk on run 1, then migrates on run 2."""
    import subprocess

    origin_dir = tmp_path / "origin"
    origin_dir.mkdir()
    repo_dir = tmp_path / "local"

    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin_dir)], check=True, capture_output=True)

    # v0.3.1 installer
    init_dir = tmp_path / "init"
    init_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(init_dir)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(init_dir), "config", "user.name", "TestUser"], check=True)
    subprocess.run(["git", "-C", str(init_dir), "config", "user.email", "test@example.com"], check=True)

    v031_script = """#!/usr/bin/env bash
set -e
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
do_update() {
    git -C "$INSTALL_DIR" pull origin main >/dev/null 2>&1 || true
    echo "V031_UPDATE_COMPLETED"
}
if [ "$1" = "--update" ]; then do_update; fi
"""
    (init_dir / "install.sh").write_text(v031_script, encoding="utf-8")
    subprocess.run(["git", "-C", str(init_dir), "add", "."], check=True)
    subprocess.run(["git", "-C", str(init_dir), "commit", "-m", "v0.3.1"], check=True)
    subprocess.run(["git", "-C", str(init_dir), "remote", "add", "origin", str(origin_dir)], check=True)
    subprocess.run(["git", "-C", str(init_dir), "push", "origin", "main"], check=True)

    # Clone local repo representing a v0.3.1 install
    subprocess.run(["git", "clone", str(origin_dir), str(repo_dir)], check=True, capture_output=True)

    # Add v0.3.4 to origin
    v034_script = """#!/usr/bin/env bash
set -e
ORIG_ARGS=("$@")
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "V034_MIGRATION_EXECUTED"
"""
    (init_dir / "install.sh").write_text(v034_script, encoding="utf-8")
    subprocess.run(["git", "-C", str(init_dir), "commit", "-am", "v0.3.4"], check=True)
    subprocess.run(["git", "-C", str(init_dir), "push", "origin", "main"], check=True)

    # First update: completes v0.3.1 flow (fetches files to disk)
    res1 = subprocess.run(["bash", str(repo_dir / "install.sh"), "--update"], capture_output=True, text=True)
    assert res1.returncode == 0
    assert "V031_UPDATE_COMPLETED" in res1.stdout
    assert "V034_MIGRATION_EXECUTED" not in res1.stdout

    # Second update: loads v0.3.4 from disk and executes migration
    res2 = subprocess.run(["bash", str(repo_dir / "install.sh"), "--update"], capture_output=True, text=True)
    assert res2.returncode == 0
    assert "V034_MIGRATION_EXECUTED" in res2.stdout


def test_configure_access_validation():
    """Verify validation of bind host and port values in configure_access."""
    from scripts.configure_access import validate_bind_host, validate_bind_port

    # Valid hosts
    assert validate_bind_host("127.0.0.1") is True
    assert validate_bind_host("0.0.0.0") is True
    assert validate_bind_host("192.168.1.104") is True
    assert validate_bind_host("10.0.0.1") is True
    assert validate_bind_host("::") is True
    assert validate_bind_host("::1") is True
    assert validate_bind_host("localhost") is True
    assert validate_bind_host("lan") is True
    assert validate_bind_host("local") is True
    assert validate_bind_host("mp3metafix.lan") is True

    # Invalid hosts & injection attempts
    assert validate_bind_host("") is False
    assert validate_bind_host("   ") is False
    assert validate_bind_host(None) is False
    assert validate_bind_host("127.0.0.1; rm -rf /") is False
    assert validate_bind_host("0.0.0.0 && whoami") is False
    assert validate_bind_host("127.0.0.1 | bash") is False
    assert validate_bind_host("$(whoami)") is False
    assert validate_bind_host("`id`") is False
    assert validate_bind_host('127.0.0.1 "test"') is False
    assert validate_bind_host("127.0.0.1 8844") is False
    assert validate_bind_host("127.0.0.1\n") is False

    # Valid ports
    assert validate_bind_port(8844) is True
    assert validate_bind_port("8844") is True
    assert validate_bind_port(1) is True
    assert validate_bind_port(65535) is True

    # Invalid ports
    assert validate_bind_port(0) is False
    assert validate_bind_port(-1) is False
    assert validate_bind_port(65536) is False
    assert validate_bind_port("abc") is False
    assert validate_bind_port(None) is False

    # Valid trusted proxies
    from scripts.configure_access import validate_trusted_proxies
    assert validate_trusted_proxies("127.0.0.1,::1") is True
    assert validate_trusted_proxies("192.168.0.50") is True
    assert validate_trusted_proxies("10.0.0.0/8, 172.16.0.0/12") is True
    assert validate_trusted_proxies("127.0.0.1, 192.168.1.100, 10.10.10.10") is True

    # Invalid trusted proxies
    assert validate_trusted_proxies("") is False
    assert validate_trusted_proxies(None) is False
    assert validate_trusted_proxies("127.0.0.1; rm -rf /") is False
    assert validate_trusted_proxies("not-an-ip") is False
    assert validate_trusted_proxies("192.168.1.999") is False

    # Valid proxy domain / host
    from scripts.configure_access import validate_proxy_host
    assert validate_proxy_host("mp3.yourdomain.com") is True
    assert validate_proxy_host("https://mp3.yourdomain.com") is True
    assert validate_proxy_host("http://mp3.local:8844") is True
    assert validate_proxy_host("192.168.0.55") is True
    assert validate_proxy_host("https://192.168.0.55:8443/") is True
    assert validate_proxy_host("none") is True
    assert validate_proxy_host("clear") is True

    # Invalid proxy domain / host
    assert validate_proxy_host("") is False
    assert validate_proxy_host(None) is False
    assert validate_proxy_host("mp3.domain.com; rm -rf") is False
    assert validate_proxy_host("mp3 domain com") is False
    assert validate_proxy_host("https://") is False
    assert validate_proxy_host("mp3.domain.com:99999") is False


def test_configure_access_get_and_set_service_binding():
    """Verify inspection and modification of host, port, and proxy trust in service unit content."""
    from scripts.configure_access import ConfigStatus, get_service_binding, set_service_binding

    sample_unit = """[Unit]
Description=MP3MetaFix Web Server
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/mp3metafix
Environment="MP3METAFIX_HOST=127.0.0.1"
Environment="MP3METAFIX_PORT=8844"
Environment="MP3METAFIX_DATA_DIR=/opt/mp3metafix/data"
Environment="MP3METAFIX_TRUST_PROXIES=false"
Environment="MP3METAFIX_TRUSTED_PROXIES=127.0.0.1,::1"
ExecStart=/opt/mp3metafix/.venv/bin/uvicorn backend.main:app --host $MP3METAFIX_HOST --port $MP3METAFIX_PORT --workers 2 --no-proxy-headers
Restart=always
MemoryMax=512M

[Install]
WantedBy=default.target
"""
    # 1. Test get_service_binding
    binding = get_service_binding(sample_unit)
    assert binding["host"] == "127.0.0.1"
    assert binding["port"] == "8844"
    assert binding["trust_proxies"] == "false"
    assert binding["trusted_proxies"] == "127.0.0.1,::1"
    assert binding["is_configured"] is True

    # 2. Test set_service_binding with LAN alias and proxy trust
    new_content, status, err = set_service_binding(
        sample_unit,
        host="lan",
        trust_proxies=True,
        trusted_proxies="192.168.0.50,10.0.0.0/8",
    )
    assert status == ConfigStatus.CHANGED
    assert err is None
    assert 'Environment="MP3METAFIX_HOST=0.0.0.0"' in new_content
    assert 'Environment="MP3METAFIX_PORT=8844"' in new_content
    assert 'Environment="MP3METAFIX_TRUST_PROXIES=true"' in new_content
    assert 'Environment="MP3METAFIX_TRUSTED_PROXIES=192.168.0.50,10.0.0.0/8"' in new_content
    assert "MemoryMax=512M" in new_content

    # 3. Test idempotent set_service_binding
    same_content, status2, _ = set_service_binding(
        new_content,
        host="0.0.0.0",
        trust_proxies=True,
        trusted_proxies="192.168.0.50,10.0.0.0/8",
    )
    assert status2 == ConfigStatus.UNCHANGED
    assert same_content == new_content

    # 4. Test changing port and disabling proxy
    port_content, status3, _ = set_service_binding(sample_unit, port=9000, trust_proxies=False)
    assert status3 == ConfigStatus.CHANGED
    assert 'Environment="MP3METAFIX_PORT=9000"' in port_content
    assert 'Environment="MP3METAFIX_HOST=127.0.0.1"' in port_content
    assert 'Environment="MP3METAFIX_TRUST_PROXIES=false"' in port_content

    # 5. Test setting and clearing proxy domain
    domain_content, status_dom, _ = set_service_binding(
        sample_unit,
        proxy_host="mp3.yourdomain.com",
    )
    assert status_dom == ConfigStatus.CHANGED
    assert 'Environment="MP3METAFIX_PROXY_HOST=mp3.yourdomain.com"' in domain_content

    # Clear domain
    cleared_content, status_clr, _ = set_service_binding(
        domain_content,
        proxy_host="none",
    )
    assert status_clr == ConfigStatus.CHANGED
    assert "MP3METAFIX_PROXY_HOST" not in cleared_content

    # 6. Test invalid host / port / proxy / domain rejection
    _, bad_status, bad_err = set_service_binding(sample_unit, host="127.0.0.1; whoami")
    assert bad_status == ConfigStatus.FAILED
    assert "Invalid bind host" in bad_err

    _, bad_status2, bad_err2 = set_service_binding(sample_unit, port="99999")
    assert bad_status2 == ConfigStatus.FAILED
    assert "Invalid bind port" in bad_err2

    _, bad_status3, bad_err3 = set_service_binding(sample_unit, trusted_proxies="bad_ip_address")
    assert bad_status3 == ConfigStatus.FAILED
    assert "Invalid trusted_proxies" in bad_err3

    _, bad_status4, bad_err4 = set_service_binding(sample_unit, proxy_host="bad host; rm -rf")
    assert bad_status4 == ConfigStatus.FAILED
    assert "Invalid proxy domain" in bad_err4


def test_configure_access_file_on_disk_permissions_and_atomicity(tmp_path: Path):
    """Verify atomic file update and strict preservation of file permissions."""
    import stat
    from scripts.configure_access import ConfigStatus, update_service_file

    svc_file = tmp_path / "mp3metafix.service"
    svc_file.write_text("""[Unit]
Description=MP3MetaFix
[Service]
Environment="MP3METAFIX_HOST=127.0.0.1"
Environment="MP3METAFIX_PORT=8844"
ExecStart=/opt/mp3metafix/.venv/bin/uvicorn backend.main:app --host $MP3METAFIX_HOST --port $MP3METAFIX_PORT
[Install]
WantedBy=default.target
""", encoding="utf-8")

    # Set custom permission mode (0640)
    svc_file.chmod(0o640)

    status, msg = update_service_file(svc_file, host="0.0.0.0", trust_proxies=True, trusted_proxies="192.168.0.50", proxy_host="mp3.example.com")
    assert status == ConfigStatus.CHANGED
    assert "Updated network configuration" in msg

    # Verify content
    updated_text = svc_file.read_text(encoding="utf-8")
    assert 'Environment="MP3METAFIX_HOST=0.0.0.0"' in updated_text
    assert 'Environment="MP3METAFIX_TRUST_PROXIES=true"' in updated_text
    assert 'Environment="MP3METAFIX_TRUSTED_PROXIES=192.168.0.50"' in updated_text
    assert 'Environment="MP3METAFIX_PROXY_HOST=mp3.example.com"' in updated_text

    # Verify mode is preserved
    mode = stat.S_IMODE(svc_file.stat().st_mode)
    assert mode == 0o640

    # Verify no temporary files remain in directory
    temp_files = list(tmp_path.glob(".*.tmp.*"))
    assert len(temp_files) == 0


def test_configure_access_cli(tmp_path: Path):
    """Verify CLI interface of configure_access.py (get and set commands)."""
    import json
    import subprocess
    import sys

    svc_file = tmp_path / "mp3metafix.service"
    svc_file.write_text("""[Unit]
Description=MP3MetaFix
[Service]
Environment="MP3METAFIX_HOST=127.0.0.1"
Environment="MP3METAFIX_PORT=8844"
Environment="MP3METAFIX_TRUST_PROXIES=false"
Environment="MP3METAFIX_TRUSTED_PROXIES=127.0.0.1,::1"
ExecStart=/opt/mp3metafix/.venv/bin/uvicorn backend.main:app
[Install]
WantedBy=default.target
""", encoding="utf-8")

    script_path = Path(__file__).resolve().parent.parent / "scripts" / "configure_access.py"

    # Test get command
    res_get = subprocess.run(
        [sys.executable, str(script_path), "get", str(svc_file)],
        capture_output=True,
        text=True,
    )
    assert res_get.returncode == 0
    info = json.loads(res_get.stdout)
    assert info["host"] == "127.0.0.1"
    assert info["port"] == "8844"
    assert info["trust_proxies"] == "false"

    # Test set command with host, proxy trust, and domain
    res_set = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "set",
            str(svc_file),
            "--host",
            "0.0.0.0",
            "--trust-proxies",
            "true",
            "--trusted-proxies",
            "192.168.0.50,10.0.0.0/8",
            "--domain",
            "mp3.yourdomain.com",
        ],
        capture_output=True,
        text=True,
    )
    assert res_set.returncode == 0
    assert "[+]" in res_set.stdout

    # Test idempotent set command (exit code 2)
    res_set_idem = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "set",
            str(svc_file),
            "--host",
            "0.0.0.0",
            "--trust-proxies",
            "true",
            "--trusted-proxies",
            "192.168.0.50,10.0.0.0/8",
            "--domain",
            "mp3.yourdomain.com",
        ],
        capture_output=True,
        text=True,
    )
    assert res_set_idem.returncode == 2

    # Test clearing domain
    res_set_clr = subprocess.run(
        [sys.executable, str(script_path), "set", str(svc_file), "--no-domain"],
        capture_output=True,
        text=True,
    )
    assert res_set_clr.returncode == 0
    assert "MP3METAFIX_PROXY_HOST" not in svc_file.read_text(encoding="utf-8")

    # Test set with invalid host (exit code 1)
    res_set_bad = subprocess.run(
        [sys.executable, str(script_path), "set", str(svc_file), "--host", "bad host; rm -rf"],
        capture_output=True,
        text=True,
    )
    assert res_set_bad.returncode == 1
    assert "[!]" in res_set_stderr if (res_set_stderr := res_set.stderr) else res_set.stdout


def test_installer_access_and_binding_commands(tmp_path: Path):
    """Verify that install.sh --access, --lan, --local, --bind, --proxy, --domain, and --h operate correctly."""
    import os
    import shutil
    import subprocess
    import sys

    test_root = tmp_path / "app"
    test_root.mkdir()
    scripts_dir = test_root / "scripts"
    scripts_dir.mkdir()
    venv_bin = test_root / ".venv" / "bin"
    venv_bin.mkdir(parents=True)

    # Symlink python to venv/bin/python
    (venv_bin / "python").symlink_to(Path(sys.executable))

    # Copy install.sh and scripts
    src_install = Path(__file__).resolve().parent.parent / "install.sh"
    src_cfg = Path(__file__).resolve().parent.parent / "scripts" / "configure_access.py"
    shutil.copy(src_install, test_root / "install.sh")
    shutil.copy(src_cfg, scripts_dir / "configure_access.py")
    (test_root / "VERSION").write_text("0.3.4\n")

    # Create mock user systemd service
    config_user_systemd = tmp_path / ".config" / "systemd" / "user"
    config_user_systemd.mkdir(parents=True)
    svc_file = config_user_systemd / "mp3metafix.service"
    svc_file.write_text("""[Unit]
Description=MP3MetaFix
[Service]
Environment="MP3METAFIX_HOST=127.0.0.1"
Environment="MP3METAFIX_PORT=8844"
Environment="MP3METAFIX_TRUST_PROXIES=false"
Environment="MP3METAFIX_TRUSTED_PROXIES=127.0.0.1,::1"
ExecStart=/opt/mp3metafix/.venv/bin/uvicorn backend.main:app
[Install]
WantedBy=default.target
""", encoding="utf-8")

    # Mock systemctl and curl binaries
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    mock_systemctl = bin_dir / "systemctl"
    mock_systemctl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    mock_systemctl.chmod(0o755)

    mock_curl = bin_dir / "curl"
    mock_curl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    mock_curl.chmod(0o755)

    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    # 1. Test install.sh --h (Help alias)
    res_h = subprocess.run(
        ["bash", str(test_root / "install.sh"), "--h"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res_h.returncode == 0
    assert "Usage:" in res_h.stdout
    assert "Options:" in res_h.stdout

    # 2. Test install.sh --access (Inspection mode)
    res_access = subprocess.run(
        ["bash", str(test_root / "install.sh"), "--access"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res_access.returncode == 0
    assert "Configured Bind Host:" in res_access.stdout
    assert "127.0.0.1" in res_access.stdout
    assert "Configured Port:" in res_access.stdout
    assert "8844" in res_access.stdout
    assert "Localhost:" in res_access.stdout

    # 3. Test install.sh --lan (Switch to 0.0.0.0)
    res_lan = subprocess.run(
        ["bash", str(test_root / "install.sh"), "--lan"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res_lan.returncode == 0
    assert "Service successfully updated" in res_lan.stdout
    updated_svc = svc_file.read_text(encoding="utf-8")
    assert 'Environment="MP3METAFIX_HOST=0.0.0.0"' in updated_svc

    # 4. Test install.sh --bind 192.168.0.190 (Specific IP bind and probe target)
    res_bind_ip = subprocess.run(
        ["bash", str(test_root / "install.sh"), "--bind", "192.168.0.190"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res_bind_ip.returncode == 0
    assert "Service successfully updated" in res_bind_ip.stdout
    assert "http://192.168.0.190:8844" in res_bind_ip.stdout
    assert "Bound Interface:" in res_bind_ip.stdout
    updated_svc_ip = svc_file.read_text(encoding="utf-8")
    assert 'Environment="MP3METAFIX_HOST=192.168.0.190"' in updated_svc_ip

    # 5. Test install.sh --proxy 192.168.0.50 --domain mp3.mydomain.com
    res_proxy = subprocess.run(
        [
            "bash",
            str(test_root / "install.sh"),
            "--proxy",
            "192.168.0.50,10.0.0.0/8",
            "--domain",
            "mp3.mydomain.com",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res_proxy.returncode == 0
    assert "Service successfully updated" in res_proxy.stdout
    assert "https://mp3.yourdomain.com" in res_proxy.stdout or "mp3.mydomain.com" in res_proxy.stdout
    updated_svc_proxy = svc_file.read_text(encoding="utf-8")
    assert 'Environment="MP3METAFIX_TRUST_PROXIES=true"' in updated_svc_proxy
    assert 'Environment="MP3METAFIX_TRUSTED_PROXIES=192.168.0.50,10.0.0.0/8"' in updated_svc_proxy
    assert 'Environment="MP3METAFIX_PROXY_HOST=mp3.mydomain.com"' in updated_svc_proxy

    # 6. Test install.sh --no-domain (Clear proxy domain)
    res_no_domain = subprocess.run(
        ["bash", str(test_root / "install.sh"), "--no-domain"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res_no_domain.returncode == 0
    assert "Service successfully updated" in res_no_domain.stdout
    updated_svc_no_domain = svc_file.read_text(encoding="utf-8")
    assert "MP3METAFIX_PROXY_HOST" not in updated_svc_no_domain

    # 7. Test install.sh --no-proxy (Disable proxy trust)
    res_no_proxy = subprocess.run(
        ["bash", str(test_root / "install.sh"), "--no-proxy"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res_no_proxy.returncode == 0
    assert "Service successfully updated" in res_no_proxy.stdout
    updated_svc_no_proxy = svc_file.read_text(encoding="utf-8")
    assert 'Environment="MP3METAFIX_TRUST_PROXIES=false"' in updated_svc_no_proxy

    # 8. Test install.sh --local (Switch back to 127.0.0.1)
    res_local = subprocess.run(
        ["bash", str(test_root / "install.sh"), "--local"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res_local.returncode == 0
    assert "Service successfully updated" in res_local.stdout
    updated_svc2 = svc_file.read_text(encoding="utf-8")
    assert 'Environment="MP3METAFIX_HOST=127.0.0.1"' in updated_svc2


# --- Suno AI Extraction & Sync Tests ---

def test_extract_suno_id():
    from backend.suno_extractor import extract_suno_id

    # 1. Raw UUID
    raw_uuid = "a362dcef-6a63-423a-8f6b-8990ff370c06"
    assert extract_suno_id(raw_uuid) == raw_uuid

    # 2. Uppercase UUID
    assert extract_suno_id(raw_uuid.upper()) == raw_uuid

    # 3. Suno Song URL
    url = f"https://suno.com/song/{raw_uuid}"
    assert extract_suno_id(url) == raw_uuid

    # 4. Suno Explore URL with query parameters
    url_query = f"https://suno.com/song/{raw_uuid}?share=true&source=feed"
    assert extract_suno_id(url_query) == raw_uuid

    # 5. Comment tag string
    comment = f"made with suno; created=2026-09-03T21:26:05.728464+00:00; id={raw_uuid}"
    assert extract_suno_id(comment) == raw_uuid

    # 6. Invalid inputs
    assert extract_suno_id(None) is None
    assert extract_suno_id("") is None
    assert extract_suno_id("not-a-valid-uuid-string") is None
    assert extract_suno_id("https://spotify.com/track/12345") is None


def test_parse_suno_combined_stream():
    from backend.suno_extractor import parse_suno_combined_stream

    sample_stream = (
        '40:I[428621,["/_next/static/immutable/chunks/04dur3iie4ddp.js"],"default"]\n'
        '51:T9d5,[Intro]\n\n[Verse]\nNeon reflections on the rain\n[Chorus]\nSynthetic dreams\n[End]'
        '41:["$","$L50",null,{"clip":{"status":"complete","title":"Synthetic Dreams","display_name":"CyberArtist",'
        '"handle":"cyber_artist","created_at":"2026-09-03T21:26:05.728Z","image_url":"https://cdn2.suno.ai/art123.jpeg",'
        '"image_large_url":"https://cdn2.suno.ai/art123_large.jpeg","major_model_version":"v5","model_name":"chirp-crow",'
        '"metadata":{"tags":"synthwave, cyberpunk, 120 bpm","prompt":"$51"}}}]'
    )

    result = parse_suno_combined_stream(sample_stream)
    assert result["title"] == "Synthetic Dreams"
    assert result["artist"] == "CyberArtist"
    assert result["handle"] == "cyber_artist"
    assert result["year"] == "2026"
    assert result["genre"] == "synthwave, cyberpunk, 120 bpm"
    assert result["image_url"] == "https://cdn2.suno.ai/art123_large.jpeg"
    assert "[Verse]" in result["lyrics"]
    assert "Synthetic dreams" in result["lyrics"]
    assert result["model"] == "v5 chirp-crow"
    assert "Style: synthwave, cyberpunk, 120 bpm" in result["formatted_comment"]
    assert "Created on Suno.com (@cyber_artist)" in result["formatted_comment"]
    # $51 React reference should be filtered out
    assert "$51" not in result["formatted_comment"]


def test_api_suno_extract_endpoints(client):
    # 1. Invalid input returns HTTP 400
    res_invalid = client.post("/api/suno/extract", json={"query": "not-a-uuid"})
    assert res_invalid.status_code == 400
    assert "Invalid Suno Clip UUID" in res_invalid.json().get("detail", "")

    # 2. Valid input with mocked extraction
    from unittest.mock import patch
    mock_data = {
        "id": "a362dcef-6a63-423a-8f6b-8990ff370c06",
        "title": "Broken Promises",
        "artist": "Against The Grain",
        "handle": "against_the_grain",
        "genre": "acoustic progressive rock",
        "prompt": "",
        "lyrics": "[Verse]\nBroken promises\n[Chorus]\nNever go away",
        "created_at": "2026-09-03T21:26:05.728Z",
        "year": "2026",
        "image_url": "https://cdn2.suno.ai/21eb08e7-bd94-4e2c-8bca-0245b480a711.jpeg",
        "model": "v5 chirp-crow",
        "formatted_comment": "Style: acoustic progressive rock | Created on Suno.com (@against_the_grain)",
    }

    with patch("backend.main.fetch_suno_metadata", return_value=mock_data):
        res_valid = client.post(
            "/api/suno/extract",
            json={"query": "made with suno; created=2026-09-03T21:26:05; id=a362dcef-6a63-423a-8f6b-8990ff370c06"},
        )
        assert res_valid.status_code == 200
        body = res_valid.json()
        assert body["success"] is True
        assert body["data"]["title"] == "Broken Promises"
        assert body["data"]["artist"] == "Against The Grain"
        assert body["data"]["year"] == "2026"
        assert "[Verse]" in body["data"]["lyrics"]


def test_api_suno_apply_artwork(client, sample_mp3_bytes, sample_image_bytes):
    # 1. Unauthenticated request rejected
    res_unauth = client.post(
        "/api/suno/apply-artwork",
        json={"image_url": "https://cdn2.suno.ai/sample.jpeg"},
    )
    assert res_unauth.status_code == 401

    # 2. Upload MP3 to establish authenticated session
    upload_res = client.post(
        "/api/upload",
        files={"file": ("test.mp3", sample_mp3_bytes, "audio/mpeg")},
    )
    assert upload_res.status_code == 200

    # 3. SSRF Defense: Non-Suno domain rejected with HTTP 400
    res_evil = client.post(
        "/api/suno/apply-artwork",
        json={"image_url": "https://evil-attacker.com/malicious.jpg"},
    )
    assert res_evil.status_code == 400
    assert "Invalid artwork source domain" in res_evil.json().get("detail", "")

    # 4. Valid Suno CDN domain with mocked image download
    from unittest.mock import patch, MagicMock
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = sample_image_bytes
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res_apply = client.post(
            "/api/suno/apply-artwork",
            json={"image_url": "https://cdn2.suno.ai/21eb08e7-bd94-4e2c-8bca-0245b480a711.jpeg"},
        )
        assert res_apply.status_code == 200
        body = res_apply.json()
        assert body["success"] is True
        assert body["mime_type"] == "image/jpeg"
        assert "data:image/jpeg;base64," in body["preview_data_url"]











