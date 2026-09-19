"""FastAPI Application entrypoint for MP3MetaFix."""

import os
import io
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
import re
import urllib.parse
from fastapi import (
    FastAPI,
    UploadFile,
    File,
    Form,
    HTTPException,
    status,
    Request,
    BackgroundTasks,
    Depends,
)
from fastapi.responses import (
    FileResponse,
    StreamingResponse,
    JSONResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from backend.config import (
    STATIC_DIR,
    ASSETS_DIR,
    VERSION,
    MAX_UPLOAD_SIZE_BYTES,
    MAX_ARTWORK_SIZE_BYTES,
    TRUST_PROXIES,
    HOST,
    PORT,
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_MAX_AGE,
)
from backend.security import (
    SecurityHeadersMiddleware,
    CSRFProtectionMiddleware,
    validate_mp3_magic_bytes,
    validate_and_normalize_image,
    sanitize_filename,
    create_signed_session_token,
    verify_signed_session_token,
    upload_rate_limiter,
)
from backend.storage import (
    storage_manager,
    start_periodic_cleanup_loop,
)
from backend.metadata_engine import (
    MetadataModel,
    extract_metadata_and_artwork,
    write_metadata_and_artwork,
    get_embedded_artwork_binary,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mp3metafix")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start background cleanup task
    cleanup_task = asyncio.create_task(start_periodic_cleanup_loop(interval_seconds=300))
    logger.info(f"MP3MetaFix v{VERSION} backend started on {HOST}:{PORT}")
    yield
    # Shutdown: Cancel background task
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    logger.info("MP3MetaFix backend shut down.")


app = FastAPI(
    title="MP3MetaFix API",
    description="Server-side MP3 metadata and album art editor",
    version=VERSION,
    lifespan=lifespan,
)

# Trust reverse proxy headers (X-Forwarded-For, X-Forwarded-Proto)
if TRUST_PROXIES:
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=["*"])

# Security & CSRF headers middleware
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CSRFProtectionMiddleware)

# CORS setup (allow same-origin by default, expandable if needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Authentication Dependency ---

def get_current_session_id(request: Request) -> str:
    """Extract and cryptographically verify the session ID from HttpOnly cookie or X-Session-Token header."""
    token = request.cookies.get(SESSION_COOKIE_NAME) or request.headers.get("X-Session-Token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session missing or expired. Please upload an MP3.",
        )
    session_id = verify_signed_session_token(token)
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or forged session token.",
        )
    return session_id


# --- API Routes ---

@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "version": VERSION}


@app.get("/api/version")
async def get_app_version():
    """Version inquiry endpoint."""
    return {"version": VERSION}


@app.post("/api/upload")
async def upload_mp3(request: Request, response: Response, file: UploadFile = File(...)):
    """Upload an MP3 file, validate magic bytes, create authenticated session cookie, and extract metadata."""
    # 1. Rate Limiting Check
    client_ip = upload_rate_limiter.get_client_ip(request)
    if not upload_rate_limiter.is_allowed(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many upload requests. Please slow down.",
        )

    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No file provided")

    # 2. Storage Quota Check
    if not storage_manager.ensure_storage_available(required_bytes=10 * 1024 * 1024):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server temporary storage quota exceeded. Please try again later.",
        )

    # Read initial chunk to validate magic bytes without storing entire file in memory
    header_chunk = await file.read(8192)
    if not validate_mp3_magic_bytes(header_chunk):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid audio file. The file is not a valid MP3 stream.",
        )

    # Create storage session
    session_id, audio_path = storage_manager.create_session(file.filename)

    # Stream the file to disk enforcing max size
    total_bytes = len(header_chunk)
    try:
        with open(audio_path, "wb") as f:
            f.write(header_chunk)
            while chunk := await file.read(1024 * 1024):  # 1MB chunks
                total_bytes += len(chunk)
                if total_bytes > MAX_UPLOAD_SIZE_BYTES:
                    storage_manager.cleanup_session(session_id)
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"File exceeds maximum allowed size ({MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)}MB)",
                    )
                f.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        storage_manager.cleanup_session(session_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save uploaded file: {str(e)}",
        )

    # Extract metadata & artwork
    try:
        parsed = extract_metadata_and_artwork(audio_path)
    except Exception as e:
        storage_manager.cleanup_session(session_id)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not parse ID3 tags: {str(e)}",
        )

    session_info = storage_manager.get_session_info(session_id) or {}

    # Set secure HttpOnly session cookie
    token = create_signed_session_token(session_id)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
    )

    return {
        "success": True,
        "original_filename": session_info.get("original_filename", file.filename),
        "metadata": parsed["metadata"],
        "audio_info": parsed["audio_info"],
        "artwork": parsed["artwork"],
    }


@app.get("/api/artwork")
async def get_artwork(session_id: str = Depends(get_current_session_id)):
    """Serve embedded or uploaded artwork binary for the authenticated session."""
    sdir = storage_manager.get_session_dir(session_id)
    if not sdir:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    # Check for newly uploaded artwork in session first
    temp_art = sdir / "artwork_pending.bin"
    meta_art = sdir / "artwork_pending_mime.txt"
    if temp_art.is_file() and meta_art.is_file():
        mime = meta_art.read_text().strip()
        return FileResponse(temp_art, media_type=mime)

    # Otherwise read from the MP3
    audio_path = storage_manager.get_audio_path(session_id)
    if not audio_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audio file not found")

    art_data = get_embedded_artwork_binary(audio_path)
    if not art_data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No artwork found in audio")

    img_bytes, mime = art_data
    return Response(content=img_bytes, media_type=mime)


@app.post("/api/artwork")
async def upload_artwork(
    image: UploadFile = File(...),
    session_id: str = Depends(get_current_session_id),
):
    """Upload and stage new album art for the authenticated session."""
    sdir = storage_manager.get_session_dir(session_id)
    if not sdir:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    raw_bytes = await image.read()
    clean_bytes, mime_type = validate_and_normalize_image(raw_bytes, MAX_ARTWORK_SIZE_BYTES)

    # Save to staging files in session
    temp_art = sdir / "artwork_pending.bin"
    meta_art = sdir / "artwork_pending_mime.txt"
    temp_art.write_bytes(clean_bytes)
    meta_art.write_text(mime_type)

    import base64
    preview_url = f"data:{mime_type};base64,{base64.b64encode(clean_bytes).decode('ascii')}"

    return {
        "success": True,
        "mime_type": mime_type,
        "size_bytes": len(clean_bytes),
        "preview_data_url": preview_url,
    }


@app.delete("/api/artwork")
async def remove_artwork_staging(session_id: str = Depends(get_current_session_id)):
    """Mark artwork for removal in the authenticated session."""
    sdir = storage_manager.get_session_dir(session_id)
    if not sdir:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    # Clear staging files and write removal marker
    temp_art = sdir / "artwork_pending.bin"
    meta_art = sdir / "artwork_pending_mime.txt"
    remove_marker = sdir / "artwork_remove.flag"

    if temp_art.exists():
        temp_art.unlink()
    if meta_art.exists():
        meta_art.unlink()

    remove_marker.write_text("1")
    return {"success": True, "artwork_removed": True}


@app.post("/api/save")
async def save_metadata(
    meta: MetadataModel,
    session_id: str = Depends(get_current_session_id),
):
    """Commit edited metadata and staged artwork to the MP3 file for the authenticated session."""
    audio_path = storage_manager.get_audio_path(session_id)
    sdir = storage_manager.get_session_dir(session_id)
    if not audio_path or not sdir:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    # Check for staged new artwork or explicit removal
    new_art_bytes = None
    new_art_mime = None
    temp_art = sdir / "artwork_pending.bin"
    meta_art = sdir / "artwork_pending_mime.txt"
    remove_marker = sdir / "artwork_remove.flag"

    if meta.remove_artwork or remove_marker.exists():
        meta.remove_artwork = True
    elif temp_art.is_file() and meta_art.is_file():
        new_art_bytes = temp_art.read_bytes()
        new_art_mime = meta_art.read_text().strip()

    try:
        write_metadata_and_artwork(
            file_path=audio_path,
            meta=meta,
            new_artwork_bytes=new_art_bytes,
            new_artwork_mime=new_art_mime,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to write ID3 tags: {str(e)}",
        )

    # Clean up artwork staging flags
    if temp_art.exists():
        temp_art.unlink()
    if meta_art.exists():
        meta_art.unlink()
    if remove_marker.exists():
        remove_marker.unlink()

    # Determine custom/sanitized output filename
    session_info = storage_manager.get_session_info(session_id) or {}
    orig_name = session_info.get("original_filename", "track.mp3")

    target_name = orig_name
    if meta.custom_filename:
        target_name = sanitize_filename(meta.custom_filename)
    elif meta.artist and meta.title:
        target_name = sanitize_filename(f"{meta.artist} - {meta.title}.mp3")

    storage_manager.update_session_info(session_id, {"target_filename": target_name})

    # Return refreshed metadata info
    refreshed = extract_metadata_and_artwork(audio_path)
    return {
        "success": True,
        "target_filename": target_name,
        "metadata": refreshed["metadata"],
        "artwork": refreshed["artwork"],
    }


@app.get("/api/download")
@app.get("/api/download/{filename:path}")
async def download_mp3(
    background_tasks: BackgroundTasks,
    filename: Optional[str] = None,
    cleanup_after: bool = False,
    session_id: str = Depends(get_current_session_id),
):
    """Download the modified MP3 file with clean Content-Disposition headers for the authenticated session."""
    audio_path = storage_manager.get_audio_path(session_id)
    session_info = storage_manager.get_session_info(session_id)
    if not audio_path or not session_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    download_name = filename or session_info.get("target_filename") or session_info.get("original_filename", "track.mp3")
    clean_name = sanitize_filename(download_name)
    encoded_name = urllib.parse.quote(clean_name, safe="")
    ascii_name = re.sub(r'[^\x20-\x7e]', '_', clean_name).replace('"', '')

    if cleanup_after:
        background_tasks.add_task(storage_manager.cleanup_session, session_id)

    headers = {
        "Content-Disposition": f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded_name}',
        "Access-Control-Expose-Headers": "Content-Disposition",
    }

    return FileResponse(
        path=audio_path,
        media_type="audio/mpeg",
        filename=clean_name,
        headers=headers,
    )


@app.get("/api/stream")
async def stream_audio(
    request: Request,
    session_id: str = Depends(get_current_session_id),
):
    """Stream audio with HTTP 206 Partial Content range support for playback preview for the authenticated session."""
    audio_path = storage_manager.get_audio_path(session_id)
    if not audio_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audio not found")

    file_size = audio_path.stat().st_size
    range_header = request.headers.get("range")

    if not range_header:
        return FileResponse(audio_path, media_type="audio/mpeg")

    # Range parsing
    try:
        range_val = range_header.replace("bytes=", "")
        parts = range_val.split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1
        start = max(0, start)
        end = min(file_size - 1, end)
        content_length = end - start + 1
    except Exception:
        return FileResponse(audio_path, media_type="audio/mpeg")

    def file_iterator():
        with open(audio_path, "rb") as f:
            f.seek(start)
            remaining = content_length
            chunk_size = 64 * 1024
            while remaining > 0:
                read_len = min(chunk_size, remaining)
                data = f.read(read_len)
                if not data:
                    break
                remaining -= len(data)
                yield data

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Type": "audio/mpeg",
    }
    return StreamingResponse(file_iterator(), status_code=206, headers=headers)


@app.delete("/api/session")
async def delete_session(
    response: Response,
    session_id: str = Depends(get_current_session_id),
):
    """Explicitly terminate and purge a session, clearing the session cookie."""
    success = storage_manager.cleanup_session(session_id)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return {"success": success}



# Serve static assets and web frontend
if ASSETS_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")

if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="frontend")
