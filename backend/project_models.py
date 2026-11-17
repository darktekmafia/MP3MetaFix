"""Data schemas and Pydantic models for the persistent File & Project Storage System."""

import re
import time
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator

_SHA256_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _validate_safe_rel_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value or value.startswith("/"):
        raise ValueError("Unsafe or non-relative artifact path.")
    parts = value.split("/")
    if any(not p or p in (".", "..") for p in parts):
        raise ValueError("Path traversal or empty segment is not permitted.")
    return value


def _validate_safe_active_basename(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or value in (".", "..")
        or value.startswith(".")
    ):
        raise ValueError("Unsafe active artifact filename.")
    return value


def _validate_sha256_hex(value: str) -> str:
    if not isinstance(value, str) or not _SHA256_HEX_RE.fullmatch(value):
        raise ValueError("Expected a 64-character hexadecimal SHA-256 digest.")
    return value.lower()


def _validate_safe_token(value: str) -> str:
    if not isinstance(value, str) or not value or not _SAFE_TOKEN_RE.fullmatch(value) or value in (".", ".."):
        raise ValueError("Identifier contains unsafe characters.")
    return value


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
    active_generation: Optional[StrictInt] = Field(default=None, ge=1)
    active_audio_filename: Optional[str] = Field(default=None, max_length=255)
    active_snapshot_filename: Optional[str] = Field(default=None, max_length=255)

    @field_validator("active_audio_filename", "active_snapshot_filename")
    @classmethod
    def _check_active_filenames(cls, v: Optional[str]) -> Optional[str]:
        return _validate_safe_active_basename(v)

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
    cover_generation: Optional[StrictInt] = Field(default=None, ge=1)
    active_cover_filename: Optional[str] = Field(default=None, max_length=255)
    last_tx_id: Optional[str] = Field(default=None, max_length=64)
    tx_history: List[str] = Field(default_factory=list)
    is_deleted: bool = Field(default=False)
    deleted_at: Optional[float] = Field(default=None)
    delete_tx_id: Optional[str] = Field(default=None, max_length=64)

    @field_validator("active_cover_filename")
    @classmethod
    def _check_active_cover_filename(cls, v: Optional[str]) -> Optional[str]:
        return _validate_safe_active_basename(v)

    @field_validator("last_tx_id", "delete_tx_id")
    @classmethod
    def _check_tx_tokens(cls, v: Optional[str]) -> Optional[str]:
        return _validate_safe_token(v) if v is not None else None

    @field_validator("tx_history")
    @classmethod
    def _check_tx_history(cls, v: List[str]) -> List[str]:
        return [_validate_safe_token(item) for item in v]
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


# --- Phase 1B Transaction Journal & Recovery Models ---

class JournalTxState(str, Enum):
    PREPARED = "prepared"
    PUBLISH_INTENT = "publish_intent"
    UNCERTAIN = "uncertain"
    COMMITTED = "committed"
    ABORTED = "aborted"


class RecoveryConsistency(str, Enum):
    CONSISTENT_IDLE = "consistent_idle"
    PROVISIONAL_COMMIT_CANDIDATE = "provisional_commit_candidate"
    PROVISIONAL_ABORT_CANDIDATE = "provisional_abort_candidate"
    UNCERTAIN = "uncertain"
    CORRUPT = "corrupt"


class StagedArtifactRef(BaseModel):
    rel_path: str = Field(..., min_length=1, max_length=512)
    st_dev: StrictInt = Field(..., ge=0)
    st_ino: StrictInt = Field(..., ge=0)
    sha256: str = Field(..., min_length=64, max_length=64)

    @field_validator("rel_path")
    @classmethod
    def _check_staging_rel_path(cls, v: str) -> str:
        rel = _validate_safe_rel_path(v)
        if not rel.split("/")[-1].startswith(".staging_"):
            raise ValueError("Staged artifact basename must start with '.staging_'.")
        return rel

    @field_validator("sha256")
    @classmethod
    def _check_sha256(cls, v: str) -> str:
        return _validate_sha256_hex(v)


class ActiveTxRecord(BaseModel):
    tx_id: str = Field(..., min_length=1, max_length=64)
    op: str = Field(..., min_length=1, max_length=64)
    state: JournalTxState
    expected_revision: StrictInt = Field(..., ge=1)
    target_revision: StrictInt = Field(..., ge=1)
    payload_sha256: str = Field(..., min_length=64, max_length=64)
    idempotency_key: Optional[str] = Field(default=None, max_length=128)
    staging_refs: List[StagedArtifactRef] = Field(default_factory=list)
    new_refs: List[str] = Field(default_factory=list)
    superseded_refs: List[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)

    @field_validator("tx_id", "op")
    @classmethod
    def _check_tokens(cls, v: str) -> str:
        return _validate_safe_token(v)

    @field_validator("idempotency_key")
    @classmethod
    def _check_idem_key(cls, v: Optional[str]) -> Optional[str]:
        return _validate_safe_token(v) if v is not None else None

    @field_validator("payload_sha256")
    @classmethod
    def _check_payload_sha(cls, v: str) -> str:
        return _validate_sha256_hex(v)

    @field_validator("new_refs", "superseded_refs")
    @classmethod
    def _check_rel_refs(cls, v: List[str]) -> List[str]:
        return [_validate_safe_rel_path(item) for item in v]

    @model_validator(mode="after")
    def _check_tx_consistency(self) -> "ActiveTxRecord":
        if self.target_revision <= self.expected_revision:
            raise ValueError("target_revision must be strictly greater than expected_revision.")
        stg_paths = [r.rel_path for r in self.staging_refs]
        stg_set = set(stg_paths)
        if len(stg_set) != len(stg_paths):
            raise ValueError("staging_refs must not contain duplicate rel_path entries.")
        new_set = set(self.new_refs)
        if len(new_set) != len(self.new_refs):
            raise ValueError("new_refs must not contain duplicate entries.")
        sup_set = set(self.superseded_refs)
        if len(sup_set) != len(self.superseded_refs):
            raise ValueError("superseded_refs must not contain duplicate entries.")
        if new_set & sup_set:
            raise ValueError("new_refs and superseded_refs must not overlap.")
        if stg_set & (new_set | sup_set):
            raise ValueError("staging_refs must not overlap new_refs or superseded_refs.")
        return self


class RetainedArtifactRecord(BaseModel):
    rel_path: str = Field(..., min_length=1, max_length=512)
    sha256: str = Field(..., min_length=64, max_length=64)
    kind: str = Field(..., min_length=1, max_length=32)
    generation: Optional[StrictInt] = Field(default=None, ge=1)
    superseded_by_tx_id: str = Field(..., min_length=1, max_length=64)
    superseded_at_rev: StrictInt = Field(..., ge=1)
    superseded_at: float = Field(default_factory=time.time)

    @field_validator("rel_path")
    @classmethod
    def _check_rel_path(cls, v: str) -> str:
        return _validate_safe_rel_path(v)

    @field_validator("sha256")
    @classmethod
    def _check_sha(cls, v: str) -> str:
        return _validate_sha256_hex(v)

    @field_validator("superseded_by_tx_id", "kind")
    @classmethod
    def _check_tokens(cls, v: str) -> str:
        return _validate_safe_token(v)


class IdempotencyRecord(BaseModel):
    key: str = Field(..., min_length=1, max_length=128)
    op: str = Field(..., min_length=1, max_length=64)
    expected_revision: StrictInt = Field(..., ge=1)
    payload_sha256: str = Field(..., min_length=64, max_length=64)
    result_revision: Optional[StrictInt] = Field(default=None, ge=1)
    tx_id: str = Field(..., min_length=1, max_length=64)
    outcome: JournalTxState
    created_at: float = Field(default_factory=time.time)

    @field_validator("key", "op", "tx_id")
    @classmethod
    def _check_tokens(cls, v: str) -> str:
        return _validate_safe_token(v)

    @field_validator("payload_sha256")
    @classmethod
    def _check_sha(cls, v: str) -> str:
        return _validate_sha256_hex(v)

    @model_validator(mode="after")
    def _check_outcome_consistency(self) -> "IdempotencyRecord":
        if self.outcome == JournalTxState.COMMITTED:
            if self.result_revision is None or self.result_revision <= self.expected_revision:
                raise ValueError("Committed idempotency record requires result_revision > expected_revision.")
        elif self.outcome == JournalTxState.ABORTED and self.result_revision is not None:
            raise ValueError("Aborted idempotency record must not set result_revision.")
        return self


class AlbumJournal(BaseModel):
    schema_version: StrictInt = Field(default=1, ge=1, le=1)
    album_id: str = Field(..., min_length=4, max_length=40)
    active_tx: Optional[ActiveTxRecord] = Field(default=None)
    retained_artifacts: List[RetainedArtifactRecord] = Field(default_factory=list)
    idempotency_records: List[IdempotencyRecord] = Field(default_factory=list)

    @field_validator("album_id")
    @classmethod
    def _check_album_id(cls, v: str) -> str:
        return _validate_safe_token(v)

    @model_validator(mode="after")
    def _check_journal_consistency(self) -> "AlbumJournal":
        seen_keys: set[str] = set()
        seen_tx_ids: set[str] = set()
        for rec in self.idempotency_records:
            if rec.key in seen_keys:
                raise ValueError(f"Duplicate idempotency key '{rec.key}' in album journal.")
            seen_keys.add(rec.key)
            if rec.tx_id in seen_tx_ids:
                raise ValueError(f"Duplicate idempotency transaction id '{rec.tx_id}' in album journal.")
            seen_tx_ids.add(rec.tx_id)

        seen_retained: set[str] = set()
        for ret in self.retained_artifacts:
            if ret.rel_path in seen_retained:
                raise ValueError(f"Duplicate retained artifact path '{ret.rel_path}' in album journal.")
            seen_retained.add(ret.rel_path)

        if self.active_tx is not None:
            tx = self.active_tx
            active_paths = set(tx.new_refs) | {r.rel_path for r in tx.staging_refs}
            if seen_retained & active_paths:
                raise ValueError("Retained artifacts must not overlap active staging or new references.")
            for rec in self.idempotency_records:
                if tx.idempotency_key is not None and rec.key == tx.idempotency_key:
                    if (
                        rec.tx_id != tx.tx_id
                        or rec.op != tx.op
                        or rec.expected_revision != tx.expected_revision
                        or rec.payload_sha256 != tx.payload_sha256
                    ):
                        raise ValueError("Active transaction conflicts with existing idempotency record.")
                if rec.tx_id == tx.tx_id:
                    if (
                        rec.key != tx.idempotency_key
                        or rec.op != tx.op
                        or rec.expected_revision != tx.expected_revision
                        or rec.payload_sha256 != tx.payload_sha256
                    ):
                        raise ValueError("Active transaction id conflicts with existing idempotency record.")
                    if rec.outcome in (JournalTxState.COMMITTED, JournalTxState.ABORTED) and tx.state != rec.outcome:
                        raise ValueError("Active transaction state contradicts terminal idempotency outcome.")
                    if rec.outcome == JournalTxState.COMMITTED and rec.result_revision != tx.target_revision:
                        raise ValueError("Committed idempotency result_revision must match active_tx target_revision.")
        return self
