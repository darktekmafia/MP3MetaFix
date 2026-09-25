import os
import io
import time
import shutil
import asyncio
import base64
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, Dict, Any
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
    APP_DIR,
    MANAGER_DIR,
    ADMIN_DIR,
    DOCS_DIR,
    DOCS_STATIC_DIR,
    ASSETS_DIR,
    DATA_DIR,
    VERSION,
    MAX_UPLOAD_SIZE_BYTES,
    MAX_ARTWORK_SIZE_BYTES,
    TRUST_PROXIES,
    TRUSTED_PROXIES,
    PROXY_HOST,
    HOST,
    PORT,
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_MAX_AGE,
    SESSION_TTL_MINUTES,
    MAX_UPLOAD_SIZE_MB,
    MAX_GLOBAL_TEMP_STORAGE_MB,
)
from backend.security import (
    SecurityHeadersMiddleware,
    CSRFProtectionMiddleware,
    validate_and_normalize_image,
    sanitize_filename,
    create_signed_session_token,
    verify_signed_session_token,
    upload_rate_limiter,
)
from pydantic import BaseModel, Field

from backend.audio_formats import audio_format, matches_audio_header

from backend.storage import (
    storage_manager,
    start_periodic_cleanup_loop,
)
from backend.metadata_engine import (
    MetadataModel,
    MetadataValidationError,
    extract_metadata_and_artwork,
    write_metadata_and_artwork,
    get_embedded_artwork_binary,
)
from backend.suno_extractor import (
    fetch_suno_metadata,
    extract_suno_id,
)
from backend.config import ALLOW_WEB_UPDATES

from backend.updater import (
    get_system_version_info,
    check_github_updates,
    stream_install_update,
    start_install_update,
)
from backend.auth import (
    auth_manager,
    AUTH_COOKIE_NAME,
    AUTH_COOKIE_MAX_AGE,
    login_rate_limiter,
    SetupRequest,
    LoginRequest,
    ChangePasswordRequest,
    SettingsUpdateRequest,
    create_auth_token,
    verify_auth_token,
    AuthStoreError,
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
    description="Server-side MP3, M4A, and WAV metadata and artwork editor",
    version=VERSION,
    lifespan=lifespan,
)

@app.exception_handler(AuthStoreError)
async def account_store_error(request, exc):
    from starlette.responses import JSONResponse
    return JSONResponse({'detail': 'Account storage unavailable. Local administrator recovery is required.'}, status_code=503)


# Trust reverse proxy headers (X-Forwarded-For, X-Forwarded-Proto) only from verified proxies
if TRUST_PROXIES:
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=TRUSTED_PROXIES)

# Security & CSRF headers middleware
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CSRFProtectionMiddleware)

# CORS setup (restrict to same-origin / local hosts with credentials support)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
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
            detail="Session missing or expired. Please upload an audio file.",
        )
    session_id = verify_signed_session_token(token)
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or forged session token.",
        )
    return session_id


# --- Account & Access Control Dependencies ---

def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    """Extract and verify authenticated user from HttpOnly auth cookie or Authorization header."""
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        elif request.headers.get("X-Auth-Token"):
            token = request.headers.get("X-Auth-Token")

    if not token:
        return None

    user_id = auth_manager.verify_token(token)
    if not user_id:
        return None

    return auth_manager.get_user_by_id(user_id)


def require_authenticated_user(request: Request) -> Dict[str, Any]:
    """Dependency that enforces a valid logged-in user account."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please log in.",
        )
    return user


def require_admin(request: Request) -> Dict[str, Any]:
    """Dependency enforcing administrator privileges."""
    user = require_authenticated_user(request)
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privilege required.",
        )
    return user


def enforce_access_policy(request: Request) -> Optional[Dict[str, Any]]:
    """Enforce access control on file-editing endpoints based on Guest Mode settings."""
    user = get_current_user(request)
    if user:
        return user
    if auth_manager.is_guest_mode_enabled():
        return None
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required. Guest mode is disabled.",
    )


# --- Authentication & Settings API Routes ---

@app.get("/api/auth/status")
async def get_auth_status(request: Request):
    """Return system setup state, current session identity, and guest mode policy."""
    setup_req = auth_manager.is_setup_required()
    user = get_current_user(request)
    return {
        "setup_required": setup_req,
        "initialized": not setup_req,
        "authenticated": user is not None,
        "username": user["username"] if user else None,
        "role": user["role"] if user else None,
        "user": {"id": user["id"], "username": user["username"], "role": user["role"]} if user else None,
        "guest_mode": auth_manager.is_guest_mode_enabled(),
        "is_guest": user is None and auth_manager.is_guest_mode_enabled(),
        "suno_integration_enabled": auth_manager.is_suno_enabled(),
    }


@app.post("/api/auth/setup")
async def initial_setup(req: SetupRequest, request: Request, response: Response):
    """First-time administrator account creation."""
    if not auth_manager.is_setup_required():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Administrator account is already configured.",
        )
    try:
        admin_user = auth_manager.create_initial_admin(req.username, req.password)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    token = auth_manager.issue_token(admin_user["id"])
    is_https = (request.url.scheme == "https")
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        max_age=AUTH_COOKIE_MAX_AGE,
        httponly=True,
        secure=is_https,
        samesite="lax",
        path="/",
    )
    return {"success": True, "user": admin_user}


@app.post("/api/auth/login")
async def login(req: LoginRequest, request: Request, response: Response):
    """Authenticate with username and password, setting HttpOnly auth cookie."""
    client_ip = upload_rate_limiter.get_client_ip(request)
    if login_rate_limiter.is_blocked(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Please wait 5 minutes before trying again.",
        )

    user = auth_manager.authenticate(req.username, req.password)
    if not user:
        login_rate_limiter.record_failure(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    login_rate_limiter.record_success(client_ip)
    token = auth_manager.issue_token(user["id"])
    is_https = (request.url.scheme == "https")
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        max_age=AUTH_COOKIE_MAX_AGE,
        httponly=True,
        secure=is_https,
        samesite="lax",
        path="/",
    )
    return {"success": True, "user": user}


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    """Log out of the account session and clear auth cookie."""
    user = get_current_user(request)
    if user:
        auth_manager.revoke_sessions(user["id"])
    is_https = (request.url.scheme == "https")
    response.delete_cookie(key=AUTH_COOKIE_NAME, path="/", secure=is_https, httponly=True)
    return {"success": True}


@app.post("/api/auth/change-password")
async def change_password(req: ChangePasswordRequest, user: Dict[str, Any] = Depends(require_authenticated_user)):
    """Change password for the currently logged-in account."""
    try:
        auth_manager.change_password(user["id"], req.current_password, req.new_password)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return {"success": True, "message": "Password updated. Sign in again on each device."}


@app.get("/api/settings")
async def get_settings(user: Dict[str, Any] = Depends(require_authenticated_user)):
    """Get active system settings."""
    settings = auth_manager.get_settings()
    guest_enabled = bool(settings.get("guest_mode_enabled", settings.get("guest_mode", False)))
    storage_quota = settings.get("max_global_storage_mb", settings.get("max_temp_storage_mb", MAX_GLOBAL_TEMP_STORAGE_MB))
    return {
        "guest_mode_enabled": guest_enabled,
        "guest_mode": guest_enabled,
        "suno_integration_enabled": auth_manager.is_suno_enabled(),
        "session_ttl_minutes": settings.get("session_ttl_minutes", SESSION_TTL_MINUTES),
        "max_upload_size_mb": settings.get("max_upload_size_mb", MAX_UPLOAD_SIZE_MB),
        "max_global_storage_mb": storage_quota,
        "max_temp_storage_mb": storage_quota,
        "max_sessions": settings.get("max_sessions", 10),
        "quick_settings_pinned": settings.get("quick_settings_pinned", ["guest_mode_enabled", "max_sessions", "max_global_storage_mb"]),
        "version": VERSION,
        "is_admin": user.get("role") == "admin",
    }


@app.post("/api/settings")
async def update_settings(req: SettingsUpdateRequest, user: Dict[str, Any] = Depends(require_admin)):
    """Update system settings (Admin only)."""
    updates = req.model_dump(exclude_unset=True)
    updated = auth_manager.update_settings(updates)
    return {"success": True, "settings": updated}


# --- API Routes ---

@app.api_route("/api/health", methods=["GET", "HEAD"])
async def health_check():
    """Health check endpoint supporting both GET and HEAD requests."""
    return {"status": "ok", "version": VERSION}


SERVER_START_TIME = time.time()


def get_system_telemetry() -> Dict[str, Any]:
    """Gather non-sensitive system resources and application metrics."""
    # 1. CPU
    cpu_count = os.cpu_count() or 1
    try:
        load_1, load_5, load_15 = os.getloadavg()
    except Exception:
        load_1, load_5, load_15 = (0.0, 0.0, 0.0)
    cpu_percent = round(min(100.0, (load_1 / cpu_count) * 100), 1)

    # 2. RAM (Linux /proc/meminfo or sysconf)
    ram_total_bytes = 0
    ram_available_bytes = 0
    meminfo_path = Path("/proc/meminfo")
    if meminfo_path.exists():
        try:
            with open(meminfo_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        ram_total_bytes = int(line.split()[1]) * 1024
                    elif line.startswith("MemAvailable:"):
                        ram_available_bytes = int(line.split()[1]) * 1024
        except Exception:
            pass
    if ram_total_bytes == 0:
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            ram_total_bytes = pages * page_size
            ram_available_bytes = ram_total_bytes // 2
        except Exception:
            ram_total_bytes = 1024 * 1024 * 1024
            ram_available_bytes = ram_total_bytes // 2

    ram_used_bytes = max(0, ram_total_bytes - ram_available_bytes)
    ram_used_percent = round((ram_used_bytes / ram_total_bytes) * 100, 1) if ram_total_bytes > 0 else 0.0

    # 3. Disk
    try:
        disk_usage = shutil.disk_usage(DATA_DIR)
        disk_total_bytes = disk_usage.total
        disk_used_bytes = disk_usage.used
        disk_free_bytes = disk_usage.free
        disk_used_percent = round((disk_used_bytes / disk_total_bytes) * 100, 1) if disk_total_bytes > 0 else 0.0
    except Exception:
        disk_total_bytes = disk_used_bytes = disk_free_bytes = 0
        disk_used_percent = 0.0

    # 4. Storage & sessions
    storage_stats = storage_manager.get_session_stats()

    # 5. Uptime & Server info
    uptime_seconds = int(time.time() - SERVER_START_TIME)

    return {
        "status": "ok",
        "version": VERSION,
        "uptime_seconds": uptime_seconds,
        "cpu": {
            "cores": cpu_count,
            "load_1m": round(load_1, 2),
            "load_5m": round(load_5, 2),
            "load_15m": round(load_15, 2),
            "estimated_percent": cpu_percent,
        },
        "memory": {
            "total_mb": round(ram_total_bytes / (1024 * 1024), 1),
            "used_mb": round(ram_used_bytes / (1024 * 1024), 1),
            "available_mb": round(ram_available_bytes / (1024 * 1024), 1),
            "used_percent": ram_used_percent,
        },
        "disk": {
            "total_gb": round(disk_total_bytes / (1024 ** 3), 2),
            "used_gb": round(disk_used_bytes / (1024 ** 3), 2),
            "free_gb": round(disk_free_bytes / (1024 ** 3), 2),
            "used_percent": disk_used_percent,
        },
        "app_storage": storage_stats,
        "network": {
            "host": HOST,
            "port": PORT,
            "trust_proxies": TRUST_PROXIES,
            "proxy_host": PROXY_HOST,
        },
    }


@app.api_route("/api/system/stats", methods=["GET", "HEAD"])
async def get_system_stats(user: Dict[str, Any] = Depends(require_admin)):
    """System telemetry and resource diagnostics endpoint."""
    return get_system_telemetry()


@app.api_route("/api/version", methods=["GET", "HEAD"])
async def get_app_version():
    """Version and system inquiry endpoint returning detailed environment metadata."""
    return get_system_version_info()


DOCS_REGISTRY: Dict[str, Dict[str, Any]] = {
    "app": {
        "id": "app",
        "title": "MP3MetaFix Editor",
        "category": "Workspaces",
        "icon": "music",
        "path": DOCS_DIR / "app" / "README.md",
        "summary": "Mobile-first single-track editor, retina waveforms, cover art studio, and Suno AI extraction.",
    },
    "manager": {
        "id": "manager",
        "title": "MP3MetaManager",
        "category": "Workspaces",
        "icon": "folder",
        "path": DOCS_DIR / "manager" / "README.md",
        "summary": "Desktop batch editor, spreadsheet controls, stem pack bundler, and universal ID3 frame manager.",
    },
    "projects": {
        "id": "projects",
        "title": "MP3Projects Studio",
        "category": "Workspaces",
        "icon": "disc",
        "path": DOCS_DIR / "projects" / "README.md",
        "summary": "Multi-track EP/LP bundling, album sequencing, unified artwork, and persistent sessions.",
    },
    "admin": {
        "id": "admin",
        "title": "Admin Control Center",
        "category": "Workspaces",
        "icon": "shield",
        "path": DOCS_DIR / "admin" / "README.md",
        "summary": "System telemetry, access controls, Quick Settings pinning, and authorized updates.",
    },
    "deployment": {
        "id": "deployment",
        "title": "Production Deployment",
        "category": "Deployment & Operations",
        "icon": "server",
        "path": DOCS_DIR / "DEPLOYMENT.md",
        "summary": "Linux systemd service, Proxmox LXC setup, reverse proxy templates, and maintenance CLI.",
    },
    "account_migration": {
        "id": "account_migration",
        "title": "Service Account Migration",
        "category": "Deployment & Operations",
        "icon": "lock",
        "path": DOCS_DIR / "ACCOUNT_MIGRATION.md",
        "summary": "Dedicated unprivileged mp3metafix service user migration with 0700 filesystem isolation.",
    },
    "architecture": {
        "id": "architecture",
        "title": "System Architecture",
        "category": "Security & Architecture",
        "icon": "cpu",
        "path": DOCS_DIR / "ARCHITECTURE.md",
        "summary": "FastAPI engine, native Mutagen audio pipelines, and decoupled cryptographic storage.",
    },
    "security": {
        "id": "security",
        "title": "Security Hardening",
        "category": "Security & Architecture",
        "icon": "shield-check",
        "path": DOCS_DIR / "SECURITY_HARDENING.md",
        "summary": "Cryptographic session tokens, POSIX permissions, anti-spoofing rate limits, and defenses.",
    },
    "workflow": {
        "id": "workflow",
        "title": "Development Workflow",
        "category": "Reference",
        "icon": "code",
        "path": DOCS_DIR / "development_workflow.md",
        "summary": "Engineering standards, staged git workflow, release gates, and testing mandates.",
    },
    "suno_tos": {
        "id": "suno_tos",
        "title": "Suno TOS Compliance",
        "category": "Reference",
        "icon": "file-text",
        "path": DOCS_DIR / "SUNO_TOS_COMPLIANCE.md",
        "summary": "Permissible metadata ingestion, watermark preservation, and third-party terms compliance.",
    },
}


@app.get("/api/docs/list")
async def list_docs():
    """Return available documentation articles and categories."""
    sections = []
    for doc_id, doc in DOCS_REGISTRY.items():
        sections.append({
            "id": doc["id"],
            "title": doc["title"],
            "category": doc["category"],
            "icon": doc.get("icon", "file-text"),
            "summary": doc.get("summary", ""),
            "available": doc["path"].is_file(),
        })
    return {"sections": sections}


@app.get("/api/docs/{doc_id}")
async def get_doc_content(doc_id: str):
    """Return the raw markdown content of a specific documentation article."""
    if doc_id not in DOCS_REGISTRY:
        raise HTTPException(404, "Documentation topic not found")
    doc_meta = DOCS_REGISTRY[doc_id]
    doc_path = doc_meta["path"]
    if not doc_path.is_file():
        raise HTTPException(404, "Documentation file is not available on host")
    
    try:
        content = doc_path.read_text(encoding="utf-8")
        return {
            "id": doc_meta["id"],
            "title": doc_meta["title"],
            "category": doc_meta["category"],
            "icon": doc_meta.get("icon", "file-text"),
            "content": content,
        }
    except Exception as e:
        logger.error(f"Failed to read documentation file {doc_path}: {e}")
        raise HTTPException(500, "Could not load documentation content")


@app.get("/api/updates/check")
async def check_updates(force: bool = False, user: Dict[str, Any] = Depends(require_admin)):
    """Check GitHub repository for new releases and changelog."""
    result = dict(await check_github_updates(force_refresh=force))
    result["web_updates_enabled"] = ALLOW_WEB_UPDATES
    return result


@app.post("/api/updates/apply")
async def apply_update(request: Request, user: Dict[str, Any] = Depends(require_admin)):
    """Stream in-app update installation output as Server-Sent Events (SSE). Admin only."""
    if not ALLOW_WEB_UPDATES:
        raise HTTPException(403, "Updates for this installation are managed locally by its administrator.")
    return StreamingResponse(
        start_install_update(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/upload")
async def upload_audio(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    _access: Optional[Dict[str, Any]] = Depends(enforce_access_policy),
):
    """Validate an audio container, create an isolated session, and extract metadata."""
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No file provided")

    try:
        descriptor = audio_format(file.filename)
    except ValueError:
        raise HTTPException(status_code=400, detail="Supported audio formats are MP3, M4A, and WAV.")

    # 2. Storage Quota Check
    if not storage_manager.ensure_storage_available(required_bytes=MAX_UPLOAD_SIZE_BYTES + MAX_ARTWORK_SIZE_BYTES):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server temporary storage quota exceeded. Please try again later.",
        )

    # Read initial chunk to validate magic bytes without storing entire file in memory
    header_chunk = await file.read(8192)
    if not matches_audio_header(header_chunk, descriptor["extension"]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid audio file. The content does not match its audio format.",
        )

    # Create storage session
    session_id, audio_path = storage_manager.create_session(file.filename, descriptor["extension"])

    # Stream the file to disk enforcing max size
    total_bytes = len(header_chunk)
    try:
        if total_bytes > MAX_UPLOAD_SIZE_BYTES:
            raise HTTPException(status_code=413, detail="File exceeds maximum allowed size.")
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
        storage_manager.cleanup_session(session_id)
        raise
    except Exception as e:
        logger.exception("Upload processing error")
        storage_manager.cleanup_session(session_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save uploaded audio file.",
        )

    # Extract metadata & artwork
    try:
        parsed = extract_metadata_and_artwork(audio_path)
    except Exception as e:
        logger.warning("Failed to parse uploaded audio: %s", type(e).__name__)
        storage_manager.cleanup_session(session_id)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not parse the uploaded audio file.",
        )

    session_info = storage_manager.get_session_info(session_id) or {}

    # Set secure HttpOnly session cookie (enforce secure flag only when trusted request scheme is HTTPS)
    is_https = (request.url.scheme == "https")
    token = create_signed_session_token(session_id)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_COOKIE_MAX_AGE,
        httponly=True,
        secure=is_https,
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
async def get_artwork(
    session_id: str = Depends(get_current_session_id),
    _access: Optional[Dict[str, Any]] = Depends(enforce_access_policy),
):
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

    # Otherwise read from the audio file
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
    _access: Optional[Dict[str, Any]] = Depends(enforce_access_policy),
):
    """Upload and stage new album art for the authenticated session."""
    sdir = storage_manager.get_session_dir(session_id)
    if not sdir:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    raw_bytes = await image.read(MAX_ARTWORK_SIZE_BYTES + 1)
    clean_bytes, mime_type = validate_and_normalize_image(raw_bytes, MAX_ARTWORK_SIZE_BYTES)

    (sdir / "artwork_remove.flag").unlink(missing_ok=True)

    if storage_manager.get_total_temp_size_bytes() + len(clean_bytes) > storage_manager.max_storage_bytes:
        raise HTTPException(507, 'Temporary storage quota exceeded.')

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
async def remove_artwork_staging(
    session_id: str = Depends(get_current_session_id),
    _access: Optional[Dict[str, Any]] = Depends(enforce_access_policy),
):
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


class SunoExtractRequest(BaseModel):
    query: str = Field(..., max_length=1000, description="Suno song URL, share link, comment string, or clip UUID")


class SunoApplyArtworkRequest(BaseModel):
    image_url: str = Field(..., max_length=2000, description="Suno CDN artwork URL")


@app.post("/api/suno/extract")
async def api_suno_extract(
    req: SunoExtractRequest,
    _access: Optional[Dict[str, Any]] = Depends(enforce_access_policy),
):
    """Extract metadata and artwork information from a Suno Clip UUID or song URL."""
    if not auth_manager.is_suno_enabled():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Suno integration is disabled in system settings.",
        )
    try:
        meta = await asyncio.to_thread(fetch_suno_metadata, req.query)
        return {
            "success": True,
            "data": meta,
        }
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as e:
        logger.error(f"Suno extraction unexpected error: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to retrieve information from Suno. Please check the link or try again.",
        )


@app.post("/api/suno/apply-artwork")
async def api_suno_apply_artwork(
    req: SunoApplyArtworkRequest,
    session_id: str = Depends(get_current_session_id),
    _access: Optional[Dict[str, Any]] = Depends(enforce_access_policy),
):
    """Fetches high-res artwork from Suno CDN and stages it in the current editing session."""
    if not auth_manager.is_suno_enabled():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Suno integration is disabled in system settings.",
        )
    sdir = storage_manager.get_session_dir(session_id)
    if not sdir:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    # SSRF protection: only allow Suno CDN hostnames
    parsed = urllib.parse.urlparse(req.image_url)
    allowed_domains = ["cdn1.suno.ai", "cdn2.suno.ai", "cdn-o.suno.com", "images.suno.ai"]
    if not parsed.hostname or not any(parsed.hostname.lower() == d or parsed.hostname.lower().endswith("." + d) for d in allowed_domains):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid artwork source domain.")

    from backend.outbound import fetch_public_bytes
    try:
        img_bytes = await asyncio.to_thread(fetch_public_bytes, req.image_url, set(allowed_domains), MAX_ARTWORK_SIZE_BYTES)
    except Exception:
        raise HTTPException(status_code=502, detail="Could not retrieve artwork from the permitted HTTPS source.") from None

    # Validate and normalize image via Pillow decompression bomb defense
    try:
        clean_bytes, mime_type = validate_and_normalize_image(img_bytes, MAX_ARTWORK_SIZE_BYTES)
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))

    if storage_manager.get_total_temp_size_bytes() + len(clean_bytes) > storage_manager.max_storage_bytes:
        raise HTTPException(507, 'Temporary storage quota exceeded.')

    # Stage artwork in session directory
    temp_art = sdir / "artwork_pending.bin"
    meta_art = sdir / "artwork_pending_mime.txt"
    remove_marker = sdir / "artwork_remove.flag"

    if remove_marker.exists():
        remove_marker.unlink()

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


@app.post("/api/save")
async def save_metadata(
    meta: MetadataModel,
    session_id: str = Depends(get_current_session_id),
    _access: Optional[Dict[str, Any]] = Depends(enforce_access_policy),
):
    """Commit edited metadata and staged artwork to the audio file for the authenticated session."""
    audio_path = storage_manager.get_audio_path(session_id)
    sdir = storage_manager.get_session_dir(session_id)
    if not audio_path or not sdir:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    # The request-wide cross-process writer lease makes this headroom check stable.
    extra = 2 * (audio_path.stat().st_size + MAX_ARTWORK_SIZE_BYTES + 1024 * 1024)
    if storage_manager.get_total_temp_size_bytes() + extra > storage_manager.max_storage_bytes:
        raise HTTPException(507, 'Insufficient temporary storage for an atomic save.')

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
    except MetadataValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("Failed to write audio tags")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to write metadata to the audio file.",
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
    orig_name = session_info.get("original_filename", f"track{audio_path.suffix}")

    target_name = orig_name
    if meta.custom_filename:
        target_name = sanitize_filename(meta.custom_filename, extension=audio_path.suffix)
    elif meta.artist and meta.title:
        target_name = sanitize_filename(f"{meta.artist} - {meta.title}", extension=audio_path.suffix)

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
async def download_audio(
    background_tasks: BackgroundTasks,
    filename: Optional[str] = None,
    cleanup_after: bool = False,
    session_id: str = Depends(get_current_session_id),
    _access: Optional[Dict[str, Any]] = Depends(enforce_access_policy),
):
    """Download the modified audio file with clean Content-Disposition headers for the authenticated session."""
    audio_path = storage_manager.get_audio_path(session_id)
    session_info = storage_manager.get_session_info(session_id)
    if not audio_path or not session_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    download_name = filename or session_info.get("target_filename") or session_info.get("original_filename", f"track{audio_path.suffix}")
    clean_name = sanitize_filename(download_name, extension=audio_path.suffix)
    encoded_name = urllib.parse.quote(clean_name, safe="")
    ascii_name = re.sub(r'[^\x20-\x7e]', '_', clean_name).replace('"', '')

    if cleanup_after:
        raise HTTPException(400, 'Use DELETE /api/session to remove a session after downloading.')

    headers = {
        "Content-Disposition": f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded_name}',
        "Access-Control-Expose-Headers": "Content-Disposition",
    }

    return FileResponse(
        path=audio_path,
        media_type=audio_format(audio_path)["mime_type"],
        filename=clean_name,
        headers=headers,
    )


@app.get("/api/stream")
async def stream_audio(
    request: Request,
    session_id: str = Depends(get_current_session_id),
    _access: Optional[Dict[str, Any]] = Depends(enforce_access_policy),
):
    """Stream audio with deterministic single-range support (HTTP 206 Partial Content).
    
    Notes:
    - Single byte ranges (normal, open-ended, suffix) are supported.
    - Multipart multi-range requests (comma-separated) are deliberately unsupported and return HTTP 416.
    - Unsatisfiable or malformed range requests return HTTP 416 with Content-Range: bytes */{file_size}.
    - Requests without a Range header or with a non-bytes unit return the full representation (HTTP 200).
    """
    audio_path = storage_manager.get_audio_path(session_id)
    if not audio_path or not audio_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audio not found")

    file_size = audio_path.stat().st_size
    range_header = request.headers.get("range")

    def full_iterator():
        with open(audio_path, "rb") as f:
            while chunk := f.read(64 * 1024):
                yield chunk

    # If no Range header or non-bytes unit, serve the full file (HTTP 200)
    if not range_header or not range_header.startswith("bytes="):
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
            "Content-Type": audio_format(audio_path)["mime_type"],
        }
        return StreamingResponse(full_iterator(), status_code=200, headers=headers)

    # If file is empty, no sub-range can be satisfied
    if file_size == 0:
        return Response(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": "bytes */0"},
        )

    raw_ranges = range_header[6:].strip()
    if not raw_ranges:
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
            "Content-Type": audio_format(audio_path)["mime_type"],
        }
        return StreamingResponse(full_iterator(), status_code=200, headers=headers)

    # Multi-range requests (e.g. bytes=0-100, 200-300) require multipart/byteranges framing and are unsupported.
    if "," in raw_ranges:
        return Response(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    range_spec = raw_ranges
    if "-" not in range_spec:
        return Response(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    parts = range_spec.split("-", 1)
    start_str = parts[0].strip()
    end_str = parts[1].strip()

    try:
        if start_str and end_str:
            # Explicit start and end (e.g. bytes=100-500)
            start = int(start_str)
            end = int(end_str)
            if start < 0 or end < 0 or start > end or start >= file_size:
                return Response(
                    status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
                    headers={"Content-Range": f"bytes */{file_size}"},
                )
            end = min(end, file_size - 1)
        elif start_str and not end_str:
            # Open-ended range (e.g. bytes=1024-)
            start = int(start_str)
            if start < 0 or start >= file_size:
                return Response(
                    status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
                    headers={"Content-Range": f"bytes */{file_size}"},
                )
            end = file_size - 1
        elif not start_str and end_str:
            # Suffix range (e.g. bytes=-500 for last 500 bytes)
            suffix_len = int(end_str)
            if suffix_len <= 0:
                return Response(
                    status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
                    headers={"Content-Range": f"bytes */{file_size}"},
                )
            start = max(0, file_size - suffix_len)
            end = file_size - 1
        else:
            # Empty range (e.g. bytes=-)
            return Response(
                status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
                headers={"Content-Range": f"bytes */{file_size}"},
            )
    except ValueError:
        return Response(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    content_length = end - start + 1
    if content_length <= 0:
        return Response(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{file_size}"},
        )

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
        "Content-Type": audio_format(audio_path)["mime_type"],
    }
    return StreamingResponse(file_iterator(), status_code=206, headers=headers)


@app.get("/api/session")
async def get_session_state(
    request: Request,
    _access: Optional[Dict[str, Any]] = Depends(enforce_access_policy),
):
    """Check if client has an active unexpired file session on disk and return current state."""
    token = request.cookies.get(SESSION_COOKIE_NAME) or request.headers.get("X-Session-Token")
    if not token:
        return {"active": False}

    session_id = verify_signed_session_token(token)
    if not session_id:
        return {"active": False}

    audio_path = storage_manager.get_audio_path(session_id)
    sdir = storage_manager.get_session_dir(session_id)
    if not audio_path or not sdir or not audio_path.is_file():
        return {"active": False}

    try:
        parsed = extract_metadata_and_artwork(audio_path)
    except Exception as e:
        logger.warning("Failed to restore audio tags: %s", type(e).__name__)
        return {"active": False}

    session_info = storage_manager.get_session_info(session_id) or {}

    # Check for staged artwork or explicit removal flag
    temp_art = sdir / "artwork_pending.bin"
    meta_art = sdir / "artwork_pending_mime.txt"
    remove_marker = sdir / "artwork_remove.flag"

    artwork_data = parsed.get("artwork", {})
    if remove_marker.exists():
        artwork_data = {
            "has_artwork": False,
            "preview_data_url": None,
            "mime_type": None,
            "size_bytes": 0,
        }
    elif temp_art.is_file() and meta_art.is_file():
        try:
            art_bytes = temp_art.read_bytes()
            art_mime = meta_art.read_text().strip()
            art_b64 = base64.b64encode(art_bytes).decode("ascii")
            artwork_data = {
                "has_artwork": True,
                "preview_data_url": f"data:{art_mime};base64,{art_b64}",
                "mime_type": art_mime,
                "size_bytes": len(art_bytes),
            }
        except Exception as e:
            logger.warning(f"Failed to read pending artwork: {e}")

    orig_name = session_info.get("original_filename", f"track{audio_path.suffix}")
    target_name = session_info.get("target_filename", orig_name)

    return {
        "active": True,
        "original_filename": orig_name,
        "target_filename": target_name,
        "metadata": parsed.get("metadata", {}),
        "audio_info": parsed.get("audio_info", {}),
        "artwork": artwork_data,
    }


@app.delete("/api/session")
async def delete_session(
    request: Request,
    response: Response,
    session_id: str = Depends(get_current_session_id),
    _access: Optional[Dict[str, Any]] = Depends(enforce_access_policy),
):
    """Explicitly terminate and purge a session, clearing the session cookie."""
    success = storage_manager.cleanup_session(session_id)
    is_https = (request.url.scheme == "https")
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/", secure=is_https, httponly=True)
    return {"success": success}



# Serve static assets and sub-applications
if ASSETS_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")

if APP_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=APP_DIR, html=True), name="app")

if MANAGER_DIR.is_dir():
    app.mount("/manager", StaticFiles(directory=MANAGER_DIR, html=True), name="manager")

if ADMIN_DIR.is_dir():
    app.mount("/admin", StaticFiles(directory=ADMIN_DIR, html=True), name="admin")

if DOCS_STATIC_DIR.is_dir():
    app.mount("/docs", StaticFiles(directory=DOCS_STATIC_DIR, html=True), name="docs")

if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="frontend")

# Registered after routes so authorization is available before multipart parsing.
from backend.request_limits import RequestLimitsMiddleware
app.add_middleware(RequestLimitsMiddleware, access_check=lambda request: enforce_access_policy(request))
