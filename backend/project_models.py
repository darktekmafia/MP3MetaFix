"""Data schemas and Pydantic models for the persistent File & Project Storage System."""

import time
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator


class WorkspaceStatus(str, Enum):
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class StemRole(str, Enum):
    VOCALS = "vocals"
    INSTRUMENTAL = "instrumental"
    BASS = "bass"
    DRUMS = "drums"
    OTHER = "other"


class LyricsFormat(str, Enum):
    NONE = "none"
    LRC = "lrc"
    SYLT = "sylt"


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


# --- Track Workspace Models ---

class TrackMetadata(BaseModel):
    id: str = Field(..., max_length=40)
    title: str = Field(..., min_length=1, max_length=200)
    artist: Optional[str] = Field(default=None, max_length=200)
    status: WorkspaceStatus = Field(default=WorkspaceStatus.DRAFT)
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


class TrackDetailResponse(BaseModel):
    track: TrackMetadata
    takes: List[TakeMetadata] = Field(default_factory=list)


# --- Album Workspace Models ---

class AlbumTrackEntry(BaseModel):
    track_number: int = Field(..., ge=1, le=999)
    disc_number: int = Field(default=1, ge=1, le=99)
    track_id: str = Field(..., max_length=40)
    take_id: Optional[str] = Field(default=None, max_length=40)
    custom_title: Optional[str] = Field(default=None, max_length=200)


class AlbumMetadata(BaseModel):
    id: str = Field(..., max_length=40)
    title: str = Field(..., min_length=1, max_length=200)
    album_artist: Optional[str] = Field(default=None, max_length=200)
    year: Optional[int] = Field(default=None, ge=1900, le=2100)
    genre: Optional[str] = Field(default=None, max_length=100)
    credits: Optional[str] = Field(default=None, max_length=10000)
    status: WorkspaceStatus = Field(default=WorkspaceStatus.DRAFT)
    has_cover: bool = Field(default=False)
    tracks: List[AlbumTrackEntry] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


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
    tracks: Optional[List[AlbumTrackEntry]] = Field(default=None)


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
