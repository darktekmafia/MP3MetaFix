"""Persistent file and workspace storage manager for MP3Projects and MP3MetaManager.

Implements Option A: Self-contained Track & Album Workspaces with decoupled
cryptographic path hashing and strict POSIX 0700 filesystem isolation.
"""

import os
import json
import uuid
import time
import shutil
import hashlib
import difflib
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, Callable

from backend.config import (
    STORAGE_DIR,
    TENANTS_DIR,
    ALLOWED_AUDIO_EXTENSIONS,
    get_runtime_global_storage_bytes,
)
from backend.locking import file_lock, atomic_json
from backend.security import (
    sanitize_filename,
    generate_track_id,
    generate_take_id,
    generate_stem_id,
    generate_album_id,
    generate_album_entry_id,
    generate_deterministic_legacy_entry_id,
    is_valid_project_id,
    get_tenant_storage_dir_name,
    get_track_dir_name,
    get_take_dir_name,
    get_album_dir_name,
    get_album_entry_dir_name,
)
from backend.project_models import (
    WorkspaceStatus,
    StemRole,
    LyricsFormat,
    RevisionEntry,
    StemMetadata,
    TakeMetadata,
    TrackMetadata,
    AlbumEntrySnapshot,
    AlbumTrackEntry,
    AlbumMetadata,
    AlbumEntryRefreshPreviewResponse,
    StorageQuotaStats,
)
from backend.audio_formats import AUDIO_FORMATS, matches_audio_header, validate_audio_container

logger = logging.getLogger("mp3metafix.project_storage")


def _probe_audio_duration(audio_path: Path) -> float:
    """Probe audio duration in seconds via Mutagen with stdlib wave fallback for WAV files."""
    try:
        import mutagen
        audio = mutagen.File(str(audio_path))
        if audio is not None and getattr(audio, "info", None) is not None:
            length = getattr(audio.info, "length", 0.0)
            if length and float(length) > 0.0:
                return round(float(length), 2)
    except Exception as e:
        logger.debug(f"Mutagen duration probe failed for {audio_path.name}: {e}")

    if audio_path.suffix.lower() == ".wav":
        try:
            import wave
            with wave.open(str(audio_path), "rb") as wav_file:
                frames = wav_file.getnframes()
                rate = wav_file.getframerate()
                if rate > 0 and frames > 0:
                    return round(frames / float(rate), 2)
        except Exception as e:
            logger.debug(f"Wave duration probe failed for {audio_path.name}: {e}")

    return 0.0


def _compute_file_sha256(file_path: Path) -> str:
    """Compute hex SHA-256 digest of a file in bounded chunks."""
    digest = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class ProjectStorageError(Exception):
    """Base exception for project storage operations."""
    pass


class ProjectStorageQuotaExceeded(ProjectStorageError):
    """Raised when an operation would exceed storage quota."""
    pass


class ProjectValidationError(ProjectStorageError):
    """Raised when an operation fails domain, ownership, or reference validation."""
    pass


class AlbumFinalizedError(ProjectStorageError):
    """Raised when attempting to mutate a finalized album."""
    pass


class AlbumRefreshConflictError(ProjectStorageError):
    """Raised when source take state changed after preview or source is unavailable."""
    pass


class AlbumFinalizationError(ProjectStorageError):
    """Raised when an album fails finalization validation checks."""

    def __init__(self, message: str, errors: Optional[List[Dict[str, Any]]] = None):
        super().__init__(message)
        self.errors = errors or []


class ProjectStorageManager:
    """Manages persistent tenant directories, track workspaces, takes, and albums."""

    def __init__(self, root_dir: Path = TENANTS_DIR):
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root_dir, 0o700)
        except Exception:
            pass

    # --- Directory Helpers with POSIX 0700 Enforcement ---

    def _ensure_dir_0700(self, directory: Path) -> Path:
        """Create directory if not existing and enforce strict POSIX 0700 permissions."""
        directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except Exception:
            pass
        return directory

    def get_tenant_dir(self, user_id: str) -> Path:
        """Return isolated directory for a given user/tenant."""
        dir_name = get_tenant_storage_dir_name(user_id)
        tdir = self.root_dir / dir_name
        return self._ensure_dir_0700(tdir)

    def _get_tracks_root(self, user_id: str) -> Path:
        tdir = self.get_tenant_dir(user_id)
        return self._ensure_dir_0700(tdir / "tracks")

    def _get_albums_root(self, user_id: str) -> Path:
        tdir = self.get_tenant_dir(user_id)
        return self._ensure_dir_0700(tdir / "albums")

    def _get_track_dir(self, user_id: str, track_id: str) -> Path:
        if not is_valid_project_id(track_id, expected_prefix="trk"):
            raise ValueError("Invalid track identifier format.")
        dir_name = get_track_dir_name(track_id)
        return self._get_tracks_root(user_id) / dir_name

    def _get_take_dir(self, user_id: str, track_id: str, take_id: str) -> Path:
        if not is_valid_project_id(take_id, expected_prefix="tak"):
            raise ValueError("Invalid take identifier format.")
        track_dir = self._get_track_dir(user_id, track_id)
        takes_root = self._ensure_dir_0700(track_dir / "takes")
        dir_name = get_take_dir_name(take_id)
        return takes_root / dir_name

    def _get_album_dir(self, user_id: str, album_id: str) -> Path:
        if not is_valid_project_id(album_id, expected_prefix="alb"):
            raise ValueError("Invalid album identifier format.")
        dir_name = get_album_dir_name(album_id)
        return self._get_albums_root(user_id) / dir_name

    # --- Quota & Storage Accounting ---

    def get_tenant_storage_size_bytes(self, user_id: str) -> int:
        """Calculate total disk space consumed by a tenant's tracks, takes, stems, and albums."""
        tenant_dir = self.get_tenant_dir(user_id)
        total = 0
        if not tenant_dir.exists():
            return 0
        try:
            for dirpath, _, filenames in os.walk(tenant_dir):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    try:
                        total += os.path.getsize(fp)
                    except OSError:
                        pass
        except Exception as e:
            logger.warning(f"Error calculating tenant storage size for {user_id}: {e}")
        return total

    def get_quota_stats(self, user_id: str) -> StorageQuotaStats:
        """Return aggregated quota metrics and item counts for a tenant."""
        used_bytes = self.get_tenant_storage_size_bytes(user_id)
        max_quota = get_runtime_global_storage_bytes()
        
        tracks = self.list_tracks(user_id)
        tracks_count = len(tracks)
        takes_count = sum(t.takes_count for t in tracks)
        albums_count = len(self.list_albums(user_id))

        used_mb = round(used_bytes / (1024 * 1024), 2)
        max_quota_mb = round(max_quota / (1024 * 1024), 2)
        used_percent = round((used_bytes / max_quota * 100), 1) if max_quota > 0 else 0.0

        return StorageQuotaStats(
            user_id=user_id,
            used_bytes=used_bytes,
            used_mb=used_mb,
            max_quota_bytes=max_quota,
            max_quota_mb=max_quota_mb,
            used_percent=used_percent,
            limit_bytes=max_quota,
            percent_used=used_percent,
            tracks_count=tracks_count,
            takes_count=takes_count,
            albums_count=albums_count,
        )

    def ensure_quota_available(self, user_id: str, required_bytes: int = 0) -> bool:
        """Verify storage capacity before committing incoming uploads."""
        current_size = self.get_tenant_storage_size_bytes(user_id)
        max_quota = get_runtime_global_storage_bytes()
        if (current_size + required_bytes) > max_quota:
            raise ProjectStorageQuotaExceeded(
                f"Storage quota exceeded ({round((current_size + required_bytes) / (1024*1024), 1)}MB > {round(max_quota / (1024*1024), 1)}MB)"
            )
        return True

    # --- Track Workspace Management ---

    def create_track(
        self,
        user_id: str,
        title: str,
        artist: Optional[str] = None,
        status: WorkspaceStatus = WorkspaceStatus.DRAFT,
        master_lyrics: Optional[str] = None,
        notes: Optional[str] = None,
        bpm: Optional[int] = None,
        musical_key: Optional[str] = None,
    ) -> TrackMetadata:
        """Create a new track workspace."""
        track_id = generate_track_id()
        track_dir = self._get_track_dir(user_id, track_id)
        self._ensure_dir_0700(track_dir)
        self._ensure_dir_0700(track_dir / "takes")

        now = time.time()
        track_meta = TrackMetadata(
            id=track_id,
            title=title.strip(),
            artist=artist.strip() if artist else None,
            status=status,
            revision=1,
            revisions=[
                RevisionEntry(
                    revision=1,
                    timestamp=now,
                    change_summary="Initial track workspace creation",
                    changed_fields=[],
                )
            ],
            primary_take_id=None,
            master_lyrics=master_lyrics,
            notes=notes,
            bpm=bpm,
            musical_key=musical_key.strip() if musical_key else None,
            takes_count=0,
            created_at=now,
            updated_at=now,
        )

        manifest_path = track_dir / "track.json"
        with file_lock(manifest_path):
            atomic_json(manifest_path, track_meta.model_dump())

        return track_meta

    def get_track(self, user_id: str, track_id: str) -> Optional[TrackMetadata]:
        """Retrieve track metadata by ID."""
        try:
            track_dir = self._get_track_dir(user_id, track_id)
        except ValueError:
            return None
        manifest_path = track_dir / "track.json"
        if not manifest_path.is_file():
            return None
        try:
            with file_lock(manifest_path):
                import json
                with open(manifest_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            # Synchronize active take count
            takes_dir = track_dir / "takes"
            actual_count = 0
            if takes_dir.exists():
                for item in takes_dir.iterdir():
                    if item.is_dir() and (item / "take.json").is_file():
                        actual_count += 1
            data["takes_count"] = actual_count
            return TrackMetadata(**data)
        except Exception as e:
            logger.error(f"Failed to read track manifest {track_id}: {e}")
            return None

    def list_tracks(self, user_id: str, status: Optional[WorkspaceStatus] = None) -> List[TrackMetadata]:
        """List all track workspaces for a user, optionally filtered by status."""
        tracks_root = self._get_tracks_root(user_id)
        results = []
        if not tracks_root.exists():
            return results

        for item in tracks_root.iterdir():
            if item.is_dir():
                manifest_path = item / "track.json"
                if manifest_path.is_file():
                    try:
                        import json
                        with open(manifest_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        # Count takes
                        takes_dir = item / "takes"
                        actual_count = 0
                        if takes_dir.exists():
                            for tdir in takes_dir.iterdir():
                                if tdir.is_dir() and (tdir / "take.json").is_file():
                                    actual_count += 1
                        data["takes_count"] = actual_count
                        meta = TrackMetadata(**data)
                        if status is None or meta.status == status:
                            results.append(meta)
                    except Exception as e:
                        logger.warning(f"Skipping corrupted track {item.name}: {e}")

        # Sort newest first
        results.sort(key=lambda t: t.updated_at, reverse=True)
        return results

    def _set_exclusive_master_flag(
        self,
        user_id: str,
        track_id: str,
        master_take_id: Optional[str],
    ) -> None:
        """Ensure at most one take in the track has is_master=True matching master_take_id."""
        track_dir = self._get_track_dir(user_id, track_id)
        takes_root = track_dir / "takes"
        if not takes_root.exists():
            return

        for item in takes_root.iterdir():
            if not item.is_dir():
                continue
            manifest_path = item / "take.json"
            if not manifest_path.is_file():
                continue
            try:
                with file_lock(manifest_path):
                    with open(manifest_path, "r", encoding="utf-8") as f:
                        t_data = json.load(f)
                    should_be_master = (master_take_id is not None and t_data.get("id") == master_take_id)
                    if bool(t_data.get("is_master", False)) != should_be_master:
                        t_data["is_master"] = should_be_master
                        TakeMetadata(**t_data)
                        atomic_json(manifest_path, t_data)
            except Exception as e:
                logger.warning(f"Failed syncing master flag for take dir {item.name}: {e}")

    def _record_track_revision(
        self,
        user_id: str,
        track_id: str,
        changed_fields: List[str],
        change_summary: str,
        field_updates: Optional[Dict[str, Any]] = None,
    ) -> Optional[TrackMetadata]:
        """Atomically update selected track fields and append one revision entry."""
        try:
            track_dir = self._get_track_dir(user_id, track_id)
        except ValueError:
            return None
        manifest_path = track_dir / "track.json"
        if not manifest_path.is_file():
            return None

        with file_lock(manifest_path):
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if field_updates:
                for k, v in field_updates.items():
                    data[k] = v

            new_rev = int(data.get("revision", 1)) + 1
            now = time.time()
            data["revision"] = new_rev
            data["updated_at"] = now
            if "revisions" not in data or not isinstance(data["revisions"], list):
                data["revisions"] = []
            data["revisions"].append({
                "revision": new_rev,
                "timestamp": now,
                "change_summary": change_summary,
                "changed_fields": changed_fields,
            })
            atomic_json(manifest_path, data)

        return self.get_track(user_id, track_id)

    def update_track(
        self,
        user_id: str,
        track_id: str,
        updates: Dict[str, Any],
        change_summary: Optional[str] = None,
    ) -> Optional[TrackMetadata]:
        """Update fields in track workspace manifest with revision history."""
        track = self.get_track(user_id, track_id)
        if not track:
            return None

        track_dir = self._get_track_dir(user_id, track_id)
        manifest_path = track_dir / "track.json"

        data = track.model_dump()
        summary_text = updates.pop("change_summary", None) or change_summary

        # Ignore unknown primary_take_id references so master takes remain valid
        if "primary_take_id" in updates and updates["primary_take_id"] is not None:
            if not self.get_take(user_id, track_id, updates["primary_take_id"]):
                updates.pop("primary_take_id", None)

        ignored_fields = ("id", "created_at", "takes_count", "revisions", "revision", "updated_at")
        changed_fields = []
        for k, v in updates.items():
            if v is not None and k in data and k not in ignored_fields and data.get(k) != v:
                changed_fields.append(k)
                data[k] = v

        if changed_fields:
            new_rev = int(data.get("revision", 1)) + 1
            data["revision"] = new_rev
            summary = summary_text if summary_text else f"Updated {', '.join(changed_fields)}"
            if "revisions" not in data or not isinstance(data["revisions"], list):
                data["revisions"] = []
            data["revisions"].append({
                "revision": new_rev,
                "timestamp": time.time(),
                "change_summary": summary,
                "changed_fields": changed_fields,
            })

        data["updated_at"] = time.time()

        with file_lock(manifest_path):
            atomic_json(manifest_path, data)

        if "primary_take_id" in changed_fields and data.get("primary_take_id"):
            self._set_exclusive_master_flag(user_id, track_id, data["primary_take_id"])

        return TrackMetadata(**data)

    def delete_track(self, user_id: str, track_id: str) -> bool:
        """Delete an entire track workspace and all its takes and stems."""
        try:
            track_dir = self._get_track_dir(user_id, track_id)
        except ValueError:
            return False
        manifest_path = track_dir / "track.json"
        if not manifest_path.is_file():
            return False
        try:
            shutil.rmtree(track_dir, ignore_errors=True)
            return True
        except Exception as e:
            logger.error(f"Failed to delete track {track_id}: {e}")
            return False

    # --- Take & Audio File Management ---

    def create_take(
        self,
        user_id: str,
        track_id: str,
        audio_bytes: bytes,
        original_filename: str,
        extension: str = ".mp3",
        label: Optional[str] = None,
        prompt: Optional[str] = None,
        style_tags: Optional[str] = None,
        seed: Optional[str] = None,
        notes: Optional[str] = None,
        is_master: bool = False,
        duration_seconds: float = 0.0,
    ) -> TakeMetadata:
        """Create a new take inside a track workspace with audio file persistence."""
        track = self.get_track(user_id, track_id)
        if not track:
            raise ValueError("Track workspace does not exist.")

        if extension not in AUDIO_FORMATS:
            raise ValueError(f"Unsupported audio extension: {extension}")

        # Quota verification
        self.ensure_quota_available(user_id, len(audio_bytes))

        take_id = generate_take_id()
        take_dir = self._get_take_dir(user_id, track_id, take_id)
        self._ensure_dir_0700(take_dir)
        self._ensure_dir_0700(take_dir / "stems")

        clean_filename = sanitize_filename(original_filename, extension=extension)
        audio_file_path = take_dir / f"audio{extension}"

        # Write audio data atomically
        tmp_audio = take_dir / f".tmp_audio{extension}"
        with open(tmp_audio, "wb") as f:
            f.write(audio_bytes)
        os.replace(tmp_audio, audio_file_path)
        try:
            os.chmod(audio_file_path, 0o600)
        except Exception:
            pass

        if float(duration_seconds or 0.0) <= 0.0:
            probed_duration = _probe_audio_duration(audio_file_path)
            if probed_duration > 0.0:
                duration_seconds = probed_duration

        now = time.time()
        effective_is_master = bool(is_master) or (track.primary_take_id is None)
        take_label = label.strip() if label else f"Take {track.takes_count + 1}"
        take_meta = TakeMetadata(
            id=take_id,
            label=take_label,
            filename=clean_filename,
            format=extension.lstrip(".").lower(),
            size_bytes=len(audio_bytes),
            duration_seconds=duration_seconds,
            is_master=effective_is_master,
            revision=1,
            revisions=[
                RevisionEntry(
                    revision=1,
                    timestamp=now,
                    change_summary="Initial take audio creation",
                    changed_fields=[],
                )
            ],
            prompt=prompt,
            style_tags=style_tags,
            seed=seed,
            notes=notes,
            lyrics_format=LyricsFormat.NONE,
            stems={},
            created_at=now,
            updated_at=now,
        )

        manifest_path = take_dir / "take.json"
        with file_lock(manifest_path):
            atomic_json(manifest_path, take_meta.model_dump())

        if effective_is_master:
            self._set_exclusive_master_flag(user_id, track_id, take_id)
            if track.primary_take_id != take_id:
                self._record_track_revision(
                    user_id,
                    track_id,
                    ["takes", "primary_take_id"],
                    f"Added master take: {take_meta.label}",
                    {"primary_take_id": take_id},
                )
            else:
                self._record_track_revision(
                    user_id,
                    track_id,
                    ["takes"],
                    f"Added master take: {take_meta.label}",
                )
        else:
            self._record_track_revision(
                user_id,
                track_id,
                ["takes"],
                f"Added take: {take_meta.label}",
            )

        return take_meta

    def get_take(self, user_id: str, track_id: str, take_id: str) -> Optional[TakeMetadata]:
        """Retrieve take metadata."""
        try:
            take_dir = self._get_take_dir(user_id, track_id, take_id)
        except ValueError:
            return None
        manifest_path = take_dir / "take.json"
        if not manifest_path.is_file():
            return None
        try:
            import json
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return TakeMetadata(**data)
        except Exception as e:
            logger.error(f"Failed to read take manifest {take_id}: {e}")
            return None

    def list_takes(self, user_id: str, track_id: str) -> List[TakeMetadata]:
        """List all takes in a track workspace."""
        try:
            track_dir = self._get_track_dir(user_id, track_id)
        except ValueError:
            return []
        takes_root = track_dir / "takes"
        results = []
        if not takes_root.exists():
            return results

        for item in takes_root.iterdir():
            if item.is_dir():
                manifest_path = item / "take.json"
                if manifest_path.is_file():
                    try:
                        import json
                        with open(manifest_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        results.append(TakeMetadata(**data))
                    except Exception as e:
                        logger.warning(f"Skipping corrupted take {item.name}: {e}")

        # Sort chronological (oldest to newest)
        results.sort(key=lambda t: t.created_at)
        return results

    def update_take(
        self,
        user_id: str,
        track_id: str,
        take_id: str,
        updates: Dict[str, Any],
        change_summary: Optional[str] = None,
    ) -> Optional[TakeMetadata]:
        """Update take metadata (label, prompt, notes, is_master) with revision tracking."""
        take = self.get_take(user_id, track_id, take_id)
        if not take:
            return None

        track = self.get_track(user_id, track_id)
        take_dir = self._get_take_dir(user_id, track_id, take_id)
        manifest_path = take_dir / "take.json"

        data = take.model_dump()
        summary_text = updates.pop("change_summary", None) or change_summary

        ignored_fields = ("id", "created_at", "filename", "format", "size_bytes", "revisions", "revision", "updated_at", "stems")
        changed_fields = []
        for k, v in updates.items():
            if v is None or k not in data or k in ignored_fields:
                continue
            if k == "is_master":
                # Keep the active primary take flagged as master until another take takes over
                if v is False and track and track.primary_take_id == take_id:
                    continue
                v = bool(v)
            if data.get(k) != v:
                changed_fields.append(k)
                data[k] = v

        if not changed_fields:
            if updates.get("is_master") is True:
                self._set_exclusive_master_flag(user_id, track_id, take_id)
                if track and track.primary_take_id != take_id:
                    self._record_track_revision(
                        user_id,
                        track_id,
                        ["primary_take_id"],
                        f"Set master take to {take.label}",
                        {"primary_take_id": take_id},
                    )
            return self.get_take(user_id, track_id, take_id)

        new_rev = int(data.get("revision", 1)) + 1
        data["revision"] = new_rev
        summary = summary_text if summary_text else f"Updated {', '.join(changed_fields)}"
        if "revisions" not in data or not isinstance(data["revisions"], list):
            data["revisions"] = []
        data["revisions"].append({
            "revision": new_rev,
            "timestamp": time.time(),
            "change_summary": summary,
            "changed_fields": changed_fields,
        })
        data["updated_at"] = time.time()

        with file_lock(manifest_path):
            atomic_json(manifest_path, data)

        updated_take = TakeMetadata(**data)
        if updated_take.is_master:
            self._set_exclusive_master_flag(user_id, track_id, take_id)
            if track and track.primary_take_id != take_id:
                self._record_track_revision(
                    user_id,
                    track_id,
                    ["primary_take_id"],
                    f"Set master take to {updated_take.label}",
                    {"primary_take_id": take_id},
                )

        return updated_take

    def update_take_audio(
        self,
        user_id: str,
        track_id: str,
        take_id: str,
        audio_bytes: bytes,
        extension: str = ".mp3",
        label: Optional[str] = None,
        notes: Optional[str] = None,
        is_master: Optional[bool] = None,
        change_summary: Optional[str] = None,
    ) -> TakeMetadata:
        """Update an existing take's audio file and metadata in place with revision tracking."""
        track = self.get_track(user_id, track_id)
        if not track:
            raise ValueError(f"Track {track_id} not found")
        take = self.get_take(user_id, track_id, take_id)
        if not take:
            raise ValueError(f"Take {take_id} not found in track {track_id}")

        size_diff = len(audio_bytes) - take.size_bytes
        if size_diff > 0:
            self.ensure_quota_available(user_id, size_diff)

        take_dir = self._get_take_dir(user_id, track_id, take_id)
        ext_norm = extension if extension.startswith(".") else f".{extension}"
        ext_norm = ext_norm.lower()
        if ext_norm not in AUDIO_FORMATS:
            ext_norm = ".mp3"
        fmt = ext_norm.lstrip(".")

        # Remove old audio file(s)
        for ext in AUDIO_FORMATS:
            old_f = take_dir / f"audio{ext}"
            if old_f.is_file():
                try:
                    old_f.unlink(missing_ok=True)
                except Exception:
                    pass

        audio_dest = take_dir / f"audio.{fmt}"
        with open(audio_dest, "wb") as f:
            f.write(audio_bytes)
        try:
            os.chmod(audio_dest, 0o600)
        except Exception:
            pass

        # Probe duration if possible, fallback to existing duration
        probed_duration = _probe_audio_duration(audio_dest)
        duration = probed_duration if probed_duration > 0.0 else take.duration_seconds

        data = take.model_dump()
        data["format"] = fmt
        data["size_bytes"] = len(audio_bytes)
        data["duration_seconds"] = duration
        if label:
            data["label"] = label
        if notes is not None:
            data["notes"] = notes

        changed_fields = ["audio"]
        if notes is not None:
            changed_fields.append("notes")

        if is_master is True:
            data["is_master"] = True
            if not take.is_master:
                changed_fields.append("is_master")
        elif is_master is False:
            # Preserve master flag if this take is already the track's active master take
            if track.primary_take_id == take_id:
                data["is_master"] = True
            else:
                data["is_master"] = False
                if take.is_master:
                    changed_fields.append("is_master")

        new_rev = int(data.get("revision", 1)) + 1
        data["revision"] = new_rev
        if "revisions" not in data or not isinstance(data["revisions"], list):
            data["revisions"] = []
        summary = change_summary or (notes if notes else "Updated take audio in place")
        data["revisions"].append({
            "revision": new_rev,
            "timestamp": time.time(),
            "change_summary": summary,
            "changed_fields": changed_fields,
        })
        data["updated_at"] = time.time()

        manifest_path = take_dir / "take.json"
        with file_lock(manifest_path):
            atomic_json(manifest_path, data)

        updated_take = TakeMetadata(**data)
        if updated_take.is_master:
            self._set_exclusive_master_flag(user_id, track_id, take_id)
            if track.primary_take_id != take_id:
                self._record_track_revision(
                    user_id,
                    track_id,
                    ["primary_take_id"],
                    f"Set master take to {updated_take.label}",
                    {"primary_take_id": take_id},
                )

        return updated_take

    def delete_take(self, user_id: str, track_id: str, take_id: str) -> bool:
        """Delete a take and its stems."""
        try:
            take_dir = self._get_take_dir(user_id, track_id, take_id)
        except ValueError:
            return False
        manifest_path = take_dir / "take.json"
        if not manifest_path.is_file():
            return False
        try:
            track = self.get_track(user_id, track_id)
            take = self.get_take(user_id, track_id, take_id)
            shutil.rmtree(take_dir, ignore_errors=True)
            if track:
                remaining = self.list_takes(user_id, track_id)
                was_primary = (track.primary_take_id == take_id) or bool(take and take.is_master)
                take_label = take.label if take else take_id
                if was_primary:
                    new_primary = remaining[-1].id if remaining else None
                    self._set_exclusive_master_flag(user_id, track_id, new_primary)
                    self._record_track_revision(
                        user_id,
                        track_id,
                        ["takes", "primary_take_id"],
                        f"Removed master take: {take_label}",
                        {"primary_take_id": new_primary},
                    )
                else:
                    self._record_track_revision(
                        user_id,
                        track_id,
                        ["takes"],
                        f"Removed take: {take_label}",
                    )
            return True
        except Exception as e:
            logger.error(f"Failed to delete take {take_id}: {e}")
            return False

    def get_take_audio_path(self, user_id: str, track_id: str, take_id: str) -> Optional[Path]:
        """Return path to take's master audio file."""
        take = self.get_take(user_id, track_id, take_id)
        if not take:
            return None
        take_dir = self._get_take_dir(user_id, track_id, take_id)
        for ext in AUDIO_FORMATS:
            audio_path = take_dir / f"audio{ext}"
            if audio_path.is_file() and not audio_path.is_symlink():
                return audio_path
        return None

    # --- Stems Management ---

    def add_stem(
        self,
        user_id: str,
        track_id: str,
        take_id: str,
        stem_bytes: bytes,
        original_filename: str,
        extension: str = ".wav",
        role: StemRole = StemRole.OTHER,
    ) -> StemMetadata:
        """Add a separated stem to a take."""
        take = self.get_take(user_id, track_id, take_id)
        if not take:
            raise ValueError("Take does not exist.")

        if extension not in AUDIO_FORMATS:
            raise ValueError(f"Unsupported audio extension: {extension}")

        # Quota verification
        self.ensure_quota_available(user_id, len(stem_bytes))

        stem_id = generate_stem_id()
        take_dir = self._get_take_dir(user_id, track_id, take_id)
        stems_dir = self._ensure_dir_0700(take_dir / "stems")

        clean_filename = sanitize_filename(original_filename, extension=extension)
        stem_file_path = stems_dir / f"{stem_id}{extension}"

        # Write stem data atomically
        tmp_stem = stems_dir / f".tmp_{stem_id}{extension}"
        with open(tmp_stem, "wb") as f:
            f.write(stem_bytes)
        os.replace(tmp_stem, stem_file_path)
        try:
            os.chmod(stem_file_path, 0o600)
        except Exception:
            pass

        stem_meta = StemMetadata(
            id=stem_id,
            role=role,
            filename=clean_filename,
            format=extension.lstrip(".").lower(),
            size_bytes=len(stem_bytes),
            created_at=time.time(),
        )

        # Update take manifest
        take_manifest = take_dir / "take.json"
        data = take.model_dump()
        if "stems" not in data:
            data["stems"] = {}
        data["stems"][stem_id] = stem_meta.model_dump()
        data["updated_at"] = time.time()

        with file_lock(take_manifest):
            atomic_json(take_manifest, data)

        return stem_meta

    def delete_stem(self, user_id: str, track_id: str, take_id: str, stem_id: str) -> bool:
        """Delete a stem from a take."""
        take = self.get_take(user_id, track_id, take_id)
        if not take or stem_id not in take.stems:
            return False

        take_dir = self._get_take_dir(user_id, track_id, take_id)
        stems_dir = take_dir / "stems"

        # Remove physical file
        if stems_dir.exists():
            for f in stems_dir.glob(f"{stem_id}.*"):
                try:
                    f.unlink(missing_ok=True)
                except Exception:
                    pass

        # Update manifest
        take_manifest = take_dir / "take.json"
        data = take.model_dump()
        if "stems" in data and stem_id in data["stems"]:
            del data["stems"][stem_id]
            data["updated_at"] = time.time()
            with file_lock(take_manifest):
                atomic_json(take_manifest, data)
            return True
        return False

    def get_stem_audio_path(self, user_id: str, track_id: str, take_id: str, stem_id: str) -> Optional[Path]:
        """Return physical path to a stem audio file."""
        take = self.get_take(user_id, track_id, take_id)
        if not take or stem_id not in take.stems:
            return None
        take_dir = self._get_take_dir(user_id, track_id, take_id)
        stems_dir = take_dir / "stems"
        if stems_dir.exists():
            for ext in AUDIO_FORMATS:
                path = stems_dir / f"{stem_id}{ext}"
                if path.is_file() and not path.is_symlink():
                    return path
        return None

    def get_stem_pack_zip(self, user_id: str, track_id: str, take_id: str) -> Optional[tuple[str, bytes]]:
        """Return (zip_filename, zip_bytes) containing all stems for a take."""
        track = self.get_track(user_id, track_id)
        take = self.get_take(user_id, track_id, take_id)
        if not track or not take or not take.stems:
            return None

        import io
        import zipfile
        import re

        clean_track = re.sub(r'[\s/\\:*?"<>|]+', '_', track.title).strip('_') or "track"
        clean_take = re.sub(r'[\s/\\:*?"<>|]+', '_', take.label).strip('_') or "take"
        zip_filename = f"{clean_track}_{clean_take}_stems.zip"

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for stem_id, stem in take.stems.items():
                stem_path = self.get_stem_audio_path(user_id, track_id, take_id, stem_id)
                if stem_path and stem_path.is_file():
                    safe_stem_name = re.sub(r'[\s/\\:*?"<>|]+', '_', stem.filename).strip('_')
                    if not safe_stem_name:
                        safe_stem_name = f"{stem.role}_{stem_id[:8]}{stem.format}"
                    zf.write(stem_path, arcname=safe_stem_name)

        buf.seek(0)
        return zip_filename, buf.getvalue()

    # --- Lyrics Management for Takes ---

    def save_take_lyrics(
        self,
        user_id: str,
        track_id: str,
        take_id: str,
        lyrics_content: str,
        lyrics_format: LyricsFormat = LyricsFormat.LRC,
    ) -> bool:
        """Save synchronized lyrics file (.lrc or .sylt) for a take."""
        take = self.get_take(user_id, track_id, take_id)
        if not take:
            return False

        take_dir = self._get_take_dir(user_id, track_id, take_id)
        ext = ".lrc" if lyrics_format == LyricsFormat.LRC else ".sylt"
        lyrics_file = take_dir / f"lyrics{ext}"

        # Clean old lyrics files
        for old_ext in (".lrc", ".sylt"):
            old_file = take_dir / f"lyrics{old_ext}"
            if old_file.is_file():
                try:
                    old_file.unlink(missing_ok=True)
                except Exception:
                    pass

        # Write new lyrics file
        with open(lyrics_file, "w", encoding="utf-8") as f:
            f.write(lyrics_content)
        try:
            os.chmod(lyrics_file, 0o600)
        except Exception:
            pass

        # Update take manifest
        self.update_take(user_id, track_id, take_id, {"lyrics_format": lyrics_format})
        return True

    def get_take_lyrics(
        self,
        user_id: str,
        track_id: str,
        take_id: str,
    ) -> Optional[Tuple[LyricsFormat, str]]:
        """Retrieve synchronized lyrics content and format for a take."""
        take = self.get_take(user_id, track_id, take_id)
        if not take or take.lyrics_format == LyricsFormat.NONE:
            return None

        take_dir = self._get_take_dir(user_id, track_id, take_id)
        for fmt, ext in ((LyricsFormat.LRC, ".lrc"), (LyricsFormat.SYLT, ".sylt")):
            lfile = take_dir / f"lyrics{ext}"
            if lfile.is_file():
                try:
                    with open(lfile, "r", encoding="utf-8") as f:
                        return fmt, f.read()
                except Exception:
                    pass
        return None

    # --- Album Workspace & Entry Recording Ownership Management ---

    def _get_album_entries_root(self, user_id: str, album_id: str) -> Path:
        album_dir = self._get_album_dir(user_id, album_id)
        return self._ensure_dir_0700(album_dir / "entries")

    def _get_album_entry_dir(self, user_id: str, album_id: str, entry_id: str) -> Path:
        if not is_valid_project_id(entry_id, expected_prefix="ale"):
            raise ValueError("Invalid album entry identifier format.")
        entries_root = self._get_album_entries_root(user_id, album_id)
        return entries_root / get_album_entry_dir_name(entry_id)

    def get_album_entry_audio_path(self, user_id: str, album_id: str, entry_id: str) -> Optional[Path]:
        """Return path to an album entry's owned audio copy if it exists."""
        try:
            entry_dir = self._get_album_entry_dir(user_id, album_id, entry_id)
        except ValueError:
            return None
        if not entry_dir.is_dir() or entry_dir.is_symlink():
            return None
        for ext in AUDIO_FORMATS:
            audio_path = entry_dir / f"audio{ext}"
            if audio_path.is_file() and not audio_path.is_symlink():
                return audio_path
        return None

    @staticmethod
    def _is_album_finalized(album: AlbumMetadata) -> bool:
        return bool(album.is_finalized or album.status == WorkspaceStatus.COMPLETED)

    def _build_take_snapshot(
        self,
        user_id: str,
        entry_id: str,
        track: TrackMetadata,
        take: TakeMetadata,
        audio_path: Path,
        copied_at: Optional[float] = None,
    ) -> AlbumEntrySnapshot:
        """Build a backend-authoritative provenance and metadata snapshot from a source take audio file."""
        sha256 = _compute_file_sha256(audio_path)
        size_bytes = audio_path.stat().st_size
        duration = _probe_audio_duration(audio_path)
        if duration <= 0.0:
            duration = float(take.duration_seconds or 0.0)

        embedded_meta: Dict[str, Any] = {}
        embedded_lyrics: Optional[str] = None
        try:
            from backend.metadata_engine import extract_metadata_and_artwork
            parsed = extract_metadata_and_artwork(audio_path)
            raw_meta = parsed.get("metadata", {}) if isinstance(parsed, dict) else {}
            for k, v in raw_meta.items():
                if k == "lyrics":
                    if v:
                        embedded_lyrics = str(v)
                elif k == "synced_lyrics":
                    continue
                elif v not in (None, ""):
                    embedded_meta[k] = v
        except Exception as e:
            logger.debug(f"Metadata snapshot extraction skipped for {audio_path.name}: {e}")

        # Fall back to attached synchronized take lyrics if embedded USLT lyrics are absent
        if not embedded_lyrics:
            take_lyrics = self.get_take_lyrics(user_id, track.id, take.id)
            if take_lyrics:
                _, lyrics_txt = take_lyrics
                embedded_lyrics = lyrics_txt

        return AlbumEntrySnapshot(
            entry_id=entry_id,
            source_track_id=track.id,
            source_track_title=track.title,
            source_track_artist=track.artist,
            source_track_revision=track.revision,
            source_take_id=take.id,
            source_take_label=take.label,
            source_take_revision=take.revision,
            source_take_updated_at=float(take.updated_at or 0.0),
            source_filename=take.filename,
            format=audio_path.suffix.lstrip(".").lower() or take.format,
            size_bytes=size_bytes,
            duration_seconds=duration,
            audio_sha256=sha256,
            embedded_metadata=embedded_meta,
            embedded_lyrics=embedded_lyrics,
            take_notes=take.notes,
            take_prompt=take.prompt,
            take_style_tags=take.style_tags,
            copied_at=copied_at if copied_at is not None else time.time(),
        )

    def _stage_entry_audio_copy(
        self,
        user_id: str,
        album_id: str,
        entry_id: str,
        track_id: str,
        take_id: str,
    ) -> Tuple[AlbumEntrySnapshot, Callable[[], None], Callable[[], None], Callable[[], None]]:
        """Stage an independent audio copy and snapshot before committing manifest changes."""
        track = self.get_track(user_id, track_id)
        if not track:
            raise ProjectValidationError(f"Track workspace '{track_id}' not found.")
        take = self.get_take(user_id, track_id, take_id)
        if not take:
            raise ProjectValidationError(f"Take '{take_id}' does not exist in track '{track_id}'.")

        source_audio = self.get_take_audio_path(user_id, track_id, take_id)
        if not source_audio or not source_audio.is_file() or source_audio.is_symlink():
            raise ProjectValidationError(f"Source audio for take '{take_id}' is missing or inaccessible.")

        source_size = source_audio.stat().st_size
        if source_size <= 0:
            raise ProjectValidationError(f"Source audio for take '{take_id}' is empty.")

        ext = source_audio.suffix.lower()
        if ext not in AUDIO_FORMATS:
            raise ProjectValidationError(f"Unsupported source audio format '{ext}'.")

        existing_audio = self.get_album_entry_audio_path(user_id, album_id, entry_id)
        existing_size = existing_audio.stat().st_size if (existing_audio and existing_audio.is_file()) else 0
        self.ensure_quota_available(user_id, max(0, source_size - existing_size))

        entry_dir = self._ensure_dir_0700(self._get_album_entry_dir(user_id, album_id, entry_id))
        stage_token = uuid.uuid4().hex[:12]
        staging_audio = entry_dir / f".staging_{stage_token}_audio{ext}"
        staging_snap = entry_dir / f".staging_{stage_token}_snapshot.json"
        dest_audio = entry_dir / f"audio{ext}"
        dest_snap = entry_dir / "snapshot.json"
        backup_records: List[Tuple[Path, Path]] = []
        state = {"committed": False}

        try:
            with open(source_audio, "rb") as src, open(staging_audio, "wb") as dst:
                shutil.copyfileobj(src, dst, length=64 * 1024)
            try:
                os.chmod(staging_audio, 0o600)
            except Exception:
                pass

            if not staging_audio.is_file() or staging_audio.stat().st_size != source_size:
                raise ProjectStorageError("Staged album audio copy failed byte-count verification.")

            snapshot = self._build_take_snapshot(user_id, entry_id, track, take, staging_audio)
            atomic_json(staging_snap, snapshot.model_dump())
        except Exception:
            for p in (staging_audio, staging_snap):
                if p.exists():
                    try:
                        p.unlink(missing_ok=True)
                    except Exception:
                        pass
            raise

        def commit_staged() -> None:
            for old_ext in AUDIO_FORMATS:
                old_audio = entry_dir / f"audio{old_ext}"
                if old_audio.is_file():
                    bak_audio = entry_dir / f".backup_{stage_token}_audio{old_ext}"
                    os.replace(old_audio, bak_audio)
                    backup_records.append((old_audio, bak_audio))
            if dest_snap.is_file():
                bak_snap = entry_dir / f".backup_{stage_token}_snapshot.json"
                os.replace(dest_snap, bak_snap)
                backup_records.append((dest_snap, bak_snap))

            os.replace(staging_audio, dest_audio)
            os.replace(staging_snap, dest_snap)
            try:
                os.chmod(dest_audio, 0o600)
            except Exception:
                pass
            state["committed"] = True

        def cleanup_backups() -> None:
            for _, bak_path in backup_records:
                try:
                    bak_path.unlink(missing_ok=True)
                except Exception:
                    pass

        def rollback_staged() -> None:
            for p in (staging_audio, staging_snap):
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
            if state["committed"]:
                for p in (dest_audio, dest_snap):
                    try:
                        p.unlink(missing_ok=True)
                    except Exception:
                        pass
                for orig_path, bak_path in reversed(backup_records):
                    if bak_path.exists():
                        os.replace(bak_path, orig_path)

        return snapshot, commit_staged, cleanup_backups, rollback_staged

    def _normalize_album_data(self, user_id: str, album_dir: Path, data: Dict[str, Any]) -> Dict[str, Any]:
        """Non-destructively normalize legacy and current album manifests when reading from disk."""
        album_id = data.get("id", "")
        data["has_cover"] = (album_dir / "cover.jpg").is_file() or (album_dir / "cover.png").is_file()
        status_val = data.get("status", WorkspaceStatus.DRAFT.value)
        data["is_finalized"] = bool(data.get("is_finalized", False)) or (status_val == WorkspaceStatus.COMPLETED.value)

        raw_tracks = data.get("tracks")
        raw_track_ids = data.get("track_ids")
        if not raw_tracks and raw_track_ids:
            raw_tracks = [
                {"track_number": i + 1, "disc_number": 1, "track_id": tid, "take_id": None, "custom_title": None}
                for i, tid in enumerate(raw_track_ids)
            ]

        norm_tracks: List[Dict[str, Any]] = []
        if isinstance(raw_tracks, list):
            for idx, item in enumerate(raw_tracks):
                if not isinstance(item, dict):
                    continue
                entry = dict(item)
                tid = entry.get("track_id", "")
                take_id = entry.get("take_id")
                eid = entry.get("id")
                if not eid or not is_valid_project_id(eid, expected_prefix="ale"):
                    eid = generate_deterministic_legacy_entry_id(album_id, idx, tid, take_id)
                entry["id"] = eid
                entry["track_number"] = int(entry.get("track_number") or (idx + 1))
                entry["disc_number"] = int(entry.get("disc_number") or 1)
                if take_id is None:
                    entry["is_placeholder"] = True
                    entry["has_owned_audio"] = False
                    entry["snapshot"] = None
                else:
                    entry["is_placeholder"] = False
                    owned_audio = self.get_album_entry_audio_path(user_id, album_id, eid)
                    entry["has_owned_audio"] = bool(owned_audio is not None and entry.get("snapshot") is not None)
                norm_tracks.append(entry)

        data["tracks"] = norm_tracks
        data["track_ids"] = [t["track_id"] for t in norm_tracks]
        return data

    def create_album(
        self,
        user_id: str,
        title: str,
        album_artist: Optional[str] = None,
        year: Optional[int] = None,
        genre: Optional[str] = None,
        credits: Optional[str] = None,
        status: WorkspaceStatus = WorkspaceStatus.DRAFT,
    ) -> AlbumMetadata:
        """Create a new album/EP curation workspace."""
        if status == WorkspaceStatus.COMPLETED:
            raise ProjectValidationError("Cannot create an empty album directly in completed/finalized status.")

        album_id = generate_album_id()
        album_dir = self._get_album_dir(user_id, album_id)
        self._ensure_dir_0700(album_dir)
        self._ensure_dir_0700(album_dir / "entries")

        now = time.time()
        album_meta = AlbumMetadata(
            id=album_id,
            title=title.strip(),
            album_artist=album_artist.strip() if album_artist else None,
            year=year,
            genre=genre.strip() if genre else None,
            credits=credits,
            status=status,
            is_finalized=False,
            finalized_at=None,
            revision=1,
            revisions=[
                RevisionEntry(
                    revision=1,
                    timestamp=now,
                    change_summary="Initial album creation",
                    changed_fields=[],
                )
            ],
            has_cover=False,
            tracks=[],
            created_at=now,
            updated_at=now,
        )

        manifest_path = album_dir / "album.json"
        with file_lock(manifest_path):
            atomic_json(manifest_path, album_meta.model_dump())

        return album_meta

    def get_album(self, user_id: str, album_id: str) -> Optional[AlbumMetadata]:
        """Retrieve album metadata."""
        try:
            album_dir = self._get_album_dir(user_id, album_id)
        except ValueError:
            return None
        manifest_path = album_dir / "album.json"
        if not manifest_path.is_file():
            return None
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data = self._normalize_album_data(user_id, album_dir, data)
            return AlbumMetadata(**data)
        except Exception as e:
            logger.error(f"Failed to read album manifest {album_id}: {e}")
            return None

    def list_albums(self, user_id: str, status: Optional[WorkspaceStatus] = None) -> List[AlbumMetadata]:
        """List all album workspaces for a user."""
        albums_root = self._get_albums_root(user_id)
        results = []
        if not albums_root.exists():
            return results

        for item in albums_root.iterdir():
            if item.is_dir():
                manifest_path = item / "album.json"
                if manifest_path.is_file():
                    try:
                        with open(manifest_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        data = self._normalize_album_data(user_id, item, data)
                        meta = AlbumMetadata(**data)
                        if status is None or meta.status == status:
                            results.append(meta)
                    except Exception as e:
                        logger.warning(f"Skipping corrupted album {item.name}: {e}")

        results.sort(key=lambda a: a.updated_at, reverse=True)
        return results

    def _resolve_existing_entry_for_item(
        self,
        item: Dict[str, Any],
        existing_by_id: Dict[str, AlbumTrackEntry],
        unmatched_existing: List[AlbumTrackEntry],
    ) -> Optional[AlbumTrackEntry]:
        """Match an incoming tracklist item to an existing album entry without losing stable identity."""
        req_id = item.get("id")
        if req_id and req_id in existing_by_id:
            matched = existing_by_id[req_id]
            if matched in unmatched_existing:
                unmatched_existing.remove(matched)
            return matched

        track_id = item.get("track_id")
        take_id = item.get("take_id")
        for candidate in unmatched_existing:
            if candidate.track_id == track_id and candidate.take_id == take_id:
                unmatched_existing.remove(candidate)
                return candidate

        track_matches = [c for c in unmatched_existing if c.track_id == track_id]
        if len(track_matches) == 1:
            matched = track_matches[0]
            unmatched_existing.remove(matched)
            return matched

        return None

    def update_album(
        self,
        user_id: str,
        album_id: str,
        updates: Dict[str, Any],
        change_summary: Optional[str] = None,
    ) -> Optional[AlbumMetadata]:
        """Update album details or sequenced tracklist with strict validation, staged audio ownership, and revision tracking."""
        album = self.get_album(user_id, album_id)
        if not album:
            return None

        if self._is_album_finalized(album):
            raise AlbumFinalizedError("Finalized albums are immutable and cannot be modified.")

        if updates.get("status") in (WorkspaceStatus.COMPLETED, WorkspaceStatus.COMPLETED.value):
            raise ProjectValidationError("Albums must be finalized via the dedicated finalization endpoint.")

        album_dir = self._get_album_dir(user_id, album_id)
        manifest_path = album_dir / "album.json"

        data = album.model_dump()
        summary_text = updates.pop("change_summary", None) or change_summary
        changed_fields: List[str] = []

        immutable_fields = ("id", "created_at", "has_cover", "revisions", "revision", "updated_at", "tracks", "track_ids", "is_finalized", "finalized_at")
        for k, v in updates.items():
            if v is not None and k not in immutable_fields:
                curr_val = data.get(k)
                norm_v = v.value if isinstance(v, WorkspaceStatus) else v
                if curr_val != norm_v:
                    changed_fields.append(k)
                    data[k] = norm_v

        staged_commits: List[Callable[[], None]] = []
        staged_cleanups: List[Callable[[], None]] = []
        staged_rollbacks: List[Callable[[], None]] = []
        obsolete_entry_dirs: List[str] = []

        track_list_input: Optional[List[Dict[str, Any]]] = None
        if "tracks" in updates and updates["tracks"] is not None:
            raw_list = []
            for item in updates["tracks"]:
                if isinstance(item, dict):
                    raw_list.append(dict(item))
                elif hasattr(item, "model_dump"):
                    raw_list.append(item.model_dump())
            track_list_input = raw_list
        elif "track_ids" in updates and updates["track_ids"] is not None:
            track_list_input = [
                {"track_number": i + 1, "disc_number": 1, "track_id": tid, "take_id": None, "custom_title": None}
                for i, tid in enumerate(updates["track_ids"])
            ]

        if track_list_input is not None:
            existing_by_id = {e.id: e for e in album.tracks if e.id}
            unmatched_existing = list(album.tracks)
            # Pre-consume explicit IDs so positional/legacy fallback cannot steal another entry's ID
            explicit_ids = {
                item.get("id") for item in track_list_input
                if item.get("id") and item.get("id") in existing_by_id
            }
            unmatched_existing = [e for e in unmatched_existing if e.id not in explicit_ids]

            seen_entry_ids = set()
            norm_tracks: List[Dict[str, Any]] = []

            try:
                for i, item in enumerate(track_list_input):
                    track_id = item.get("track_id")
                    if not track_id or not is_valid_project_id(track_id, expected_prefix="trk"):
                        raise ProjectValidationError(f"Invalid track identifier '{track_id}' at position {i + 1}.")

                    take_id = item.get("take_id")
                    if take_id is not None and not is_valid_project_id(take_id, expected_prefix="tak"):
                        raise ProjectValidationError(f"Invalid take identifier '{take_id}' at position {i + 1}.")

                    req_id = item.get("id")
                    if req_id is not None and not is_valid_project_id(req_id, expected_prefix="ale"):
                        raise ProjectValidationError(f"Invalid album entry identifier '{req_id}'.")

                    matched = self._resolve_existing_entry_for_item(item, existing_by_id, unmatched_existing)
                    same_track = bool(matched and matched.track_id == track_id)
                    same_recording = bool(same_track and matched.take_id == take_id)

                    if not same_track:
                        track = self.get_track(user_id, track_id)
                        if not track:
                            raise ProjectValidationError(f"Track workspace '{track_id}' not found.")

                    if take_id is not None and not same_recording:
                        track = self.get_track(user_id, track_id)
                        if not track:
                            raise ProjectValidationError(f"Track workspace '{track_id}' not found.")
                        take = self.get_take(user_id, track_id, take_id)
                        if not take:
                            raise ProjectValidationError(f"Take '{take_id}' does not exist in track '{track_id}'.")

                    entry_id = req_id or (matched.id if matched else None) or generate_album_entry_id()
                    if entry_id in seen_entry_ids:
                        raise ProjectValidationError(f"Duplicate album entry identifier '{entry_id}'.")
                    seen_entry_ids.add(entry_id)

                    disc_num = int(item.get("disc_number") or 1)
                    custom_title = item.get("custom_title")

                    if take_id is None:
                        if matched and matched.has_owned_audio:
                            obsolete_entry_dirs.append(entry_id)
                        norm_tracks.append({
                            "id": entry_id,
                            "track_number": i + 1,
                            "disc_number": disc_num,
                            "track_id": track_id,
                            "take_id": None,
                            "custom_title": custom_title,
                            "is_placeholder": True,
                            "has_owned_audio": False,
                            "snapshot": None,
                        })
                    else:
                        same_recording = bool(matched and matched.track_id == track_id and matched.take_id == take_id)
                        if same_recording and matched.has_owned_audio and matched.snapshot is not None:
                            snap_dict = matched.snapshot.model_dump()
                            has_owned = True
                        elif same_recording and not matched.has_owned_audio:
                            # Preserve legacy unassigned recording reference until explicitly changed or refreshed
                            snap_dict = matched.snapshot.model_dump() if matched.snapshot else None
                            has_owned = False
                        else:
                            snap, commit_fn, cleanup_fn, rollback_fn = self._stage_entry_audio_copy(
                                user_id, album_id, entry_id, track_id, take_id
                            )
                            staged_commits.append(commit_fn)
                            staged_cleanups.append(cleanup_fn)
                            staged_rollbacks.append(rollback_fn)
                            snap_dict = snap.model_dump()
                            has_owned = True

                        norm_tracks.append({
                            "id": entry_id,
                            "track_number": i + 1,
                            "disc_number": disc_num,
                            "track_id": track_id,
                            "take_id": take_id,
                            "custom_title": custom_title,
                            "is_placeholder": False,
                            "has_owned_audio": has_owned,
                            "snapshot": snap_dict,
                        })
            except Exception:
                for rb in reversed(staged_rollbacks):
                    try:
                        rb()
                    except Exception:
                        pass
                raise

            for old_entry in album.tracks:
                if old_entry.id and old_entry.id not in seen_entry_ids:
                    obsolete_entry_dirs.append(old_entry.id)

            if data.get("tracks") != norm_tracks or staged_commits:
                changed_fields.append("tracks")
                data["tracks"] = norm_tracks
                data["track_ids"] = [t["track_id"] for t in norm_tracks]

        if changed_fields:
            new_rev = int(data.get("revision", 1)) + 1
            data["revision"] = new_rev
            summary = summary_text if summary_text else f"Updated {', '.join(changed_fields)}"
            if "revisions" not in data or not isinstance(data["revisions"], list):
                data["revisions"] = []
            data["revisions"].append({
                "revision": new_rev,
                "timestamp": time.time(),
                "change_summary": summary,
                "changed_fields": changed_fields,
            })

        data["updated_at"] = time.time()

        try:
            with file_lock(manifest_path):
                for commit_fn in staged_commits:
                    commit_fn()
                atomic_json(manifest_path, data)
        except Exception:
            for rb in reversed(staged_rollbacks):
                try:
                    rb()
                except Exception:
                    pass
            raise

        for cleanup_fn in staged_cleanups:
            cleanup_fn()

        for old_eid in set(obsolete_entry_dirs):
            try:
                old_dir = self._get_album_entry_dir(user_id, album_id, old_eid)
                if old_dir.exists():
                    shutil.rmtree(old_dir, ignore_errors=True)
            except Exception as e:
                logger.warning(f"Failed cleaning obsolete entry directory {old_eid}: {e}")

        return self.get_album(user_id, album_id)

    def add_album_entry(
        self,
        user_id: str,
        album_id: str,
        track_id: str,
        take_id: Optional[str] = None,
        disc_number: int = 1,
        track_number: Optional[int] = None,
        custom_title: Optional[str] = None,
    ) -> Optional[AlbumMetadata]:
        """Add a track entry (with explicit take or as placeholder) to an unfinished album."""
        album = self.get_album(user_id, album_id)
        if not album:
            return None
        if self._is_album_finalized(album):
            raise AlbumFinalizedError("Cannot add tracks to a finalized album.")

        new_entry = {
            "id": generate_album_entry_id(),
            "disc_number": disc_number,
            "track_id": track_id,
            "take_id": take_id,
            "custom_title": custom_title,
        }
        tracks = [t.model_dump() for t in album.tracks]
        if track_number is not None and 1 <= track_number <= len(tracks) + 1:
            tracks.insert(track_number - 1, new_entry)
        else:
            tracks.append(new_entry)

        summary = f"Added track entry ({'placeholder' if take_id is None else take_id})"
        return self.update_album(user_id, album_id, {"tracks": tracks}, change_summary=summary)

    def update_album_entry(
        self,
        user_id: str,
        album_id: str,
        entry_id: str,
        take_id: Optional[str] = None,
        clear_take_to_placeholder: bool = False,
        disc_number: Optional[int] = None,
        custom_title: Optional[str] = None,
    ) -> Optional[AlbumMetadata]:
        """Change an existing entry's selected take, placeholder state, or metadata without changing entry identity."""
        album = self.get_album(user_id, album_id)
        if not album:
            return None
        if self._is_album_finalized(album):
            raise AlbumFinalizedError("Cannot modify entries of a finalized album.")

        tracks = [t.model_dump() for t in album.tracks]
        target = next((t for t in tracks if t.get("id") == entry_id), None)
        if not target:
            raise ProjectValidationError(f"Album entry '{entry_id}' not found.")

        if clear_take_to_placeholder:
            target["take_id"] = None
        elif take_id is not None:
            target["take_id"] = take_id
        if disc_number is not None:
            target["disc_number"] = disc_number
        if custom_title is not None:
            target["custom_title"] = custom_title or None

        return self.update_album(
            user_id,
            album_id,
            {"tracks": tracks},
            change_summary=f"Updated album entry {entry_id}",
        )

    def remove_album_entry(self, user_id: str, album_id: str, entry_id: str) -> Optional[AlbumMetadata]:
        """Remove an album entry and clean up its album-owned audio copy without altering source takes."""
        album = self.get_album(user_id, album_id)
        if not album:
            return None
        if self._is_album_finalized(album):
            raise AlbumFinalizedError("Cannot remove entries from a finalized album.")

        remaining = [t.model_dump() for t in album.tracks if t.id != entry_id]
        if len(remaining) == len(album.tracks):
            raise ProjectValidationError(f"Album entry '{entry_id}' not found.")

        return self.update_album(
            user_id,
            album_id,
            {"tracks": remaining},
            change_summary=f"Removed album entry {entry_id}",
        )

    def preview_album_entry_refresh(
        self,
        user_id: str,
        album_id: str,
        entry_id: str,
    ) -> Optional[AlbumEntryRefreshPreviewResponse]:
        """Generate a read-only comparison between an album entry's owned copy and its source take."""
        album = self.get_album(user_id, album_id)
        if not album:
            return None
        if self._is_album_finalized(album):
            raise AlbumFinalizedError("Finalized albums do not permit refreshing from source takes.")

        entry = next((t for t in album.tracks if t.id == entry_id), None)
        if not entry:
            raise ProjectValidationError(f"Album entry '{entry_id}' not found.")
        if entry.is_placeholder or not entry.take_id:
            raise ProjectValidationError("Placeholder entries have no source take to refresh from. Select a take first.")

        album_stream_url = f"/api/projects/albums/{album_id}/entries/{entry_id}/stream" if entry.has_owned_audio else None
        track = self.get_track(user_id, entry.track_id)
        take = self.get_take(user_id, entry.track_id, entry.take_id)
        source_audio = self.get_take_audio_path(user_id, entry.track_id, entry.take_id)

        if not track or not take or not source_audio or not source_audio.is_file():
            return AlbumEntryRefreshPreviewResponse(
                album_id=album_id,
                entry_id=entry_id,
                track_id=entry.track_id,
                take_id=entry.take_id,
                source_available=False,
                unavailable_reason="Source track or take has been deleted or is unavailable. The album-owned recording is preserved.",
                has_changes=False,
                album_snapshot=entry.snapshot,
                album_audio_stream_url=album_stream_url,
            )

        source_snap = self._build_take_snapshot(user_id, entry_id, track, take, source_audio)
        album_snap = entry.snapshot

        audio_changed = (album_snap is None) or (not entry.has_owned_audio) or (album_snap.audio_sha256 != source_snap.audio_sha256)

        metadata_changes: List[Dict[str, Any]] = []
        if album_snap is None:
            metadata_changes.append({"field": "album_copy", "label": "Album Copy", "old": None, "new": "Initial copy missing"})
        else:
            compare_fields = [
                ("source_take_label", "Take Label"),
                ("source_take_revision", "Take Revision"),
                ("source_track_title", "Source Track Title"),
                ("source_track_artist", "Source Track Artist"),
                ("format", "Audio Format"),
                ("size_bytes", "Audio Size (bytes)"),
                ("duration_seconds", "Duration (s)"),
                ("take_notes", "Take Notes"),
                ("take_prompt", "Take Prompt"),
                ("take_style_tags", "Style Tags"),
            ]
            for attr, label in compare_fields:
                old_val = getattr(album_snap, attr, None)
                new_val = getattr(source_snap, attr, None)
                if old_val != new_val:
                    metadata_changes.append({"field": attr, "label": label, "old": old_val, "new": new_val})

            all_meta_keys = sorted(set(album_snap.embedded_metadata.keys()) | set(source_snap.embedded_metadata.keys()))
            for mk in all_meta_keys:
                old_mv = album_snap.embedded_metadata.get(mk)
                new_mv = source_snap.embedded_metadata.get(mk)
                if old_mv != new_mv:
                    metadata_changes.append({"field": f"tag:{mk}", "label": f"Tag: {mk}", "old": old_mv, "new": new_mv})

        old_lyrics = (album_snap.embedded_lyrics if album_snap else "") or ""
        new_lyrics = source_snap.embedded_lyrics or ""
        lyrics_changed = (old_lyrics != new_lyrics)
        lyrics_diff = ""
        if lyrics_changed:
            lyrics_diff = "\n".join(
                difflib.unified_diff(
                    old_lyrics.splitlines(),
                    new_lyrics.splitlines(),
                    fromfile="album_copy_lyrics",
                    tofile="source_take_lyrics",
                    lineterm="",
                )
            )

        has_changes = bool(audio_changed or lyrics_changed or metadata_changes)
        return AlbumEntryRefreshPreviewResponse(
            album_id=album_id,
            entry_id=entry_id,
            track_id=entry.track_id,
            take_id=entry.take_id,
            source_available=True,
            has_changes=has_changes,
            audio_changed=audio_changed,
            lyrics_changed=lyrics_changed,
            lyrics_diff=lyrics_diff,
            metadata_changes=metadata_changes,
            album_snapshot=album_snap,
            source_snapshot=source_snap,
            expected_source_revision=take.revision,
            expected_source_audio_sha256=source_snap.audio_sha256,
            expected_source_updated_at=float(take.updated_at or 0.0),
            album_audio_stream_url=album_stream_url,
            source_audio_stream_url=f"/api/projects/tracks/{entry.track_id}/takes/{entry.take_id}/stream",
        )

    def confirm_album_entry_refresh(
        self,
        user_id: str,
        album_id: str,
        entry_id: str,
        expected_source_revision: int,
        expected_source_audio_sha256: str,
        expected_source_updated_at: Optional[float] = None,
    ) -> Optional[AlbumMetadata]:
        """Explicitly replace an album entry's owned copy from its source take after verifying version match."""
        album = self.get_album(user_id, album_id)
        if not album:
            return None
        if self._is_album_finalized(album):
            raise AlbumFinalizedError("Cannot refresh entries of a finalized album.")

        entry = next((t for t in album.tracks if t.id == entry_id), None)
        if not entry:
            raise ProjectValidationError(f"Album entry '{entry_id}' not found.")
        if entry.is_placeholder or not entry.take_id:
            raise ProjectValidationError("Cannot refresh a placeholder entry with no selected take.")

        track = self.get_track(user_id, entry.track_id)
        take = self.get_take(user_id, entry.track_id, entry.take_id)
        source_audio = self.get_take_audio_path(user_id, entry.track_id, entry.take_id)
        if not track or not take or not source_audio or not source_audio.is_file():
            raise AlbumRefreshConflictError(
                "Source track or take has been deleted or is unavailable. Existing album recording preserved."
            )

        current_sha = _compute_file_sha256(source_audio)
        if take.revision != expected_source_revision or current_sha != expected_source_audio_sha256:
            raise AlbumRefreshConflictError(
                "Source take changed since preview was generated. Request a new refresh preview before confirming."
            )
        if expected_source_updated_at is not None and abs(float(take.updated_at or 0.0) - expected_source_updated_at) > 1e-4:
            raise AlbumRefreshConflictError(
                "Source take changed since preview was generated. Request a new refresh preview before confirming."
            )

        snap, commit_fn, cleanup_fn, rollback_fn = self._stage_entry_audio_copy(
            user_id, album_id, entry_id, entry.track_id, entry.take_id
        )

        album_dir = self._get_album_dir(user_id, album_id)
        manifest_path = album_dir / "album.json"
        data = album.model_dump()
        for t in data.get("tracks", []):
            if t.get("id") == entry_id:
                t["has_owned_audio"] = True
                t["is_placeholder"] = False
                t["snapshot"] = snap.model_dump()
                break

        new_rev = int(data.get("revision", 1)) + 1
        now = time.time()
        data["revision"] = new_rev
        data["updated_at"] = now
        if "revisions" not in data or not isinstance(data["revisions"], list):
            data["revisions"] = []
        data["revisions"].append({
            "revision": new_rev,
            "timestamp": now,
            "change_summary": f"Refreshed album entry #{entry.track_number} from source take {take.label}",
            "changed_fields": ["tracks"],
        })

        try:
            with file_lock(manifest_path):
                commit_fn()
                atomic_json(manifest_path, data)
        except Exception:
            rollback_fn()
            raise

        cleanup_fn()
        return self.get_album(user_id, album_id)

    def finalize_album(self, user_id: str, album_id: str) -> Optional[AlbumMetadata]:
        """Validate all recorded entries and storage integrity before atomically locking an album release."""
        album = self.get_album(user_id, album_id)
        if not album:
            return None
        if self._is_album_finalized(album):
            raise AlbumFinalizedError("Album is already finalized.")

        errors: List[Dict[str, Any]] = []
        if not album.tracks:
            errors.append({
                "entry_id": None,
                "track_number": None,
                "track_id": None,
                "reason": "Album must contain at least one recorded entry before finalization.",
            })

        album_dir = self._get_album_dir(user_id, album_id)
        entries_root = album_dir / "entries"
        manifest_entry_ids = set()

        for entry in album.tracks:
            manifest_entry_ids.add(entry.id)
            if entry.is_placeholder or not entry.take_id:
                errors.append({
                    "entry_id": entry.id,
                    "track_number": entry.track_number,
                    "track_id": entry.track_id,
                    "reason": f"Track #{entry.track_number} (entry {entry.id}) is a placeholder with no selected take.",
                })
                continue

            try:
                entry_dir = self._get_album_entry_dir(user_id, album_id, entry.id)
            except ValueError:
                errors.append({
                    "entry_id": entry.id,
                    "track_number": entry.track_number,
                    "track_id": entry.track_id,
                    "reason": f"Track #{entry.track_number} has an invalid entry identifier.",
                })
                continue

            if entry_dir.is_dir():
                incomplete = [
                    p.name for p in entry_dir.iterdir()
                    if p.name.startswith((".staging_", ".tmp_", ".backup_"))
                ]
                if incomplete:
                    errors.append({
                        "entry_id": entry.id,
                        "track_number": entry.track_number,
                        "track_id": entry.track_id,
                        "reason": f"Track #{entry.track_number} (entry {entry.id}) has incomplete recording transfer files.",
                    })

            audio_path = self.get_album_entry_audio_path(user_id, album_id, entry.id)
            if not audio_path or not entry.has_owned_audio or entry.snapshot is None:
                errors.append({
                    "entry_id": entry.id,
                    "track_number": entry.track_number,
                    "track_id": entry.track_id,
                    "reason": f"Track #{entry.track_number} (entry {entry.id}) is missing its album-owned audio copy or metadata snapshot.",
                })
                continue

            try:
                file_size = audio_path.stat().st_size
                if file_size < 32:
                    raise ValueError("Audio file is empty or truncated (< 32 bytes).")
                with open(audio_path, "rb") as f:
                    header = f.read(64)
                ext = audio_path.suffix.lower()
                if not matches_audio_header(header, ext):
                    raise ValueError("Audio file header does not match container format.")
                if ext in (".wav", ".m4a"):
                    validate_audio_container(audio_path)
                if file_size != entry.snapshot.size_bytes or _compute_file_sha256(audio_path) != entry.snapshot.audio_sha256:
                    raise ValueError("Album audio copy checksum or size does not match snapshot.")
            except Exception as e:
                errors.append({
                    "entry_id": entry.id,
                    "track_number": entry.track_number,
                    "track_id": entry.track_id,
                    "reason": f"Track #{entry.track_number} (entry {entry.id}) failed audio integrity check: {e}",
                })

        if entries_root.is_dir():
            expected_dirs = {get_album_entry_dir_name(eid) for eid in manifest_entry_ids if eid}
            for item in entries_root.iterdir():
                if item.is_dir() and item.name not in expected_dirs and any(item.iterdir()):
                    errors.append({
                        "entry_id": None,
                        "track_number": None,
                        "track_id": None,
                        "reason": f"Album storage directory '{item.name}' is inconsistent with the manifest.",
                    })

        if errors:
            raise AlbumFinalizationError(
                "Album cannot be finalized: " + " ".join(e["reason"] for e in errors),
                errors=errors,
            )

        manifest_path = album_dir / "album.json"
        now = time.time()
        with file_lock(manifest_path):
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data = self._normalize_album_data(user_id, album_dir, data)
            new_rev = int(data.get("revision", 1)) + 1
            data["status"] = WorkspaceStatus.COMPLETED.value
            data["is_finalized"] = True
            data["finalized_at"] = now
            data["revision"] = new_rev
            data["updated_at"] = now
            if "revisions" not in data or not isinstance(data["revisions"], list):
                data["revisions"] = []
            data["revisions"].append({
                "revision": new_rev,
                "timestamp": now,
                "change_summary": "Finalized album release",
                "changed_fields": ["status", "is_finalized", "finalized_at"],
            })
            atomic_json(manifest_path, data)

        return self.get_album(user_id, album_id)

    def delete_album(self, user_id: str, album_id: str) -> bool:
        """Delete an album project (does not delete referenced tracks)."""
        try:
            album_dir = self._get_album_dir(user_id, album_id)
        except ValueError:
            return False
        manifest_path = album_dir / "album.json"
        if not manifest_path.is_file():
            return False
        try:
            shutil.rmtree(album_dir, ignore_errors=True)
            return True
        except Exception as e:
            logger.error(f"Failed to delete album {album_id}: {e}")
            return False

    def save_album_cover(
        self,
        user_id: str,
        album_id: str,
        image_bytes: bytes,
        extension: str = ".jpg",
    ) -> bool:
        """Save normalized album cover artwork."""
        album = self.get_album(user_id, album_id)
        if not album:
            return False
        if self._is_album_finalized(album):
            raise AlbumFinalizedError("Cannot update cover artwork on a finalized album.")

        self.ensure_quota_available(user_id, len(image_bytes))

        album_dir = self._get_album_dir(user_id, album_id)
        # Remove old cover files
        for old_ext in (".jpg", ".jpeg", ".png", ".webp"):
            old_file = album_dir / f"cover{old_ext}"
            if old_file.is_file():
                try:
                    old_file.unlink(missing_ok=True)
                except Exception:
                    pass

        target_ext = ".png" if extension.lower() == ".png" else ".jpg"
        cover_path = album_dir / f"cover{target_ext}"
        tmp_cover = album_dir / f".tmp_cover{target_ext}"

        with open(tmp_cover, "wb") as f:
            f.write(image_bytes)
        os.replace(tmp_cover, cover_path)
        try:
            os.chmod(cover_path, 0o600)
        except Exception:
            pass

        # Update album revision for cover artwork change
        manifest_path = album_dir / "album.json"
        if manifest_path.is_file():
            try:
                with file_lock(manifest_path):
                    with open(manifest_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    new_rev = int(data.get("revision", 1)) + 1
                    data["revision"] = new_rev
                    data["has_cover"] = True
                    data["updated_at"] = time.time()
                    if "revisions" not in data or not isinstance(data["revisions"], list):
                        data["revisions"] = []
                    data["revisions"].append({
                        "revision": new_rev,
                        "timestamp": time.time(),
                        "change_summary": "Updated album cover artwork",
                        "changed_fields": ["cover_art"],
                    })
                    atomic_json(manifest_path, data)
            except Exception as e:
                logger.warning(f"Failed to record revision for album cover update: {e}")

        return True

    def get_album_cover_path(self, user_id: str, album_id: str) -> Optional[Path]:
        """Return path to album cover artwork if it exists."""
        album = self.get_album(user_id, album_id)
        if not album:
            return None
        album_dir = self._get_album_dir(user_id, album_id)
        for ext in (".jpg", ".jpeg", ".png"):
            cover_path = album_dir / f"cover{ext}"
            if cover_path.is_file() and not cover_path.is_symlink():
                return cover_path
        return None


# Global singleton instance
project_storage_manager = ProjectStorageManager()
