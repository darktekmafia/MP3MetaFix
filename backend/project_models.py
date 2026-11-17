"""Data schemas and Pydantic models for the persistent File & Project Storage System."""

import time
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, StrictInt, field_validator


class WorkspaceStatus(str, Enum):
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class StemRole(str, Enum):
    VOCALS = "vocals"
    LEAD_VOCALS = "lead_vocals"
    BACKING_VOCALS = "backing_vocals"
    INSTRUMENTAL = "instrumental"
    DRUMS = "drums"
    PERCUSSION = "percussion"
    BASS = "bass"
    GUITAR = "guitar"
    ACOUSTIC_GUITAR = "acoustic_guitar"
    ELECTRIC_GUITAR = "electric_guitar"
    KEYBOARD = "keyboard"
    PIANO = "piano"
    SYNTH = "synth"
    STRINGS = "strings"
    BRASS = "brass"
    WOODWINDS = "woodwinds"
    FX = "fx"
    OTHER = "other"


class LyricsFormat(str, Enum):
    NONE = "none"
    LRC = "lrc"
    SYLT = "sylt"


# --- Revision Tracking Model ---

class RevisionEntry(BaseModel):
    revision: int = Field(..., ge=1)
    timestamp: float = Field(default_factory=time.time)
    change_summary: str = Field(..., max_length=500)
    changed_fields: List[str] = Field(default_factory=list)


# --- Stem Models ---

class StemMetadata(BaseModel):
    id: str = Field(..., max_length=40)
    role: StemRole = Field(default=StemRole.OTHER)
    filename: str = Field(..., max_length=255)
    format: str = Field(..., max_length=10)
    size_bytes: int = Field(default=0, ge=0)
    created_at: float = Field(default_factory=time.time)


# --- Take Models ---

class TakeMetadata(BaseModel):
    id: str = Field(..., max_length=40)
    label: str = Field(..., max_length=100)
    filename: str = Field(..., max_length=255)
    format: str = Field(..., max_length=10)
    size_bytes: int = Field(default=0, ge=0)
    duration_seconds: float = Field(default=0.0, ge=0.0)
    is_master: bool = Field(default=False)
    revision: int = Field(default=1, ge=1)
    revisions: List[RevisionEntry] = Field(default_factory=list)
    prompt: Optional[str] = Field(default=None, max_length=5000)
    style_tags: Optional[str] = Field(default=None, max_length=500)
    seed: Optional[str] = Field(default=None, max_length=100)
    notes: Optional[str] = Field(default=None, max_length=10000)
    lyrics_format: LyricsFormat = Field(default=LyricsFormat.NONE)
    stems: Dict[str, StemMetadata] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class TakeCreateRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=100)
    prompt: Optional[str] = Field(default=None, max_length=5000)
    style_tags: Optional[str] = Field(default=None, max_length=500)
    seed: Optional[str] = Field(default=None, max_length=100)
    notes: Optional[str] = Field(default=None, max_length=10000)
    is_master: bool = Field(default=False)


class TakeUpdateRequest(BaseModel):
    label: Optional[str] = Field(default=None, min_length=1, max_length=100)
    prompt: Optional[str] = Field(default=None, max_length=5000)
    style_tags: Optional[str] = Field(default=None, max_length=500)
    seed: Optional[str] = Field(default=None, max_length=100)
    notes: Optional[str] = Field(default=None, max_length=10000)
    is_master: Optional[bool] = Field(default=None)
    change_summary: Optional[str] = Field(default=None, max_length=500)


# --- Track Workspace Models ---

class TrackMetadata(BaseModel):
    id: str = Field(..., max_length=40)
    title: str = Field(..., min_length=1, max_length=200)
    artist: Optional[str] = Field(default=None, max_length=200)
    status: WorkspaceStatus = Field(default=WorkspaceStatus.DRAFT)
    revision: int = Field(default=1, ge=1)
    revisions: List[RevisionEntry] = Field(default_factory=list)
    primary_take_id: Optional[str] = Field(default=None, max_length=40)
    master_lyrics: Optional[str] = Field(default=None, max_length=50000)
    notes: Optional[str] = Field(default=None, max_length=10000)
    bpm: Optional[int] = Field(default=None, ge=1, le=500)
    musical_key: Optional[str] = Field(default=None, max_length=20)
    takes_count: int = Field(default=0, ge=0)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class TrackCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    artist: Optional[str] = Field(default=None, max_length=200)
    status: WorkspaceStatus = Field(default=WorkspaceStatus.DRAFT)
    master_lyrics: Optional[str] = Field(default=None, max_length=50000)
    notes: Optional[str] = Field(default=None, max_length=10000)
    bpm: Optional[int] = Field(default=None, ge=1, le=500)
    musical_key: Optional[str] = Field(default=None, max_length=20)


class TrackUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    artist: Optional[str] = Field(default=None, max_length=200)
    status: Optional[WorkspaceStatus] = Field(default=None)
    primary_take_id: Optional[str] = Field(default=None, max_length=40)
    master_lyrics: Optional[str] = Field(default=None, max_length=50000)
    notes: Optional[str] = Field(default=None, max_length=10000)
    bpm: Optional[int] = Field(default=None, ge=1, le=500)
    musical_key: Optional[str] = Field(default=None, max_length=20)
    change_summary: Optional[str] = Field(default=None, max_length=500)


class TrackDetailResponse(BaseModel):
    track: TrackMetadata
    takes: List[TakeMetadata] = Field(default_factory=list)


# --- Album Workspace Models ---

class AlbumEntrySnapshot(BaseModel):
    entry_id: str = Field(..., max_length=40)
    source_track_id: str = Field(..., max_length=40)
    source_track_title: str = Field(..., max_length=200)
    source_track_artist: Optional[str] = Field(default=None, max_length=200)
    source_track_revision: int = Field(default=1, ge=1)
    source_take_id: str = Field(..., max_length=40)
    source_take_label: str = Field(..., max_length=100)
    source_take_revision: int = Field(default=1, ge=1)
    source_take_updated_at: float = Field(default=0.0)
    source_filename: str = Field(..., max_length=255)
    format: str = Field(..., max_length=10)
    size_bytes: int = Field(default=0, ge=0)
    duration_seconds: float = Field(default=0.0, ge=0.0)
    audio_sha256: str = Field(..., max_length=64)
    embedded_metadata: Dict[str, Any] = Field(default_factory=dict)
    embedded_lyrics: Optional[str] = Field(default=None, max_length=50000)
    take_notes: Optional[str] = Field(default=None, max_length=10000)
    take_prompt: Optional[str] = Field(default=None, max_length=5000)
    take_style_tags: Optional[str] = Field(default=None, max_length=500)
    copied_at: float = Field(default_factory=time.time)


class AlbumTrackEntry(BaseModel):
    id: Optional[str] = Field(default=None, max_length=40)
    track_number: int = Field(..., ge=1, le=999)
    disc_number: int = Field(default=1, ge=1, le=99)
    track_id: str = Field(..., max_length=40)
    take_id: Optional[str] = Field(default=None, max_length=40)
    custom_title: Optional[str] = Field(default=None, max_length=200)
    is_placeholder: bool = Field(default=False)
    has_owned_audio: bool = Field(default=False)
    snapshot: Optional[AlbumEntrySnapshot] = Field(default=None)

    def model_post_init(self, __context: Any) -> None:
        if self.take_id is None:
            self.is_placeholder = True
            self.has_owned_audio = False
            self.snapshot = None


class AlbumMetadata(BaseModel):
    id: str = Field(..., max_length=40)
    title: str = Field(..., min_length=1, max_length=200)
    album_artist: Optional[str] = Field(default=None, max_length=200)
    year: Optional[int] = Field(default=None, ge=1900, le=2100)
    genre: Optional[str] = Field(default=None, max_length=100)
    credits: Optional[str] = Field(default=None, max_length=10000)
    status: WorkspaceStatus = Field(default=WorkspaceStatus.DRAFT)
    is_finalized: bool = Field(default=False)
    finalized_at: Optional[float] = Field(default=None)
    revision: int = Field(default=1, ge=1)
    revisions: List[RevisionEntry] = Field(default_factory=list)
    has_cover: bool = Field(default=False)
    tracks: List[AlbumTrackEntry] = Field(default_factory=list)
    track_ids: List[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    def model_post_init(self, __context: Any) -> None:
        if self.status == WorkspaceStatus.COMPLETED:
            self.is_finalized = True
        if not self.track_ids and self.tracks:
            self.track_ids = [t.track_id for t in self.tracks]
        elif not self.tracks and self.track_ids:
            self.tracks = [
                AlbumTrackEntry(track_number=i + 1, disc_number=1, track_id=tid, take_id=None, is_placeholder=True)
                for i, tid in enumerate(self.track_ids)
            ]


class AlbumCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    album_artist: Optional[str] = Field(default=None, max_length=200)
    year: Optional[int] = Field(default=None, ge=1900, le=2100)
    genre: Optional[str] = Field(default=None, max_length=100)
    credits: Optional[str] = Field(default=None, max_length=10000)
    status: WorkspaceStatus = Field(default=WorkspaceStatus.DRAFT)


class AlbumUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    album_artist: Optional[str] = Field(default=None, max_length=200)
    year: Optional[int] = Field(default=None, ge=1900, le=2100)
    genre: Optional[str] = Field(default=None, max_length=100)
    credits: Optional[str] = Field(default=None, max_length=10000)
    status: Optional[WorkspaceStatus] = Field(default=None)
    expected_revision: StrictInt = Field(..., ge=1)
    tracks: Optional[List[AlbumTrackEntry]] = Field(default=None)
    track_ids: Optional[List[str]] = Field(default=None)
    change_summary: Optional[str] = Field(default=None, max_length=500)


class AlbumEntryAddRequest(BaseModel):
    track_id: str = Field(..., max_length=40)
    take_id: Optional[str] = Field(default=None, max_length=40)
    disc_number: int = Field(default=1, ge=1, le=99)
    track_number: Optional[int] = Field(default=None, ge=1, le=999)
    custom_title: Optional[str] = Field(default=None, max_length=200)
    expected_revision: StrictInt = Field(..., ge=1)


class AlbumEntryUpdateRequest(BaseModel):
    take_id: Optional[str] = Field(default=None, max_length=40)
    clear_take_to_placeholder: bool = Field(default=False)
    disc_number: Optional[int] = Field(default=None, ge=1, le=99)
    custom_title: Optional[str] = Field(default=None, max_length=200)
    expected_revision: StrictInt = Field(..., ge=1)


class AlbumEntryRefreshPreviewResponse(BaseModel):
    album_id: str
    entry_id: str
    track_id: str
    take_id: Optional[str] = None
    source_available: bool
    unavailable_reason: Optional[str] = None
    has_changes: bool = False
    audio_changed: bool = False
    lyrics_changed: bool = False
    lyrics_diff: str = ""
    metadata_changes: List[Dict[str, Any]] = Field(default_factory=list)
    album_snapshot: Optional[AlbumEntrySnapshot] = None
    source_snapshot: Optional[AlbumEntrySnapshot] = None
    expected_source_revision: Optional[int] = None
    expected_source_audio_sha256: Optional[str] = None
    expected_source_updated_at: Optional[float] = None
    album_audio_stream_url: Optional[str] = None
    source_audio_stream_url: Optional[str] = None


class AlbumEntryRefreshConfirmRequest(BaseModel):
    expected_source_revision: int = Field(..., ge=1)
    expected_source_audio_sha256: str = Field(..., min_length=64, max_length=64)
    expected_source_updated_at: Optional[float] = Field(default=None)


# --- Storage Quota Models ---

class StorageQuotaStats(BaseModel):
    user_id: str
    used_bytes: int = Field(default=0, ge=0)
    used_mb: float = Field(default=0.0, ge=0.0)
    max_quota_bytes: int = Field(default=0, ge=0)
    max_quota_mb: float = Field(default=0.0, ge=0.0)
    used_percent: float = Field(default=0.0, ge=0.0)
    limit_bytes: int = Field(default=0, ge=0)
    percent_used: float = Field(default=0.0, ge=0.0)
    tracks_count: int = Field(default=0, ge=0)
    takes_count: int = Field(default=0, ge=0)
    albums_count: int = Field(default=0, ge=0)
