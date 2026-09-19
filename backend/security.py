"""Security validation, sanitization, and middleware for MP3MetaFix."""

import re
import io
from pathlib import Path
from typing import Tuple, Optional
from PIL import Image
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from fastapi import HTTPException, status

# Magic byte signatures
MP3_ID3_SIGNATURE = b"ID3"
JPEG_SIGNATURE = b"\xff\xd8\xff"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
RIFF_SIGNATURE = b"RIFF"
WEBP_SIGNATURE = b"WEBP"


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
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Corrupted or invalid image file: {str(e)}",
        )

    # Re-open for conversion/standardization (since verify() closes the stream)
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to process artwork image: {str(e)}",
        )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware enforcing standard secure HTTP response headers."""

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        
        # Generous but secure CSP compatible with modern browsers and Google fonts
        csp_directives = [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline'",
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            "font-src 'self' https://fonts.gstatic.com data:",
            "img-src 'self' data: blob:",
            "media-src 'self' blob: data:",
            "connect-src 'self'",
            "base-uri 'self'",
            "form-action 'self'",
        ]
        response.headers["Content-Security-Policy"] = "; ".join(csp_directives)
        return response
