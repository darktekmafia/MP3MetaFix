"""API route handlers for MP3Projects, Track/Take Workspaces, Stems, and Albums."""

import os
import shutil
import stat
import urllib.parse
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    File,
    Form,
    status,
)
from fastapi.responses import StreamingResponse, FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field

from backend.config import (
    MAX_UPLOAD_SIZE_BYTES,
    MAX_ARTWORK_SIZE_BYTES,
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_MAX_AGE,
    get_runtime_upload_limit_bytes,
)
from backend.security import (
    validate_and_normalize_image,
    verify_signed_session_token,
    create_signed_session_token,
)
from backend.auth import auth_manager, AUTH_COOKIE_NAME
from backend.audio_formats import audio_format, matches_audio_header
from backend.storage import storage_manager
from backend.project_models import (
    WorkspaceStatus,
    StemRole,
    LyricsFormat,
    StemMetadata,
    TakeMetadata,
    TrackMetadata,
    TrackCreateRequest,
    TrackUpdateRequest,
    TrackDetailResponse,
    TakeUpdateRequest,
    AlbumMetadata,
    AlbumCreateRequest,
    AlbumUpdateRequest,
    AlbumEntryAddRequest,
    AlbumEntryUpdateRequest,
    AlbumEntryRefreshPreviewResponse,
    AlbumEntryRefreshConfirmRequest,
    StorageQuotaStats,
)
from backend.project_storage import (
    project_storage_manager,
    ProjectStorageError,
    ProjectStorageQuotaExceeded,
    ProjectValidationError,
    AlbumFinalizedError,
    AlbumReplacementDisabledError,
    AlbumRevisionConflictError,
    AlbumManifestCorruptError,
    AlbumDurabilityUncertainError,
    AlbumMutationNotEnrolledError,
    AlbumIdempotencyConflictError,
    AlbumTransactionAbortedError,
    AlbumRefreshConflictError,
    AlbumFinalizationError,
    ManifestPublishError,
    ManifestPublishState,
)


def _raise_album_durability_or_conflict_http_error(exc: Exception) -> None:
    """Translate Stage 1.3B durability, enrollment, idempotency, and revision errors consistently."""
    if isinstance(exc, AlbumDurabilityUncertainError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": str(exc),
                "error_code": "durability_uncertain",
                "guidance": "Do not resubmit with a new Idempotency-Key; run durability reconciliation or retry with the same Idempotency-Key after reconciliation.",
            },
            headers={"X-Error-Code": "durability_uncertain"},
        )
    if isinstance(exc, AlbumMutationNotEnrolledError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "error_code": "mutation_not_enrolled"},
            headers={"X-Error-Code": "mutation_not_enrolled"},
        )
    if isinstance(exc, AlbumIdempotencyConflictError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "error_code": "idempotency_conflict"},
            headers={"X-Error-Code": "idempotency_conflict"},
        )
    if isinstance(exc, AlbumTransactionAbortedError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "error_code": "transaction_aborted"},
            headers={"X-Error-Code": "transaction_aborted", "X-Idempotent-Replay": "true"},
        )
    if isinstance(exc, AlbumRevisionConflictError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
            headers={"X-Error-Code": "revision_conflict"},
        )

projects_router = APIRouter(prefix="/api/projects", tags=["projects"])
storage_router = APIRouter(prefix="/api/storage", tags=["storage"])


# --- Security & User Dependency ---

def require_auth(request: Request) -> Dict[str, Any]:
    """Ensure authenticated user session; guests are strictly excluded from persistent storage."""
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        elif request.headers.get("X-Auth-Token"):
            token = request.headers.get("X-Auth-Token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required for persistent project storage.",
        )

    user_id = auth_manager.verify_token(token)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session token.",
        )

    user = auth_manager.get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account not found.",
        )

    if user.get("role") != "admin" and not auth_manager.is_workspace_enabled("projects"):
        msg = auth_manager.get_workspace_maintenance_message("projects")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Maintenance Mode: {msg}",
        )

    return user


# --- HTTP 206 Range Streaming Helper ---

def _parse_strict_positive_revision(raw: str) -> int:
    """Strictly parse positive integer expected_revision from query/form strings (rejects bool/float/non-digits)."""
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdigit():
        raise HTTPException(status_code=422, detail="expected_revision must be a positive integer.")
    val = int(raw)
    if val < 1:
        raise HTTPException(status_code=422, detail="expected_revision must be >= 1.")
    return val


class _FdStreamResource:
    """Owns a pre-opened file descriptor and guarantees idempotent, deterministic cleanup."""

    def __init__(self, fd: int):
        self._fd: Optional[int] = fd
        self._raw_fd: Optional[int] = fd
        self._file = None

    def close(self) -> None:
        f = self._file
        raw_fd = self._raw_fd
        self._file = None
        self._raw_fd = None
        fd = self._fd
        self._fd = None
        if f is not None:
            try:
                f.close()
                return
            except Exception:
                pass
        target_fd = raw_fd if raw_fd is not None else fd
        if target_fd is not None:
            try:
                os.close(target_fd)
            except OSError:
                pass

    def iter_chunks(self, start: int = 0, chunk_length: Optional[int] = None):
        fd = self._fd
        if fd is None:
            return
        self._fd = None
        f = os.fdopen(fd, "rb")
        self._file = f
        try:
            if start > 0:
                f.seek(start)
            if chunk_length is None:
                while chunk := f.read(64 * 1024):
                    yield chunk
            else:
                bytes_left = chunk_length
                while bytes_left > 0:
                    read_size = min(64 * 1024, bytes_left)
                    data = f.read(read_size)
                    if not data:
                        break
                    bytes_left -= len(data)
                    yield data
        finally:
            self.close()

    def __del__(self):
        self.close()


class _OwnedFdStreamingResponse(StreamingResponse):
    """StreamingResponse that deterministically closes its _FdStreamResource on any ASGI exit path."""

    def __init__(self, *args, resource: _FdStreamResource, **kwargs):
        super().__init__(*args, **kwargs)
        self._fd_resource = resource

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._fd_resource.close()


def serve_audio_range_stream(
    audio_path: Path,
    request: Request,
    *,
    preopened_resource: Optional[_FdStreamResource] = None,
    preopened_size: Optional[int] = None,
) -> Response:
    """Deterministic HTTP 206 Partial Content range audio streaming."""
    try:
        if preopened_resource is not None:
            file_size = int(preopened_size if preopened_size is not None else 0)
        else:
            if not audio_path.is_file():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audio file not found.")
            file_size = audio_path.stat().st_size

        range_header = request.headers.get("range")
        mime_type = audio_format(audio_path)["mime_type"]

        def _close_preopened():
            if preopened_resource is not None:
                preopened_resource.close()

        def full_iterator():
            if preopened_resource is not None:
                yield from preopened_resource.iter_chunks()
                return
            with open(audio_path, "rb") as f:
                while chunk := f.read(64 * 1024):
                    yield chunk

        # If no Range header or non-bytes unit, serve full file (HTTP 200)
        if not range_header or not range_header.startswith("bytes="):
            headers = {
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
                "Content-Type": mime_type,
            }
            if preopened_resource is not None:
                return _OwnedFdStreamingResponse(
                    full_iterator(),
                    status_code=200,
                    headers=headers,
                    resource=preopened_resource,
                )
            return StreamingResponse(full_iterator(), status_code=200, headers=headers)

        if file_size == 0:
            _close_preopened()
            return Response(
                status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
                headers={"Content-Range": "bytes */0"},
            )

        raw_ranges = range_header[6:].strip()
        if not raw_ranges or "," in raw_ranges or "-" not in raw_ranges:
            _close_preopened()
            return Response(
                status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
                headers={"Content-Range": f"bytes */{file_size}"},
            )

        parts = raw_ranges.split("-", 1)
        start_str = parts[0].strip()
        end_str = parts[1].strip()

        try:
            if start_str and end_str:
                start = int(start_str)
                end = int(end_str)
                if start < 0 or end < 0 or start > end or start >= file_size:
                    _close_preopened()
                    return Response(
                        status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
                        headers={"Content-Range": f"bytes */{file_size}"},
                    )
                end = min(end, file_size - 1)
            elif start_str and not end_str:
                start = int(start_str)
                if start < 0 or start >= file_size:
                    _close_preopened()
                    return Response(
                        status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
                        headers={"Content-Range": f"bytes */{file_size}"},
                    )
                end = file_size - 1
            elif not start_str and end_str:
                suffix_len = int(end_str)
                if suffix_len <= 0:
                    _close_preopened()
                    return Response(
                        status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
                        headers={"Content-Range": f"bytes */{file_size}"},
                    )
                start = max(0, file_size - suffix_len)
                end = file_size - 1
            else:
                _close_preopened()
                return Response(
                    status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
                    headers={"Content-Range": f"bytes */{file_size}"},
                )
        except ValueError:
            _close_preopened()
            return Response(
                status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
                headers={"Content-Range": f"bytes */{file_size}"},
            )

        chunk_length = end - start + 1

        def range_iterator():
            if preopened_resource is not None:
                yield from preopened_resource.iter_chunks(start=start, chunk_length=chunk_length)
                return
            with open(audio_path, "rb") as f:
                f.seek(start)
                bytes_left = chunk_length
                while bytes_left > 0:
                    read_size = min(64 * 1024, bytes_left)
                    data = f.read(read_size)
                    if not data:
                        break
                    bytes_left -= len(data)
                    yield data

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(chunk_length),
            "Content-Type": mime_type,
        }
        if preopened_resource is not None:
            return _OwnedFdStreamingResponse(
                range_iterator(),
                status_code=206,
                headers=headers,
                resource=preopened_resource,
            )
        return StreamingResponse(range_iterator(), status_code=206, headers=headers)
    except BaseException:
        if preopened_resource is not None:
            preopened_resource.close()
        raise


# --- Storage Quota Endpoints ---

@storage_router.get("/quota", response_model=StorageQuotaStats)
async def get_storage_quota(user: Dict[str, Any] = Depends(require_auth)):
    """Return active storage metrics and item counts for the authenticated user."""
    return project_storage_manager.get_quota_stats(user["id"])


# --- Track Workspace Endpoints ---

@projects_router.get("/tracks", response_model=List[TrackMetadata])
async def list_tracks(
    status_filter: Optional[WorkspaceStatus] = None,
    user: Dict[str, Any] = Depends(require_auth),
):
    """List all track workspaces belonging to the authenticated user."""
    return project_storage_manager.list_tracks(user["id"], status=status_filter)


@projects_router.post("/tracks", response_model=TrackMetadata, status_code=status.HTTP_201_CREATED)
async def create_track(
    payload: TrackCreateRequest,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Create a new track workspace."""
    return project_storage_manager.create_track(
        user_id=user["id"],
        title=payload.title,
        artist=payload.artist,
        status=payload.status,
        master_lyrics=payload.master_lyrics,
        notes=payload.notes,
        bpm=payload.bpm,
        musical_key=payload.musical_key,
    )


@projects_router.get("/tracks/{track_id}", response_model=TrackDetailResponse)
async def get_track_detail(
    track_id: str,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Retrieve full details of a track workspace including all its takes."""
    track = project_storage_manager.get_track(user["id"], track_id)
    if not track:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Track workspace not found.")
    takes = project_storage_manager.list_takes(user["id"], track_id)
    return TrackDetailResponse(track=track, takes=takes)


@projects_router.patch("/tracks/{track_id}", response_model=TrackMetadata)
async def update_track(
    track_id: str,
    payload: TrackUpdateRequest,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Update metadata fields of a track workspace."""
    track = project_storage_manager.get_track(user["id"], track_id)
    if not track:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Track workspace not found.")

    updates = payload.model_dump(exclude_unset=True)
    updated = project_storage_manager.update_track(user["id"], track_id, updates)
    if not updated:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update track.")
    return updated


@projects_router.delete("/tracks/{track_id}")
async def delete_track(
    track_id: str,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Delete a track workspace and all its takes/stems."""
    success = project_storage_manager.delete_track(user["id"], track_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Track workspace not found.")
    return {"status": "deleted", "id": track_id}


# --- Take & Audio Management Endpoints ---

@projects_router.post("/tracks/{track_id}/takes", response_model=TakeMetadata, status_code=status.HTTP_201_CREATED)
async def upload_take(
    track_id: str,
    file: UploadFile = File(...),
    label: Optional[str] = Form(None),
    prompt: Optional[str] = Form(None),
    style_tags: Optional[str] = Form(None),
    seed: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    is_master: bool = Form(False),
    user: Dict[str, Any] = Depends(require_auth),
):
    """Upload audio file to create a new take in a track workspace."""
    track = project_storage_manager.get_track(user["id"], track_id)
    if not track:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Track workspace not found.")

    filename = file.filename or "track.mp3"
    ext = Path(filename).suffix.lower()
    if ext not in (".mp3", ".m4a", ".wav"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported audio format. Supported formats: .mp3, .m4a, .wav",
        )

    # Read and enforce max upload size limit
    max_bytes = get_runtime_upload_limit_bytes()
    audio_data = bytearray()
    
    while chunk := await file.read(64 * 1024):
        audio_data.extend(chunk)
        if len(audio_data) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Audio file exceeds maximum size limit ({max_bytes // (1024*1024)}MB)",
            )

    if len(audio_data) < 32:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty or too short.")

    # Validate container magic bytes
    if not matches_audio_header(bytes(audio_data[:64]), ext):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid audio file header/signature.")

    try:
        take = project_storage_manager.create_take(
            user_id=user["id"],
            track_id=track_id,
            audio_bytes=bytes(audio_data),
            original_filename=filename,
            extension=ext,
            label=label,
            prompt=prompt,
            style_tags=style_tags,
            seed=seed,
            notes=notes,
            is_master=is_master,
        )
        return take
    except ProjectStorageQuotaExceeded as e:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to persist take: {e}")


@projects_router.get("/tracks/{track_id}/takes/{take_id}", response_model=TakeMetadata)
async def get_take(
    track_id: str,
    take_id: str,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Retrieve metadata for a specific take."""
    take = project_storage_manager.get_take(user["id"], track_id, take_id)
    if not take:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Take not found.")
    return take


@projects_router.patch("/tracks/{track_id}/takes/{take_id}", response_model=TakeMetadata)
async def update_take(
    track_id: str,
    take_id: str,
    payload: TakeUpdateRequest,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Update metadata of a specific take (label, prompt, notes, master flag)."""
    take = project_storage_manager.get_take(user["id"], track_id, take_id)
    if not take:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Take not found.")

    updates = payload.model_dump(exclude_unset=True)
    updated = project_storage_manager.update_take(user["id"], track_id, take_id, updates)
    if not updated:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update take.")
    return updated


@projects_router.delete("/tracks/{track_id}/takes/{take_id}")
async def delete_take(
    track_id: str,
    take_id: str,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Delete a specific take and its associated stems."""
    success = project_storage_manager.delete_take(user["id"], track_id, take_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Take not found.")
    return {"status": "deleted", "id": take_id}


# --- Stems Endpoints ---

@projects_router.post("/tracks/{track_id}/takes/{take_id}/stems", response_model=StemMetadata, status_code=status.HTTP_201_CREATED)
async def upload_stem(
    track_id: str,
    take_id: str,
    file: UploadFile = File(...),
    role: StemRole = Form(StemRole.OTHER),
    user: Dict[str, Any] = Depends(require_auth),
):
    """Upload a separated stem file for a take."""
    take = project_storage_manager.get_take(user["id"], track_id, take_id)
    if not take:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Take not found.")

    filename = file.filename or f"stem_{role.value}.wav"
    ext = Path(filename).suffix.lower()
    if ext not in (".mp3", ".m4a", ".wav"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported audio format for stem.")

    max_bytes = get_runtime_upload_limit_bytes()
    stem_data = bytearray()
    while chunk := await file.read(64 * 1024):
        stem_data.extend(chunk)
        if len(stem_data) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Stem file exceeds maximum size limit ({max_bytes // (1024*1024)}MB)",
            )

    if not matches_audio_header(bytes(stem_data[:64]), ext):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid audio file header for stem.")

    try:
        stem = project_storage_manager.add_stem(
            user_id=user["id"],
            track_id=track_id,
            take_id=take_id,
            stem_bytes=bytes(stem_data),
            original_filename=filename,
            extension=ext,
            role=role,
        )
        return stem
    except ProjectStorageQuotaExceeded as e:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(e))


@projects_router.delete("/tracks/{track_id}/takes/{take_id}/stems/{stem_id}")
async def delete_stem(
    track_id: str,
    take_id: str,
    stem_id: str,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Delete a stem from a take."""
    success = project_storage_manager.delete_stem(user["id"], track_id, take_id, stem_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stem not found.")
    return {"status": "deleted", "id": stem_id}


# --- Lyrics Endpoints ---

class SaveLyricsRequest(BaseModel):
    lyrics: str = Field(..., max_length=50000)
    format: LyricsFormat = Field(default=LyricsFormat.LRC)


@projects_router.post("/tracks/{track_id}/takes/{take_id}/lyrics")
async def save_take_lyrics(
    track_id: str,
    take_id: str,
    payload: SaveLyricsRequest,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Save synchronized lyrics (LRC or SYLT) for a take."""
    success = project_storage_manager.save_take_lyrics(
        user_id=user["id"],
        track_id=track_id,
        take_id=take_id,
        lyrics_content=payload.lyrics,
        lyrics_format=payload.format,
    )
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Take not found.")
    return {"status": "saved", "format": payload.format.value}


@projects_router.get("/tracks/{track_id}/takes/{take_id}/lyrics")
async def get_take_lyrics(
    track_id: str,
    take_id: str,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Retrieve synchronized lyrics for a take."""
    result = project_storage_manager.get_take_lyrics(user["id"], track_id, take_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No synchronized lyrics found for take.")
    fmt, content = result
    return {"format": fmt.value, "lyrics": content}


# --- Audio Streaming & Downloads ---

@projects_router.get("/tracks/{track_id}/takes/{take_id}/stream")
async def stream_take_audio(
    track_id: str,
    take_id: str,
    request: Request,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Stream audio for a take using HTTP 206 Partial Content range requests."""
    audio_path = project_storage_manager.get_take_audio_path(user["id"], track_id, take_id)
    if not audio_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Take audio not found.")
    return serve_audio_range_stream(audio_path, request)


@projects_router.get("/tracks/{track_id}/takes/{take_id}/download")
async def download_take_audio(
    track_id: str,
    take_id: str,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Download take audio with sanitized RFC 5987 Content-Disposition filename."""
    take = project_storage_manager.get_take(user["id"], track_id, take_id)
    audio_path = project_storage_manager.get_take_audio_path(user["id"], track_id, take_id)
    if not take or not audio_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Take audio not found.")

    ascii_name = take.filename.encode("ascii", "ignore").decode("ascii") or "track.mp3"
    encoded_name = urllib.parse.quote(take.filename)
    headers = {
        "Content-Disposition": f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded_name}'
    }
    return FileResponse(
        path=audio_path,
        media_type=audio_format(audio_path)["mime_type"],
        headers=headers,
    )


@projects_router.get("/tracks/{track_id}/takes/{take_id}/stems/{stem_id}/stream")
async def stream_stem_audio(
    track_id: str,
    take_id: str,
    stem_id: str,
    request: Request,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Stream stem audio using HTTP 206 Partial Content range requests."""
    stem_path = project_storage_manager.get_stem_audio_path(user["id"], track_id, take_id, stem_id)
    if not stem_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stem audio not found.")
    return serve_audio_range_stream(stem_path, request)


@projects_router.get("/tracks/{track_id}/takes/{take_id}/stems/{stem_id}/download")
async def download_stem_audio(
    track_id: str,
    take_id: str,
    stem_id: str,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Download individual stem audio with sanitized RFC 5987 Content-Disposition filename."""
    take = project_storage_manager.get_take(user["id"], track_id, take_id)
    if not take or stem_id not in take.stems:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stem not found.")
    stem = take.stems[stem_id]
    stem_path = project_storage_manager.get_stem_audio_path(user["id"], track_id, take_id, stem_id)
    if not stem_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stem audio not found.")

    ascii_name = stem.filename.encode("ascii", "ignore").decode("ascii") or "stem.wav"
    encoded_name = urllib.parse.quote(stem.filename)
    headers = {
        "Content-Disposition": f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded_name}'
    }
    return FileResponse(
        path=stem_path,
        media_type=audio_format(stem_path)["mime_type"],
        headers=headers,
    )


@projects_router.get("/tracks/{track_id}/takes/{take_id}/stems/download-pack")
async def download_stem_pack(
    track_id: str,
    take_id: str,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Download all stems for a take packaged in a single ZIP archive."""
    result = project_storage_manager.get_stem_pack_zip(user["id"], track_id, take_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No stems found for this take.")

    zip_filename, zip_bytes = result
    ascii_name = zip_filename.encode("ascii", "ignore").decode("ascii") or "stems.zip"
    encoded_name = urllib.parse.quote(zip_filename)
    headers = {
        "Content-Disposition": f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded_name}'
    }
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers=headers,
    )


# --- Album Workspace Endpoints ---

@projects_router.get("/albums", response_model=List[AlbumMetadata])
async def list_albums(
    status_filter: Optional[WorkspaceStatus] = None,
    user: Dict[str, Any] = Depends(require_auth),
):
    """List all album workspaces for the authenticated user."""
    try:
        return project_storage_manager.list_albums(user["id"], status=status_filter)
    except AlbumDurabilityUncertainError as e:
        _raise_album_durability_or_conflict_http_error(e)
    except AlbumManifestCorruptError as e:
        raise HTTPException(status_code=422, detail=str(e))


@projects_router.post("/albums", response_model=AlbumMetadata, status_code=status.HTTP_201_CREATED)
async def create_album(
    payload: AlbumCreateRequest,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Create a new album/EP project workspace."""
    try:
        return project_storage_manager.create_album(
            user_id=user["id"],
            title=payload.title,
            album_artist=payload.album_artist,
            year=payload.year,
            genre=payload.genre,
            credits=payload.credits,
            status=payload.status,
        )
    except ProjectValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


def _get_album_or_http(user_id: str, album_id: str) -> AlbumMetadata:
    """Fetch album or raise 404/422/503 cleanly, failing closed on corrupt or uncertain manifests."""
    try:
        album = project_storage_manager.get_album(user_id, album_id)
    except AlbumDurabilityUncertainError as e:
        _raise_album_durability_or_conflict_http_error(e)
    except AlbumManifestCorruptError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not album:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Album workspace not found.")
    return album


@projects_router.get("/albums/{album_id}", response_model=AlbumMetadata)
async def get_album(
    album_id: str,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Retrieve details and tracklist for an album project."""
    return _get_album_or_http(user["id"], album_id)


@projects_router.patch("/albums/{album_id}", response_model=AlbumMetadata)
async def update_album(
    album_id: str,
    payload: AlbumUpdateRequest,
    response: Response,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    user: Dict[str, Any] = Depends(require_auth),
):
    """Update album details or sequenced tracklist."""
    _get_album_or_http(user["id"], album_id)

    updates = payload.model_dump(exclude_unset=True)
    try:
        res = project_storage_manager.update_album_with_replay_status(
            user["id"], album_id, updates, idempotency_key=idempotency_key
        )
    except (AlbumDurabilityUncertainError, AlbumMutationNotEnrolledError, AlbumIdempotencyConflictError, AlbumTransactionAbortedError, AlbumRevisionConflictError) as e:
        _raise_album_durability_or_conflict_http_error(e)
    except (AlbumFinalizedError, AlbumReplacementDisabledError) as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except AlbumManifestCorruptError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ProjectValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except ProjectStorageQuotaExceeded as e:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(e))
    if not res:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Album workspace not found.")
    updated, is_replay = res
    if is_replay:
        response.headers["X-Idempotent-Replay"] = "true"
    return updated


@projects_router.delete("/albums/{album_id}")
async def delete_album(
    album_id: str,
    expected_revision: str = Query(...),
    user: Dict[str, Any] = Depends(require_auth),
):
    """Delete an unfinished empty album workspace (does not delete individual tracks)."""
    rev = _parse_strict_positive_revision(expected_revision)
    try:
        success = project_storage_manager.delete_album(
            user["id"], album_id, expected_revision=rev
        )
    except (AlbumDurabilityUncertainError, AlbumMutationNotEnrolledError, AlbumIdempotencyConflictError, AlbumRevisionConflictError) as e:
        _raise_album_durability_or_conflict_http_error(e)
    except (AlbumFinalizedError, AlbumReplacementDisabledError) as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except AlbumManifestCorruptError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ProjectStorageError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Album workspace not found.")
    return {"status": "deleted", "id": album_id}


@projects_router.post("/albums/{album_id}/entries", response_model=AlbumMetadata, status_code=status.HTTP_201_CREATED)
async def add_album_entry(
    album_id: str,
    payload: AlbumEntryAddRequest,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Add a Track Workspace entry (explicit Take copy or placeholder) to an unfinished album."""
    _get_album_or_http(user["id"], album_id)

    try:
        updated = project_storage_manager.add_album_entry(
            user_id=user["id"],
            album_id=album_id,
            track_id=payload.track_id,
            expected_revision=payload.expected_revision,
            take_id=payload.take_id,
            disc_number=payload.disc_number,
            track_number=payload.track_number,
            custom_title=payload.custom_title,
        )
    except (AlbumDurabilityUncertainError, AlbumMutationNotEnrolledError, AlbumIdempotencyConflictError, AlbumRevisionConflictError) as e:
        _raise_album_durability_or_conflict_http_error(e)
    except (AlbumFinalizedError, AlbumReplacementDisabledError) as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except AlbumManifestCorruptError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ProjectValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except ProjectStorageQuotaExceeded as e:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(e))

    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Album workspace not found.")
    return updated


@projects_router.patch("/albums/{album_id}/entries/{entry_id}", response_model=AlbumMetadata)
async def update_album_entry(
    album_id: str,
    entry_id: str,
    payload: AlbumEntryUpdateRequest,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Update an unfinished album entry's selected Take, placeholder state, or entry metadata."""
    _get_album_or_http(user["id"], album_id)

    try:
        updated = project_storage_manager.update_album_entry(
            user_id=user["id"],
            album_id=album_id,
            entry_id=entry_id,
            expected_revision=payload.expected_revision,
            take_id=payload.take_id,
            clear_take_to_placeholder=payload.clear_take_to_placeholder,
            disc_number=payload.disc_number,
            custom_title=payload.custom_title,
        )
    except (AlbumDurabilityUncertainError, AlbumMutationNotEnrolledError, AlbumIdempotencyConflictError, AlbumRevisionConflictError) as e:
        _raise_album_durability_or_conflict_http_error(e)
    except (AlbumFinalizedError, AlbumReplacementDisabledError) as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except AlbumManifestCorruptError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ProjectValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except ProjectStorageQuotaExceeded as e:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(e))

    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Album workspace not found.")
    return updated


@projects_router.delete("/albums/{album_id}/entries/{entry_id}", response_model=AlbumMetadata)
async def remove_album_entry(
    album_id: str,
    entry_id: str,
    expected_revision: str = Query(...),
    user: Dict[str, Any] = Depends(require_auth),
):
    """Remove an empty placeholder entry from an unfinished album."""
    rev = _parse_strict_positive_revision(expected_revision)
    _get_album_or_http(user["id"], album_id)

    try:
        updated = project_storage_manager.remove_album_entry(
            user["id"], album_id, entry_id, expected_revision=rev
        )
    except (AlbumDurabilityUncertainError, AlbumMutationNotEnrolledError, AlbumIdempotencyConflictError, AlbumRevisionConflictError) as e:
        _raise_album_durability_or_conflict_http_error(e)
    except (AlbumFinalizedError, AlbumReplacementDisabledError) as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except AlbumManifestCorruptError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ProjectValidationError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Album workspace not found.")
    return updated


@projects_router.get("/albums/{album_id}/entries/{entry_id}/stream")
async def stream_album_entry_audio(
    album_id: str,
    entry_id: str,
    request: Request,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Stream an album entry's owned audio copy using HTTP 206 Partial Content range requests (side-effect-free)."""
    try:
        opened = project_storage_manager.open_album_entry_audio_for_read(user["id"], album_id, entry_id)
    except AlbumDurabilityUncertainError as e:
        _raise_album_durability_or_conflict_http_error(e)
    except AlbumManifestCorruptError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if opened is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Album entry audio copy not found.")

    _, audio_path, fd, file_size = opened
    resource = _FdStreamResource(fd)
    return serve_audio_range_stream(audio_path, request, preopened_resource=resource, preopened_size=file_size)


@projects_router.get("/albums/{album_id}/entries/{entry_id}/download")
async def download_album_entry_audio(
    album_id: str,
    entry_id: str,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Download an album entry's owned audio copy with RFC 5987 filename headers (side-effect-free)."""
    try:
        opened = project_storage_manager.open_album_entry_audio_for_read(user["id"], album_id, entry_id)
    except AlbumDurabilityUncertainError as e:
        _raise_album_durability_or_conflict_http_error(e)
    except AlbumManifestCorruptError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if opened is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Album entry audio copy not found.")

    entry, audio_path, fd, file_size = opened
    resource = _FdStreamResource(fd)
    try:
        orig_name = (entry.snapshot.source_filename if entry.snapshot else None) or f"track_{entry.track_number}{audio_path.suffix}"
        ascii_name = orig_name.encode("ascii", "ignore").decode("ascii") or f"track_{entry.track_number}{audio_path.suffix}"
        encoded_name = urllib.parse.quote(orig_name)
        headers = {
            "Content-Disposition": f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded_name}',
            "Content-Length": str(file_size),
        }
        return _OwnedFdStreamingResponse(
            resource.iter_chunks(),
            media_type=audio_format(audio_path)["mime_type"],
            headers=headers,
            resource=resource,
        )
    except BaseException:
        resource.close()
        raise


class AlbumEntryRefreshPreviewApiResponse(AlbumEntryRefreshPreviewResponse):
    expected_source_metadata_fingerprint: Optional[str] = None


class AlbumEntryRefreshConfirmApiRequest(AlbumEntryRefreshConfirmRequest):
    expected_source_metadata_fingerprint: str = Field(..., min_length=64, max_length=64)


@projects_router.get("/albums/{album_id}/entries/{entry_id}/refresh-preview", response_model=AlbumEntryRefreshPreviewApiResponse)
async def preview_album_entry_refresh(
    album_id: str,
    entry_id: str,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Return a read-only comparison between the album-owned copy and latest source take."""
    album = _get_album_or_http(user["id"], album_id)

    try:
        preview = project_storage_manager.preview_album_entry_refresh(user["id"], album_id, entry_id)
    except AlbumFinalizedError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except ProjectValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    if not preview:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Album workspace not found.")
    meta_fp = (
        project_storage_manager.compute_refresh_source_metadata_fingerprint(preview.source_snapshot)
        if preview.source_available and preview.source_snapshot is not None
        else None
    )
    return AlbumEntryRefreshPreviewApiResponse(
        **preview.model_dump(),
        expected_source_metadata_fingerprint=meta_fp,
    )


@projects_router.post("/albums/{album_id}/entries/{entry_id}/refresh", response_model=AlbumMetadata)
async def confirm_album_entry_refresh(
    album_id: str,
    entry_id: str,
    payload: AlbumEntryRefreshConfirmApiRequest,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Confirm replacing an album entry's owned copy from its source take if the previewed version still matches."""
    album = _get_album_or_http(user["id"], album_id)

    try:
        updated = project_storage_manager.confirm_album_entry_refresh(
            user_id=user["id"],
            album_id=album_id,
            entry_id=entry_id,
            expected_source_revision=payload.expected_source_revision,
            expected_source_audio_sha256=payload.expected_source_audio_sha256,
            expected_source_updated_at=payload.expected_source_updated_at,
            expected_revision=payload.expected_revision,
            expected_source_metadata_fingerprint=payload.expected_source_metadata_fingerprint,
        )
    except (AlbumDurabilityUncertainError, AlbumMutationNotEnrolledError, AlbumIdempotencyConflictError, AlbumRevisionConflictError) as e:
        _raise_album_durability_or_conflict_http_error(e)
    except (AlbumFinalizedError, AlbumRefreshConflictError, AlbumReplacementDisabledError) as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except AlbumManifestCorruptError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ProjectValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except ProjectStorageQuotaExceeded as e:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(e))

    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Album workspace not found.")
    return updated


@projects_router.post("/albums/{album_id}/finalize", response_model=AlbumMetadata)
async def finalize_album(
    album_id: str,
    response: Response,
    expected_revision: str = Query(...),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    user: Dict[str, Any] = Depends(require_auth),
):
    """Validate all album recordings and atomically mark an album release as finalized and immutable."""
    rev = _parse_strict_positive_revision(expected_revision)
    _get_album_or_http(user["id"], album_id)

    try:
        res = project_storage_manager.finalize_album_with_replay_status(
            user["id"], album_id, expected_revision=rev, idempotency_key=idempotency_key
        )
    except (
        AlbumDurabilityUncertainError,
        AlbumMutationNotEnrolledError,
        AlbumIdempotencyConflictError,
        AlbumTransactionAbortedError,
        AlbumRevisionConflictError,
    ) as e:
        _raise_album_durability_or_conflict_http_error(e)
    except ManifestPublishError as e:
        if e.publish_state == ManifestPublishState.NOT_PUBLISHED:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "message": "Album finalization failed before transaction journal publication.",
                    "error_code": "publication_failed",
                },
                headers={"X-Error-Code": "publication_failed"},
            )
        _raise_album_durability_or_conflict_http_error(
            AlbumDurabilityUncertainError("Album durability state is uncertain after publication failure.")
        )
    except AlbumFinalizedError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except AlbumManifestCorruptError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ProjectValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except AlbumFinalizationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"message": str(e), "errors": e.errors})

    if not res:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Album workspace not found.")
    finalized, is_replay = res
    if is_replay:
        response.headers["X-Idempotent-Replay"] = "true"
    return finalized


@projects_router.post("/albums/{album_id}/artwork")
async def upload_album_artwork(
    album_id: str,
    expected_revision: str = Form(...),
    file: UploadFile = File(...),
    user: Dict[str, Any] = Depends(require_auth),
):
    """Upload and normalize master album cover artwork."""
    rev = _parse_strict_positive_revision(expected_revision)
    _get_album_or_http(user["id"], album_id)

    img_data = await file.read()
    clean_bytes, mime_type = validate_and_normalize_image(img_data, MAX_ARTWORK_SIZE_BYTES)
    ext = ".png" if mime_type == "image/png" else ".jpg"

    try:
        updated_album = project_storage_manager.save_album_cover(
            user["id"],
            album_id,
            clean_bytes,
            expected_revision=rev,
            extension=ext,
        )
        return {"status": "saved", "mime_type": mime_type, "album": updated_album}
    except (AlbumDurabilityUncertainError, AlbumMutationNotEnrolledError, AlbumIdempotencyConflictError, AlbumRevisionConflictError) as e:
        _raise_album_durability_or_conflict_http_error(e)
    except (AlbumFinalizedError, AlbumReplacementDisabledError) as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except AlbumManifestCorruptError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ProjectStorageQuotaExceeded as e:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(e))


@projects_router.get("/albums/{album_id}/artwork")
async def get_album_artwork(
    album_id: str,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Serve master album cover artwork (side-effect-free)."""
    try:
        opened = project_storage_manager.open_album_cover_for_read(user["id"], album_id)
    except AlbumDurabilityUncertainError as e:
        _raise_album_durability_or_conflict_http_error(e)
    except AlbumManifestCorruptError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if opened is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Album artwork not found.")

    cover_path, fd, file_size = opened
    resource = _FdStreamResource(fd)
    try:
        ext = cover_path.suffix.lower()
        mime_type = (
            "image/png"
            if ext == ".png"
            else ("image/webp" if ext == ".webp" else "image/jpeg")
        )
        return _OwnedFdStreamingResponse(
            resource.iter_chunks(),
            media_type=mime_type,
            headers={"Content-Length": str(file_size)},
            resource=resource,
        )
    except BaseException:
        resource.close()
        raise


# --- Promotion / Ingestion from `/app` Ephemeral Session ---

class IngestSessionRequest(BaseModel):
    track_id: Optional[str] = Field(default=None, max_length=40)
    take_id: Optional[str] = Field(default=None, max_length=40)
    new_track_title: Optional[str] = Field(default=None, max_length=200)
    take_label: Optional[str] = Field(default=None, max_length=100)
    notes: Optional[str] = Field(default=None, max_length=10000)
    is_master: bool = Field(default=True)


@projects_router.post("/ingest-session", response_model=TakeMetadata, status_code=status.HTTP_201_CREATED)
async def ingest_session(
    request: Request,
    payload: IngestSessionRequest,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Promote or update an active ephemeral session into a persistent track workspace take."""
    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    session_id = verify_signed_session_token(cookie_token)
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No active file editing session found to ingest.",
        )

    audio_path = storage_manager.get_audio_path(session_id)
    session_info = storage_manager.get_session_info(session_id)
    if not audio_path or not audio_path.is_file() or not session_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session audio file not found.")

    with open(audio_path, "rb") as f:
        audio_bytes = f.read()

    ext = audio_path.suffix.lower()
    original_filename = session_info.get("original_filename", f"track{ext}")

    # Extract normalized tags from updated audio file
    tags = {}
    try:
        from backend.metadata_engine import extract_metadata_and_artwork
        meta = extract_metadata_and_artwork(audio_path)
        tags = meta.get("tags", {})
    except Exception:
        pass

    key_tag = tags.get("initial_key") or tags.get("key")
    bpm_val = None
    if tags.get("bpm"):
        try:
            bpm_val = int(float(str(tags["bpm"]).strip()))
        except (ValueError, TypeError):
            bpm_val = None

    # Case 1: Update existing take in place
    if payload.track_id and payload.take_id:
        existing_track = project_storage_manager.get_track(user["id"], payload.track_id)
        if not existing_track:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target track workspace not found.")
        existing_take = project_storage_manager.get_take(user["id"], payload.track_id, payload.take_id)
        if not existing_take:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target take not found.")

        try:
            take = project_storage_manager.update_take_audio(
                user_id=user["id"],
                track_id=payload.track_id,
                take_id=payload.take_id,
                audio_bytes=audio_bytes,
                extension=ext,
                label=payload.take_label or existing_take.label,
                notes=payload.notes if payload.notes is not None else existing_take.notes,
                is_master=payload.is_master,
                change_summary=payload.notes,
            )
            return take
        except ProjectStorageQuotaExceeded as e:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(e))

    # Case 2 & 3: Add new take to existing track or create new track
    target_track_id = payload.track_id
    if not target_track_id:
        title = payload.new_track_title or tags.get("title") or Path(original_filename).stem
        artist = tags.get("artist")
        new_track = project_storage_manager.create_track(
            user_id=user["id"],
            title=title,
            artist=artist,
            status=WorkspaceStatus.IN_PROGRESS,
            notes=payload.notes,
            bpm=bpm_val,
            musical_key=key_tag,
            master_lyrics=tags.get("lyrics"),
        )
        target_track_id = new_track.id
    else:
        existing_track = project_storage_manager.get_track(user["id"], target_track_id)
        if not existing_track:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target track workspace not found.")

    try:
        take = project_storage_manager.create_take(
            user_id=user["id"],
            track_id=target_track_id,
            audio_bytes=audio_bytes,
            original_filename=original_filename,
            extension=ext,
            label=payload.take_label or "Imported from Editor",
            notes=payload.notes,
            is_master=payload.is_master,
        )
        return take
    except ProjectStorageQuotaExceeded as e:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(e))


@projects_router.post("/tracks/{track_id}/takes/{take_id}/load-session")
async def load_take_into_session(
    track_id: str,
    take_id: str,
    request: Request,
    response: Response,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Load a persistent take from storage into an active editing session."""
    track = project_storage_manager.get_track(user["id"], track_id)
    take = project_storage_manager.get_take(user["id"], track_id, take_id)
    audio_path = project_storage_manager.get_take_audio_path(user["id"], track_id, take_id)
    if not take or not audio_path or not audio_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Take audio not found.")

    ext = f".{take.format}"
    session_id, session_audio_path = storage_manager.create_session(take.filename, ext)
    shutil.copyfile(audio_path, session_audio_path)

    # If take has lyrics, copy them if possible
    lyrics_data = project_storage_manager.get_take_lyrics(user["id"], track_id, take_id)
    if lyrics_data:
        fmt, lyrics_txt = lyrics_data
        session_dir = storage_manager.get_session_dir(session_id)
        if session_dir:
            lfile = session_dir / f"lyrics{'.lrc' if fmt == LyricsFormat.LRC else '.sylt'}"
            with open(lfile, "w", encoding="utf-8") as f:
                f.write(lyrics_txt)

    loaded_take_ref = {
        "track_id": track_id,
        "take_id": take_id,
        "track_title": track.title if track else "Workspace Track",
        "take_label": take.label,
        "filename": take.filename,
    }
    storage_manager.update_session_info(
        session_id,
        {"loaded_project_take": loaded_take_ref},
    )

    token = create_signed_session_token(session_id)
    is_https = (request.url.scheme == "https")
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_COOKIE_MAX_AGE,
        path="/",
        secure=is_https,
        httponly=True,
        samesite="lax",
    )
    return {
        "status": "loaded",
        "session_id": session_id,
        "filename": take.filename,
        "label": take.label,
        "track_id": track_id,
        "take_id": take_id,
        "loaded_project_take": loaded_take_ref,
    }
