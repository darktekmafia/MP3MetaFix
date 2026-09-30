"""API route handlers for MP3Projects, Track/Take Workspaces, Stems, and Albums."""

import os
import shutil
import urllib.parse
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    UploadFile,
    File,
    Form,
    status,
)
from fastapi.responses import StreamingResponse, FileResponse
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
    StorageQuotaStats,
)
from backend.project_storage import (
    project_storage_manager,
    ProjectStorageQuotaExceeded,
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
    return user


# --- HTTP 206 Range Streaming Helper ---

def serve_audio_range_stream(audio_path: Path, request: Request) -> Response:
    """Deterministic HTTP 206 Partial Content range audio streaming."""
    if not audio_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audio file not found.")

    file_size = audio_path.stat().st_size
    range_header = request.headers.get("range")
    mime_type = audio_format(audio_path)["mime_type"]

    def full_iterator():
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
        return StreamingResponse(full_iterator(), status_code=200, headers=headers)

    if file_size == 0:
        return Response(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": "bytes */0"},
        )

    raw_ranges = range_header[6:].strip()
    if not raw_ranges or "," in raw_ranges or "-" not in raw_ranges:
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
                return Response(
                    status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
                    headers={"Content-Range": f"bytes */{file_size}"},
                )
            end = min(end, file_size - 1)
        elif start_str and not end_str:
            start = int(start_str)
            if start < 0 or start >= file_size:
                return Response(
                    status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
                    headers={"Content-Range": f"bytes */{file_size}"},
                )
            end = file_size - 1
        elif not start_str and end_str:
            suffix_len = int(end_str)
            if suffix_len <= 0:
                return Response(
                    status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
                    headers={"Content-Range": f"bytes */{file_size}"},
                )
            start = max(0, file_size - suffix_len)
            end = file_size - 1
        else:
            return Response(
                status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
                headers={"Content-Range": f"bytes */{file_size}"},
            )
    except ValueError:
        return Response(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    chunk_length = end - start + 1

    def range_iterator():
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
    return StreamingResponse(range_iterator(), status_code=206, headers=headers)


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


# --- Album Workspace Endpoints ---

@projects_router.get("/albums", response_model=List[AlbumMetadata])
async def list_albums(
    status_filter: Optional[WorkspaceStatus] = None,
    user: Dict[str, Any] = Depends(require_auth),
):
    """List all album workspaces for the authenticated user."""
    return project_storage_manager.list_albums(user["id"], status=status_filter)


@projects_router.post("/albums", response_model=AlbumMetadata, status_code=status.HTTP_201_CREATED)
async def create_album(
    payload: AlbumCreateRequest,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Create a new album/EP project workspace."""
    return project_storage_manager.create_album(
        user_id=user["id"],
        title=payload.title,
        album_artist=payload.album_artist,
        year=payload.year,
        genre=payload.genre,
        credits=payload.credits,
        status=payload.status,
    )


@projects_router.get("/albums/{album_id}", response_model=AlbumMetadata)
async def get_album(
    album_id: str,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Retrieve details and tracklist for an album project."""
    album = project_storage_manager.get_album(user["id"], album_id)
    if not album:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Album workspace not found.")
    return album


@projects_router.patch("/albums/{album_id}", response_model=AlbumMetadata)
async def update_album(
    album_id: str,
    payload: AlbumUpdateRequest,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Update album details or sequenced tracklist."""
    album = project_storage_manager.get_album(user["id"], album_id)
    if not album:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Album workspace not found.")

    updates = payload.model_dump(exclude_unset=True)
    updated = project_storage_manager.update_album(user["id"], album_id, updates)
    if not updated:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update album.")
    return updated


@projects_router.delete("/albums/{album_id}")
async def delete_album(
    album_id: str,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Delete an album workspace (does not delete individual tracks)."""
    success = project_storage_manager.delete_album(user["id"], album_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Album workspace not found.")
    return {"status": "deleted", "id": album_id}


@projects_router.post("/albums/{album_id}/artwork")
async def upload_album_artwork(
    album_id: str,
    file: UploadFile = File(...),
    user: Dict[str, Any] = Depends(require_auth),
):
    """Upload and normalize master album cover artwork."""
    album = project_storage_manager.get_album(user["id"], album_id)
    if not album:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Album workspace not found.")

    img_data = await file.read()
    clean_bytes, mime_type = validate_and_normalize_image(img_data, MAX_ARTWORK_SIZE_BYTES)
    ext = ".png" if mime_type == "image/png" else ".jpg"

    try:
        project_storage_manager.save_album_cover(user["id"], album_id, clean_bytes, extension=ext)
        return {"status": "saved", "mime_type": mime_type}
    except ProjectStorageQuotaExceeded as e:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(e))


@projects_router.get("/albums/{album_id}/artwork")
async def get_album_artwork(
    album_id: str,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Serve master album cover artwork."""
    cover_path = project_storage_manager.get_album_cover_path(user["id"], album_id)
    if not cover_path or not cover_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Album artwork not found.")
    mime_type = "image/png" if cover_path.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(path=cover_path, media_type=mime_type)


# --- Promotion / Ingestion from `/app` Ephemeral Session ---

class IngestSessionRequest(BaseModel):
    track_id: Optional[str] = Field(default=None, max_length=40)
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
    """Promote an active ephemeral /app session directly into a persistent track workspace take."""
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

    # Determine track workspace (create new if not provided)
    target_track_id = payload.track_id
    if not target_track_id:
        title = payload.new_track_title or Path(original_filename).stem
        new_track = project_storage_manager.create_track(
            user_id=user["id"],
            title=title,
            status=WorkspaceStatus.IN_PROGRESS,
            notes=payload.notes,
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
    }
