"""Security validation, sanitization, and middleware for MP3MetaFix."""

import re
import io
import hmac
import hashlib
import uuid
from pathlib import Path
from typing import Tuple, Optional
from PIL import Image
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from fastapi import HTTPException, status

import logging
from backend.config import SESSION_SECRET_KEY, SESSION_COOKIE_MAX_AGE, TRUST_PROXIES, TRUSTED_PROXIES
import time

logger = logging.getLogger("mp3metafix.security")

# Magic byte signatures
MP3_ID3_SIGNATURE = b"ID3"
JPEG_SIGNATURE = b"\xff\xd8\xff"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
RIFF_SIGNATURE = b"RIFF"
WEBP_SIGNATURE = b"WEBP"


def create_signed_session_token(session_id: str) -> str:
    """Sign a UUID4 session ID and current UNIX timestamp using HMAC-SHA256."""
    ts = int(time.time())
    payload = f"{session_id}.{ts}"
    sig = hmac.new(
        SESSION_SECRET_KEY.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{sig}"


def verify_signed_session_token(token: str, max_age_seconds: int = SESSION_COOKIE_MAX_AGE) -> Optional[str]:
    """Verify HMAC signature and timestamp expiry. Returns authenticated UUID4 session ID or None."""
    if not token or "." not in token:
        return None
    parts = token.split(".")
    
    # Strictly require time-bounded format: session_id.timestamp.sig
    if len(parts) != 3:
        return None

    session_id, ts_str, sig = parts[0], parts[1], parts[2]
    try:
        ts = int(ts_str)
        now = int(time.time())
        # Enforce timestamp freshness (not in future by more than 60s, and not expired)
        if ts > now + 60 or (now - ts) > max_age_seconds:
            return None
    except ValueError:
        return None

    # Validate UUID4 structure
    try:
        uuid_obj = uuid.UUID(session_id, version=4)
        if str(uuid_obj) != session_id:
            return None
    except (ValueError, TypeError, AttributeError):
        return None

    expected_sig = hmac.new(
        SESSION_SECRET_KEY.encode("utf-8"),
        f"{session_id}.{ts_str}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if hmac.compare_digest(sig, expected_sig):
        return session_id
    return None



def get_storage_dir_name(session_id: str) -> str:
    """Generate a one-way deterministic SHA-256 hash for the isolated storage directory.
    This decouples physical directory names on disk from both session IDs and client tokens."""
    return hashlib.sha256(f"storage_dir:{SESSION_SECRET_KEY}:{session_id}".encode("utf-8")).hexdigest()[:32]


def sanitize_filename(filename: str, default: str = "track.mp3") -> str:
    """Sanitize client-supplied filenames to prevent path traversal and shell injection."""
    if not filename:
        return default
    
    # Strip path separators
    name = Path(filename).name
    
    # Remove null bytes and control chars
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    
    # Replace dangerous characters while keeping unicode letters, numbers, spaces, dots, dashes, underscores
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    
    # Avoid leading dots or spaces
    name = name.strip(". ")
    
    # Fallback if empty
    if not name:
        return default
    
    # Ensure .mp3 extension
    if not name.lower().endswith(".mp3"):
        name = f"{name}.mp3"
        
    return name[:255]


def validate_mp3_magic_bytes(header_bytes: bytes) -> bool:
    """Check if header starts with ID3 header or MPEG sync word (0xFFEx or 0xFFFx)."""
    if len(header_bytes) < 3:
        return False
    
    # Check ID3v2 container header
    if header_bytes.startswith(MP3_ID3_SIGNATURE):
        return True
    
    # Check MPEG-1 / MPEG-2 Audio Layer III sync word (11 consecutive 1 bits: 0xFF followed by 0b111xxxxx)
    if len(header_bytes) >= 2:
        b0, b1 = header_bytes[0], header_bytes[1]
        if b0 == 0xFF and (b1 & 0xE0) == 0xE0:
            return True
            
    # Also scan first 8KB for ID3 or sync frame in case of stream pre-padding
    for i in range(min(len(header_bytes) - 2, 8192)):
        if header_bytes[i:i+3] == MP3_ID3_SIGNATURE:
            return True
        if header_bytes[i] == 0xFF and (header_bytes[i+1] & 0xE0) == 0xE0:
            return True
            
    return False


# Pillow Decompression Bomb & Resource Protection
# Cap image pixels to ~10 million (e.g., 3162x3162) to prevent memory exhaustion DoS
Image.MAX_IMAGE_PIXELS = 10_000_000
MAX_COVER_ART_DIMENSION = 4096


def validate_and_normalize_image(image_bytes: bytes, max_bytes: int) -> Tuple[bytes, str]:
    """Validate image magic bytes, format, and dimensions using Pillow. Returns (clean_bytes, mime_type)."""
    if len(image_bytes) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Artwork exceeds maximum allowed size ({max_bytes // (1024 * 1024)}MB)",
        )

    # Magic byte quick check
    is_valid_magic = (
        image_bytes.startswith(JPEG_SIGNATURE)
        or image_bytes.startswith(PNG_SIGNATURE)
        or (image_bytes.startswith(RIFF_SIGNATURE) and len(image_bytes) >= 12 and image_bytes[8:12] == WEBP_SIGNATURE)
    )
    if not is_valid_magic:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid image format. Only JPEG, PNG, and WebP images are supported.",
        )

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img.verify()
    except Image.DecompressionBombError:
        logger.warning("Image decompression bomb rejected: exceeds pixel threshold")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Decompression bomb detected: Image exceeds maximum pixel limit.",
        )
    except Exception as e:
        logger.warning("Corrupted image verification failure: %s", type(e).__name__)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Corrupted or invalid image file. Please upload a valid JPEG, PNG, or WebP image.",
        )

    # Re-open for conversion/standardization and dimension validation
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            if img.width > MAX_COVER_ART_DIMENSION or img.height > MAX_COVER_ART_DIMENSION:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Image dimensions ({img.width}x{img.height}) exceed maximum allowed ({MAX_COVER_ART_DIMENSION}x{MAX_COVER_ART_DIMENSION})",
                )

            fmt = (img.format or "JPEG").upper()
            if fmt in ("JPEG", "JPG"):
                mime_type = "image/jpeg"
                # If RGB/L mode, keep as JPEG
                if img.mode in ("RGB", "L"):
                    out_io = io.BytesIO()
                    img.save(out_io, format="JPEG", quality=92, optimize=True)
                    return out_io.getvalue(), mime_type
                else:
                    # Convert palette/RGBA to RGB JPEG
                    rgb_img = img.convert("RGB")
                    out_io = io.BytesIO()
                    rgb_img.save(out_io, format="JPEG", quality=92, optimize=True)
                    return out_io.getvalue(), mime_type
            elif fmt == "PNG":
                mime_type = "image/png"
                out_io = io.BytesIO()
                img.save(out_io, format="PNG", optimize=True)
                return out_io.getvalue(), mime_type
            elif fmt == "WEBP":
                # Convert WebP to JPEG for maximum ID3 compatibility across hardware/car players
                rgb_img = img.convert("RGB")
                out_io = io.BytesIO()
                rgb_img.save(out_io, format="JPEG", quality=92, optimize=True)
                return out_io.getvalue(), "image/jpeg"
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unsupported image format '{fmt}'",
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Image processing failure: %s", type(e).__name__)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to process artwork image. Please upload a valid JPEG, PNG, or WebP image.",
        )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware enforcing standard secure HTTP response headers."""

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        
        # Generous but secure CSP compatible with modern browsers, Cloudflare analytics, and Google fonts
        csp_directives = [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline' https://static.cloudflareinsights.com",
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            "font-src 'self' https://fonts.gstatic.com data:",
            "img-src 'self' data: blob: https://*.githubusercontent.com",
            "media-src 'self' blob: data:",
            "connect-src 'self' https://cloudflareinsights.com https://api.github.com",
            "base-uri 'self'",
            "form-action 'self'",
        ]
        response.headers["Content-Security-Policy"] = "; ".join(csp_directives)
        return response


class CSRFProtectionMiddleware(BaseHTTPMiddleware):
    """Middleware preventing Cross-Site Request Forgery (CSRF) on state-changing endpoints."""

    async def dispatch(self, request: Request, call_next):
        # Only inspect state-mutating requests
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            # Check Sec-Fetch-Site (supported by modern browsers)
            fetch_site = request.headers.get("sec-fetch-site", "").lower()
            if fetch_site == "cross-site":
                from starlette.responses import JSONResponse
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "Cross-site request forgery protection blocked this request."},
                )

            # Check Origin header if present
            origin = request.headers.get("origin")
            if origin:
                origin_host = origin.split("://")[-1].split("/")[0].lower()
                request_host = request.headers.get("host", "").lower()
                if origin_host and request_host and origin_host != request_host:
                    dev_hosts = {"localhost", "127.0.0.1"}
                    o_clean = origin_host.split(":")[0]
                    r_clean = request_host.split(":")[0]
                    if not (o_clean in dev_hosts and r_clean in dev_hosts):
                        from starlette.responses import JSONResponse
                        return JSONResponse(
                            status_code=status.HTTP_403_FORBIDDEN,
                            content={"detail": "Untrusted Origin blocked."},
                        )

        return await call_next(request)


import ipaddress


def is_trusted_proxy_ip(ip_str: str) -> bool:
    """Determine whether an IP address matches explicitly configured trusted reverse proxies."""
    if not ip_str:
        return False
    try:
        ip = ipaddress.ip_address(ip_str)
        for trusted in TRUSTED_PROXIES:
            try:
                net = ipaddress.ip_network(trusted, strict=False)
                if ip in net:
                    return True
            except ValueError:
                continue
        return False
    except ValueError:
        return False


class InMemoryRateLimiter:
    """Sliding-window in-memory rate limiter per IP address with auto-purging and proxy anti-spoofing."""

    def __init__(self, max_requests: int = 25, window_seconds: int = 60, max_tracked_ips: int = 5000):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.max_tracked_ips = max_tracked_ips
        import time
        from collections import defaultdict
        self.history = defaultdict(list)
        self._last_purge_time = time.time()

    def _purge_stale(self, now: float):
        """Purge empty and expired IP entries to prevent heap memory exhaustion over time."""
        window_start = now - self.window_seconds
        stale_ips = []
        for ip, timestamps in self.history.items():
            valid_times = [t for t in timestamps if t > window_start]
            if not valid_times:
                stale_ips.append(ip)
            else:
                self.history[ip] = valid_times

        for ip in stale_ips:
            del self.history[ip]

        # If still over max tracked IPs, clear oldest half
        if len(self.history) > self.max_tracked_ips:
            sorted_ips = sorted(self.history.items(), key=lambda item: max(item[1]) if item[1] else 0)
            to_remove = len(sorted_ips) - (self.max_tracked_ips // 2)
            for ip, _ in sorted_ips[:to_remove]:
                self.history.pop(ip, None)

        self._last_purge_time = now

    def is_allowed(self, client_ip: str) -> bool:
        import time
        now = time.time()

        # Periodic cleanup every 60 seconds or when storage exceeds max_tracked_ips
        if now - self._last_purge_time > 60 or len(self.history) > self.max_tracked_ips:
            self._purge_stale(now)

        window_start = now - self.window_seconds
        req_times = [t for t in self.history[client_ip] if t > window_start]

        if len(req_times) >= self.max_requests:
            self.history[client_ip] = req_times
            return False

        req_times.append(now)
        self.history[client_ip] = req_times
        return True

    def get_client_ip(self, request: Request) -> str:
        """Securely extract client IP from request.client.host.

        When TRUST_PROXIES is enabled, ProxyHeadersMiddleware at the ASGI layer validates
        the socket peer against TRUSTED_PROXIES and resolves the client IP from X-Forwarded-For.
        When TRUST_PROXIES is disabled or the socket peer is untrusted, request.client.host
        remains the raw socket peer, preventing header spoofing.
        """
        if request.client and request.client.host:
            return request.client.host
        return "127.0.0.1"


upload_rate_limiter = InMemoryRateLimiter(max_requests=25, window_seconds=60)


