"""Persistent file and workspace storage manager for MP3Projects and MP3MetaManager.

Implements Option A: Self-contained Track & Album Workspaces with decoupled
cryptographic path hashing and strict POSIX 0700 filesystem isolation.
"""

import os
import re
import copy
import json
import math
import stat
import uuid
import time
import shutil
import hashlib
import difflib
import logging
import secrets
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, Callable
from pydantic import ValidationError

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
    JournalTxState,
    JournalProtocol,
    _validate_safe_active_basename,
    RecoveryConsistency,
    ArtifactRole,
    ArtifactRefRecord,
    StagedArtifactRef,
    ActiveTxRecord,
    RetainedArtifactRecord,
    IdempotencyRecord,
    TerminalTxReceipt,
    AlbumJournal,
    _validate_safe_token,
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


class AlbumReplacementDisabledError(ProjectStorageError):
    """Raised when attempting to replace/refresh existing album-owned audio while Phase 1B is pending."""
    pass


class AlbumRevisionConflictError(ProjectStorageError):
    """Raised when expected_revision is stale or omitted on a destructive bulk album update."""
    pass


class AlbumManifestCorruptError(ProjectStorageError):
    """Raised when an existing album.json manifest cannot be read or validated."""
    pass


class AlbumDurabilityUncertainError(ProjectStorageError):
    """Raised when album durability or recovery evidence is ambiguous or un-synchronized."""
    pass


class AlbumMutationNotEnrolledError(ProjectStorageError):
    """Raised when an operation is not yet enrolled for v2-journaled albums."""
    pass


class AlbumIdempotencyConflictError(ProjectStorageError):
    """Raised when an Idempotency-Key conflicts with an existing transaction or receipt."""
    pass


class AlbumTransactionAbortedError(ProjectStorageError):
    """Raised when replaying an Idempotency-Key whose terminal receipt outcome is ABORTED."""
    pass


class ManifestPublishState(str, Enum):
    NOT_PUBLISHED = "not_published"
    PUBLISHED = "published"
    UNCERTAIN = "uncertain"


class ManifestPublishError(ProjectStorageError):
    """Wraps an error during manifest publication with explicit publication certainty."""

    def __init__(self, message: str, publish_state: ManifestPublishState, cause: Optional[Exception] = None):
        super().__init__(message)
        self.publish_state = publish_state
        self.cause = cause


_GEN_AUDIO_RE = re.compile(r"^audio_g(\d+)(\.[A-Za-z0-9]+)$")
_GEN_SNAP_RE = re.compile(r"^snapshot_g(\d+)\.json$")
_GEN_COVER_RE = re.compile(r"^cover_g(\d+)(\.[A-Za-z0-9]+)$")


@dataclass
class _AlbumReadContext:
    """Descriptor-bound read snapshot retaining verified manifest and optional journal evidence."""

    album_id: str
    album_dir: Path
    album_dir_fd: Optional[int]
    raw_data: Dict[str, Any]
    album_identity: Tuple[int, int]
    raw_album_sha256: str
    album: AlbumMetadata
    norm_data: Dict[str, Any]
    observed_identities: Dict[str, Any]
    journal: Optional[AlbumJournal] = None
    journal_identity: Optional[Tuple[int, int]] = None
    journal_sha256: Optional[str] = None

    def close(self) -> None:
        if self.album_dir_fd is not None:
            try:
                os.close(self.album_dir_fd)
            except OSError:
                pass
            self.album_dir_fd = None


def _fsync_file_fd(file_fd: int) -> None:
    """Synchronize file contents and metadata on Linux POSIX filesystems without swallowing I/O errors."""
    os.fsync(file_fd)


def _fsync_dir_fd(dir_fd: int) -> None:
    """Synchronize directory entries on Linux POSIX filesystems without swallowing I/O errors."""
    os.fsync(dir_fd)


def _publish_manifest_atomic_in_dir(dir_fd: int, filename: str, data: Dict[str, Any]) -> Tuple[int, int]:
    """Write and atomically replace a manifest JSON file strictly inside a validated dir_fd, returning (st_dev, st_ino)."""
    publish_state = ManifestPublishState.NOT_PUBLISHED
    tmp_name = f".tmp_manifest_{secrets.token_hex(8)}.json"
    tmp_created = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(tmp_name, flags, 0o600, dir_fd=dir_fd)
        tmp_created = True
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
                os.fchmod(f.fileno(), 0o600)
                st = os.fstat(f.fileno())
                published_identity = (st.st_dev, st.st_ino)
        except Exception:
            if tmp_created:
                try:
                    os.unlink(tmp_name, dir_fd=dir_fd)
                    tmp_created = False
                except Exception:
                    pass
            raise

        publish_state = ManifestPublishState.UNCERTAIN
        os.replace(tmp_name, filename, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        tmp_created = False
        _fsync_dir_fd(dir_fd)
        publish_state = ManifestPublishState.PUBLISHED
        return published_identity
    except ManifestPublishError:
        raise
    except Exception as e:
        if publish_state == ManifestPublishState.NOT_PUBLISHED and tmp_created:
            try:
                os.unlink(tmp_name, dir_fd=dir_fd)
            except Exception:
                pass
        raise ManifestPublishError(str(e), publish_state=publish_state, cause=e) from e


def _publish_manifest_atomic(manifest_path: Path, data: Dict[str, Any]) -> None:
    """Write and atomically replace a manifest JSON file, tracking whether os.replace may have run."""
    publish_state = ManifestPublishState.NOT_PUBLISHED
    tmp_path: Optional[Path] = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=str(manifest_path.parent),
            prefix=".tmp_manifest_",
            suffix=".json",
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp_path, 0o600)
        except Exception:
            if tmp_path and tmp_path.exists():
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
            raise

        publish_state = ManifestPublishState.UNCERTAIN
        os.replace(str(tmp_path), str(manifest_path))
        publish_state = ManifestPublishState.PUBLISHED
    except ManifestPublishError:
        raise
    except Exception as e:
        if publish_state == ManifestPublishState.NOT_PUBLISHED and tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
        raise ManifestPublishError(str(e), publish_state=publish_state, cause=e) from e


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
        self._thread_local = threading.local()
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

    def _get_tenant_dir_path(self, user_id: str) -> Path:
        """Pure path computation for a tenant directory (does not create directories)."""
        return self.root_dir / get_tenant_storage_dir_name(user_id)

    def get_tenant_dir(self, user_id: str) -> Path:
        """Return isolated directory for a given user/tenant."""
        return self._ensure_dir_0700(self._get_tenant_dir_path(user_id))

    def _get_tracks_root_path(self, user_id: str) -> Path:
        """Pure path computation for a tenant's tracks root (does not create directories)."""
        return self._get_tenant_dir_path(user_id) / "tracks"

    def _get_tracks_root(self, user_id: str) -> Path:
        tdir = self.get_tenant_dir(user_id)
        return self._ensure_dir_0700(tdir / "tracks")

    def _get_albums_root_path(self, user_id: str) -> Path:
        """Pure path computation for a tenant's albums root (does not create directories)."""
        return self._get_tenant_dir_path(user_id) / "albums"

    def _get_albums_root(self, user_id: str) -> Path:
        tdir = self.get_tenant_dir(user_id)
        return self._ensure_dir_0700(tdir / "albums")

    def _get_track_dir_path(self, user_id: str, track_id: str) -> Path:
        """Pure path computation for a track directory (does not create directories)."""
        if not is_valid_project_id(track_id, expected_prefix="trk"):
            raise ValueError("Invalid track identifier format.")
        return self._get_tracks_root_path(user_id) / get_track_dir_name(track_id)

    def _get_track_dir(self, user_id: str, track_id: str) -> Path:
        if not is_valid_project_id(track_id, expected_prefix="trk"):
            raise ValueError("Invalid track identifier format.")
        dir_name = get_track_dir_name(track_id)
        return self._get_tracks_root(user_id) / dir_name

    def _get_take_dir_path(self, user_id: str, track_id: str, take_id: str) -> Path:
        """Pure path computation for a take directory (does not create directories)."""
        if not is_valid_project_id(take_id, expected_prefix="tak"):
            raise ValueError("Invalid take identifier format.")
        return self._get_track_dir_path(user_id, track_id) / "takes" / get_take_dir_name(take_id)

    def _get_take_dir(self, user_id: str, track_id: str, take_id: str) -> Path:
        if not is_valid_project_id(take_id, expected_prefix="tak"):
            raise ValueError("Invalid take identifier format.")
        track_dir = self._get_track_dir(user_id, track_id)
        takes_root = self._ensure_dir_0700(track_dir / "takes")
        dir_name = get_take_dir_name(take_id)
        return takes_root / dir_name

    def _verify_ancestors_nofollow(self, rel_parts: List[str], *, allow_missing: bool = True) -> bool:
        """Verify every existing ancestor component under self.root_dir is a real non-symlink directory."""
        if not self.root_dir.exists():
            return False if allow_missing else self._raise_corrupt_path("Storage root is missing.")
        dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            cur_fd = os.open(self.root_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError as e:
            raise AlbumManifestCorruptError(f"Storage root is invalid: {e}") from e
        try:
            for part in rel_parts:
                if not part or "/" in part or "\\" in part or part in (".", ".."):
                    raise AlbumManifestCorruptError("Invalid path component in storage hierarchy.")
                try:
                    next_fd = os.open(part, dir_flags, dir_fd=cur_fd)
                except FileNotFoundError:
                    if allow_missing:
                        return False
                    raise AlbumManifestCorruptError(f"Required storage directory '{part}' is missing.")
                except OSError as e:
                    raise AlbumManifestCorruptError(
                        f"Ambiguous or symlinked directory '{part}' in album storage hierarchy."
                    ) from e
                os.close(cur_fd)
                cur_fd = next_fd
            return True
        finally:
            try:
                os.close(cur_fd)
            except OSError:
                pass

    @staticmethod
    def _raise_corrupt_path(msg: str) -> bool:
        raise AlbumManifestCorruptError(msg)

    def _open_albums_root_fd_nofollow(self, user_id: str) -> Optional[int]:
        """Traverse [tenant_hash, 'albums'] under self.root_dir with O_NOFOLLOW and return albums_root dir_fd."""
        rel_parts = [get_tenant_storage_dir_name(user_id), "albums"]
        if not self.root_dir.exists():
            return None
        dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            cur_fd = os.open(self.root_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError as e:
            raise AlbumManifestCorruptError(f"Storage root is invalid: {e}") from e
        try:
            for part in rel_parts:
                try:
                    next_fd = os.open(part, dir_flags, dir_fd=cur_fd)
                except FileNotFoundError:
                    os.close(cur_fd)
                    return None
                except OSError as e:
                    os.close(cur_fd)
                    raise AlbumManifestCorruptError(
                        f"Ambiguous or symlinked directory '{part}' in album storage hierarchy."
                    ) from e
                os.close(cur_fd)
                cur_fd = next_fd
            return cur_fd
        except BaseException:
            try:
                os.close(cur_fd)
            except OSError:
                pass
            raise

    def _open_album_dir_fd_nofollow(self, user_id: str, album_id: str) -> Optional[int]:
        """Traverse [tenant_hash, 'albums', album_hash] under self.root_dir with O_NOFOLLOW and return album dir_fd."""
        rel_parts = [
            get_tenant_storage_dir_name(user_id),
            "albums",
            get_album_dir_name(album_id),
        ]
        if not self.root_dir.exists():
            return None
        dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            cur_fd = os.open(self.root_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError as e:
            raise AlbumManifestCorruptError(f"Storage root is invalid: {e}") from e
        try:
            for part in rel_parts:
                try:
                    next_fd = os.open(part, dir_flags, dir_fd=cur_fd)
                except FileNotFoundError:
                    os.close(cur_fd)
                    return None
                except OSError as e:
                    os.close(cur_fd)
                    raise AlbumManifestCorruptError(
                        f"Ambiguous or symlinked directory '{part}' in album storage hierarchy."
                    ) from e
                os.close(cur_fd)
                cur_fd = next_fd
            return cur_fd
        except BaseException:
            try:
                os.close(cur_fd)
            except OSError:
                pass
            raise

    @staticmethod
    def _cleanup_newly_created_subdir(parent_fd: int, name: str, expected_id: Optional[Tuple[int, int]] = None) -> None:
        """Remove a newly created empty subdirectory only when its (st_dev, st_ino) identity is verified."""
        if expected_id is None:
            return
        try:
            lst = os.lstat(name, dir_fd=parent_fd)
            if not stat.S_ISDIR(lst.st_mode):
                return
            if (lst.st_dev, lst.st_ino) != expected_id:
                return
            os.rmdir(name, dir_fd=parent_fd)
        except OSError:
            pass

    @staticmethod
    def _open_or_create_subdir_fd_tracked(parent_fd: int, name: str) -> Tuple[int, bool, Tuple[int, int]]:
        """Open or create a subdirectory relative to parent_fd without following symlinks, returning (fd, created, (dev, ino))."""
        dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(name, dir_flags, dir_fd=parent_fd)
        except FileNotFoundError:
            created = False
            try:
                os.mkdir(name, mode=0o700, dir_fd=parent_fd)
                created = True
            except FileExistsError:
                created = False

            fd: Optional[int] = None
            expected_id: Optional[Tuple[int, int]] = None
            try:
                try:
                    fd = os.open(name, dir_flags, dir_fd=parent_fd)
                except OSError as e:
                    raise AlbumManifestCorruptError(
                        f"Ambiguous or symlinked directory '{name}' in album storage hierarchy."
                    ) from e
                st = os.fstat(fd)
                expected_id = (st.st_dev, st.st_ino)
                try:
                    os.fchmod(fd, 0o700)
                except OSError:
                    pass
                return fd, created, expected_id
            except BaseException:
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                if created and expected_id is not None:
                    ProjectStorageManager._cleanup_newly_created_subdir(parent_fd, name, expected_id)
                raise
        except OSError as e:
            raise AlbumManifestCorruptError(
                f"Ambiguous or symlinked directory '{name}' in album storage hierarchy."
            ) from e

        try:
            st = os.fstat(fd)
            return fd, False, (st.st_dev, st.st_ino)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            raise

    @staticmethod
    def _open_or_create_subdir_fd_nofollow(parent_fd: int, name: str) -> int:
        fd, _, _ = ProjectStorageManager._open_or_create_subdir_fd_tracked(parent_fd, name)
        return fd

    def _open_file_under_root_nofollow(self, dir_parts: List[str], filename: str) -> Optional[tuple[int, int]]:
        """Traverse directories under self.root_dir with O_NOFOLLOW and open filename as a regular file."""
        if not filename or "/" in filename or "\\" in filename or filename in (".", ".."):
            return None
        if not self.root_dir.exists():
            return None
        dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            cur_fd = os.open(self.root_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError:
            return None
        try:
            for part in dir_parts:
                if not part or "/" in part or "\\" in part or part in (".", ".."):
                    return None
                try:
                    next_fd = os.open(part, dir_flags, dir_fd=cur_fd)
                except FileNotFoundError:
                    return None
                except OSError as e:
                    raise AlbumManifestCorruptError(
                        f"Ambiguous or symlinked directory '{part}' in album storage hierarchy."
                    ) from e
                os.close(cur_fd)
                cur_fd = next_fd

            try:
                file_fd = os.open(filename, file_flags, dir_fd=cur_fd)
            except FileNotFoundError:
                return None
            except OSError as e:
                raise AlbumManifestCorruptError(
                    f"Target file '{filename}' is a symlink or unreadable."
                ) from e
            try:
                st = os.fstat(file_fd)
                if not stat.S_ISREG(st.st_mode):
                    os.close(file_fd)
                    raise AlbumManifestCorruptError(f"Target '{filename}' is not a regular file.")
                return file_fd, st.st_size
            except BaseException:
                try:
                    os.close(file_fd)
                except OSError:
                    pass
                raise
        finally:
            try:
                os.close(cur_fd)
            except OSError:
                pass

    def _get_album_dir_path(self, user_id: str, album_id: str) -> Path:
        """Pure path computation for an album directory (does not create directories)."""
        if not is_valid_project_id(album_id, expected_prefix="alb"):
            raise ValueError("Invalid album identifier format.")
        return self._get_albums_root_path(user_id) / get_album_dir_name(album_id)

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
        """Retrieve track metadata by ID (side-effect-free)."""
        try:
            track_dir = self._get_track_dir_path(user_id, track_id)
        except ValueError:
            return None
        manifest_path = track_dir / "track.json"
        if not manifest_path.is_file() or manifest_path.is_symlink():
            return None
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Synchronize active take count
            takes_dir = track_dir / "takes"
            actual_count = 0
            if takes_dir.is_dir() and not takes_dir.is_symlink():
                for item in takes_dir.iterdir():
                    if item.is_dir() and not item.is_symlink() and (item / "take.json").is_file():
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
        """Retrieve take metadata (side-effect-free)."""
        try:
            take_dir = self._get_take_dir_path(user_id, track_id, take_id)
        except ValueError:
            return None
        manifest_path = take_dir / "take.json"
        if not manifest_path.is_file() or manifest_path.is_symlink():
            return None
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return TakeMetadata(**data)
        except Exception as e:
            logger.error(f"Failed to read take manifest {take_id}: {e}")
            return None

    def list_takes(self, user_id: str, track_id: str) -> List[TakeMetadata]:
        """List all takes in a track workspace (side-effect-free)."""
        try:
            track_dir = self._get_track_dir_path(user_id, track_id)
        except ValueError:
            return []
        takes_root = track_dir / "takes"
        results = []
        if not takes_root.is_dir() or takes_root.is_symlink():
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
        """Return path to take's master audio file (side-effect-free)."""
        take = self.get_take(user_id, track_id, take_id)
        if not take:
            return None
        take_dir = self._get_take_dir_path(user_id, track_id, take_id)
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

    def _get_album_entries_root_path(self, user_id: str, album_id: str) -> Path:
        """Pure path computation for an album's entries root (does not create directories)."""
        return self._get_album_dir_path(user_id, album_id) / "entries"

    def _get_album_entries_root(self, user_id: str, album_id: str) -> Path:
        album_dir = self._get_album_dir(user_id, album_id)
        return self._ensure_dir_0700(album_dir / "entries")

    def _get_album_entry_dir_path(self, user_id: str, album_id: str, entry_id: str) -> Path:
        """Pure path computation for an album entry directory (does not create directories)."""
        if not is_valid_project_id(entry_id, expected_prefix="ale"):
            raise ValueError("Invalid album entry identifier format.")
        return self._get_album_entries_root_path(user_id, album_id) / get_album_entry_dir_name(entry_id)

    def _get_album_entry_dir(self, user_id: str, album_id: str, entry_id: str) -> Path:
        if not is_valid_project_id(entry_id, expected_prefix="ale"):
            raise ValueError("Invalid album entry identifier format.")
        entries_root = self._get_album_entries_root(user_id, album_id)
        return entries_root / get_album_entry_dir_name(entry_id)

    def _entry_has_filesystem_artifacts(self, user_id: str, album_id: str, entry_id: str) -> bool:
        """Non-following fail-closed check for any file, symlink, or artifact in an entry directory."""
        try:
            entry_dir = self._get_album_entry_dir_path(user_id, album_id, entry_id)
            if not entry_dir.exists() and not entry_dir.is_symlink():
                return False
            st = os.lstat(entry_dir)
            if not stat.S_ISDIR(st.st_mode):
                return True
            return any(entry_dir.iterdir())
        except OSError:
            return True
        except ValueError:
            return True

    def _open_album_read_context(
        self,
        user_id: str,
        album_id: str,
        *,
        protect_finalized: bool = False,
        load_journal: bool = False,
    ) -> Optional[_AlbumReadContext]:
        """Open a single descriptor-bound read snapshot of album.json (and journal.json when requested)."""
        try:
            album_dir = self._get_album_dir_path(user_id, album_id)
            album_dir_fd = self._open_album_dir_fd_nofollow(user_id, album_id)
        except ValueError:
            return None
        if album_dir_fd is None:
            return None
        try:
            raw_holder: List[Dict[str, Any]] = []
            identities_holder: Dict[str, Any] = {}
            loaded = self._read_album_from_dir_fd(
                album_id,
                album_dir_fd,
                protect_finalized=protect_finalized,
                raw_out=raw_holder,
                identities_out=identities_holder,
            )
            if loaded is None:
                os.close(album_dir_fd)
                return None
            album, norm_data, manifest_st = loaded
            raw_data = raw_holder[0]
            album_identity = (int(manifest_st.st_dev), int(manifest_st.st_ino))
            raw_album_sha256 = self._compute_canonical_manifest_sha256(raw_data)

            journal: Optional[AlbumJournal] = None
            journal_identity: Optional[Tuple[int, int]] = None
            journal_sha256: Optional[str] = None
            if load_journal:
                j_res = self._read_json_file_verified_in_dir(
                    album_dir_fd, "journal.json", sync_file=False, required=False
                )
                if j_res is not None:
                    j_raw, j_id, j_sha = j_res
                    try:
                        journal = AlbumJournal(**j_raw)
                    except Exception as exc:
                        raise AlbumManifestCorruptError(
                            f"Album '{album_id}' journal.json failed validation: {exc}"
                        ) from exc
                    if journal.album_id != album_id:
                        raise AlbumManifestCorruptError(
                            f"Album '{album_id}' journal.json album_id mismatch ('{journal.album_id}')."
                        )
                    journal_identity = j_id
                    journal_sha256 = j_sha
                    # Re-verify album.json identity/digest to detect concurrent drift between manifest and journal
                    try:
                        _, cur_id, cur_sha = self._read_json_file_verified_in_dir(
                            album_dir_fd, "album.json", sync_file=False, required=True
                        )
                    except AlbumManifestCorruptError:
                        raise
                    except Exception as exc:
                        raise AlbumDurabilityUncertainError(
                            f"Cannot re-verify album.json after journal acquisition for '{album_id}': {exc}"
                        ) from exc
                    if (cur_id, cur_sha) != (album_identity, raw_album_sha256):
                        raise AlbumDurabilityUncertainError(
                            f"Album '{album_id}' manifest changed concurrently during read acquisition."
                        )
                    cons = self._classify_album_consistency(raw_data, journal)
                    if cons == RecoveryConsistency.CORRUPT:
                        raise AlbumManifestCorruptError(
                            f"Album '{album_id}' manifest and journal evidence are contradictory."
                        )

            return _AlbumReadContext(
                album_id=album_id,
                album_dir=album_dir,
                album_dir_fd=album_dir_fd,
                raw_data=raw_data,
                album_identity=album_identity,
                raw_album_sha256=raw_album_sha256,
                album=album,
                norm_data=norm_data,
                observed_identities=identities_holder,
                journal=journal,
                journal_identity=journal_identity,
                journal_sha256=journal_sha256,
            )
        except BaseException:
            try:
                os.close(album_dir_fd)
            except OSError:
                pass
            raise

    @staticmethod
    def _open_regular_file_in_dir_for_read(
        dir_fd: int,
        filename: str,
        *,
        missing_is_uncertain: bool,
    ) -> Optional[Tuple[int, os.stat_result]]:
        """Open filename under dir_fd with O_NOFOLLOW and classify missing/unsafe/I/O errors cleanly."""
        import errno

        if not filename or "/" in filename or "\\" in filename or filename in (".", ".."):
            raise AlbumManifestCorruptError(f"Invalid artifact filename '{filename}'.")
        file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            ffd = os.open(filename, file_flags, dir_fd=dir_fd)
        except FileNotFoundError as exc:
            if missing_is_uncertain:
                raise AlbumDurabilityUncertainError(
                    f"Declared active artifact '{filename}' is missing on disk."
                ) from exc
            return None
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR):
                raise AlbumManifestCorruptError(f"Artifact '{filename}' is unsafe or non-regular: {exc}") from exc
            raise AlbumDurabilityUncertainError(f"Cannot open artifact '{filename}' for reading: {exc}") from exc
        try:
            st = os.fstat(ffd)
            if not stat.S_ISREG(st.st_mode):
                raise AlbumManifestCorruptError(f"Artifact '{filename}' is not a regular file.")
            return ffd, st
        except (AlbumManifestCorruptError, AlbumDurabilityUncertainError):
            try:
                os.close(ffd)
            except OSError:
                pass
            raise
        except OSError as exc:
            try:
                os.close(ffd)
            except OSError:
                pass
            raise AlbumDurabilityUncertainError(
                f"Cannot stat opened artifact '{filename}': {exc}"
            ) from exc
        except BaseException:
            try:
                os.close(ffd)
            except OSError:
                pass
            raise

    def _verify_read_artifact_provenance_locked(
        self,
        context: _AlbumReadContext,
        *,
        role: ArtifactRole,
        rel_path: str,
        entry_id: Optional[str] = None,
        generation: Optional[int] = None,
        snapshot_rel_path: Optional[str] = None,
    ) -> Tuple[Optional[ArtifactRefRecord], Optional[ArtifactRefRecord]]:
        """Verify read-only artifact provenance via historical COMMITTED receipts or exact visible active_tx target."""
        journal = context.journal
        if journal is None:
            if generation is not None:
                raise AlbumManifestCorruptError(
                    f"Album '{context.album_id}' declares generated artifact '{rel_path}' without a transaction journal."
                )
            return None, None

        for ret in journal.retained_artifacts:
            if ret.rel_path == rel_path or (snapshot_rel_path and ret.rel_path == snapshot_rel_path):
                raise AlbumManifestCorruptError(
                    f"Album '{context.album_id}' active artifact overlaps retained artifact '{ret.rel_path}'."
                )

        selected_ref: Optional[ArtifactRefRecord] = None
        paired_snap_ref: Optional[ArtifactRefRecord] = None

        # Path A: Exact visible target of PUBLISH_INTENT or UNCERTAIN
        tx = journal.active_tx
        if tx is not None and tx.state in (JournalTxState.PUBLISH_INTENT, JournalTxState.UNCERTAIN):
            if context.raw_data.get("last_tx_id") == tx.tx_id:
                if int(context.raw_data.get("revision", 0)) != tx.target_revision or context.raw_album_sha256 != tx.intended_manifest_sha256:
                    raise AlbumManifestCorruptError(
                        f"Album '{context.album_id}' visible manifest contradicts active transaction '{tx.tx_id}'."
                    )
                for ref in tx.new_refs:
                    if ref.rel_path == rel_path:
                        selected_ref = ref
                    elif snapshot_rel_path and ref.rel_path == snapshot_rel_path:
                        paired_snap_ref = ref

        # Path B: Historical COMMITTED receipt introducing rel_path (also handles predecessor reads)
        if selected_ref is None:
            tx_history_set = set(context.album.tx_history or [])
            for rcp in reversed(journal.tx_receipts):
                if rcp.outcome != JournalTxState.COMMITTED:
                    continue
                found = next((r for r in rcp.new_refs if r.rel_path == rel_path), None)
                if found is not None:
                    if (
                        rcp.tx_id not in tx_history_set
                        and context.album.last_tx_id != rcp.tx_id
                    ) or rcp.result_revision > context.album.revision:
                        raise AlbumManifestCorruptError(
                            f"Album '{context.album_id}' committed receipt '{rcp.tx_id}' contradicts manifest lineage."
                        )
                    selected_ref = found
                    if snapshot_rel_path:
                        paired_snap_ref = next((r for r in rcp.new_refs if r.rel_path == snapshot_rel_path), None)
                    break

        # Path C: Missing provenance
        if selected_ref is None:
            if generation is not None:
                raise AlbumDurabilityUncertainError(
                    f"Album '{context.album_id}' lacks transaction provenance for generated artifact '{rel_path}'."
                )
            # Legacy fixed-name artifact on a v2-enrolled album: reject if superseded by a committed receipt
            for rcp in journal.tx_receipts:
                if rcp.outcome == JournalTxState.COMMITTED and any(r.rel_path == rel_path for r in rcp.superseded_refs):
                    raise AlbumManifestCorruptError(
                        f"Album '{context.album_id}' legacy artifact '{rel_path}' was previously superseded."
                    )
            return None, None

        if selected_ref.role != role or selected_ref.entry_id != entry_id or selected_ref.generation != generation:
            raise AlbumManifestCorruptError(
                f"Album '{context.album_id}' artifact reference metadata mismatch for '{rel_path}'."
            )

        refs_to_check = [selected_ref]
        if role == ArtifactRole.ENTRY_AUDIO and generation is not None:
            if paired_snap_ref is None:
                raise AlbumDurabilityUncertainError(
                    f"Album '{context.album_id}' lacks paired snapshot provenance for '{rel_path}'."
                )
            if (
                paired_snap_ref.role != ArtifactRole.ENTRY_SNAPSHOT
                or paired_snap_ref.entry_id != entry_id
                or paired_snap_ref.generation != generation
            ):
                raise AlbumManifestCorruptError(
                    f"Album '{context.album_id}' paired snapshot reference contradicts '{rel_path}'."
                )
            refs_to_check.append(paired_snap_ref)

        binding_status = self._verify_v2_refs_against_manifest(context.raw_data, refs_to_check)
        if binding_status == RecoveryConsistency.CORRUPT:
            raise AlbumManifestCorruptError(
                f"Album '{context.album_id}' artifact reference bindings contradict manifest for '{rel_path}'."
            )
        if binding_status == RecoveryConsistency.UNCERTAIN:
            raise AlbumDurabilityUncertainError(
                f"Album '{context.album_id}' artifact reference bindings are incomplete for '{rel_path}'."
            )

        return selected_ref, paired_snap_ref

    def _verify_generated_snapshot_for_read_locked(
        self,
        context: _AlbumReadContext,
        entry_fd: int,
        entry: AlbumTrackEntry,
        snap_name: str,
        snap_ref: ArtifactRefRecord,
    ) -> None:
        """Open, stability-check, hash-check, and semantically verify a generated snapshot_g<N>.json."""
        opened = self._open_regular_file_in_dir_for_read(entry_fd, snap_name, missing_is_uncertain=True)
        assert opened is not None
        sfd, pre_st = opened
        try:
            pre_tup = self._seal_stat_tuple(pre_st)
            try:
                with os.fdopen(os.dup(sfd), "rb") as sf:
                    snap_bytes = sf.read()
                post_st = os.fstat(sfd)
                dir_st = os.lstat(snap_name, dir_fd=entry_fd)
            except OSError as exc:
                raise AlbumDurabilityUncertainError(
                    f"Cannot read or stat generated snapshot '{snap_name}' for entry '{entry.id}': {exc}"
                ) from exc
            if self._seal_stat_tuple(post_st) != pre_tup or self._seal_stat_tuple(dir_st) != pre_tup:
                raise AlbumDurabilityUncertainError(
                    f"Generated snapshot '{snap_name}' changed concurrently during read verification."
                )
            snap_sha = hashlib.sha256(snap_bytes).hexdigest()
            if len(snap_bytes) != snap_ref.size_bytes or snap_sha != snap_ref.sha256:
                raise AlbumManifestCorruptError(
                    f"Generated snapshot '{snap_name}' size or SHA-256 contradicts reference for entry '{entry.id}'."
                )
            try:
                disk_snap = AlbumEntrySnapshot(**json.loads(snap_bytes.decode("utf-8")))
            except Exception as exc:
                raise AlbumManifestCorruptError(
                    f"Generated snapshot '{snap_name}' is invalid JSON or schema for entry '{entry.id}': {exc}"
                ) from exc
            if (
                disk_snap.entry_id != entry.id
                or disk_snap.source_track_id != entry.track_id
                or disk_snap.source_take_id != entry.take_id
            ):
                raise AlbumManifestCorruptError(
                    f"Generated snapshot '{snap_name}' entry/track/take identity mismatch for entry '{entry.id}'."
                )
            if entry.active_audio_filename:
                expected_ext = f".{disk_snap.format.lower().lstrip('.')}"
                if os.path.splitext(entry.active_audio_filename)[1].lower() != expected_ext:
                    raise AlbumManifestCorruptError(
                        f"Generated snapshot '{snap_name}' format contradicts '{entry.active_audio_filename}'."
                    )
            if entry.snapshot is None or not self._snapshots_semantically_equal(disk_snap, entry.snapshot):
                raise AlbumManifestCorruptError(
                    f"Generated snapshot '{snap_name}' does not match manifest snapshot for entry '{entry.id}'."
                )
        finally:
            try:
                os.close(sfd)
            except OSError:
                pass

    def _resolve_and_open_entry_audio_from_context(
        self,
        context: _AlbumReadContext,
        entry: AlbumTrackEntry,
        *,
        verify_snapshot: bool,
    ) -> Optional[Tuple[str, int, int]]:
        """Resolve and open entry audio relative to context.album_dir_fd with provenance and snapshot checks."""
        import errno

        assert context.album_dir_fd is not None
        assert entry.id is not None
        is_generated = entry.active_generation is not None
        dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        ehash = get_album_entry_dir_name(entry.id)

        entries_fd: Optional[int] = None
        entry_fd: Optional[int] = None
        try:
            try:
                entries_fd = os.open("entries", dir_flags, dir_fd=context.album_dir_fd)
                entry_fd = os.open(ehash, dir_flags, dir_fd=entries_fd)
            except FileNotFoundError as exc:
                if is_generated:
                    raise AlbumDurabilityUncertainError(
                        f"Entry directory missing for generated entry '{entry.id}'."
                    ) from exc
                return None
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR):
                    raise AlbumManifestCorruptError(
                        f"Unsafe or symlinked entry directory for '{entry.id}': {exc}"
                    ) from exc
                raise AlbumDurabilityUncertainError(
                    f"Cannot access entry directory for '{entry.id}': {exc}"
                ) from exc

            try:
                ent_root_st = os.fstat(entries_fd)
                edir_st = os.fstat(entry_fd)
            except OSError as exc:
                raise AlbumDurabilityUncertainError(
                    f"Cannot stat entry directory for '{entry.id}': {exc}"
                ) from exc

            obs_entries_dir_id = context.observed_identities.get("entries_dir_id")
            if obs_entries_dir_id is not None and (int(ent_root_st.st_dev), int(ent_root_st.st_ino)) != obs_entries_dir_id:
                raise AlbumDurabilityUncertainError(
                    f"Album '{context.album_id}' entries directory identity drifted during read acquisition."
                )
            obs_entry = context.observed_identities.get("entries", {}).get(entry.id)
            if obs_entry is not None and (int(edir_st.st_dev), int(edir_st.st_ino)) != obs_entry["entry_dir_id"]:
                raise AlbumDurabilityUncertainError(
                    f"Entry directory identity drifted for '{entry.id}' during read acquisition."
                )

            if is_generated:
                assert entry.active_audio_filename is not None and entry.active_snapshot_filename is not None
                audio_rel = f"entries/{ehash}/{entry.active_audio_filename}"
                snap_rel = f"entries/{ehash}/{entry.active_snapshot_filename}"
                audio_ref, snap_ref = self._verify_read_artifact_provenance_locked(
                    context,
                    role=ArtifactRole.ENTRY_AUDIO,
                    rel_path=audio_rel,
                    entry_id=entry.id,
                    generation=entry.active_generation,
                    snapshot_rel_path=snap_rel,
                )
                opened = self._open_regular_file_in_dir_for_read(
                    entry_fd, entry.active_audio_filename, missing_is_uncertain=True
                )
                assert opened is not None
                afd, ast = opened
                try:
                    if obs_entry is not None and obs_entry.get("audio") is not None:
                        if self._seal_stat_tuple(ast) != obs_entry["audio"][1]:
                            raise AlbumDurabilityUncertainError(
                                f"Generated audio '{entry.active_audio_filename}' identity changed during read acquisition."
                            )
                    if audio_ref is not None and ast.st_size != audio_ref.size_bytes:
                        raise AlbumManifestCorruptError(
                            f"Generated audio '{entry.active_audio_filename}' size contradicts reference."
                        )
                    if verify_snapshot:
                        if audio_ref is not None:
                            h_audio = hashlib.sha256()
                            try:
                                os.lseek(afd, 0, os.SEEK_SET)
                                while True:
                                    chunk = os.read(afd, 65536)
                                    if not chunk:
                                        break
                                    h_audio.update(chunk)
                                os.lseek(afd, 0, os.SEEK_SET)
                                post_ast = os.fstat(afd)
                                dir_ast = os.lstat(entry.active_audio_filename, dir_fd=entry_fd)
                            except OSError as exc:
                                raise AlbumDurabilityUncertainError(
                                    f"Cannot verify generated audio '{entry.active_audio_filename}' contents: {exc}"
                                ) from exc
                            if (
                                self._seal_stat_tuple(post_ast) != self._seal_stat_tuple(ast)
                                or self._seal_stat_tuple(dir_ast) != self._seal_stat_tuple(ast)
                            ):
                                raise AlbumDurabilityUncertainError(
                                    f"Generated audio '{entry.active_audio_filename}' changed during verification."
                                )
                            if h_audio.hexdigest() != audio_ref.sha256:
                                raise AlbumManifestCorruptError(
                                    f"Generated audio '{entry.active_audio_filename}' SHA-256 contradicts reference."
                                )
                        assert snap_ref is not None
                        self._verify_generated_snapshot_for_read_locked(
                            context, entry_fd, entry, entry.active_snapshot_filename, snap_ref
                        )
                        try:
                            cur_ent_root_st = os.fstat(entries_fd)
                            cur_edir_st = os.fstat(entry_fd)
                            cur_dir_ast = os.lstat(entry.active_audio_filename, dir_fd=entry_fd)
                            cur_ast = os.fstat(afd)
                        except OSError as exc:
                            raise AlbumDurabilityUncertainError(
                                f"Cannot re-verify descriptors after snapshot check: {exc}"
                            ) from exc
                        if (
                            (int(cur_ent_root_st.st_dev), int(cur_ent_root_st.st_ino)) != (int(ent_root_st.st_dev), int(ent_root_st.st_ino))
                            or (int(cur_edir_st.st_dev), int(cur_edir_st.st_ino)) != (int(edir_st.st_dev), int(edir_st.st_ino))
                            or self._seal_stat_tuple(cur_dir_ast) != self._seal_stat_tuple(ast)
                            or self._seal_stat_tuple(cur_ast) != self._seal_stat_tuple(ast)
                        ):
                            raise AlbumDurabilityUncertainError(
                                f"Entry or audio identity drifted during read verification for '{entry.id}'."
                            )
                    return entry.active_audio_filename, afd, int(ast.st_size)
                except BaseException:
                    try:
                        os.close(afd)
                    except OSError:
                        pass
                    raise
            else:
                for ext in AUDIO_FORMATS:
                    filename = f"audio{ext}"
                    opened = self._open_regular_file_in_dir_for_read(
                        entry_fd, filename, missing_is_uncertain=False
                    )
                    if opened is not None:
                        afd, ast = opened
                        try:
                            self._verify_read_artifact_provenance_locked(
                                context,
                                role=ArtifactRole.ENTRY_AUDIO,
                                rel_path=f"entries/{ehash}/{filename}",
                                entry_id=entry.id,
                                generation=None,
                            )
                            return filename, afd, int(ast.st_size)
                        except BaseException:
                            try:
                                os.close(afd)
                            except OSError:
                                pass
                            raise
                return None
        finally:
            if entry_fd is not None:
                try:
                    os.close(entry_fd)
                except OSError:
                    pass
            if entries_fd is not None:
                try:
                    os.close(entries_fd)
                except OSError:
                    pass

    def _resolve_and_open_cover_from_context(
        self, context: _AlbumReadContext
    ) -> Optional[Tuple[str, int, int]]:
        """Resolve and open album cover relative to context.album_dir_fd with provenance checks."""
        assert context.album_dir_fd is not None
        if context.album.active_cover_filename is not None:
            cover_name = context.album.active_cover_filename
            cov_ref, _ = self._verify_read_artifact_provenance_locked(
                context,
                role=ArtifactRole.COVER_ART,
                rel_path=cover_name,
                entry_id=None,
                generation=context.album.cover_generation,
            )
            opened = self._open_regular_file_in_dir_for_read(
                context.album_dir_fd, cover_name, missing_is_uncertain=True
            )
            assert opened is not None
            cfd, cst = opened
            try:
                obs_cov = context.observed_identities.get("cover")
                if obs_cov is not None and self._seal_stat_tuple(cst) != obs_cov[1]:
                    raise AlbumDurabilityUncertainError(
                        f"Generated cover '{cover_name}' identity changed during read acquisition."
                    )
                if cov_ref is not None:
                    if cst.st_size != cov_ref.size_bytes:
                        raise AlbumManifestCorruptError(
                            f"Generated cover '{cover_name}' size contradicts reference."
                        )
                    h_cov = hashlib.sha256()
                    try:
                        os.lseek(cfd, 0, os.SEEK_SET)
                        while True:
                            chunk = os.read(cfd, 65536)
                            if not chunk:
                                break
                            h_cov.update(chunk)
                        os.lseek(cfd, 0, os.SEEK_SET)
                        post_cst = os.fstat(cfd)
                    except OSError as exc:
                        raise AlbumDurabilityUncertainError(
                            f"Cannot verify generated cover '{cover_name}' contents: {exc}"
                        ) from exc
                    if self._seal_stat_tuple(post_cst) != self._seal_stat_tuple(cst):
                        raise AlbumDurabilityUncertainError(
                            f"Generated cover '{cover_name}' changed during verification."
                        )
                    if h_cov.hexdigest() != cov_ref.sha256:
                        raise AlbumManifestCorruptError(
                            f"Generated cover '{cover_name}' SHA-256 contradicts reference."
                        )
                return cover_name, cfd, int(cst.st_size)
            except BaseException:
                try:
                    os.close(cfd)
                except OSError:
                    pass
                raise

        for ext in (".jpg", ".jpeg", ".png"):
            cover_name = f"cover{ext}"
            opened = self._open_regular_file_in_dir_for_read(
                context.album_dir_fd, cover_name, missing_is_uncertain=False
            )
            if opened is not None:
                cfd, cst = opened
                try:
                    self._verify_read_artifact_provenance_locked(
                        context,
                        role=ArtifactRole.COVER_ART,
                        rel_path=cover_name,
                        entry_id=None,
                        generation=None,
                    )
                    return cover_name, cfd, int(cst.st_size)
                except BaseException:
                    try:
                        os.close(cfd)
                    except OSError:
                        pass
                    raise
        return None


    def get_album_entry_audio_path(self, user_id: str, album_id: str, entry_id: str) -> Optional[Path]:
        """Return path to an album entry's active owned audio copy via shared read context (side-effect-free)."""
        if not is_valid_project_id(album_id, expected_prefix="alb") or not is_valid_project_id(entry_id, expected_prefix="ale"):
            return None
        ctx = self._open_album_read_context(user_id, album_id, load_journal=True)
        if ctx is None:
            return None
        try:
            entry = next((t for t in ctx.album.tracks if t.id == entry_id), None)
            if not entry or entry.is_placeholder or not entry.has_owned_audio:
                return None
            rel_parts = [
                get_tenant_storage_dir_name(user_id),
                "albums",
                get_album_dir_name(album_id),
                "entries",
                get_album_entry_dir_name(entry_id),
            ]
            entry_dir = self._get_album_entry_dir_path(user_id, album_id, entry_id)
            if not self._verify_ancestors_nofollow(rel_parts, allow_missing=True):
                return None
            opened = self._resolve_and_open_entry_audio_from_context(ctx, entry, verify_snapshot=False)
            if opened is None:
                return None
            filename, fd, _size = opened
            try:
                os.close(fd)
            except OSError:
                pass
            return entry_dir / filename
        finally:
            ctx.close()

    def open_album_entry_audio_for_read(
        self, user_id: str, album_id: str, entry_id: str
    ) -> Optional[tuple[AlbumTrackEntry, Path, int, int]]:
        """Validate manifest, provenance, and generated snapshot, then open entry audio fd via shared read context."""
        if not is_valid_project_id(album_id, expected_prefix="alb") or not is_valid_project_id(entry_id, expected_prefix="ale"):
            return None
        ctx = self._open_album_read_context(user_id, album_id, load_journal=True)
        if ctx is None:
            return None
        try:
            entry = next((t for t in ctx.album.tracks if t.id == entry_id), None)
            if not entry or entry.is_placeholder or not entry.has_owned_audio:
                return None
            opened = self._resolve_and_open_entry_audio_from_context(ctx, entry, verify_snapshot=True)
            if opened is None:
                return None
            filename, fd, size = opened
            entry_dir = self._get_album_entry_dir_path(user_id, album_id, entry_id)
            return entry, entry_dir / filename, fd, size
        finally:
            ctx.close()

    def open_album_cover_for_read(self, user_id: str, album_id: str) -> Optional[tuple[Path, int, int]]:
        """Validate album manifest and provenance, then open cover fd via shared read context."""
        if not is_valid_project_id(album_id, expected_prefix="alb"):
            return None
        ctx = self._open_album_read_context(user_id, album_id, load_journal=True)
        if ctx is None:
            return None
        try:
            opened = self._resolve_and_open_cover_from_context(ctx)
            if opened is None:
                return None
            filename, fd, size = opened
            return ctx.album_dir / filename, fd, size
        finally:
            ctx.close()

    def _get_album_lock_path(self, user_id: str, album_id: str) -> Path:
        if not is_valid_project_id(album_id, expected_prefix="alb"):
            raise ValueError("Invalid album identifier format.")
        return self._get_albums_root(user_id) / f".{get_album_dir_name(album_id)}.lock"

    @contextmanager
    def _album_lock(self, user_id: str, album_id: str):
        """Acquire an inode-stable, cross-process, re-entrancy-safe lock for an album.

        Stored outside album_dir so manifest atomic_json replacements and album deletions
        never swap or unlink the lock inode while other operations are waiting on it.
        """
        lock_path = self._get_album_lock_path(user_id, album_id)
        held = getattr(self._thread_local, "album_locks", None)
        if held is None:
            held = {}
            self._thread_local.album_locks = held
        key = str(lock_path)
        depth = held.get(key, 0)
        if depth > 0:
            held[key] = depth + 1
            try:
                yield lock_path
            finally:
                held[key] = depth
            return

        with file_lock(lock_path):
            held[key] = 1
            try:
                yield lock_path
            finally:
                held.pop(key, None)

    @staticmethod
    def _inspect_raw_album_manifest(raw_data: Any, album_id: str, *, protect_finalized: bool = False) -> None:
        """Inspect raw album.json before normalization to preserve finalized protections and reject contradictory state."""
        if not isinstance(raw_data, dict):
            raise AlbumManifestCorruptError(
                f"Album manifest '{album_id}' has invalid structure; refusing operation to preserve existing data."
            )

        is_raw_finalized = bool(raw_data.get("is_finalized", False)) or (
            raw_data.get("status") == WorkspaceStatus.COMPLETED.value
        )
        raw_tracks = raw_data.get("tracks")
        if raw_tracks is not None:
            if not isinstance(raw_tracks, list):
                if protect_finalized and is_raw_finalized:
                    raise AlbumFinalizedError("Finalized albums are immutable and cannot be modified or deleted.")
                raise AlbumManifestCorruptError(
                    f"Album manifest '{album_id}' has invalid tracks list; refusing operation to preserve existing data."
                )
            for item in raw_tracks:
                if not isinstance(item, dict):
                    if protect_finalized and is_raw_finalized:
                        raise AlbumFinalizedError("Finalized albums are immutable and cannot be modified or deleted.")
                    raise AlbumManifestCorruptError(
                        f"Album manifest '{album_id}' contains malformed track entry; refusing operation to preserve existing data."
                    )
                if item.get("take_id") is None and (item.get("snapshot") is not None or bool(item.get("has_owned_audio", False))):
                    if protect_finalized and is_raw_finalized:
                        raise AlbumFinalizedError("Finalized albums are immutable and cannot be modified or deleted.")
                    raise AlbumManifestCorruptError(
                        f"Album manifest '{album_id}' contains contradictory placeholder/snapshot data; refusing operation to preserve existing data."
                    )

    @staticmethod
    def _validate_raw_generation_declarations(raw_data: Dict[str, Any], album_id: str) -> None:
        """Validate raw generation, ownership, placeholder/take, and snapshot declarations before normalization."""
        cov_gen = raw_data.get("cover_generation")
        cov_name = raw_data.get("active_cover_filename")
        if cov_gen is not None or cov_name is not None:
            if cov_gen is None or cov_name is None:
                raise AlbumManifestCorruptError(
                    f"Album manifest '{album_id}' has partial cover generation declaration."
                )
            if isinstance(cov_gen, bool) or not isinstance(cov_gen, int) or cov_gen < 1:
                raise AlbumManifestCorruptError(
                    f"Album manifest '{album_id}' has invalid cover_generation."
                )
            if not isinstance(cov_name, str):
                raise AlbumManifestCorruptError(
                    f"Album manifest '{album_id}' has invalid active_cover_filename."
                )
            try:
                _validate_safe_active_basename(cov_name)
            except ValueError as exc:
                raise AlbumManifestCorruptError(
                    f"Album manifest '{album_id}' has invalid active_cover_filename: {exc}"
                ) from exc
            cm = _GEN_COVER_RE.match(cov_name)
            if not cm or int(cm.group(1)) != cov_gen or cm.group(2).lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                raise AlbumManifestCorruptError(
                    f"Album manifest '{album_id}' cover_generation/active_cover_filename mismatch."
                )
            if raw_data.get("has_cover") is False:
                raise AlbumManifestCorruptError(
                    f"Album manifest '{album_id}' declares active cover '{cov_name}' with has_cover=False."
                )

        raw_tracks = raw_data.get("tracks")
        if not isinstance(raw_tracks, list):
            return
        for item in raw_tracks:
            if not isinstance(item, dict):
                continue
            take_id = item.get("take_id")
            is_ph = item.get("is_placeholder")
            has_owned = item.get("has_owned_audio")
            snap = item.get("snapshot")
            gen = item.get("active_generation")
            a_name = item.get("active_audio_filename")
            s_name = item.get("active_snapshot_filename")

            if is_ph is True and take_id is not None:
                raise AlbumManifestCorruptError(
                    f"Album manifest '{album_id}' has contradictory is_placeholder=True with take_id."
                )
            if take_id is None and (gen is not None or a_name is not None or s_name is not None):
                raise AlbumManifestCorruptError(
                    f"Album manifest '{album_id}' contains placeholder entry with generated artifact fields."
                )

            if gen is not None or a_name is not None or s_name is not None:
                if gen is None or a_name is None or s_name is None:
                    raise AlbumManifestCorruptError(
                        f"Album manifest '{album_id}' has partial entry generation declaration."
                    )
                if isinstance(gen, bool) or not isinstance(gen, int) or gen < 1:
                    raise AlbumManifestCorruptError(
                        f"Album manifest '{album_id}' has invalid entry active_generation."
                    )
                if not isinstance(a_name, str):
                    raise AlbumManifestCorruptError(
                        f"Album manifest '{album_id}' has invalid active_audio_filename."
                    )
                try:
                    _validate_safe_active_basename(a_name)
                except ValueError as exc:
                    raise AlbumManifestCorruptError(
                        f"Album manifest '{album_id}' has invalid active_audio_filename: {exc}"
                    ) from exc
                am = _GEN_AUDIO_RE.match(a_name)
                if not am or int(am.group(1)) != gen or am.group(2).lower() not in AUDIO_FORMATS:
                    raise AlbumManifestCorruptError(
                        f"Album manifest '{album_id}' active_generation/active_audio_filename mismatch."
                    )
                if not isinstance(s_name, str) or s_name != f"snapshot_g{gen}.json":
                    raise AlbumManifestCorruptError(
                        f"Album manifest '{album_id}' active_generation/active_snapshot_filename mismatch."
                    )
                if has_owned is not True:
                    raise AlbumManifestCorruptError(
                        f"Album manifest '{album_id}' generated entry must declare has_owned_audio=True."
                    )
                if not isinstance(snap, dict):
                    raise AlbumManifestCorruptError(
                        f"Album manifest '{album_id}' generated entry is missing embedded snapshot."
                    )
                try:
                    snap_obj = AlbumEntrySnapshot(**snap)
                except Exception as exc:
                    raise AlbumManifestCorruptError(
                        f"Album manifest '{album_id}' has invalid embedded snapshot: {exc}"
                    ) from exc
                eid = item.get("id")
                if (
                    snap_obj.source_track_id != item.get("track_id")
                    or snap_obj.source_take_id != take_id
                    or (isinstance(eid, str) and is_valid_project_id(eid, expected_prefix="ale") and snap_obj.entry_id != eid)
                ):
                    raise AlbumManifestCorruptError(
                        f"Album manifest '{album_id}' embedded snapshot identity mismatch."
                    )
                audio_ext = os.path.splitext(a_name)[1].lower()
                if f".{snap_obj.format.lower().lstrip('.')}" != audio_ext:
                    raise AlbumManifestCorruptError(
                        f"Album manifest '{album_id}' snapshot format '{snap_obj.format}' contradicts '{a_name}'."
                    )
            elif snap is not None:
                if not isinstance(snap, dict):
                    raise AlbumManifestCorruptError(
                        f"Album manifest '{album_id}' has invalid embedded snapshot."
                    )
                try:
                    snap_obj = AlbumEntrySnapshot(**snap)
                except Exception as exc:
                    raise AlbumManifestCorruptError(
                        f"Album manifest '{album_id}' has invalid embedded snapshot: {exc}"
                    ) from exc
                eid = item.get("id")
                if (
                    snap_obj.source_track_id != item.get("track_id")
                    or snap_obj.source_take_id != take_id
                    or (isinstance(eid, str) and is_valid_project_id(eid, expected_prefix="ale") and snap_obj.entry_id != eid)
                ):
                    raise AlbumManifestCorruptError(
                        f"Album manifest '{album_id}' embedded snapshot identity mismatch."
                    )

    @staticmethod
    def _normalize_album_entries_shared(album_id: str, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Normalize album entry dicts with two-pass persisted ID reservation and deterministic legacy derivation."""
        raw_tracks = data.get("tracks")
        raw_track_ids = data.get("track_ids")
        if not raw_tracks and raw_track_ids:
            raw_tracks = [
                {"track_number": i + 1, "disc_number": 1, "track_id": tid, "take_id": None, "custom_title": None}
                for i, tid in enumerate(raw_track_ids)
            ]

        if not isinstance(raw_tracks, list):
            return []

        persisted_ids: set[str] = set()
        for item in raw_tracks:
            if not isinstance(item, dict):
                continue
            eid = item.get("id")
            if isinstance(eid, str) and is_valid_project_id(eid, expected_prefix="ale"):
                if eid in persisted_ids:
                    raise AlbumManifestCorruptError(
                        f"Album manifest '{album_id}' contains duplicate persisted entry identifier '{eid}'."
                    )
                persisted_ids.add(eid)

        seen_ids: set[str] = set(persisted_ids)
        norm_tracks: List[Dict[str, Any]] = []
        for idx, item in enumerate(raw_tracks):
            if not isinstance(item, dict):
                continue
            entry = dict(item)
            eid = entry.get("id")
            if not (isinstance(eid, str) and is_valid_project_id(eid, expected_prefix="ale")):
                tid = str(entry.get("track_id") or "")
                take_id = entry.get("take_id")
                candidate = generate_deterministic_legacy_entry_id(album_id, idx, tid, take_id)
                salt_idx = idx
                while candidate in seen_ids:
                    salt_idx += 100000
                    candidate = generate_deterministic_legacy_entry_id(album_id, salt_idx, tid, take_id)
                seen_ids.add(candidate)
                entry["id"] = candidate

            entry["track_number"] = int(entry.get("track_number") or (idx + 1))
            entry["disc_number"] = int(entry.get("disc_number") or 1)
            if entry.get("take_id") is None:
                entry["is_placeholder"] = True
                entry["has_owned_audio"] = False
                entry["snapshot"] = None
            else:
                entry["is_placeholder"] = False
            norm_tracks.append(entry)

        return norm_tracks

    def _normalize_album_data_from_dir_fd(
        self,
        album_dir_fd: int,
        data: Dict[str, Any],
        *,
        identities_out: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Non-destructively normalize album manifest using validated album_dir_fd."""
        import errno

        album_id = str(data.get("id") or "")
        obs_entries: Dict[str, Any] = {}
        obs_cover: Optional[Tuple[str, Tuple[int, int, int, int, int]]] = None
        obs_entries_dir_id: Optional[Tuple[int, int]] = None

        active_cov = data.get("active_cover_filename")
        if active_cov is not None:
            try:
                st = os.lstat(active_cov, dir_fd=album_dir_fd)
            except FileNotFoundError as exc:
                raise AlbumDurabilityUncertainError(
                    f"Album '{album_id}' declared active cover '{active_cov}' is missing on disk."
                ) from exc
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR):
                    raise AlbumManifestCorruptError(
                        f"Album '{album_id}' active cover '{active_cov}' is unsafe or symlinked."
                    ) from exc
                raise AlbumDurabilityUncertainError(
                    f"Cannot stat declared active cover '{active_cov}' for album '{album_id}': {exc}"
                ) from exc
            if not stat.S_ISREG(st.st_mode):
                raise AlbumManifestCorruptError(
                    f"Album '{album_id}' active cover '{active_cov}' is not a regular file."
                )
            obs_cover = (active_cov, self._seal_stat_tuple(st))
            data["has_cover"] = True
        else:
            has_cover = False
            for cov_name in ("cover.jpg", "cover.jpeg", "cover.png"):
                try:
                    st = os.lstat(cov_name, dir_fd=album_dir_fd)
                    if stat.S_ISREG(st.st_mode):
                        has_cover = True
                        break
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR):
                        raise AlbumManifestCorruptError(
                            f"Album '{album_id}' cover '{cov_name}' is unsafe or symlinked."
                        ) from exc
                    raise AlbumDurabilityUncertainError(
                        f"Cannot stat album cover '{cov_name}' for '{album_id}': {exc}"
                    ) from exc
            data["has_cover"] = has_cover

        status_val = data.get("status", WorkspaceStatus.DRAFT.value)
        data["is_finalized"] = bool(data.get("is_finalized", False)) or (status_val == WorkspaceStatus.COMPLETED.value)

        has_generated_entry = any(
            isinstance(t, dict) and t.get("active_audio_filename") is not None
            for t in (data.get("tracks") or [])
        )
        entries_fd: Optional[int] = None
        dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            try:
                entries_fd = os.open("entries", dir_flags, dir_fd=album_dir_fd)
            except FileNotFoundError as exc:
                if has_generated_entry:
                    raise AlbumDurabilityUncertainError(
                        f"Album '{album_id}' entries directory is missing for declared generated recordings."
                    ) from exc
                entries_fd = None
            except OSError as e:
                if e.errno in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR):
                    raise AlbumManifestCorruptError("Album 'entries' directory is invalid or a symlink.") from e
                raise AlbumDurabilityUncertainError(f"Cannot open album 'entries' directory: {e}") from e

            if entries_fd is not None:
                try:
                    ent_st = os.fstat(entries_fd)
                except OSError as e:
                    raise AlbumDurabilityUncertainError(f"Cannot stat album 'entries' directory: {e}") from e
                obs_entries_dir_id = (int(ent_st.st_dev), int(ent_st.st_ino))

            norm_tracks = self._normalize_album_entries_shared(album_id, data)
            for norm_item in norm_tracks:
                if not norm_item["is_placeholder"]:
                    gen_audio = norm_item.get("active_audio_filename")
                    gen_snap = norm_item.get("active_snapshot_filename")
                    if gen_audio is not None:
                        ehash = get_album_entry_dir_name(norm_item["id"])
                        try:
                            efd = os.open(ehash, dir_flags, dir_fd=entries_fd)
                        except FileNotFoundError as exc:
                            raise AlbumDurabilityUncertainError(
                                f"Entry directory missing for generated entry '{norm_item['id']}'."
                            ) from exc
                        except OSError as e:
                            if e.errno in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR):
                                raise AlbumManifestCorruptError(
                                    f"Entry directory '{ehash}' is invalid or a symlink."
                                ) from e
                            raise AlbumDurabilityUncertainError(
                                f"Cannot access entry directory '{ehash}': {e}"
                            ) from e
                        try:
                            try:
                                edir_st = os.fstat(efd)
                            except OSError as exc:
                                raise AlbumDurabilityUncertainError(
                                    f"Cannot stat entry directory '{ehash}': {exc}"
                                ) from exc
                            entry_obs: Dict[str, Any] = {
                                "entry_dir_id": (int(edir_st.st_dev), int(edir_st.st_ino))
                            }
                            for key_name, fname in (("audio", gen_audio), ("snapshot", gen_snap)):
                                try:
                                    fst = os.lstat(fname, dir_fd=efd)
                                except FileNotFoundError as exc:
                                    raise AlbumDurabilityUncertainError(
                                        f"Declared generated artifact '{fname}' missing for entry '{norm_item['id']}'."
                                    ) from exc
                                except OSError as exc:
                                    if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR):
                                        raise AlbumManifestCorruptError(
                                            f"Generated artifact '{fname}' is unsafe or symlinked."
                                        ) from exc
                                    raise AlbumDurabilityUncertainError(
                                        f"Cannot stat generated artifact '{fname}': {exc}"
                                    ) from exc
                                if not stat.S_ISREG(fst.st_mode):
                                    raise AlbumManifestCorruptError(
                                        f"Generated artifact '{fname}' is not a regular file."
                                    )
                                entry_obs[key_name] = (fname, self._seal_stat_tuple(fst))
                            obs_entries[norm_item["id"]] = entry_obs
                        finally:
                            os.close(efd)
                        norm_item["has_owned_audio"] = True
                        continue

                    has_owned = False
                    if entries_fd is not None and is_valid_project_id(norm_item["id"], expected_prefix="ale"):
                        ehash = get_album_entry_dir_name(norm_item["id"])
                        try:
                            efd = os.open(ehash, dir_flags, dir_fd=entries_fd)
                        except FileNotFoundError:
                            efd = None
                        except OSError as e:
                            raise AlbumManifestCorruptError(
                                f"Entry directory '{ehash}' is invalid or a symlink."
                            ) from e
                        if efd is not None:
                            try:
                                for ext in AUDIO_FORMATS:
                                    try:
                                        ast = os.lstat(f"audio{ext}", dir_fd=efd)
                                        if stat.S_ISREG(ast.st_mode):
                                            has_owned = True
                                            break
                                    except FileNotFoundError:
                                        continue
                            finally:
                                os.close(efd)
                    norm_item["has_owned_audio"] = has_owned

            data["tracks"] = norm_tracks
            data["track_ids"] = [t["track_id"] for t in norm_tracks if t.get("track_id")]
            if identities_out is not None:
                identities_out["entries_dir_id"] = obs_entries_dir_id
                identities_out["cover"] = obs_cover
                identities_out["entries"] = obs_entries
            return data
        finally:
            if entries_fd is not None:
                try:
                    os.close(entries_fd)
                except OSError:
                    pass

    def _read_album_from_dir_fd(
        self,
        album_id: str,
        album_dir_fd: int,
        *,
        protect_finalized: bool = False,
        raw_out: Optional[List[Dict[str, Any]]] = None,
        identities_out: Optional[Dict[str, Any]] = None,
    ) -> Optional[Tuple[AlbumMetadata, Dict[str, Any], os.stat_result]]:
        """Read, inspect, normalize, and validate album.json strictly through album_dir_fd."""
        try:
            mfd = os.open("album.json", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=album_dir_fd)
        except FileNotFoundError:
            return None
        except OSError as e:
            raise AlbumManifestCorruptError(
                f"Album manifest '{album_id}' is a symlink or unreadable; refusing operation to preserve existing data."
            ) from e

        mfd_owned = True
        try:
            manifest_st = os.fstat(mfd)
            if not stat.S_ISREG(manifest_st.st_mode):
                raise AlbumManifestCorruptError(f"Album manifest '{album_id}' is not a regular file.")
            f = os.fdopen(mfd, "r", encoding="utf-8")
            mfd_owned = False
            with f:
                raw_data = json.load(f)
        except AlbumManifestCorruptError:
            raise
        except Exception as e:
            logger.error(f"Failed to read/parse album manifest {album_id}: {e}")
            raise AlbumManifestCorruptError(
                f"Album manifest '{album_id}' is unreadable or invalid; refusing operation to preserve existing data."
            ) from e
        finally:
            if mfd_owned:
                try:
                    os.close(mfd)
                except OSError:
                    pass

        self._inspect_raw_album_manifest(raw_data, album_id, protect_finalized=protect_finalized)
        self._validate_raw_generation_declarations(raw_data, album_id)
        if raw_out is not None:
            raw_out.append(raw_data)

        if protect_finalized and (
            bool(raw_data.get("is_finalized", False))
            or raw_data.get("status") == WorkspaceStatus.COMPLETED.value
        ):
            try:
                norm_data = self._normalize_album_data_from_dir_fd(
                    album_dir_fd, copy.deepcopy(raw_data), identities_out=identities_out
                )
                return AlbumMetadata(**norm_data), norm_data, manifest_st
            except Exception as e:
                raise AlbumFinalizedError("Finalized albums are immutable and cannot be modified or deleted.") from e

        try:
            norm_data = self._normalize_album_data_from_dir_fd(
                album_dir_fd, copy.deepcopy(raw_data), identities_out=identities_out
            )
            return AlbumMetadata(**norm_data), norm_data, manifest_st
        except (AlbumManifestCorruptError, AlbumDurabilityUncertainError, AlbumFinalizedError):
            raise
        except Exception as e:
            logger.error(f"Failed to validate album manifest {album_id}: {e}")
            raise AlbumManifestCorruptError(
                f"Album manifest '{album_id}' failed validation; refusing operation to preserve existing data."
            ) from e

    def _read_album_unlocked(self, user_id: str, album_id: str, *, protect_finalized: bool = False) -> Optional[AlbumMetadata]:
        """Read and normalize album.json, failing closed with AlbumManifestCorruptError if invalid."""
        try:
            album_dir_fd = self._open_album_dir_fd_nofollow(user_id, album_id)
        except ValueError:
            return None
        if album_dir_fd is None:
            return None
        try:
            loaded = self._read_album_from_dir_fd(album_id, album_dir_fd, protect_finalized=protect_finalized)
            return loaded[0] if loaded is not None else None
        finally:
            try:
                os.close(album_dir_fd)
            except OSError:
                pass

    @staticmethod
    def _is_album_finalized(album: AlbumMetadata) -> bool:
        return bool(album.is_finalized or album.status == WorkspaceStatus.COMPLETED)

    def _build_take_snapshot(
        self,
        user_id: str,
        entry_id: str,
        track: TrackMetadata,
        take: TakeMetadata,
        audio_path: Optional[Path] = None,
        copied_at: Optional[float] = None,
        *,
        audio_fd: Optional[int] = None,
        audio_ext: Optional[str] = None,
    ) -> AlbumEntrySnapshot:
        """Build a backend-authoritative provenance and metadata snapshot from a retained audio fd or path."""
        embedded_meta: Dict[str, Any] = {}
        embedded_lyrics: Optional[str] = None
        duration = 0.0

        if audio_fd is not None:
            ast = os.fstat(audio_fd)
            size_bytes = ast.st_size
            os.lseek(audio_fd, 0, os.SEEK_SET)
            h = hashlib.sha256()
            while True:
                chunk = os.read(audio_fd, 65536)
                if not chunk:
                    break
                h.update(chunk)
            sha256 = h.hexdigest()
            fmt_ext = (audio_ext or f".{take.format}").lower()
            try:
                from backend.metadata_engine import extract_metadata_from_fd
                parsed = extract_metadata_from_fd(audio_fd, fmt_ext)
                duration = float(parsed.get("duration") or 0.0)
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
                logger.debug(f"Metadata snapshot extraction skipped for descriptor: {e}")
            if duration <= 0.0:
                duration = float(take.duration_seconds or 0.0)
            fmt_str = fmt_ext.lstrip(".").lower() or take.format
        else:
            assert audio_path is not None
            sha256 = _compute_file_sha256(audio_path)
            size_bytes = audio_path.stat().st_size
            duration = _probe_audio_duration(audio_path)
            if duration <= 0.0:
                duration = float(take.duration_seconds or 0.0)
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
            fmt_str = audio_path.suffix.lstrip(".").lower() or take.format

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
            format=fmt_str,
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
        *,
        album_dir_fd: int,
        generation: Optional[int] = None,
        staged_refs_out: Optional[List[ArtifactRefRecord]] = None,
    ) -> Tuple[AlbumEntrySnapshot, Callable[[], None], Callable[[], None], Callable[[bool], None]]:
        """Stage and promote an independent audio copy and snapshot strictly via retained dir_fds."""
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

        self.ensure_quota_available(user_id, source_size)

        entries_fd, created_entries_dir, entries_dir_id = self._open_or_create_subdir_fd_tracked(album_dir_fd, "entries")
        try:
            entry_dir_hash = get_album_entry_dir_name(entry_id)
            entry_dir_fd, created_entry_dir, entry_dir_id = self._open_or_create_subdir_fd_tracked(
                entries_fd, entry_dir_hash
            )
        except BaseException:
            if created_entries_dir:
                try:
                    cur_est = os.fstat(entries_fd)
                    if (cur_est.st_dev, cur_est.st_ino) == entries_dir_id and len(os.listdir(entries_fd)) == 0:
                        os.rmdir("entries", dir_fd=album_dir_fd)
                except OSError:
                    pass
            raise
        finally:
            try:
                os.close(entries_fd)
            except OSError:
                pass

        fd_closed = False

        def _close_entry_fd() -> None:
            nonlocal fd_closed
            if not fd_closed:
                fd_closed = True
                try:
                    os.close(entry_dir_fd)
                except OSError:
                    pass

        def _cleanup_created_dirs() -> None:
            if not created_entry_dir and not created_entries_dir:
                return
            dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                cur_entries_fd = os.open("entries", dir_flags, dir_fd=album_dir_fd)
            except OSError:
                return
            try:
                cur_est = os.fstat(cur_entries_fd)
                if (cur_est.st_dev, cur_est.st_ino) != entries_dir_id:
                    return
                if created_entry_dir:
                    try:
                        sub_st = os.lstat(entry_dir_hash, dir_fd=cur_entries_fd)
                        if stat.S_ISDIR(sub_st.st_mode) and (sub_st.st_dev, sub_st.st_ino) == entry_dir_id:
                            sub_fd = os.open(entry_dir_hash, dir_flags, dir_fd=cur_entries_fd)
                            try:
                                is_empty = (len(os.listdir(sub_fd)) == 0)
                            finally:
                                os.close(sub_fd)
                            if is_empty:
                                os.rmdir(entry_dir_hash, dir_fd=cur_entries_fd)
                    except OSError:
                        pass
                entries_empty = (len(os.listdir(cur_entries_fd)) == 0)
            finally:
                try:
                    os.close(cur_entries_fd)
                except OSError:
                    pass
            if created_entries_dir and entries_empty:
                try:
                    os.rmdir("entries", dir_fd=album_dir_fd)
                except OSError:
                    pass

        try:
            existing_items = os.listdir(entry_dir_fd)
            if existing_items:
                raise AlbumReplacementDisabledError(
                    "Destination album entry already contains existing audio, snapshots, or recovery artifacts."
                )

            stage_token = uuid.uuid4().hex[:12]
            staging_audio_name = f".staging_{stage_token}_audio{ext}"
            staging_snap_name = f".staging_{stage_token}_snapshot.json"
            if generation is not None:
                dest_audio_name = f"audio_g{generation}{ext}"
                dest_snap_name = f"snapshot_g{generation}.json"
            else:
                dest_audio_name = f"audio{ext}"
                dest_snap_name = "snapshot.json"

            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            audio_fd = os.open(staging_audio_name, flags, 0o600, dir_fd=entry_dir_fd)
            staging_audio_id: Optional[Tuple[int, int]] = None
            staging_snap_id: Optional[Tuple[int, int]] = None
            promoted_files: List[Tuple[str, int, int]] = []

            try:
                try:
                    ast_init = os.fstat(audio_fd)
                    staging_audio_id = (ast_init.st_dev, ast_init.st_ino)
                    dst = os.fdopen(audio_fd, "wb")
                except BaseException:
                    try:
                        os.close(audio_fd)
                    except OSError:
                        pass
                    raise

                with dst:
                    with open(source_audio, "rb") as src:
                        shutil.copyfileobj(src, dst, length=64 * 1024)
                    dst.flush()
                    os.fsync(dst.fileno())
                    os.fchmod(dst.fileno(), 0o600)
                    ast = os.fstat(dst.fileno())
                    staging_audio_id = (ast.st_dev, ast.st_ino)
                    if ast.st_size != source_size:
                        raise ProjectStorageError("Staged album audio copy failed byte-count verification.")

                ro_fd = os.open(staging_audio_name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=entry_dir_fd)
                try:
                    ro_st = os.fstat(ro_fd)
                    if (ro_st.st_dev, ro_st.st_ino) != staging_audio_id:
                        raise ProjectStorageError("Staged audio file identity changed before snapshot construction.")
                    snapshot = self._build_take_snapshot(
                        user_id, entry_id, track, take, audio_fd=ro_fd, audio_ext=ext
                    )
                finally:
                    try:
                        os.close(ro_fd)
                    except OSError:
                        pass

                snap_fd = os.open(staging_snap_name, flags, 0o600, dir_fd=entry_dir_fd)
                try:
                    sst_init = os.fstat(snap_fd)
                    staging_snap_id = (sst_init.st_dev, sst_init.st_ino)
                    sf = os.fdopen(snap_fd, "wb")
                except BaseException:
                    try:
                        os.close(snap_fd)
                    except OSError:
                        pass
                    raise

                import io
                sbuf = io.StringIO()
                json.dump(snapshot.model_dump(), sbuf, indent=2)
                snap_bytes = sbuf.getvalue().encode("utf-8")
                with sf:
                    sf.write(snap_bytes)
                    sf.flush()
                    os.fsync(sf.fileno())
                    os.fchmod(sf.fileno(), 0o600)
                    sst = os.fstat(sf.fileno())
                    staging_snap_id = (sst.st_dev, sst.st_ino)

                if staged_refs_out is not None and generation is not None:
                    staged_refs_out.extend([
                        ArtifactRefRecord(
                            rel_path=f"entries/{entry_dir_hash}/{dest_audio_name}",
                            role=ArtifactRole.ENTRY_AUDIO,
                            sha256=snapshot.audio_sha256,
                            size_bytes=snapshot.size_bytes,
                            entry_id=entry_id,
                            generation=generation,
                        ),
                        ArtifactRefRecord(
                            rel_path=f"entries/{entry_dir_hash}/{dest_snap_name}",
                            role=ArtifactRole.ENTRY_SNAPSHOT,
                            sha256=hashlib.sha256(snap_bytes).hexdigest(),
                            size_bytes=len(snap_bytes),
                            entry_id=entry_id,
                            generation=generation,
                        ),
                    ])
            except Exception:
                for sname, expected_id in ((staging_audio_name, staging_audio_id), (staging_snap_name, staging_snap_id)):
                    if expected_id is not None:
                        try:
                            cur_st = os.lstat(sname, dir_fd=entry_dir_fd)
                            if stat.S_ISREG(cur_st.st_mode) and (cur_st.st_dev, cur_st.st_ino) == expected_id:
                                os.unlink(sname, dir_fd=entry_dir_fd)
                        except Exception:
                            pass
                _close_entry_fd()
                _cleanup_created_dirs()
                raise

            def _promote_one(src_name: str, dst_name: str, expected_id: Tuple[int, int]) -> None:
                cur_st = os.lstat(src_name, dir_fd=entry_dir_fd)
                if not stat.S_ISREG(cur_st.st_mode) or (cur_st.st_dev, cur_st.st_ino) != expected_id:
                    raise ProjectStorageError(f"Staged file '{src_name}' changed identity before promotion.")
                # os.link atomically fails with FileExistsError if dst_name already exists (never overwrites)
                os.link(src_name, dst_name, src_dir_fd=entry_dir_fd, dst_dir_fd=entry_dir_fd)
                promoted_files.append((dst_name, expected_id[0], expected_id[1]))
                os.unlink(src_name, dir_fd=entry_dir_fd)

            def commit_staged() -> None:
                if fd_closed:
                    raise ProjectStorageError("Entry directory descriptor closed before promotion.")
                _promote_one(staging_audio_name, dest_audio_name, staging_audio_id)
                _promote_one(staging_snap_name, dest_snap_name, staging_snap_id)
                try:
                    _fsync_dir_fd(entry_dir_fd)
                except OSError as exc:
                    raise AlbumDurabilityUncertainError(
                        f"Entry directory synchronization failed after artifact promotion for '{entry_id}': {exc}"
                    ) from exc
                dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
                try:
                    cur_entries_fd = os.open("entries", dir_flags, dir_fd=album_dir_fd)
                except OSError as exc:
                    raise AlbumDurabilityUncertainError(
                        f"Cannot open album 'entries' directory for synchronization after artifact promotion for '{entry_id}': {exc}"
                    ) from exc
                try:
                    cur_est = os.fstat(cur_entries_fd)
                    if (cur_est.st_dev, cur_est.st_ino) != entries_dir_id:
                        raise AlbumDurabilityUncertainError(
                            f"Album 'entries' directory identity changed during artifact promotion for '{entry_id}'."
                        )
                    _fsync_dir_fd(cur_entries_fd)
                except AlbumDurabilityUncertainError:
                    raise
                except OSError as exc:
                    raise AlbumDurabilityUncertainError(
                        f"Album 'entries' directory synchronization failed after artifact promotion for '{entry_id}': {exc}"
                    ) from exc
                finally:
                    try:
                        os.close(cur_entries_fd)
                    except OSError:
                        pass

            def cleanup_backups() -> None:
                _close_entry_fd()

            def rollback_staged(allow_promoted_unlink: bool = True) -> None:
                try:
                    if not fd_closed:
                        for sname, expected_id in ((staging_audio_name, staging_audio_id), (staging_snap_name, staging_snap_id)):
                            if expected_id is not None:
                                try:
                                    cur_st = os.lstat(sname, dir_fd=entry_dir_fd)
                                    if stat.S_ISREG(cur_st.st_mode) and (cur_st.st_dev, cur_st.st_ino) == expected_id:
                                        os.unlink(sname, dir_fd=entry_dir_fd)
                                except FileNotFoundError:
                                    pass
                                except Exception:
                                    pass
                        if allow_promoted_unlink:
                            for fname, exp_dev, exp_ino in reversed(promoted_files):
                                try:
                                    cur_st = os.lstat(fname, dir_fd=entry_dir_fd)
                                    if stat.S_ISREG(cur_st.st_mode) and (cur_st.st_dev, cur_st.st_ino) == (exp_dev, exp_ino):
                                        os.unlink(fname, dir_fd=entry_dir_fd)
                                except FileNotFoundError:
                                    pass
                                except Exception:
                                    pass
                finally:
                    _close_entry_fd()
                    if allow_promoted_unlink:
                        _cleanup_created_dirs()

            return snapshot, commit_staged, cleanup_backups, rollback_staged
        except BaseException:
            _close_entry_fd()
            _cleanup_created_dirs()
            raise

    def _normalize_album_data(self, user_id: str, album_dir: Path, data: Dict[str, Any]) -> Dict[str, Any]:
        """Non-destructively normalize legacy and current album manifests when reading from disk."""
        album_id = data.get("id", "")
        active_cov = data.get("active_cover_filename")
        if active_cov is not None:
            cov_path = album_dir / active_cov
            if not cov_path.exists():
                raise AlbumDurabilityUncertainError(
                    f"Album '{album_id}' declared active cover '{active_cov}' is missing on disk."
                )
            if cov_path.is_symlink() or not cov_path.is_file():
                raise AlbumManifestCorruptError(
                    f"Album '{album_id}' active cover '{active_cov}' is unsafe or not a regular file."
                )
            data["has_cover"] = True
        else:
            data["has_cover"] = any(
                (album_dir / f"cover{ext}").is_file() and not (album_dir / f"cover{ext}").is_symlink()
                for ext in (".jpg", ".jpeg", ".png")
            )
        status_val = data.get("status", WorkspaceStatus.DRAFT.value)
        data["is_finalized"] = bool(data.get("is_finalized", False)) or (status_val == WorkspaceStatus.COMPLETED.value)

        norm_tracks = self._normalize_album_entries_shared(album_id, data)
        for entry in norm_tracks:
            if not entry["is_placeholder"]:
                if entry.get("active_audio_filename") is not None:
                    entry_dir = self._get_album_entry_dir_path(user_id, album_id, entry["id"])
                    a_path = entry_dir / entry["active_audio_filename"]
                    s_path = entry_dir / entry["active_snapshot_filename"]
                    for p in (a_path, s_path):
                        if not p.exists():
                            raise AlbumDurabilityUncertainError(
                                f"Declared generated artifact '{p.name}' missing for entry '{entry['id']}'."
                            )
                        if p.is_symlink() or not p.is_file():
                            raise AlbumManifestCorruptError(
                                f"Generated artifact '{p.name}' is unsafe or not a regular file."
                            )
                    entry["has_owned_audio"] = True
                else:
                    owned_audio = self.get_album_entry_audio_path(user_id, album_id, entry["id"])
                    entry["has_owned_audio"] = bool(owned_audio is not None and entry.get("snapshot") is not None)

        data["tracks"] = norm_tracks
        data["track_ids"] = [t["track_id"] for t in norm_tracks if t.get("track_id")]
        return data

    @staticmethod
    def _rmdir_if_created_empty(parent_fd: int, name: str, expected_id: Optional[Tuple[int, int]]) -> None:
        """Remove a newly created, empty, identity-verified subdirectory relative to parent_fd."""
        if expected_id is None:
            return
        dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            sub_st = os.lstat(name, dir_fd=parent_fd)
            if not stat.S_ISDIR(sub_st.st_mode) or (sub_st.st_dev, sub_st.st_ino) != expected_id:
                return
            sub_fd = os.open(name, dir_flags, dir_fd=parent_fd)
            try:
                is_empty = (len(os.listdir(sub_fd)) == 0)
            finally:
                os.close(sub_fd)
            if is_empty:
                os.rmdir(name, dir_fd=parent_fd)
        except OSError:
            pass

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
        now = time.time()
        try:
            album_meta = AlbumMetadata(
                id=album_id,
                title=title.strip() if isinstance(title, str) else title,
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
        except ValidationError as e:
            raise ProjectValidationError(f"Invalid album metadata: {e}") from e

        self._get_albums_root(user_id)
        album_hash = get_album_dir_name(album_id)
        with self._album_lock(user_id, album_id):
            albums_root_fd = self._open_albums_root_fd_nofollow(user_id)
            if albums_root_fd is None:
                raise ProjectStorageError("Albums storage root could not be opened.")
            album_dir_fd: Optional[int] = None
            created_album_dir = False
            album_dir_id: Optional[Tuple[int, int]] = None
            created_entries_dir = False
            entries_dir_id: Optional[Tuple[int, int]] = None
            published_or_uncertain = False
            try:
                album_dir_fd, created_album_dir, album_dir_id = self._open_or_create_subdir_fd_tracked(
                    albums_root_fd, album_hash
                )
                entries_fd, created_entries_dir, entries_dir_id = self._open_or_create_subdir_fd_tracked(
                    album_dir_fd, "entries"
                )
                try:
                    os.close(entries_fd)
                except OSError:
                    pass

                try:
                    _publish_manifest_atomic_in_dir(album_dir_fd, "album.json", album_meta.model_dump())
                    published_or_uncertain = True
                except ManifestPublishError as e:
                    if e.publish_state != ManifestPublishState.NOT_PUBLISHED:
                        published_or_uncertain = True
                    raise
            except BaseException:
                if not published_or_uncertain:
                    if album_dir_fd is not None and created_entries_dir:
                        self._rmdir_if_created_empty(album_dir_fd, "entries", entries_dir_id)
                    if created_album_dir:
                        self._rmdir_if_created_empty(albums_root_fd, album_hash, album_dir_id)
                raise
            finally:
                if album_dir_fd is not None:
                    try:
                        os.close(album_dir_fd)
                    except OSError:
                        pass
                try:
                    os.close(albums_root_fd)
                except OSError:
                    pass

        return album_meta

    def get_album(self, user_id: str, album_id: str) -> Optional[AlbumMetadata]:
        """Retrieve album metadata, failing closed if an existing manifest is corrupt."""
        return self._read_album_unlocked(user_id, album_id)

    def list_albums(self, user_id: str, status: Optional[WorkspaceStatus] = None) -> List[AlbumMetadata]:
        """List all album workspaces for a user using descriptor-safe reads (side-effect-free)."""
        import errno

        results: List[AlbumMetadata] = []
        albums_root_fd = self._open_albums_root_fd_nofollow(user_id)
        if albums_root_fd is None:
            return results

        dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            try:
                names = os.listdir(albums_root_fd)
            except OSError as exc:
                raise AlbumDurabilityUncertainError(f"Cannot list albums root directory: {exc}") from exc

            for dname in names:
                if dname.startswith("."):
                    continue
                try:
                    dst = os.lstat(dname, dir_fd=albums_root_fd)
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR):
                        raise AlbumManifestCorruptError(f"Unsafe album directory entry '{dname}': {exc}") from exc
                    raise AlbumDurabilityUncertainError(f"Cannot stat album directory '{dname}': {exc}") from exc

                if stat.S_ISLNK(dst.st_mode):
                    raise AlbumManifestCorruptError(f"Album directory '{dname}' must not be a symlink.")
                if not stat.S_ISDIR(dst.st_mode):
                    continue

                album_dir_fd: Optional[int] = None
                try:
                    try:
                        album_dir_fd = os.open(dname, dir_flags, dir_fd=albums_root_fd)
                    except FileNotFoundError:
                        continue
                    except OSError as exc:
                        if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR):
                            raise AlbumManifestCorruptError(f"Unsafe album directory '{dname}': {exc}") from exc
                        raise AlbumDurabilityUncertainError(f"Cannot open album directory '{dname}': {exc}") from exc

                    raw_holder: List[Dict[str, Any]] = []
                    loaded = self._read_album_from_dir_fd(dname, album_dir_fd, protect_finalized=False, raw_out=raw_holder)
                    if loaded is None:
                        continue
                    meta, _norm, _st = loaded
                    if not is_valid_project_id(meta.id, expected_prefix="alb") or get_album_dir_name(meta.id) != dname:
                        raise AlbumManifestCorruptError(
                            f"Album directory '{dname}' manifest id '{meta.id}' mismatch."
                        )
                    if status is None or meta.status == status:
                        results.append(meta)
                finally:
                    if album_dir_fd is not None:
                        try:
                            os.close(album_dir_fd)
                        except OSError:
                            pass
        finally:
            try:
                os.close(albums_root_fd)
            except OSError:
                pass

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
        exact_matches = [c for c in unmatched_existing if c.track_id == track_id and c.take_id == take_id]
        if len(exact_matches) == 1:
            matched = exact_matches[0]
            unmatched_existing.remove(matched)
            return matched
        if len(exact_matches) > 1:
            raise ProjectValidationError(
                f"Ambiguous tracklist entry for track '{track_id}' and take '{take_id}'; provide explicit album entry IDs."
            )

        track_matches = [c for c in unmatched_existing if c.track_id == track_id]
        if len(track_matches) == 1:
            matched = track_matches[0]
            unmatched_existing.remove(matched)
            return matched
        if len(track_matches) > 1:
            raise ProjectValidationError(
                f"Ambiguous tracklist entry for track '{track_id}'; provide explicit album entry IDs."
            )

        return None

    @staticmethod
    def _validate_expected_revision(expected_revision: Any) -> int:
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise ProjectValidationError("A valid positive integer 'expected_revision' precondition is required.")
        return expected_revision

    @staticmethod
    def _validate_idempotency_key(idempotency_key: Optional[str]) -> Optional[str]:
        if idempotency_key is None:
            return None
        if not isinstance(idempotency_key, str) or not (1 <= len(idempotency_key) <= 128):
            raise ProjectValidationError("Idempotency-Key must be a non-empty token (1..128 characters).")
        try:
            return _validate_safe_token(idempotency_key)
        except ValueError as exc:
            raise ProjectValidationError(f"Invalid Idempotency-Key: {exc}") from exc

    @staticmethod
    def _compute_update_album_payload_sha256(
        op: str,
        expected_revision: int,
        updates: Dict[str, Any],
    ) -> str:
        """Compute deterministic canonical SHA-256 for an enrolled update_album request."""
        canonical_params: Dict[str, Any] = {}
        for k in sorted(updates.keys()):
            if k == "expected_revision":
                continue
            v = updates[k]
            if v is None:
                continue
            if isinstance(v, WorkspaceStatus):
                canonical_params[k] = v.value
            elif k == "tracks" and isinstance(v, list):
                norm_items = []
                for item in v:
                    if isinstance(item, dict):
                        norm_items.append(dict(item))
                    elif hasattr(item, "model_dump"):
                        norm_items.append(item.model_dump(mode="json"))
                    else:
                        norm_items.append(item)
                canonical_params[k] = norm_items
            else:
                canonical_params[k] = v
        payload = {
            "op": op,
            "expected_revision": int(expected_revision),
            "params": canonical_params,
        }
        canonical_bytes = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical_bytes).hexdigest()

    def update_album(
        self,
        user_id: str,
        album_id: str,
        updates: Dict[str, Any],
        expected_revision: Optional[int] = None,
        change_summary: Optional[str] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> Optional[AlbumMetadata]:
        """Update album metadata or sequenced tracklist under stable cross-process album locking."""
        res = self.update_album_with_replay_status(
            user_id,
            album_id,
            updates,
            expected_revision=expected_revision,
            change_summary=change_summary,
            idempotency_key=idempotency_key,
        )
        return res[0] if res is not None else None

    def update_album_with_replay_status(
        self,
        user_id: str,
        album_id: str,
        updates: Dict[str, Any],
        expected_revision: Optional[int] = None,
        change_summary: Optional[str] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> Optional[Tuple[AlbumMetadata, bool]]:
        """Enrolled v2 metadata/reorder writer returning (AlbumMetadata, is_idempotent_replay)."""
        payload = dict(updates)
        rev = expected_revision if expected_revision is not None else payload.pop("expected_revision", None)
        rev = self._validate_expected_revision(rev)
        clean_key = self._validate_idempotency_key(idempotency_key)
        try:
            self._get_album_dir_path(user_id, album_id)
        except ValueError:
            return None

        op = "update_album"
        payload_sha256 = self._compute_update_album_payload_sha256(op, rev, payload)

        with self._album_lock(user_id, album_id):
            album_dir_fd = self._open_album_dir_fd_nofollow(user_id, album_id)
            if album_dir_fd is None:
                return None
            try:
                raw_holder: List[Dict[str, Any]] = []
                loaded = self._read_album_from_dir_fd(
                    album_id, album_dir_fd, protect_finalized=True, raw_out=raw_holder
                )
                if not loaded:
                    return None
                album, norm_data, manifest_st = loaded
                raw_identity = (manifest_st.st_dev, manifest_st.st_ino)
                evidence: Dict[str, Any] = {}
                _, journal, consistency = self._validate_album_evidence_phase_a_locked(
                    album_id,
                    album_dir_fd,
                    raw_album_data=raw_holder[0],
                    raw_album_identity=raw_identity,
                    evidence_out=evidence,
                )

                # Exact terminal idempotency replay runs between Phase A and Phase B
                replay = self._resolve_v2_idempotency_replay_locked(
                    album_id=album_id,
                    album_dir_fd=album_dir_fd,
                    current_album=album,
                    evidence=evidence,
                    idempotency_key=clean_key,
                    op=op,
                    expected_revision=rev,
                    payload_sha256=payload_sha256,
                )
                if replay is not None:
                    return replay

                self._check_album_mutation_eligibility_phase_b_locked(
                    album_id, journal, consistency, is_enrolled=True
                )

                if self._is_album_finalized(album):
                    raise AlbumFinalizedError("Finalized albums are immutable and cannot be modified.")
                if int(rev) != int(album.revision):
                    raise AlbumRevisionConflictError(
                        f"Album was modified concurrently (expected revision {rev}, current revision {album.revision}). Reload and try again."
                    )
                if payload.get("status") in (WorkspaceStatus.COMPLETED, WorkspaceStatus.COMPLETED.value):
                    raise ProjectValidationError("Albums must be finalized via the dedicated finalization endpoint.")

                target_data, changed = self._build_metadata_reorder_v2_candidate(
                    user_id=user_id,
                    album_id=album_id,
                    album=album,
                    raw_data=raw_holder[0],
                    norm_data=norm_data,
                    updates=payload,
                    change_summary=change_summary,
                )
                if not changed:
                    # No-op: do not create journal or consume unused idempotency key
                    return album, False

                published = self._execute_v2_album_tx_locked(
                    album_id=album_id,
                    album_dir_fd=album_dir_fd,
                    evidence=evidence,
                    target_data=target_data,
                    op=op,
                    expected_revision=rev,
                    payload_sha256=payload_sha256,
                    idempotency_key=clean_key,
                )
                return published, False
            finally:
                try:
                    os.close(album_dir_fd)
                except OSError:
                    pass

    def _resolve_v2_idempotency_replay_locked(
        self,
        *,
        album_id: str,
        album_dir_fd: int,
        current_album: AlbumMetadata,
        evidence: Dict[str, Any],
        idempotency_key: Optional[str],
        op: str,
        expected_revision: int,
        payload_sha256: str,
    ) -> Optional[Tuple[AlbumMetadata, bool]]:
        """Resolve exact terminal idempotency replay between Phase A and Phase B."""
        journal: Optional[AlbumJournal] = evidence.get("journal")
        if idempotency_key is None or journal is None:
            return None

        if journal.active_tx is not None and journal.active_tx.idempotency_key == idempotency_key:
            tx = journal.active_tx
            if (tx.op, tx.expected_revision, tx.payload_sha256) != (op, expected_revision, payload_sha256):
                raise AlbumIdempotencyConflictError(
                    f"Idempotency-Key '{idempotency_key}' conflicts with active transaction '{tx.tx_id}'."
                )
            raise AlbumDurabilityUncertainError(
                f"Transaction '{tx.tx_id}' for Idempotency-Key '{idempotency_key}' is unresolved; run durability reconciliation before retrying."
            )

        matched_rcp: Optional[TerminalTxReceipt] = None
        for rcp in journal.tx_receipts:
            if rcp.idempotency_key == idempotency_key:
                matched_rcp = rcp
                break

        if matched_rcp is None:
            for rec in journal.idempotency_records:
                if rec.key == idempotency_key:
                    if (rec.op, rec.expected_revision, rec.payload_sha256) != (op, expected_revision, payload_sha256):
                        raise AlbumIdempotencyConflictError(
                            f"Idempotency-Key '{idempotency_key}' conflicts with existing idempotency record."
                        )
                    raise AlbumDurabilityUncertainError(
                        f"Idempotency-Key '{idempotency_key}' lacks a synchronized terminal receipt."
                    )
            return None

        if (matched_rcp.op, matched_rcp.expected_revision, matched_rcp.payload_sha256) != (
            op,
            expected_revision,
            payload_sha256,
        ):
            raise AlbumIdempotencyConflictError(
                f"Idempotency-Key '{idempotency_key}' conflicts with recorded transaction '{matched_rcp.tx_id}'."
            )

        # Synchronize validated journal (AlbumManifestCorruptError & AlbumDurabilityUncertainError propagate unmodified)
        self._sync_terminal_journal_in_dir(
            album_dir_fd,
            expected_journal_sha256=evidence["journal_sha256"],
            expected_identity=evidence["journal_identity"],
        )

        if matched_rcp.outcome == JournalTxState.ABORTED:
            raise AlbumTransactionAbortedError(
                f"Transaction '{matched_rcp.tx_id}' for Idempotency-Key '{idempotency_key}' previously aborted."
            )

        album_data = evidence["album_data"]
        if (
            matched_rcp.tx_id == album_data.get("last_tx_id")
            and album_data.get("revision") == matched_rcp.result_revision
        ):
            for ref in matched_rcp.new_refs:
                self._verify_and_sync_live_artifact_in_dir(album_dir_fd, ref)
            self._read_json_file_verified_in_dir(
                album_dir_fd,
                "album.json",
                sync_file=True,
                required=True,
                expected_identity=evidence["album_identity"],
                expected_sha256=evidence["album_sha256"],
            )

        return current_album, True

    def _build_metadata_reorder_v2_candidate(
        self,
        *,
        user_id: str,
        album_id: str,
        album: AlbumMetadata,
        raw_data: Dict[str, Any],
        norm_data: Dict[str, Any],
        updates: Dict[str, Any],
        change_summary: Optional[str],
    ) -> Tuple[Dict[str, Any], bool]:
        """Build and validate a metadata/reorder target manifest without legacy staging/callbacks."""
        payload = dict(updates)
        payload.pop("expected_revision", None)
        summary_text = payload.pop("change_summary", None) or change_summary

        target_data = copy.deepcopy(raw_data)
        changed_fields: List[str] = []

        immutable_fields = (
            "id",
            "created_at",
            "has_cover",
            "cover_generation",
            "active_cover_filename",
            "revisions",
            "revision",
            "updated_at",
            "tracks",
            "track_ids",
            "is_finalized",
            "finalized_at",
            "last_tx_id",
            "tx_history",
            "is_deleted",
            "deleted_at",
            "delete_tx_id",
        )
        for k, v in payload.items():
            if v is not None and k not in immutable_fields:
                curr_val = norm_data.get(k)
                norm_v = v.value if isinstance(v, WorkspaceStatus) else v
                if curr_val != norm_v:
                    changed_fields.append(k)
                    target_data[k] = norm_v

        raw_list: Optional[List[Dict[str, Any]]] = None
        if "tracks" in payload and payload["tracks"] is not None:
            raw_list = []
            for item in payload["tracks"]:
                if isinstance(item, dict):
                    raw_list.append(dict(item))
                elif hasattr(item, "model_dump"):
                    raw_list.append(item.model_dump())
                else:
                    raise ProjectValidationError("Invalid track entry item in update_album.")
        elif "track_ids" in payload and payload["track_ids"] is not None:
            from collections import Counter
            legacy_ids = list(payload["track_ids"])
            existing_by_tid: Dict[str, List[AlbumTrackEntry]] = {}
            for existing_entry in album.tracks:
                existing_by_tid.setdefault(existing_entry.track_id, []).append(existing_entry)
            if Counter(e.track_id for e in album.tracks) != Counter(legacy_ids):
                raise ProjectValidationError(
                    "Bulk legacy track_ids updates must preserve the exact existing track set; use add_album_entry or remove_album_entry."
                )
            raw_list = []
            for i, tid in enumerate(legacy_ids):
                preserved = existing_by_tid[tid].pop(0).model_dump()
                preserved["track_number"] = i + 1
                raw_list.append(preserved)

        if raw_list is not None:
            existing_by_id = {t.id: t for t in album.tracks if t.id}
            unmatched_existing = list(album.tracks)
            explicit_ids = {
                item.get("id") for item in raw_list
                if item.get("id") and item.get("id") in existing_by_id
            }
            if explicit_ids:
                unmatched_existing = [e for e in unmatched_existing if e.id not in explicit_ids]
            seen_entry_ids: set[str] = set()
            reordered_tracks: List[Dict[str, Any]] = []

            for i, item in enumerate(raw_list):
                track_id = item.get("track_id")
                has_explicit_take = "take_id" in item
                req_take_id = item.get("take_id")
                disc_num = item.get("disc_number", 1)
                custom_title = item.get("custom_title")
                req_id = item.get("id")

                if req_id is not None and req_id not in existing_by_id:
                    raise ProjectValidationError(f"Album entry '{req_id}' not found.")

                matched = self._resolve_existing_entry_for_item(
                    item, existing_by_id, unmatched_existing
                )
                if matched is None:
                    raise ProjectValidationError(
                        "Adding new entries via bulk album update is not allowed; use add_album_entry."
                    )
                if not matched.id:
                    raise ProjectValidationError("Matched album entry is missing an entry identifier.")
                entry_id = matched.id
                if entry_id in seen_entry_ids:
                    raise ProjectValidationError(f"Duplicate album entry ID '{entry_id}' in tracklist.")
                seen_entry_ids.add(entry_id)

                if track_id is not None and track_id != matched.track_id:
                    raise AlbumReplacementDisabledError(
                        "Changing an existing entry's source track via metadata/reorder update is not allowed."
                    )
                if has_explicit_take and req_take_id != matched.take_id:
                    if matched.take_id is not None or matched.has_owned_audio or matched.snapshot is not None:
                        raise AlbumReplacementDisabledError(
                            "Replacing existing album-owned audio is temporarily disabled while audio-replacement safety upgrades are in progress."
                        )
                    raise ProjectValidationError(
                        "Assigning a take via bulk album update is not allowed; use update_album_entry."
                    )

                entry_dict = matched.model_dump()
                entry_dict["track_number"] = i + 1
                entry_dict["disc_number"] = disc_num
                if "custom_title" in item:
                    entry_dict["custom_title"] = custom_title
                reordered_tracks.append(entry_dict)

            for old_entry in album.tracks:
                if old_entry.id and old_entry.id not in seen_entry_ids:
                    if (
                        old_entry.has_owned_audio
                        or old_entry.take_id is not None
                        or old_entry.snapshot is not None
                        or self._entry_has_filesystem_artifacts(user_id, album_id, old_entry.id)
                    ):
                        raise AlbumReplacementDisabledError(
                            "Removing recorded entries is temporarily disabled in Phase 1A."
                        )
                    raise ProjectValidationError(
                        "Removing entries via bulk album update is not allowed; use remove_album_entry."
                    )

            curr_norm_tracks = [t.model_dump() for t in album.tracks]
            if curr_norm_tracks != reordered_tracks:
                changed_fields.append("tracks")
                target_data["tracks"] = reordered_tracks
                target_data["track_ids"] = [t["track_id"] for t in reordered_tracks]

        if not changed_fields:
            return target_data, False

        new_rev = int(norm_data.get("revision", 1)) + 1
        target_data["revision"] = new_rev
        summary = summary_text if summary_text else f"Updated {', '.join(changed_fields)}"
        revs = list(target_data.get("revisions") or [])
        revs.append({
            "revision": new_rev,
            "timestamp": time.time(),
            "change_summary": summary,
            "changed_fields": changed_fields,
        })
        target_data["revisions"] = revs
        target_data["updated_at"] = time.time()

        try:
            AlbumMetadata(**target_data)
        except Exception as exc:
            raise ProjectValidationError(f"Invalid album manifest candidate: {exc}") from exc

        return target_data, True

    def _execute_v2_album_tx_locked(
        self,
        *,
        album_id: str,
        album_dir_fd: int,
        evidence: Dict[str, Any],
        target_data: Dict[str, Any],
        op: str,
        expected_revision: int,
        payload_sha256: str,
        idempotency_key: Optional[str],
        pre_manifest_publish_check: Optional[Callable[[bool], None]] = None,
        new_refs: Optional[List[ArtifactRefRecord]] = None,
        superseded_refs: Optional[List[ArtifactRefRecord]] = None,
    ) -> AlbumMetadata:
        """Execute PREPARED -> PUBLISH_INTENT -> manifest -> COMMITTED receipt under _album_lock."""
        tx_id = f"tx_{secrets.token_hex(12)}"
        target_revision = int(target_data["revision"])
        target_data = dict(target_data)
        target_data["last_tx_id"] = tx_id
        tx_hist = list(target_data.get("tx_history") or [])
        tx_hist.append(tx_id)
        target_data["tx_history"] = tx_hist

        try:
            validated_target = AlbumMetadata(**target_data)
        except Exception as exc:
            raise ProjectValidationError(f"Invalid target album manifest: {exc}") from exc

        intended_manifest_sha256 = self._compute_canonical_manifest_sha256(target_data)
        prior_journal: Optional[AlbumJournal] = evidence["journal"]
        album_identity: Tuple[int, int] = evidence["album_identity"]
        album_sha256: str = evidence["album_sha256"]

        # Predecessor check before initial PREPARED
        if prior_journal is None:
            try:
                os.lstat("journal.json", dir_fd=album_dir_fd)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise AlbumDurabilityUncertainError(f"Cannot verify absence of journal.json: {exc}") from exc
            else:
                raise AlbumManifestCorruptError("Unexpected journal.json appeared before initial PREPARED publication.")
        else:
            self._read_json_file_verified_in_dir(
                album_dir_fd,
                "journal.json",
                sync_file=False,
                required=True,
                expected_identity=evidence["journal_identity"],
                expected_sha256=evidence["journal_sha256"],
            )
        self._read_json_file_verified_in_dir(
            album_dir_fd,
            "album.json",
            sync_file=False,
            required=True,
            expected_identity=album_identity,
            expected_sha256=album_sha256,
        )

        existing_receipts = list(prior_journal.tx_receipts) if prior_journal else []
        existing_retained = list(prior_journal.retained_artifacts) if prior_journal else []
        existing_idem = list(prior_journal.idempotency_records) if prior_journal else []

        active_tx = ActiveTxRecord(
            tx_id=tx_id,
            op=op,
            state=JournalTxState.PREPARED,
            expected_revision=expected_revision,
            target_revision=target_revision,
            payload_sha256=payload_sha256,
            intended_manifest_sha256=intended_manifest_sha256,
            idempotency_key=idempotency_key,
            staging_refs=[],
            new_refs=list(new_refs or []),
            superseded_refs=list(superseded_refs or []),
        )
        prep_idem = list(existing_idem)
        if idempotency_key is not None:
            prep_idem.append(
                IdempotencyRecord(
                    key=idempotency_key,
                    op=op,
                    expected_revision=expected_revision,
                    payload_sha256=payload_sha256,
                    result_revision=None,
                    tx_id=tx_id,
                    outcome=JournalTxState.PREPARED,
                )
            )
        prep_journal = AlbumJournal(
            schema_version=2,
            protocol=JournalProtocol.ENFORCED_V2,
            album_id=album_id,
            active_tx=active_tx,
            tx_receipts=existing_receipts,
            retained_artifacts=existing_retained,
            idempotency_records=prep_idem,
        )
        prep_sha = self._compute_canonical_manifest_sha256(prep_journal.model_dump(mode="json"))

        if pre_manifest_publish_check is not None:
            pre_manifest_publish_check(False)

        # Step 1: Durable PREPARED
        try:
            self._write_album_journal_atomic_in_dir(album_dir_fd, prep_journal)
            prep_identity = self._sync_terminal_journal_in_dir(album_dir_fd, expected_journal_sha256=prep_sha)
        except ManifestPublishError as exc:
            if prior_journal is None and exc.publish_state == ManifestPublishState.NOT_PUBLISHED:
                raise
            raise AlbumDurabilityUncertainError(f"PREPARED journal publication uncertain for '{tx_id}': {exc}") from exc

        # Predecessor check before PUBLISH_INTENT
        self._read_json_file_verified_in_dir(
            album_dir_fd,
            "journal.json",
            sync_file=False,
            required=True,
            expected_identity=prep_identity,
            expected_sha256=prep_sha,
        )
        self._read_json_file_verified_in_dir(
            album_dir_fd, "album.json", sync_file=False, required=True, expected_identity=album_identity, expected_sha256=album_sha256
        )

        # Step 2: Durable PUBLISH_INTENT
        intent_tx = active_tx.model_copy(update={"state": JournalTxState.PUBLISH_INTENT, "state_updated_at": time.time()})
        intent_journal = AlbumJournal(
            schema_version=2,
            protocol=JournalProtocol.ENFORCED_V2,
            album_id=album_id,
            active_tx=intent_tx,
            tx_receipts=existing_receipts,
            retained_artifacts=existing_retained,
            idempotency_records=prep_idem,
        )
        intent_sha = self._compute_canonical_manifest_sha256(intent_journal.model_dump(mode="json"))
        try:
            self._write_album_journal_atomic_in_dir(album_dir_fd, intent_journal)
            intent_identity = self._sync_terminal_journal_in_dir(album_dir_fd, expected_journal_sha256=intent_sha)
        except (ManifestPublishError, AlbumDurabilityUncertainError) as exc:
            raise AlbumDurabilityUncertainError(f"PUBLISH_INTENT journal publication uncertain for '{tx_id}': {exc}") from exc

        # Predecessor check before Manifest publication
        self._read_json_file_verified_in_dir(
            album_dir_fd,
            "journal.json",
            sync_file=False,
            required=True,
            expected_identity=intent_identity,
            expected_sha256=intent_sha,
        )
        self._read_json_file_verified_in_dir(
            album_dir_fd, "album.json", sync_file=False, required=True, expected_identity=album_identity, expected_sha256=album_sha256
        )

        if pre_manifest_publish_check is not None:
            try:
                pre_manifest_publish_check(True)
            except (AlbumManifestCorruptError, AlbumDurabilityUncertainError):
                raise
            except Exception as exc:
                raise AlbumDurabilityUncertainError(
                    f"Pre-publication artifact revalidation failed after durable PUBLISH_INTENT for '{tx_id}': {exc}"
                ) from exc

        # Step 3: Atomic Manifest Publication
        try:
            published_manifest_identity = _publish_manifest_atomic_in_dir(album_dir_fd, "album.json", target_data)
        except ManifestPublishError as exc:
            # Best-effort UNCERTAIN marker without masking primary uncertainty or overwriting unexpected evidence
            self._read_json_file_verified_in_dir(
                album_dir_fd,
                "journal.json",
                sync_file=False,
                required=True,
                expected_identity=intent_identity,
                expected_sha256=intent_sha,
            )
            uncertain_tx = intent_tx.model_copy(update={"state": JournalTxState.UNCERTAIN, "state_updated_at": time.time()})
            uncertain_journal = AlbumJournal(
                schema_version=2,
                protocol=JournalProtocol.ENFORCED_V2,
                album_id=album_id,
                active_tx=uncertain_tx,
                tx_receipts=existing_receipts,
                retained_artifacts=existing_retained,
                idempotency_records=prep_idem,
            )
            try:
                self._write_album_journal_atomic_in_dir(album_dir_fd, uncertain_journal)
            except Exception:
                pass
            raise AlbumDurabilityUncertainError(
                f"Manifest publication uncertain after durable PUBLISH_INTENT for '{tx_id}': {exc}"
            ) from exc

        # Predecessor check before Terminal Receipt publication (binds exact published manifest inode + digest)
        self._read_json_file_verified_in_dir(
            album_dir_fd,
            "journal.json",
            sync_file=False,
            required=True,
            expected_identity=intent_identity,
            expected_sha256=intent_sha,
        )
        self._read_json_file_verified_in_dir(
            album_dir_fd,
            "album.json",
            sync_file=True,
            required=True,
            expected_identity=published_manifest_identity,
            expected_sha256=intended_manifest_sha256,
        )

        # Step 4: Terminal COMMITTED Receipt Publication & Clearance
        receipt = TerminalTxReceipt(
            tx_id=tx_id,
            op=op,
            outcome=JournalTxState.COMMITTED,
            expected_revision=expected_revision,
            result_revision=target_revision,
            payload_sha256=payload_sha256,
            intended_manifest_sha256=intended_manifest_sha256,
            new_refs=list(new_refs or []),
            superseded_refs=list(superseded_refs or []),
            idempotency_key=idempotency_key,
        )
        final_journal = self._build_reconciled_journal(intent_journal, receipt, add_retained=False)
        final_sha = self._compute_canonical_manifest_sha256(final_journal.model_dump(mode="json"))
        try:
            self._write_album_journal_atomic_in_dir(album_dir_fd, final_journal)
            self._sync_terminal_journal_in_dir(album_dir_fd, expected_journal_sha256=final_sha)
        except ManifestPublishError as exc:
            raise AlbumDurabilityUncertainError(
                f"Terminal COMMITTED receipt publication uncertain for '{tx_id}': {exc}"
            ) from exc

        return validated_target

    def _validate_album_evidence_phase_a_locked(
        self,
        album_id: str,
        album_dir_fd: int,
        *,
        raw_album_data: Optional[Dict[str, Any]] = None,
        raw_album_identity: Optional[Tuple[int, int]] = None,
        evidence_out: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], Optional[AlbumJournal], RecoveryConsistency]:
        """Phase A (read-only): validate manifest/journal structure and reject proven CORRUPT without blocking replayable UNCERTAIN/active_tx."""
        if raw_album_data is not None and raw_album_identity is not None:
            album_data = raw_album_data
            album_identity = raw_album_identity
            album_sha256 = self._compute_canonical_manifest_sha256(album_data)
        else:
            album_loaded = self._read_json_file_verified_in_dir(album_dir_fd, "album.json", sync_file=False, required=False)
            if album_loaded is None:
                raise AlbumManifestCorruptError(f"Album manifest 'album.json' is missing for '{album_id}'.")
            album_data, album_identity, album_sha256 = album_loaded

        journal_loaded = self._read_json_file_verified_in_dir(album_dir_fd, "journal.json", sync_file=False, required=False)
        journal: Optional[AlbumJournal] = None
        journal_identity: Optional[Tuple[int, int]] = None
        journal_sha256: Optional[str] = None
        if journal_loaded is not None:
            journal_raw, journal_identity, journal_sha256 = journal_loaded
            try:
                journal = AlbumJournal(**journal_raw)
                if journal.album_id != album_id:
                    raise AlbumManifestCorruptError(
                        f"Album journal identity mismatch (expected '{album_id}', found '{journal.album_id}')."
                    )
            except AlbumManifestCorruptError:
                raise
            except Exception as exc:
                raise AlbumManifestCorruptError(
                    f"Album journal failed validation; preserving all artifacts: {exc}"
                ) from exc

        consistency = self._classify_album_consistency(album_data, journal)
        if consistency == RecoveryConsistency.CORRUPT:
            raise AlbumManifestCorruptError(f"Album '{album_id}' failed structural/consistency validation.")

        if evidence_out is not None:
            evidence_out.update({
                "album_data": album_data,
                "album_identity": album_identity,
                "album_sha256": album_sha256,
                "journal": journal,
                "journal_identity": journal_identity,
                "journal_sha256": journal_sha256,
                "consistency": consistency,
            })
        return album_data, journal, consistency

    @staticmethod
    def _check_album_mutation_eligibility_phase_b_locked(
        album_id: str,
        journal: Optional[AlbumJournal],
        consistency: RecoveryConsistency,
        *,
        is_enrolled: bool = False,
    ) -> None:
        """Phase B (read-only): reject UNCERTAIN/active_tx/v1 journals and unenrolled operations on idle v2 albums."""
        if journal is None:
            return
        if journal.protocol == JournalProtocol.LEGACY_PROVISIONAL:
            raise AlbumDurabilityUncertainError(
                f"Album '{album_id}' has a legacy v1 journal; mutations are blocked pending explicit v1 handling."
            )
        if journal.active_tx is not None or consistency == RecoveryConsistency.UNCERTAIN:
            raise AlbumDurabilityUncertainError(
                f"Album '{album_id}' has an unresolved transaction or uncertain durability evidence."
            )
        if not is_enrolled:
            raise AlbumMutationNotEnrolledError(
                f"Operation is not enrolled in the v2 transaction writer for album '{album_id}'."
            )

    def _update_album_locked(
        self,
        user_id: str,
        album_id: str,
        updates: Dict[str, Any],
        expected_revision: int,
        change_summary: Optional[str] = None,
        *,
        allow_new_entry_id: Optional[str] = None,
        allow_remove_entry_id: Optional[str] = None,
        allow_entry_take_assignment: bool = False,
    ) -> Optional[AlbumMetadata]:
        """Update album details or sequenced tracklist (caller must hold _album_lock)."""
        expected_revision = self._validate_expected_revision(expected_revision)
        try:
            album_dir_fd = self._open_album_dir_fd_nofollow(user_id, album_id)
        except ValueError:
            return None
        if album_dir_fd is None:
            return None
        try:
            raw_holder: List[Dict[str, Any]] = []
            loaded = self._read_album_from_dir_fd(album_id, album_dir_fd, protect_finalized=True, raw_out=raw_holder)
            if not loaded:
                return None
            album, data, _ = loaded
            _, journal, consistency = self._validate_album_evidence_phase_a_locked(
                album_id, album_dir_fd, raw_album_data=raw_holder[0]
            )
            self._check_album_mutation_eligibility_phase_b_locked(
                album_id, journal, consistency, is_enrolled=False
            )

            if self._is_album_finalized(album):
                raise AlbumFinalizedError("Finalized albums are immutable and cannot be modified.")

            updates.pop("expected_revision", None)
            if int(expected_revision) != int(album.revision):
                raise AlbumRevisionConflictError(
                    f"Album was modified concurrently (expected revision {expected_revision}, current revision {album.revision}). Reload and try again."
                )

            if updates.get("status") in (WorkspaceStatus.COMPLETED, WorkspaceStatus.COMPLETED.value):
                raise ProjectValidationError("Albums must be finalized via the dedicated finalization endpoint.")

            return self._update_album_locked_with_fd(
                album_dir_fd=album_dir_fd,
                user_id=user_id,
                album_id=album_id,
                album=album,
                data=data,
                updates=updates,
                change_summary=change_summary,
                allow_new_entry_id=allow_new_entry_id,
                allow_remove_entry_id=allow_remove_entry_id,
                allow_entry_take_assignment=allow_entry_take_assignment,
            )
        finally:
            try:
                os.close(album_dir_fd)
            except OSError:
                pass

    def _update_album_locked_with_fd(
        self,
        *,
        album_dir_fd: int,
        user_id: str,
        album_id: str,
        album: AlbumMetadata,
        data: Dict[str, Any],
        updates: Dict[str, Any],
        change_summary: Optional[str] = None,
        allow_new_entry_id: Optional[str] = None,
        allow_remove_entry_id: Optional[str] = None,
        allow_entry_take_assignment: bool = False,
    ) -> Optional[AlbumMetadata]:
        data = dict(data)
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
            from collections import Counter
            legacy_ids = list(updates["track_ids"])
            existing_by_tid: Dict[str, List[AlbumTrackEntry]] = {}
            for existing_entry in album.tracks:
                existing_by_tid.setdefault(existing_entry.track_id, []).append(existing_entry)
            existing_counts = Counter(e.track_id for e in album.tracks)
            incoming_counts = Counter(legacy_ids)
            if existing_counts != incoming_counts:
                raise ProjectValidationError(
                    "Bulk legacy track_ids updates must preserve the exact existing track set; use add_album_entry or remove_album_entry."
                )

            track_list_input = []
            for i, tid in enumerate(legacy_ids):
                preserved = existing_by_tid[tid].pop(0).model_dump()
                preserved["track_number"] = i + 1
                track_list_input.append(preserved)

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
                    item.pop("_allow_new_entry_id", None)
                    track_id = item.get("track_id")
                    if not track_id or not is_valid_project_id(track_id, expected_prefix="trk"):
                        raise ProjectValidationError(f"Invalid track identifier '{track_id}' at position {i + 1}.")

                    take_id_provided = ("take_id" in item)
                    raw_take_id = item.get("take_id")
                    if raw_take_id is not None and not is_valid_project_id(raw_take_id, expected_prefix="tak"):
                        raise ProjectValidationError(f"Invalid take identifier '{raw_take_id}' at position {i + 1}.")

                    req_id = item.get("id")
                    is_authorized_new_entry = bool(allow_new_entry_id is not None and req_id == allow_new_entry_id)
                    if req_id is not None:
                        if not is_valid_project_id(req_id, expected_prefix="ale"):
                            raise ProjectValidationError(f"Invalid album entry identifier '{req_id}'.")
                        if not is_authorized_new_entry and req_id not in existing_by_id:
                            raise ProjectValidationError(f"Unknown album entry identifier '{req_id}'.")

                    if is_authorized_new_entry:
                        matched = None
                    else:
                        matched = self._resolve_existing_entry_for_item(item, existing_by_id, unmatched_existing)
                        if allow_new_entry_id is None and allow_remove_entry_id is None and matched is None:
                            raise ProjectValidationError(
                                "Adding entries via bulk album update is not allowed; use add_album_entry."
                            )

                    if matched is not None:
                        has_existing_recording = bool(
                            matched.take_id is not None
                            or matched.has_owned_audio
                            or matched.snapshot is not None
                            or self._entry_has_filesystem_artifacts(user_id, album_id, matched.id)
                        )
                        is_bulk_patch = (
                            allow_new_entry_id is None
                            and allow_remove_entry_id is None
                            and not allow_entry_take_assignment
                        )
                        if not take_id_provided:
                            take_id = matched.take_id
                        elif raw_take_id is None:
                            if has_existing_recording:
                                raise AlbumReplacementDisabledError(
                                    "Clearing an existing recording to a placeholder is temporarily disabled in Phase 1A."
                                )
                            take_id = None
                        else:
                            if has_existing_recording and (raw_take_id != matched.take_id or track_id != matched.track_id):
                                raise AlbumReplacementDisabledError(
                                    "Replacing existing album-owned audio is temporarily disabled while audio-replacement safety upgrades are in progress."
                                )
                            if is_bulk_patch and raw_take_id != matched.take_id:
                                raise ProjectValidationError(
                                    "Track/Take assignment changes are not allowed in bulk album updates; use add_album_entry or update_album_entry."
                                )
                            take_id = raw_take_id
                    else:
                        take_id = raw_take_id

                    same_track = bool(matched and matched.track_id == track_id)
                    same_recording = bool(same_track and matched.take_id == take_id)
                    if matched is not None and not same_track:
                        raise ProjectValidationError(
                            "Changing an entry's source track via bulk update is not allowed."
                        )

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

                    has_existing_audio = bool(
                        matched
                        and (matched.has_owned_audio or self.get_album_entry_audio_path(user_id, album_id, entry_id) is not None)
                    )
                    if take_id is not None and has_existing_audio and not same_recording:
                        raise AlbumReplacementDisabledError(
                            "Replacing existing album-owned audio is temporarily disabled while audio-replacement safety upgrades are in progress."
                        )

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
                                user_id, album_id, entry_id, track_id, take_id, album_dir_fd=album_dir_fd
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
            except Exception as e:
                allow_unlink = not isinstance(e, ManifestPublishError) or e.publish_state == ManifestPublishState.NOT_PUBLISHED
                for rb in reversed(staged_rollbacks):
                    try:
                        rb(allow_promoted_unlink=allow_unlink)
                    except Exception:
                        pass
                raise

            for old_entry in album.tracks:
                if old_entry.id and old_entry.id not in seen_entry_ids:
                    if old_entry.id != allow_remove_entry_id:
                        has_old_recording = bool(
                            old_entry.has_owned_audio
                            or old_entry.take_id is not None
                            or old_entry.snapshot is not None
                            or self._entry_has_filesystem_artifacts(user_id, album_id, old_entry.id)
                        )
                        if has_old_recording:
                            raise AlbumReplacementDisabledError(
                                "Removing recorded entries is temporarily disabled in Phase 1A."
                            )
                        raise ProjectValidationError(
                            "Removing entries via bulk album update is not allowed; use remove_album_entry."
                        )
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
            validated_album = AlbumMetadata(**data)
        except Exception as e:
            for rb in reversed(staged_rollbacks):
                try:
                    rb(allow_promoted_unlink=True)
                except Exception as rb_err:
                    logger.error(f"Rollback failed after manifest validation error: {rb_err}")
            raise ProjectValidationError(f"Invalid album manifest candidate: {e}") from e

        try:
            for commit_fn in staged_commits:
                commit_fn()
            _publish_manifest_atomic_in_dir(album_dir_fd, "album.json", data)
        except ManifestPublishError as mpe:
            allow_unlink = (mpe.publish_state == ManifestPublishState.NOT_PUBLISHED)
            for rb in reversed(staged_rollbacks):
                try:
                    rb(allow_promoted_unlink=allow_unlink)
                except Exception as rb_err:
                    logger.error(f"Rollback failed after manifest error ({mpe.publish_state}): {rb_err}")
            raise (mpe.cause or mpe)
        except Exception:
            for rb in reversed(staged_rollbacks):
                try:
                    rb(allow_promoted_unlink=True)
                except Exception as rb_err:
                    logger.error(f"Rollback failed prior to manifest publish: {rb_err}")
            raise

        for cleanup_fn in staged_cleanups:
            cleanup_fn()

        if obsolete_entry_dirs:
            dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                entries_fd = os.open("entries", dir_flags, dir_fd=album_dir_fd)
            except OSError:
                entries_fd = None
            if entries_fd is not None:
                try:
                    for old_eid in set(obsolete_entry_dirs):
                        try:
                            ehash = get_album_entry_dir_name(old_eid)
                            sub_fd = os.open(ehash, dir_flags, dir_fd=entries_fd)
                            try:
                                is_empty = (len(os.listdir(sub_fd)) == 0)
                            finally:
                                os.close(sub_fd)
                            if is_empty:
                                os.rmdir(ehash, dir_fd=entries_fd)
                        except FileNotFoundError:
                            pass
                        except Exception as e:
                            logger.warning(f"Failed removing empty obsolete entry directory {old_eid}: {e}")
                finally:
                    os.close(entries_fd)

        return validated_album

    def add_album_entry(
        self,
        user_id: str,
        album_id: str,
        track_id: str,
        expected_revision: int,
        take_id: Optional[str] = None,
        disc_number: int = 1,
        track_number: Optional[int] = None,
        custom_title: Optional[str] = None,
    ) -> Optional[AlbumMetadata]:
        """Add a track entry (with explicit take or as placeholder) to an unfinished album."""
        expected_revision = self._validate_expected_revision(expected_revision)
        try:
            self._get_album_dir_path(user_id, album_id)
        except ValueError:
            return None

        with self._album_lock(user_id, album_id):
            album_dir_fd = self._open_album_dir_fd_nofollow(user_id, album_id)
            if album_dir_fd is None:
                return None
            try:
                raw_holder: List[Dict[str, Any]] = []
                loaded = self._read_album_from_dir_fd(album_id, album_dir_fd, protect_finalized=True, raw_out=raw_holder)
                if not loaded:
                    return None
                album, norm_data, manifest_st = loaded
                evidence: Dict[str, Any] = {}
                _, journal, consistency = self._validate_album_evidence_phase_a_locked(
                    album_id,
                    album_dir_fd,
                    raw_album_data=raw_holder[0],
                    raw_album_identity=(manifest_st.st_dev, manifest_st.st_ino),
                    evidence_out=evidence,
                )
                self._check_album_mutation_eligibility_phase_b_locked(
                    album_id, journal, consistency, is_enrolled=True
                )
                if self._is_album_finalized(album):
                    raise AlbumFinalizedError("Cannot add tracks to a finalized album.")
                if int(expected_revision) != int(album.revision):
                    raise AlbumRevisionConflictError(
                        f"Album was modified concurrently (expected revision {expected_revision}, current revision {album.revision}). Reload and try again."
                    )

                if not track_id or not is_valid_project_id(track_id, expected_prefix="trk"):
                    raise ProjectValidationError(f"Invalid track identifier '{track_id}'.")
                if take_id is not None and not is_valid_project_id(take_id, expected_prefix="tak"):
                    raise ProjectValidationError(f"Invalid take identifier '{take_id}'.")

                new_entry_id = generate_album_entry_id()
                if journal is None:
                    new_entry = {
                        "id": new_entry_id,
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
                    return self._update_album_locked_with_fd(
                        album_dir_fd=album_dir_fd,
                        user_id=user_id,
                        album_id=album_id,
                        album=album,
                        data=norm_data,
                        updates={"tracks": tracks},
                        change_summary=summary,
                        allow_new_entry_id=new_entry_id,
                    )

                # Enrolled v2 path: stage generation-1 audio + snapshot (if take_id) and publish via _execute_v2_album_tx_locked
                new_refs: List[ArtifactRefRecord] = []
                commit_fn: Optional[Callable[[], None]] = None
                cleanup_fn: Optional[Callable[[], None]] = None
                rollback_fn: Optional[Callable[[bool], None]] = None
                promoted = False

                if take_id is None:
                    track = self.get_track(user_id, track_id)
                    if not track:
                        raise ProjectValidationError(f"Track workspace '{track_id}' not found.")
                    new_entry_dict: Dict[str, Any] = {
                        "id": new_entry_id,
                        "track_number": 1,
                        "disc_number": int(disc_number or 1),
                        "track_id": track_id,
                        "take_id": None,
                        "custom_title": custom_title,
                        "is_placeholder": True,
                        "has_owned_audio": False,
                        "snapshot": None,
                        "active_generation": None,
                        "active_audio_filename": None,
                        "active_snapshot_filename": None,
                    }
                else:
                    snap, commit_fn, cleanup_fn, rollback_fn = self._stage_entry_audio_copy(
                        user_id,
                        album_id,
                        new_entry_id,
                        track_id,
                        take_id,
                        album_dir_fd=album_dir_fd,
                        generation=1,
                        staged_refs_out=new_refs,
                    )
                    new_entry_dict = {
                        "id": new_entry_id,
                        "track_number": 1,
                        "disc_number": int(disc_number or 1),
                        "track_id": track_id,
                        "take_id": take_id,
                        "custom_title": custom_title,
                        "is_placeholder": False,
                        "has_owned_audio": True,
                        "snapshot": snap.model_dump(),
                        "active_generation": 1,
                        "active_audio_filename": f"audio_g1.{snap.format}",
                        "active_snapshot_filename": "snapshot_g1.json",
                    }

                try:
                    target_data = dict(norm_data)
                    tracks_list = [dict(t) if isinstance(t, dict) else t.model_dump() for t in (target_data.get("tracks") or [])]
                    if track_number is not None and 1 <= track_number <= len(tracks_list) + 1:
                        tracks_list.insert(track_number - 1, new_entry_dict)
                    else:
                        tracks_list.append(new_entry_dict)
                    for idx, t_item in enumerate(tracks_list):
                        t_item["track_number"] = idx + 1

                    new_rev = int(target_data.get("revision", 1)) + 1
                    summary = f"Added track entry ({'placeholder' if take_id is None else take_id})"
                    revs = list(target_data.get("revisions") or [])
                    revs.append({
                        "revision": new_rev,
                        "timestamp": time.time(),
                        "change_summary": summary,
                        "changed_fields": ["tracks"],
                    })
                    target_data["tracks"] = tracks_list
                    target_data["track_ids"] = [t["track_id"] for t in tracks_list if t.get("track_id")]
                    target_data["revision"] = new_rev
                    target_data["revisions"] = revs
                    target_data["updated_at"] = time.time()

                    payload_sha256 = self._compute_update_album_payload_sha256(
                        "add_album_entry",
                        expected_revision,
                        {
                            "track_id": track_id,
                            "take_id": take_id,
                            "disc_number": disc_number,
                            "track_number": track_number,
                            "custom_title": custom_title,
                        },
                    )

                    def _promote_post_intent(is_post_intent: bool) -> None:
                        nonlocal promoted
                        if is_post_intent and commit_fn is not None and not promoted:
                            commit_fn()
                            promoted = True

                    published = self._execute_v2_album_tx_locked(
                        album_id=album_id,
                        album_dir_fd=album_dir_fd,
                        evidence=evidence,
                        target_data=target_data,
                        op="add_album_entry",
                        expected_revision=expected_revision,
                        payload_sha256=payload_sha256,
                        idempotency_key=None,
                        pre_manifest_publish_check=_promote_post_intent,
                        new_refs=new_refs,
                        superseded_refs=[],
                    )
                except Exception as exc:
                    if rollback_fn is not None:
                        allow_unlink = not isinstance(exc, AlbumDurabilityUncertainError)
                        try:
                            rollback_fn(allow_promoted_unlink=allow_unlink)
                        except Exception:
                            pass
                    raise

                if cleanup_fn is not None:
                    cleanup_fn()
                return published
            finally:
                try:
                    os.close(album_dir_fd)
                except OSError:
                    pass

    def update_album_entry(
        self,
        user_id: str,
        album_id: str,
        entry_id: str,
        expected_revision: int,
        take_id: Optional[str] = None,
        clear_take_to_placeholder: bool = False,
        disc_number: Optional[int] = None,
        custom_title: Optional[str] = None,
    ) -> Optional[AlbumMetadata]:
        """Update an entry's metadata or perform initial assignment to an empty placeholder."""
        expected_revision = self._validate_expected_revision(expected_revision)
        try:
            self._get_album_dir_path(user_id, album_id)
        except ValueError:
            return None

        with self._album_lock(user_id, album_id):
            album_dir_fd = self._open_album_dir_fd_nofollow(user_id, album_id)
            if album_dir_fd is None:
                return None
            try:
                raw_holder: List[Dict[str, Any]] = []
                loaded = self._read_album_from_dir_fd(album_id, album_dir_fd, protect_finalized=True, raw_out=raw_holder)
                if not loaded:
                    return None
                album, norm_data, _ = loaded
                _, journal, consistency = self._validate_album_evidence_phase_a_locked(
                    album_id, album_dir_fd, raw_album_data=raw_holder[0]
                )
                self._check_album_mutation_eligibility_phase_b_locked(
                    album_id, journal, consistency, is_enrolled=False
                )
                if self._is_album_finalized(album):
                    raise AlbumFinalizedError("Cannot modify entries of a finalized album.")
                if int(expected_revision) != int(album.revision):
                    raise AlbumRevisionConflictError(
                        f"Album was modified concurrently (expected revision {expected_revision}, current revision {album.revision}). Reload and try again."
                    )

                tracks = [t.model_dump() for t in album.tracks]
                target = next((t for t in tracks if t.get("id") == entry_id), None)
                if not target:
                    raise ProjectValidationError(f"Album entry '{entry_id}' not found.")

                has_existing_recording = bool(
                    target.get("take_id") is not None
                    or target.get("has_owned_audio")
                    or target.get("snapshot") is not None
                    or self._entry_has_filesystem_artifacts(user_id, album_id, entry_id)
                )
                if clear_take_to_placeholder:
                    if has_existing_recording:
                        raise AlbumReplacementDisabledError(
                            "Clearing an existing recording to a placeholder is temporarily disabled in Phase 1A."
                        )
                    target["take_id"] = None
                elif take_id is not None:
                    if has_existing_recording and take_id != target.get("take_id"):
                        raise AlbumReplacementDisabledError(
                            "Replacing existing album-owned audio is temporarily disabled while audio-replacement safety upgrades are in progress."
                        )
                    target["take_id"] = take_id

                if disc_number is not None:
                    target["disc_number"] = disc_number
                if custom_title is not None:
                    target["custom_title"] = custom_title or None

                return self._update_album_locked_with_fd(
                    album_dir_fd=album_dir_fd,
                    user_id=user_id,
                    album_id=album_id,
                    album=album,
                    data=norm_data,
                    updates={"tracks": tracks},
                    change_summary=f"Updated album entry {entry_id}",
                    allow_entry_take_assignment=True,
                )
            finally:
                try:
                    os.close(album_dir_fd)
                except OSError:
                    pass

    def remove_album_entry(
        self,
        user_id: str,
        album_id: str,
        entry_id: str,
        expected_revision: int,
    ) -> Optional[AlbumMetadata]:
        """Remove an empty placeholder album entry (recorded/occupied entries are disabled in Phase 1A)."""
        expected_revision = self._validate_expected_revision(expected_revision)
        try:
            self._get_album_dir_path(user_id, album_id)
        except ValueError:
            return None

        with self._album_lock(user_id, album_id):
            album_dir_fd = self._open_album_dir_fd_nofollow(user_id, album_id)
            if album_dir_fd is None:
                return None
            try:
                raw_holder: List[Dict[str, Any]] = []
                loaded = self._read_album_from_dir_fd(album_id, album_dir_fd, protect_finalized=True, raw_out=raw_holder)
                if not loaded:
                    return None
                album, norm_data, _ = loaded
                _, journal, consistency = self._validate_album_evidence_phase_a_locked(
                    album_id, album_dir_fd, raw_album_data=raw_holder[0]
                )
                self._check_album_mutation_eligibility_phase_b_locked(
                    album_id, journal, consistency, is_enrolled=False
                )
                if self._is_album_finalized(album):
                    raise AlbumFinalizedError("Cannot remove entries from a finalized album.")
                if int(expected_revision) != int(album.revision):
                    raise AlbumRevisionConflictError(
                        f"Album was modified concurrently (expected revision {expected_revision}, current revision {album.revision}). Reload and try again."
                    )

                target = next((t for t in album.tracks if t.id == entry_id), None)
                if not target:
                    raise ProjectValidationError(f"Album entry '{entry_id}' not found.")

                has_existing_recording = bool(
                    target.take_id is not None
                    or target.has_owned_audio
                    or target.snapshot is not None
                    or self._entry_has_filesystem_artifacts(user_id, album_id, entry_id)
                )
                if has_existing_recording:
                    raise AlbumReplacementDisabledError(
                        "Removing entries with recordings or existing artifacts is temporarily disabled in Phase 1A."
                    )

                remaining = [t.model_dump() for t in album.tracks if t.id != entry_id]
                return self._update_album_locked_with_fd(
                    album_dir_fd=album_dir_fd,
                    user_id=user_id,
                    album_id=album_id,
                    album=album,
                    data=norm_data,
                    updates={"tracks": remaining},
                    change_summary=f"Removed album entry {entry_id}",
                    allow_remove_entry_id=entry_id,
                )
            finally:
                try:
                    os.close(album_dir_fd)
                except OSError:
                    pass

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
        expected_revision: Any = None,
    ) -> Optional[AlbumMetadata]:
        """Validate lock, revision, finalization, and entry identity via album_dir_fd; replacement remains disabled."""
        expected_revision = self._validate_expected_revision(expected_revision)
        if not is_valid_project_id(entry_id, expected_prefix="ale"):
            raise ProjectValidationError(f"Invalid album entry identifier '{entry_id}'.")
        try:
            self._get_album_dir_path(user_id, album_id)
        except ValueError:
            return None

        with self._album_lock(user_id, album_id):
            album_dir_fd = self._open_album_dir_fd_nofollow(user_id, album_id)
            if album_dir_fd is None:
                return None
            try:
                raw_holder: List[Dict[str, Any]] = []
                loaded = self._read_album_from_dir_fd(album_id, album_dir_fd, protect_finalized=True, raw_out=raw_holder)
                if not loaded:
                    return None
                album, _, _ = loaded
                _, journal, consistency = self._validate_album_evidence_phase_a_locked(
                    album_id, album_dir_fd, raw_album_data=raw_holder[0]
                )
                self._check_album_mutation_eligibility_phase_b_locked(
                    album_id, journal, consistency, is_enrolled=False
                )
                if self._is_album_finalized(album):
                    raise AlbumFinalizedError("Cannot refresh entries of a finalized album.")
                if int(expected_revision) != int(album.revision):
                    raise AlbumRevisionConflictError(
                        f"Album was modified concurrently (expected revision {expected_revision}, current revision {album.revision}). Reload and try again."
                    )

                matches = [t for t in album.tracks if t.id == entry_id]
                if not matches:
                    raise ProjectValidationError(f"Album entry '{entry_id}' not found.")
                if len(matches) > 1:
                    raise AlbumManifestCorruptError(f"Ambiguous album entry identifier '{entry_id}'.")
                entry = matches[0]
                if entry.is_placeholder or not entry.take_id:
                    raise ProjectValidationError("Cannot refresh a placeholder entry with no selected take.")

                raise AlbumReplacementDisabledError(
                    "Refreshing existing album-owned audio from a source take is temporarily disabled while audio-replacement safety upgrades are in progress."
                )
            finally:
                try:
                    os.close(album_dir_fd)
                except OSError:
                    pass

    @staticmethod
    def _snapshots_semantically_equal(a: AlbumEntrySnapshot, b: AlbumEntrySnapshot) -> bool:
        exact_fields = (
            "entry_id",
            "source_track_id",
            "source_track_title",
            "source_track_artist",
            "source_track_revision",
            "source_take_id",
            "source_take_label",
            "source_take_revision",
            "source_filename",
            "format",
            "size_bytes",
            "audio_sha256",
            "embedded_metadata",
            "embedded_lyrics",
            "take_notes",
            "take_prompt",
            "take_style_tags",
        )
        for fld in exact_fields:
            if getattr(a, fld) != getattr(b, fld):
                return False
        for float_fld in ("source_take_updated_at", "duration_seconds", "copied_at"):
            raw_a = getattr(a, float_fld)
            raw_b = getattr(b, float_fld)
            try:
                va = 0.0 if raw_a is None else float(raw_a)
                vb = 0.0 if raw_b is None else float(raw_b)
            except (TypeError, ValueError):
                return False
            if not (math.isfinite(va) and math.isfinite(vb)):
                return False
            if abs(va - vb) > 1e-6:
                return False
        return True

    @staticmethod
    def _compute_finalize_album_payload_sha256(expected_revision: int) -> str:
        """Compute deterministic canonical SHA-256 for an enrolled finalize_album request."""
        payload = {
            "op": "finalize_album",
            "expected_revision": int(expected_revision),
        }
        canonical_bytes = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical_bytes).hexdigest()

    @staticmethod
    def _seal_stat_tuple(st: os.stat_result) -> Tuple[int, int, int, int, int]:
        """Return (st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns) for artifact sealing."""
        mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
        ctime_ns = getattr(st, "st_ctime_ns", int(st.st_ctime * 1_000_000_000))
        return (int(st.st_dev), int(st.st_ino), int(st.st_size), int(mtime_ns), int(ctime_ns))

    def _seal_album_artifacts_for_finalization_locked(
        self,
        album_dir_fd: int,
        album: AlbumMetadata,
        *,
        has_journal: bool,
    ) -> Dict[str, Any]:
        """Verify and durably seal all Album recordings, snapshots, cover, and directories before PREPARED."""
        import errno

        errors: List[Dict[str, Any]] = []
        if not album.tracks:
            errors.append({
                "entry_id": None,
                "track_number": None,
                "track_id": None,
                "reason": "Album must contain at least one recorded entry before finalization.",
            })

        dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            alb_st = os.fstat(album_dir_fd)
        except OSError as exc:
            raise AlbumDurabilityUncertainError(f"I/O failure stating album directory: {exc}") from exc
        album_dir_id = (int(alb_st.st_dev), int(alb_st.st_ino))

        entries_fd: Optional[int] = None
        entries_dir_id: Optional[Tuple[int, int]] = None
        sealed_entries: List[Tuple[str, str, Tuple[int, int], str, Tuple[int, int, int, int, int], str, Tuple[int, int, int, int, int], str]] = []
        sealed_cover: Optional[Tuple[str, Tuple[int, int, int, int, int], str]] = None

        try:
            try:
                entries_fd = os.open("entries", dir_flags, dir_fd=album_dir_fd)
            except FileNotFoundError:
                entries_fd = None
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR):
                    raise AlbumManifestCorruptError(f"Album entries root is not a regular directory: {exc}") from exc
                raise AlbumDurabilityUncertainError(
                    f"Cannot access album entries directory during sealing: {exc}"
                ) from exc
            if entries_fd is not None:
                try:
                    ent_st = os.fstat(entries_fd)
                except OSError as exc:
                    raise AlbumDurabilityUncertainError(
                        f"Cannot stat album entries directory during sealing: {exc}"
                    ) from exc
                if not stat.S_ISDIR(ent_st.st_mode):
                    raise AlbumManifestCorruptError("Album entries root is not a regular directory.")
                entries_dir_id = (int(ent_st.st_dev), int(ent_st.st_ino))

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

                if not entry.id or not is_valid_project_id(entry.id, expected_prefix="ale"):
                    errors.append({
                        "entry_id": entry.id,
                        "track_number": entry.track_number,
                        "track_id": entry.track_id,
                        "reason": f"Track #{entry.track_number} has an invalid entry identifier.",
                    })
                    continue

                if entries_fd is None or not entry.has_owned_audio or entry.snapshot is None:
                    errors.append({
                        "entry_id": entry.id,
                        "track_number": entry.track_number,
                        "track_id": entry.track_id,
                        "reason": f"Track #{entry.track_number} (entry {entry.id}) is missing its album-owned audio copy or metadata snapshot.",
                    })
                    continue

                ehash = get_album_entry_dir_name(entry.id)
                entry_fd: Optional[int] = None
                try:
                    entry_fd = os.open(ehash, dir_flags, dir_fd=entries_fd)
                    edir_st = os.fstat(entry_fd)
                    entry_dir_id = (int(edir_st.st_dev), int(edir_st.st_ino))

                    if entry.active_generation is not None:
                        audio_name = entry.active_audio_filename
                        snap_name = entry.active_snapshot_filename or "snapshot.json"
                    else:
                        audio_name = None
                        for ext in AUDIO_FORMATS:
                            cand = f"audio{ext}"
                            try:
                                cst = os.lstat(cand, dir_fd=entry_fd)
                                if stat.S_ISREG(cst.st_mode):
                                    audio_name = cand
                                    break
                            except FileNotFoundError:
                                continue
                        snap_name = "snapshot.json"
                    if not audio_name:
                        raise ValueError("Album-owned audio file is missing or not a regular file.")

                    expected_entry_files = {snap_name, audio_name}
                    for cname in os.listdir(entry_fd):
                        if cname.startswith((".staging_", ".tmp_", ".backup_")):
                            raise ValueError("Incomplete recording transfer files present in entry directory.")
                        if cname not in expected_entry_files:
                            raise ValueError(f"Unexpected artifact '{cname}' present in entry directory.")
                        cst = os.lstat(cname, dir_fd=entry_fd)
                        if not stat.S_ISREG(cst.st_mode):
                            raise ValueError(f"Entry artifact '{cname}' is not a regular file.")

                    afd = os.open(audio_name, file_flags, dir_fd=entry_fd)
                    try:
                        pre_ast = os.fstat(afd)
                        if not stat.S_ISREG(pre_ast.st_mode):
                            raise AlbumManifestCorruptError(f"Entry audio '{audio_name}' is not a regular file.")
                        if pre_ast.st_size < 32:
                            raise ValueError("Audio file is empty or truncated (< 32 bytes).")
                        h = hashlib.sha256()
                        first_chunk = os.read(afd, 64)
                        h.update(first_chunk)
                        ext = os.path.splitext(audio_name)[1].lower()
                        if not matches_audio_header(first_chunk, ext):
                            raise ValueError("Audio file header does not match container format.")
                        while True:
                            chunk = os.read(afd, 65536)
                            if not chunk:
                                break
                            h.update(chunk)
                        if ext == ".wav":
                            import wave
                            os.lseek(afd, 0, os.SEEK_SET)
                            dup_fd = os.dup(afd)
                            with os.fdopen(dup_fd, "rb") as wf:
                                with wave.open(wf, "rb") as wv:
                                    if wv.getnchannels() <= 0 or wv.getframerate() <= 0:
                                        raise ValueError("Invalid WAV container parameters.")
                        elif ext == ".m4a":
                            import mutagen.mp4
                            os.lseek(afd, 0, os.SEEK_SET)
                            dup_fd = os.dup(afd)
                            with os.fdopen(dup_fd, "rb") as mf:
                                mutagen.mp4.MP4(mf)
                        audio_sha = h.hexdigest()
                        if pre_ast.st_size != entry.snapshot.size_bytes or audio_sha != entry.snapshot.audio_sha256:
                            raise ValueError("Album audio copy checksum or size does not match snapshot.")
                        try:
                            _fsync_file_fd(afd)
                            post_ast = os.fstat(afd)
                            dir_ast = os.lstat(audio_name, dir_fd=entry_fd)
                        except OSError as exc:
                            raise AlbumDurabilityUncertainError(
                                f"Audio sealing synchronization failed for entry '{entry.id}': {exc}"
                            ) from exc
                        pre_a_tup = self._seal_stat_tuple(pre_ast)
                        if self._seal_stat_tuple(post_ast) != pre_a_tup or self._seal_stat_tuple(dir_ast) != pre_a_tup:
                            raise AlbumManifestCorruptError(
                                f"Entry audio '{audio_name}' changed or was substituted during finalization sealing."
                            )
                    finally:
                        os.close(afd)

                    sfd = os.open(snap_name, file_flags, dir_fd=entry_fd)
                    try:
                        pre_sst = os.fstat(sfd)
                        if not stat.S_ISREG(pre_sst.st_mode):
                            raise AlbumManifestCorruptError(f"Entry {snap_name} is not a regular file.")
                        with os.fdopen(os.dup(sfd), "rb") as sf:
                            snap_bytes = sf.read()
                        snap_sha = hashlib.sha256(snap_bytes).hexdigest()
                        try:
                            disk_snap = AlbumEntrySnapshot(**json.loads(snap_bytes.decode("utf-8")))
                        except Exception as se:
                            raise ValueError(f"Standalone {snap_name} is missing or invalid: {se}") from se
                        if (
                            disk_snap.entry_id != entry.id
                            or disk_snap.source_track_id != entry.track_id
                            or disk_snap.source_take_id != entry.take_id
                        ):
                            raise ValueError(f"Standalone {snap_name} entry/track/take identity mismatch.")
                        if not self._snapshots_semantically_equal(disk_snap, entry.snapshot):
                            raise ValueError(f"Standalone {snap_name} does not match committed manifest snapshot.")
                        try:
                            _fsync_file_fd(sfd)
                            post_sst = os.fstat(sfd)
                            dir_sst = os.lstat(snap_name, dir_fd=entry_fd)
                        except OSError as exc:
                            raise AlbumDurabilityUncertainError(
                                f"Snapshot sealing synchronization failed for entry '{entry.id}': {exc}"
                            ) from exc
                        pre_s_tup = self._seal_stat_tuple(pre_sst)
                        if self._seal_stat_tuple(post_sst) != pre_s_tup or self._seal_stat_tuple(dir_sst) != pre_s_tup:
                            raise AlbumManifestCorruptError(
                                f"Entry {snap_name} changed or was substituted during finalization sealing for '{entry.id}'."
                            )
                    finally:
                        os.close(sfd)

                    try:
                        _fsync_dir_fd(entry_fd)
                        post_edir_st = os.fstat(entry_fd)
                    except OSError as exc:
                        raise AlbumDurabilityUncertainError(
                            f"Entry directory sealing synchronization failed for '{entry.id}': {exc}"
                        ) from exc
                    if (int(post_edir_st.st_dev), int(post_edir_st.st_ino)) != entry_dir_id:
                        raise AlbumManifestCorruptError(f"Entry directory identity changed during sealing for '{entry.id}'.")

                    sealed_entries.append((entry.id, ehash, entry_dir_id, audio_name, pre_a_tup, audio_sha, snap_name, pre_s_tup, snap_sha))
                except (AlbumDurabilityUncertainError, AlbumManifestCorruptError):
                    raise
                except FileNotFoundError as exc:
                    errors.append({
                        "entry_id": entry.id,
                        "track_number": entry.track_number,
                        "track_id": entry.track_id,
                        "reason": f"Track #{entry.track_number} (entry {entry.id}) failed integrity check: {exc}",
                    })
                except OSError as exc:
                    if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR):
                        raise AlbumManifestCorruptError(
                            f"Unsafe or non-regular entry artifact for '{entry.id}': {exc}"
                        ) from exc
                    raise AlbumDurabilityUncertainError(
                        f"Unavailable verification evidence for entry '{entry.id}': {exc}"
                    ) from exc
                except Exception as e:
                    errors.append({
                        "entry_id": entry.id,
                        "track_number": entry.track_number,
                        "track_id": entry.track_id,
                        "reason": f"Track #{entry.track_number} (entry {entry.id}) failed integrity check: {e}",
                    })
                finally:
                    if entry_fd is not None:
                        try:
                            os.close(entry_fd)
                        except OSError:
                            pass

            allowed_cover_names = {"cover.jpg", "cover.jpeg", "cover.png", "cover.webp"} if album.has_cover else set()
            seen_covers = []
            try:
                root_names = os.listdir(album_dir_fd)
            except OSError as exc:
                raise AlbumDurabilityUncertainError(
                    f"Cannot list album root directory during sealing: {exc}"
                ) from exc
            for rname in root_names:
                try:
                    rst = os.lstat(rname, dir_fd=album_dir_fd)
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR):
                        raise AlbumManifestCorruptError(
                            f"Unsafe album root artifact '{rname}': {exc}"
                        ) from exc
                    raise AlbumDurabilityUncertainError(
                        f"Cannot stat album root artifact '{rname}' during sealing: {exc}"
                    ) from exc
                if rname == "album.json":
                    if not stat.S_ISREG(rst.st_mode):
                        errors.append({"entry_id": None, "track_number": None, "track_id": None, "reason": "Album manifest is not a regular file."})
                elif rname == "journal.json" and has_journal:
                    if not stat.S_ISREG(rst.st_mode):
                        errors.append({"entry_id": None, "track_number": None, "track_id": None, "reason": "Album journal is not a regular file."})
                elif rname == "entries":
                    if not stat.S_ISDIR(rst.st_mode):
                        errors.append({"entry_id": None, "track_number": None, "track_id": None, "reason": "Album entries root is not a regular directory."})
                elif rname in allowed_cover_names:
                    if not stat.S_ISREG(rst.st_mode):
                        errors.append({"entry_id": None, "track_number": None, "track_id": None, "reason": f"Album cover '{rname}' is not a regular file."})
                    seen_covers.append(rname)
                else:
                    errors.append({
                        "entry_id": None,
                        "track_number": None,
                        "track_id": None,
                        "reason": f"Unexpected album root artifact '{rname}' prevents finalization.",
                    })
            if len(seen_covers) > 1:
                errors.append({"entry_id": None, "track_number": None, "track_id": None, "reason": "Multiple cover artwork files present in album root."})

            if entries_fd is not None:
                expected_dirs = {get_album_entry_dir_name(eid) for eid in manifest_entry_ids if eid}
                try:
                    entries_list = os.listdir(entries_fd)
                except OSError as exc:
                    raise AlbumDurabilityUncertainError(f"Cannot list entries directory during sealing: {exc}") from exc
                for iname in entries_list:
                    try:
                        ist = os.lstat(iname, dir_fd=entries_fd)
                    except FileNotFoundError:
                        continue
                    except OSError as exc:
                        if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR):
                            raise AlbumManifestCorruptError(f"Unsafe entry directory '{iname}': {exc}") from exc
                        raise AlbumDurabilityUncertainError(f"Cannot stat entry directory '{iname}' during sealing: {exc}") from exc
                    if not stat.S_ISDIR(ist.st_mode) or iname not in expected_dirs:
                        errors.append({
                            "entry_id": None,
                            "track_number": None,
                            "track_id": None,
                            "reason": f"Album storage directory '{iname}' is inconsistent with the manifest.",
                        })

            if errors:
                raise AlbumFinalizationError(
                    "Album cannot be finalized: " + " ".join(e["reason"] for e in errors),
                    errors=errors,
                )

            if len(seen_covers) == 1:
                cover_name = seen_covers[0]
                try:
                    cfd = os.open(cover_name, file_flags, dir_fd=album_dir_fd)
                except FileNotFoundError as exc:
                    raise AlbumFinalizationError(
                        f"Album cannot be finalized: Cover '{cover_name}' disappeared during validation.",
                        errors=[{"entry_id": None, "track_number": None, "track_id": None, "reason": f"Cover '{cover_name}' missing."}],
                    ) from exc
                except OSError as exc:
                    if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR):
                        raise AlbumManifestCorruptError(f"Unsafe album cover '{cover_name}': {exc}") from exc
                    raise AlbumDurabilityUncertainError(f"Cannot open album cover '{cover_name}' during sealing: {exc}") from exc
                try:
                    try:
                        pre_cst = os.fstat(cfd)
                    except OSError as exc:
                        raise AlbumDurabilityUncertainError(f"Cannot stat album cover '{cover_name}' during sealing: {exc}") from exc
                    if not stat.S_ISREG(pre_cst.st_mode) or pre_cst.st_size <= 0:
                        raise AlbumManifestCorruptError(f"Album cover '{cover_name}' is invalid.")
                    try:
                        with os.fdopen(os.dup(cfd), "rb") as cf:
                            cover_bytes = cf.read()
                    except OSError as exc:
                        raise AlbumDurabilityUncertainError(f"Cannot read album cover '{cover_name}' during sealing: {exc}") from exc
                    cover_sha = hashlib.sha256(cover_bytes).hexdigest()
                    try:
                        _fsync_file_fd(cfd)
                        post_cst = os.fstat(cfd)
                        dir_cst = os.lstat(cover_name, dir_fd=album_dir_fd)
                    except OSError as exc:
                        raise AlbumDurabilityUncertainError(
                            f"Cover artwork sealing synchronization failed for '{cover_name}': {exc}"
                        ) from exc
                    pre_c_tup = self._seal_stat_tuple(pre_cst)
                    if self._seal_stat_tuple(post_cst) != pre_c_tup or self._seal_stat_tuple(dir_cst) != pre_c_tup:
                        raise AlbumManifestCorruptError(
                            f"Album cover '{cover_name}' changed or was substituted during finalization sealing."
                        )
                    sealed_cover = (cover_name, pre_c_tup, cover_sha)
                finally:
                    try:
                        os.close(cfd)
                    except OSError:
                        pass

            if entries_fd is not None:
                try:
                    _fsync_dir_fd(entries_fd)
                    post_ent = os.fstat(entries_fd)
                except OSError as exc:
                    raise AlbumDurabilityUncertainError(f"Entries directory sealing synchronization failed: {exc}") from exc
                if (int(post_ent.st_dev), int(post_ent.st_ino)) != entries_dir_id:
                    raise AlbumManifestCorruptError("Entries directory identity changed during sealing.")

            try:
                _fsync_dir_fd(album_dir_fd)
                post_alb = os.fstat(album_dir_fd)
            except OSError as exc:
                raise AlbumDurabilityUncertainError(f"Album root directory sealing synchronization failed: {exc}") from exc
            if (int(post_alb.st_dev), int(post_alb.st_ino)) != album_dir_id:
                raise AlbumManifestCorruptError("Album root directory identity changed during sealing.")

            return {
                "album_dir_id": album_dir_id,
                "entries_dir_id": entries_dir_id,
                "entries": sealed_entries,
                "cover": sealed_cover,
            }
        finally:
            if entries_fd is not None:
                try:
                    os.close(entries_fd)
                except OSError:
                    pass

    def _revalidate_sealed_finalization_bindings_locked(
        self,
        album_dir_fd: int,
        sealed: Dict[str, Any],
        *,
        post_intent: bool,
    ) -> None:
        """Revalidate sealed artifact identities and hashes before PREPARED or manifest publication."""
        import errno

        dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)

        def _handle_missing_or_io(msg: str, exc: OSError, *, is_missing: bool) -> None:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR):
                raise AlbumManifestCorruptError(f"{msg}: {exc}") from exc
            if post_intent:
                raise AlbumDurabilityUncertainError(f"{msg}: {exc}") from exc
            if is_missing:
                raise AlbumFinalizationError(
                    f"Album cannot be finalized: {msg}",
                    errors=[{"entry_id": None, "track_number": None, "track_id": None, "reason": msg}],
                ) from exc
            raise AlbumDurabilityUncertainError(f"{msg}: {exc}") from exc

        try:
            alb_st = os.fstat(album_dir_fd)
        except OSError as exc:
            _handle_missing_or_io("Cannot stat album directory during revalidation", exc, is_missing=False)
            return
        if (int(alb_st.st_dev), int(alb_st.st_ino)) != sealed["album_dir_id"]:
            raise AlbumManifestCorruptError("Album root directory identity changed before publication.")

        entries_fd: Optional[int] = None
        try:
            if sealed["entries_dir_id"] is not None:
                try:
                    entries_fd = os.open("entries", dir_flags, dir_fd=album_dir_fd)
                    ent_st = os.fstat(entries_fd)
                except FileNotFoundError as exc:
                    _handle_missing_or_io("Album entries directory disappeared before publication", exc, is_missing=True)
                    return
                except OSError as exc:
                    _handle_missing_or_io("Cannot access album entries directory before publication", exc, is_missing=False)
                    return
                if (int(ent_st.st_dev), int(ent_st.st_ino)) != sealed["entries_dir_id"]:
                    raise AlbumManifestCorruptError("Album entries directory identity changed before publication.")

            for entry_id, ehash, entry_dir_id, audio_name, audio_stat, _audio_sha, snap_name, snap_stat, snap_sha in sealed["entries"]:
                entry_fd: Optional[int] = None
                try:
                    try:
                        entry_fd = os.open(ehash, dir_flags, dir_fd=entries_fd)
                        edir_st = os.fstat(entry_fd)
                    except FileNotFoundError as exc:
                        _handle_missing_or_io(f"Entry directory missing for '{entry_id}'", exc, is_missing=True)
                        return
                    except OSError as exc:
                        _handle_missing_or_io(f"Cannot access entry directory for '{entry_id}'", exc, is_missing=False)
                        return
                    if (int(edir_st.st_dev), int(edir_st.st_ino)) != entry_dir_id:
                        raise AlbumManifestCorruptError(f"Entry directory identity changed for '{entry_id}' before publication.")

                    try:
                        afd = os.open(audio_name, file_flags, dir_fd=entry_fd)
                    except FileNotFoundError as exc:
                        _handle_missing_or_io(f"Entry audio '{audio_name}' missing for '{entry_id}'", exc, is_missing=True)
                        return
                    except OSError as exc:
                        _handle_missing_or_io(f"Cannot open entry audio '{audio_name}' for '{entry_id}'", exc, is_missing=False)
                        return
                    try:
                        try:
                            ast = os.fstat(afd)
                        except OSError as exc:
                            _handle_missing_or_io(f"Cannot stat entry audio '{audio_name}' for '{entry_id}'", exc, is_missing=False)
                            return
                        if not stat.S_ISREG(ast.st_mode) or self._seal_stat_tuple(ast) != audio_stat:
                            raise AlbumManifestCorruptError(
                                f"Entry audio '{audio_name}' was substituted or modified before publication for '{entry_id}'."
                            )
                    finally:
                        try:
                            os.close(afd)
                        except OSError:
                            pass

                    try:
                        sfd = os.open(snap_name, file_flags, dir_fd=entry_fd)
                    except FileNotFoundError as exc:
                        _handle_missing_or_io(f"Entry {snap_name} missing for '{entry_id}'", exc, is_missing=True)
                        return
                    except OSError as exc:
                        _handle_missing_or_io(f"Cannot open entry {snap_name} for '{entry_id}'", exc, is_missing=False)
                        return
                    try:
                        try:
                            sst = os.fstat(sfd)
                        except OSError as exc:
                            _handle_missing_or_io(f"Cannot stat entry {snap_name} for '{entry_id}'", exc, is_missing=False)
                            return
                        if not stat.S_ISREG(sst.st_mode) or self._seal_stat_tuple(sst) != snap_stat:
                            raise AlbumManifestCorruptError(
                                f"Entry {snap_name} was substituted or modified before publication for '{entry_id}'."
                            )
                        try:
                            with os.fdopen(os.dup(sfd), "rb") as sf:
                                snap_read = sf.read()
                        except OSError as exc:
                            _handle_missing_or_io(f"Cannot read entry snapshot.json for '{entry_id}'", exc, is_missing=False)
                            return
                        if hashlib.sha256(snap_read).hexdigest() != snap_sha:
                            raise AlbumManifestCorruptError(
                                f"Entry snapshot.json content changed before publication for '{entry_id}'."
                            )
                    finally:
                        try:
                            os.close(sfd)
                        except OSError:
                            pass
                finally:
                    if entry_fd is not None:
                        try:
                            os.close(entry_fd)
                        except OSError:
                            pass

            if sealed["cover"] is not None:
                cover_name, cover_stat, cover_sha = sealed["cover"]
                try:
                    cfd = os.open(cover_name, file_flags, dir_fd=album_dir_fd)
                except FileNotFoundError as exc:
                    _handle_missing_or_io(f"Album cover '{cover_name}' missing before publication", exc, is_missing=True)
                    return
                except OSError as exc:
                    _handle_missing_or_io(f"Cannot open album cover '{cover_name}' before publication", exc, is_missing=False)
                    return
                try:
                    try:
                        cst = os.fstat(cfd)
                    except OSError as exc:
                        _handle_missing_or_io(f"Cannot stat album cover '{cover_name}' before publication", exc, is_missing=False)
                        return
                    if not stat.S_ISREG(cst.st_mode) or self._seal_stat_tuple(cst) != cover_stat:
                        raise AlbumManifestCorruptError(
                            f"Album cover '{cover_name}' was substituted or modified before publication."
                        )
                    try:
                        with os.fdopen(os.dup(cfd), "rb") as cf:
                            cov_read = cf.read()
                    except OSError as exc:
                        _handle_missing_or_io(f"Cannot read album cover '{cover_name}' before publication", exc, is_missing=False)
                        return
                    if hashlib.sha256(cov_read).hexdigest() != cover_sha:
                        raise AlbumManifestCorruptError(
                            f"Album cover '{cover_name}' content changed before publication."
                        )
                finally:
                    try:
                        os.close(cfd)
                    except OSError:
                        pass
        finally:
            if entries_fd is not None:
                try:
                    os.close(entries_fd)
                except OSError:
                    pass

    def finalize_album(
        self,
        user_id: str,
        album_id: str,
        expected_revision: int,
        *,
        idempotency_key: Optional[str] = None,
    ) -> Optional[AlbumMetadata]:
        """Validate, seal, and durably finalize an Album release under v2 transaction journaling."""
        res = self.finalize_album_with_replay_status(
            user_id, album_id, expected_revision=expected_revision, idempotency_key=idempotency_key
        )
        return res[0] if res is not None else None

    def finalize_album_with_replay_status(
        self,
        user_id: str,
        album_id: str,
        expected_revision: int,
        *,
        idempotency_key: Optional[str] = None,
    ) -> Optional[Tuple[AlbumMetadata, bool]]:
        """Enrolled v2 Album finalization writer returning (AlbumMetadata, is_idempotent_replay)."""
        expected_revision = self._validate_expected_revision(expected_revision)
        clean_key = self._validate_idempotency_key(idempotency_key)
        try:
            self._get_album_dir_path(user_id, album_id)
        except ValueError:
            return None

        op = "finalize_album"
        payload_sha256 = self._compute_finalize_album_payload_sha256(expected_revision)

        with self._album_lock(user_id, album_id):
            album_dir_fd = self._open_album_dir_fd_nofollow(user_id, album_id)
            if album_dir_fd is None:
                return None
            try:
                raw_holder: List[Dict[str, Any]] = []
                loaded = self._read_album_from_dir_fd(
                    album_id, album_dir_fd, protect_finalized=True, raw_out=raw_holder
                )
                if not loaded:
                    return None
                album, norm_data, manifest_st = loaded
                raw_identity = (manifest_st.st_dev, manifest_st.st_ino)
                evidence: Dict[str, Any] = {}
                _, journal, consistency = self._validate_album_evidence_phase_a_locked(
                    album_id,
                    album_dir_fd,
                    raw_album_data=raw_holder[0],
                    raw_album_identity=raw_identity,
                    evidence_out=evidence,
                )

                replay = self._resolve_v2_idempotency_replay_locked(
                    album_id=album_id,
                    album_dir_fd=album_dir_fd,
                    current_album=album,
                    evidence=evidence,
                    idempotency_key=clean_key,
                    op=op,
                    expected_revision=expected_revision,
                    payload_sha256=payload_sha256,
                )
                if replay is not None:
                    return replay

                self._check_album_mutation_eligibility_phase_b_locked(
                    album_id, journal, consistency, is_enrolled=True
                )

                if self._is_album_finalized(album):
                    raise AlbumFinalizedError("Album is already finalized.")
                if int(expected_revision) != int(album.revision):
                    raise AlbumRevisionConflictError(
                        f"Album was modified concurrently (expected revision {expected_revision}, current revision {album.revision}). Reload and try again."
                    )

                sealed = self._seal_album_artifacts_for_finalization_locked(
                    album_dir_fd, album, has_journal=(journal is not None)
                )

                now = time.time()
                target_data = copy.deepcopy(norm_data)
                new_rev = int(norm_data.get("revision", 1)) + 1
                target_data["status"] = WorkspaceStatus.COMPLETED.value
                target_data["is_finalized"] = True
                target_data["finalized_at"] = now
                target_data["revision"] = new_rev
                target_data["updated_at"] = now
                revs = list(target_data.get("revisions") or [])
                revs.append({
                    "revision": new_rev,
                    "timestamp": now,
                    "change_summary": "Finalized album release",
                    "changed_fields": ["status", "is_finalized", "finalized_at"],
                })
                target_data["revisions"] = revs

                published = self._execute_v2_album_tx_locked(
                    album_id=album_id,
                    album_dir_fd=album_dir_fd,
                    evidence=evidence,
                    target_data=target_data,
                    op=op,
                    expected_revision=expected_revision,
                    payload_sha256=payload_sha256,
                    idempotency_key=clean_key,
                    pre_manifest_publish_check=lambda post_intent: self._revalidate_sealed_finalization_bindings_locked(
                        album_dir_fd, sealed, post_intent=post_intent
                    ),
                )
                return published, False
            finally:
                try:
                    os.close(album_dir_fd)
                except OSError:
                    pass

    def delete_album(self, user_id: str, album_id: str, expected_revision: int) -> bool:
        """Delete a verified-empty draft album using retained dir_fds and non-following allowlist inspection."""
        expected_revision = self._validate_expected_revision(expected_revision)
        try:
            self._get_album_dir_path(user_id, album_id)
        except ValueError:
            return False

        album_dir_name = get_album_dir_name(album_id)
        dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)

        with self._album_lock(user_id, album_id):
            albums_root_fd = self._open_albums_root_fd_nofollow(user_id)
            if albums_root_fd is None:
                return False
            album_dir_fd: Optional[int] = None
            entries_fd: Optional[int] = None
            try:
                try:
                    dir_st = os.lstat(album_dir_name, dir_fd=albums_root_fd)
                except FileNotFoundError:
                    return False
                except OSError as e:
                    raise ProjectStorageError(f"Cannot inspect album directory '{album_id}': {e}") from e
                if not stat.S_ISDIR(dir_st.st_mode):
                    raise AlbumManifestCorruptError(f"Album path '{album_id}' is not a regular directory.")

                try:
                    album_dir_fd = os.open(album_dir_name, dir_flags, dir_fd=albums_root_fd)
                except OSError as e:
                    raise AlbumManifestCorruptError(f"Cannot open album directory '{album_id}': {e}") from e

                raw_holder: List[Dict[str, Any]] = []
                loaded = self._read_album_from_dir_fd(album_id, album_dir_fd, protect_finalized=True, raw_out=raw_holder)
                if not loaded:
                    if os.listdir(album_dir_fd):
                        raise AlbumManifestCorruptError(
                            f"Album manifest '{album_id}' is missing while album files still exist; refusing deletion."
                        )
                    return False
                album, _, manifest_st = loaded
                _, journal, consistency = self._validate_album_evidence_phase_a_locked(
                    album_id, album_dir_fd, raw_album_data=raw_holder[0]
                )
                self._check_album_mutation_eligibility_phase_b_locked(
                    album_id, journal, consistency, is_enrolled=False
                )

                if self._is_album_finalized(album):
                    raise AlbumFinalizedError("Finalized albums are immutable and cannot be deleted.")
                if int(expected_revision) != int(album.revision):
                    raise AlbumRevisionConflictError(
                        f"Album was modified concurrently (expected revision {expected_revision}, current revision {album.revision}). Reload and try again."
                    )

                placeholder_dir_names = set()
                for entry in album.tracks:
                    if entry.take_id is not None or entry.has_owned_audio or entry.snapshot is not None:
                        raise AlbumReplacementDisabledError(
                            "Deleting albums containing recordings is temporarily disabled in Phase 1A."
                        )
                    if entry.id:
                        placeholder_dir_names.add(get_album_entry_dir_name(entry.id))

                empty_entry_dirs_to_remove: List[str] = []
                has_entries_root = False
                try:
                    for child_name in os.listdir(album_dir_fd):
                        cst = os.lstat(child_name, dir_fd=album_dir_fd)
                        if child_name == "album.json":
                            if not stat.S_ISREG(cst.st_mode):
                                raise AlbumManifestCorruptError("Album manifest is not a regular file.")
                        elif child_name == "entries":
                            if not stat.S_ISDIR(cst.st_mode):
                                raise AlbumReplacementDisabledError(
                                    "Album 'entries' path is not a regular directory; refusing deletion."
                                )
                            has_entries_root = True
                            entries_fd = os.open("entries", dir_flags, dir_fd=album_dir_fd)
                            for sub_name in os.listdir(entries_fd):
                                sst = os.lstat(sub_name, dir_fd=entries_fd)
                                if not stat.S_ISDIR(sst.st_mode) or sub_name not in placeholder_dir_names:
                                    raise AlbumReplacementDisabledError(
                                        "Album contains unexpected or non-empty entry artifacts; deletion is blocked in Phase 1A."
                                    )
                                sub_fd = os.open(sub_name, dir_flags, dir_fd=entries_fd)
                                try:
                                    if os.listdir(sub_fd):
                                        raise AlbumReplacementDisabledError(
                                            "Album contains non-empty entry files; deletion is blocked in Phase 1A."
                                        )
                                finally:
                                    os.close(sub_fd)
                                empty_entry_dirs_to_remove.append(sub_name)
                        else:
                            raise AlbumReplacementDisabledError(
                                f"Album contains '{child_name}' (artwork, artifacts, or unknown files); deletion is blocked in Phase 1A."
                            )
                except (AlbumReplacementDisabledError, AlbumManifestCorruptError):
                    raise
                except OSError as e:
                    raise ProjectStorageError(f"Failed inspecting album '{album_id}' before deletion: {e}") from e

                try:
                    if entries_fd is not None:
                        for empty_sub_name in empty_entry_dirs_to_remove:
                            os.rmdir(empty_sub_name, dir_fd=entries_fd)
                        os.close(entries_fd)
                        entries_fd = None
                    if has_entries_root:
                        os.rmdir("entries", dir_fd=album_dir_fd)
                    cur_mst = os.lstat("album.json", dir_fd=album_dir_fd)
                    if not stat.S_ISREG(cur_mst.st_mode) or (cur_mst.st_dev, cur_mst.st_ino) != (manifest_st.st_dev, manifest_st.st_ino):
                        raise AlbumManifestCorruptError("Album manifest identity changed prior to deletion.")
                    os.unlink("album.json", dir_fd=album_dir_fd)
                    os.close(album_dir_fd)
                    album_dir_fd = None
                    os.rmdir(album_dir_name, dir_fd=albums_root_fd)
                    return True
                except OSError as e:
                    logger.error(f"Failed to delete album {album_id}: {e}")
                    raise ProjectStorageError(f"Failed to completely delete album '{album_id}': {e}") from e
            finally:
                if entries_fd is not None:
                    try:
                        os.close(entries_fd)
                    except OSError:
                        pass
                if album_dir_fd is not None:
                    try:
                        os.close(album_dir_fd)
                    except OSError:
                        pass
                try:
                    os.close(albums_root_fd)
                except OSError:
                    pass

    def save_album_cover(
        self,
        user_id: str,
        album_id: str,
        image_bytes: bytes,
        expected_revision: int,
        extension: str = ".jpg",
    ) -> Optional[AlbumMetadata]:
        """Save initial album cover artwork safely under lock; replacing existing covers is disabled in Phase 1A."""
        expected_revision = self._validate_expected_revision(expected_revision)
        try:
            album_dir = self._get_album_dir_path(user_id, album_id)
        except ValueError:
            return None

        with self._album_lock(user_id, album_id):
            album_dir_fd = self._open_album_dir_fd_nofollow(user_id, album_id)
            if album_dir_fd is None:
                return None

            try:
                raw_holder: List[Dict[str, Any]] = []
                loaded = self._read_album_from_dir_fd(album_id, album_dir_fd, protect_finalized=True, raw_out=raw_holder)
                if not loaded:
                    return None
                album, data, _ = loaded
                _, journal, consistency = self._validate_album_evidence_phase_a_locked(
                    album_id, album_dir_fd, raw_album_data=raw_holder[0]
                )
                self._check_album_mutation_eligibility_phase_b_locked(
                    album_id, journal, consistency, is_enrolled=False
                )
                if self._is_album_finalized(album):
                    raise AlbumFinalizedError("Cannot update cover artwork on a finalized album.")
                if int(expected_revision) != int(album.revision):
                    raise AlbumRevisionConflictError(
                        f"Album was modified concurrently (expected revision {expected_revision}, current revision {album.revision}). Reload and try again."
                    )

                existing_cover = False
                for ext in (".jpg", ".jpeg", ".png", ".webp"):
                    try:
                        os.lstat(f"cover{ext}", dir_fd=album_dir_fd)
                        existing_cover = True
                        break
                    except FileNotFoundError:
                        continue
                if album.has_cover or existing_cover:
                    raise AlbumReplacementDisabledError(
                        "Replacing existing album cover artwork is temporarily disabled in Phase 1A."
                    )

                self.ensure_quota_available(user_id, len(image_bytes))

                target_ext = ".png" if extension.lower() == ".png" else ".jpg"
                cover_name = f"cover{target_ext}"
                tmp_cover_name = f".staging_cover_{secrets.token_hex(8)}{target_ext}"

                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(tmp_cover_name, flags, 0o600, dir_fd=album_dir_fd)
                tmp_exists = True
                cover_promoted = False
                manifest_published = False
                try:
                    with os.fdopen(fd, "wb") as f:
                        f.write(image_bytes)
                        f.flush()
                        os.fsync(f.fileno())
                        os.fchmod(f.fileno(), 0o600)

                    try:
                        os.lstat(cover_name, dir_fd=album_dir_fd)
                        raise AlbumReplacementDisabledError(
                            "Replacing existing album cover artwork is temporarily disabled in Phase 1A."
                        )
                    except FileNotFoundError:
                        pass

                    os.replace(tmp_cover_name, cover_name, src_dir_fd=album_dir_fd, dst_dir_fd=album_dir_fd)
                    tmp_exists = False
                    cover_promoted = True

                    data = dict(data)
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
                    _publish_manifest_atomic_in_dir(album_dir_fd, "album.json", data)
                    manifest_published = True
                except ManifestPublishError as mpe:
                    if cover_promoted and mpe.publish_state == ManifestPublishState.NOT_PUBLISHED:
                        try:
                            os.unlink(cover_name, dir_fd=album_dir_fd)
                        except Exception:
                            pass
                    raise (mpe.cause or mpe)
                except Exception:
                    if tmp_exists and not cover_promoted:
                        try:
                            os.unlink(tmp_cover_name, dir_fd=album_dir_fd)
                        except Exception:
                            pass
                    elif cover_promoted and not manifest_published:
                        try:
                            os.unlink(cover_name, dir_fd=album_dir_fd)
                        except Exception:
                            pass
                    raise
            finally:
                try:
                    os.close(album_dir_fd)
                except OSError:
                    pass

            return self._read_album_unlocked(user_id, album_id)

    def get_album_cover_path(self, user_id: str, album_id: str) -> Optional[Path]:
        """Return path to active album cover artwork via shared read context (side-effect-free)."""
        if not is_valid_project_id(album_id, expected_prefix="alb"):
            return None
        ctx = self._open_album_read_context(user_id, album_id, load_journal=True)
        if ctx is None:
            return None
        try:
            opened = self._resolve_and_open_cover_from_context(ctx)
            if opened is None:
                return None
            filename, cfd, _size = opened
            try:
                os.close(cfd)
            except OSError:
                pass
            return ctx.album_dir / filename
        finally:
            ctx.close()

    # --- Phase 1B Stage 1.1: Durable Journal, Recovery Classification & Verified Staging Cleanup ---

    @staticmethod
    def _read_album_journal_from_dir_fd(
        album_dir_fd: int,
        expected_album_id: Optional[str] = None,
    ) -> Optional[AlbumJournal]:
        """Read and validate journal.json strictly through album_dir_fd without creating or mutating files."""
        file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            jfd = os.open("journal.json", file_flags, dir_fd=album_dir_fd)
        except FileNotFoundError:
            return None
        except OSError as e:
            raise AlbumManifestCorruptError(f"Album journal is inaccessible or a symlink: {e}") from e

        try:
            jst = os.fstat(jfd)
            if not stat.S_ISREG(jst.st_mode):
                raise AlbumManifestCorruptError("Album journal is not a regular file.")
            with os.fdopen(jfd, "r", encoding="utf-8") as f:
                jfd = -1
                raw = json.load(f)
            if not isinstance(raw, dict):
                raise AlbumManifestCorruptError("Album journal root must be a JSON object.")
            journal = AlbumJournal(**raw)
            if expected_album_id is not None and journal.album_id != expected_album_id:
                raise AlbumManifestCorruptError(
                    f"Album journal identity mismatch (expected '{expected_album_id}', found '{journal.album_id}')."
                )
            return journal
        except AlbumManifestCorruptError:
            raise
        except Exception as e:
            raise AlbumManifestCorruptError(f"Album journal failed validation; preserving all artifacts: {e}") from e
        finally:
            if jfd >= 0:
                try:
                    os.close(jfd)
                except OSError:
                    pass

    @staticmethod
    def _compute_canonical_manifest_sha256(manifest_data: Dict[str, Any]) -> str:
        """Compute deterministic SHA-256 hex digest over canonical serialized Album manifest JSON."""
        if not isinstance(manifest_data, dict):
            raise ValueError("Album manifest payload must be a JSON object.")
        canonical_bytes = json.dumps(
            manifest_data,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical_bytes).hexdigest()

    @staticmethod
    def _write_album_journal_atomic_in_dir(album_dir_fd: int, journal: AlbumJournal) -> None:
        """Atomically publish and directory-sync journal.json inside a validated album_dir_fd."""
        _publish_manifest_atomic_in_dir(album_dir_fd, "journal.json", journal.model_dump(mode="json"))

    @staticmethod
    @staticmethod
    def _verify_v2_refs_against_manifest(
        album_data: Dict[str, Any],
        refs: List[ArtifactRefRecord],
    ) -> Optional[RecoveryConsistency]:
        """Verify structured ArtifactRefRecord descriptors against raw manifest fields (read-only).

        Returns CORRUPT on direct contradiction, UNCERTAIN when manifest layout/metadata
        is insufficient to verify a relationship, or None when all descriptors match.
        Evaluates all applicable relationships so CORRUPT always takes precedence over UNCERTAIN.
        """
        if not refs:
            return None

        raw_tracks = album_data.get("tracks")
        entries_by_id: Dict[str, Dict[str, Any]] = {}
        if isinstance(raw_tracks, list):
            for item in raw_tracks:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    entries_by_id[item["id"]] = item

        uncertain = False
        for ref in refs:
            if not isinstance(ref, ArtifactRefRecord):
                return RecoveryConsistency.CORRUPT

            if ref.role in (ArtifactRole.ENTRY_AUDIO, ArtifactRole.ENTRY_SNAPSHOT):
                if ref.entry_id is None:
                    return RecoveryConsistency.CORRUPT
                entry = entries_by_id.get(ref.entry_id)
                if entry is None:
                    return RecoveryConsistency.CORRUPT
                if entry.get("is_placeholder") is True:
                    return RecoveryConsistency.CORRUPT

                has_owned = entry.get("has_owned_audio")
                if has_owned is False:
                    return RecoveryConsistency.CORRUPT
                if has_owned is None:
                    uncertain = True
                elif has_owned is not True:
                    return RecoveryConsistency.CORRUPT

                expected_ehash = get_album_entry_dir_name(ref.entry_id)
                parts = ref.rel_path.split("/")
                if len(parts) != 3 or parts[0] != "entries" or parts[1] != expected_ehash:
                    return RecoveryConsistency.CORRUPT
                filename = parts[2]

                entry_gen = entry.get("active_generation")
                if ref.generation is not None:
                    if entry_gen is None:
                        uncertain = True
                    elif entry_gen != ref.generation:
                        return RecoveryConsistency.CORRUPT
                elif entry_gen is not None:
                    uncertain = True

                if ref.role == ArtifactRole.ENTRY_AUDIO:
                    active_audio = entry.get("active_audio_filename")
                    if active_audio is None:
                        uncertain = True
                    elif active_audio != filename:
                        return RecoveryConsistency.CORRUPT
                else:
                    active_snap = entry.get("active_snapshot_filename")
                    if active_snap is None:
                        uncertain = True
                    elif active_snap != filename:
                        return RecoveryConsistency.CORRUPT

                snap = entry.get("snapshot")
                if snap is None:
                    uncertain = True
                elif not isinstance(snap, dict):
                    return RecoveryConsistency.CORRUPT
                else:
                    snap_entry_id = snap.get("entry_id")
                    if snap_entry_id is None:
                        uncertain = True
                    elif snap_entry_id != ref.entry_id:
                        return RecoveryConsistency.CORRUPT

                    snap_track_id = snap.get("source_track_id")
                    entry_track_id = entry.get("track_id")
                    if snap_track_id is None or entry_track_id is None:
                        uncertain = True
                    elif snap_track_id != entry_track_id:
                        return RecoveryConsistency.CORRUPT

                    snap_take_id = snap.get("source_take_id")
                    entry_take_id = entry.get("take_id")
                    if snap_take_id is None or entry_take_id is None:
                        uncertain = True
                    elif snap_take_id != entry_take_id:
                        return RecoveryConsistency.CORRUPT

                    if ref.role == ArtifactRole.ENTRY_AUDIO:
                        snap_sha = snap.get("audio_sha256")
                        if snap_sha is None:
                            uncertain = True
                        elif snap_sha != ref.sha256:
                            return RecoveryConsistency.CORRUPT

                        snap_size = snap.get("size_bytes")
                        if snap_size is None:
                            uncertain = True
                        elif snap_size != ref.size_bytes:
                            return RecoveryConsistency.CORRUPT

                        snap_fmt = snap.get("format")
                        if snap_fmt is None:
                            uncertain = True
                        elif isinstance(snap_fmt, str) and snap_fmt:
                            expected_ext = f".{snap_fmt.lower().lstrip('.')}"
                            if os.path.splitext(filename)[1].lower() != expected_ext:
                                return RecoveryConsistency.CORRUPT
                        else:
                            return RecoveryConsistency.CORRUPT

            elif ref.role == ArtifactRole.COVER_ART:
                has_cover = album_data.get("has_cover")
                if has_cover is False:
                    return RecoveryConsistency.CORRUPT
                if has_cover is None:
                    uncertain = True
                elif has_cover is not True:
                    return RecoveryConsistency.CORRUPT

                active_cover = album_data.get("active_cover_filename")
                if active_cover is None:
                    uncertain = True
                elif active_cover != ref.rel_path:
                    return RecoveryConsistency.CORRUPT

                cover_gen = album_data.get("cover_generation")
                if ref.generation is not None:
                    if cover_gen is None:
                        uncertain = True
                    elif cover_gen != ref.generation:
                        return RecoveryConsistency.CORRUPT
                elif cover_gen is not None:
                    uncertain = True

            elif ref.role == ArtifactRole.RETAINED_BACKUP:
                if ref.entry_id is not None:
                    entry = entries_by_id.get(ref.entry_id)
                    if entry is not None:
                        active_audio = entry.get("active_audio_filename")
                        active_snap = entry.get("active_snapshot_filename")
                        basename = ref.rel_path.split("/")[-1]
                        if basename in (active_audio, active_snap) and len(ref.rel_path.split("/")) == 3:
                            return RecoveryConsistency.CORRUPT
                elif ref.rel_path == album_data.get("active_cover_filename"):
                    return RecoveryConsistency.CORRUPT

        return RecoveryConsistency.UNCERTAIN if uncertain else None

    @classmethod
    def _classify_v2_album_consistency(
        cls,
        *,
        album_data: Dict[str, Any],
        journal: AlbumJournal,
        manifest_rev: int,
        last_tx_id: Optional[str],
        tx_history: Optional[List[str]],
        is_deleted: Optional[bool],
        delete_tx_id: Optional[str],
    ) -> RecoveryConsistency:
        """Pure provisional classifier for enforced_v2 journals (verifies canonical manifest SHA-256, receipts, & artifact bindings)."""
        if journal.schema_version != 2 or journal.protocol != JournalProtocol.ENFORCED_V2:
            return RecoveryConsistency.CORRUPT
        try:
            manifest_sha256 = cls._compute_canonical_manifest_sha256(album_data)
        except Exception:
            return RecoveryConsistency.CORRUPT

        hist_list = tx_history if isinstance(tx_history, list) else []
        lineage_uncertain = False

        for rcp in journal.tx_receipts:
            in_current_marker = (rcp.tx_id == last_tx_id) or (is_deleted is True and rcp.tx_id == delete_tx_id)
            in_hist_marker = rcp.tx_id in hist_list

            if rcp.outcome == JournalTxState.ABORTED:
                if in_current_marker or in_hist_marker:
                    return RecoveryConsistency.CORRUPT
                if rcp.result_revision > manifest_rev:
                    return RecoveryConsistency.CORRUPT
                continue

            if rcp.outcome == JournalTxState.COMMITTED:
                if rcp.result_revision > manifest_rev:
                    return RecoveryConsistency.CORRUPT
                if in_current_marker:
                    if manifest_rev == rcp.result_revision:
                        # Exact-current candidate
                        if rcp.intended_manifest_sha256 != manifest_sha256:
                            return RecoveryConsistency.CORRUPT
                        ref_issue = cls._verify_v2_refs_against_manifest(album_data, rcp.new_refs)
                        if ref_issue == RecoveryConsistency.CORRUPT:
                            return RecoveryConsistency.CORRUPT
                        if ref_issue == RecoveryConsistency.UNCERTAIN:
                            lineage_uncertain = True
                    else:
                        # Advanced-revision candidate (manifest_rev > rcp.result_revision retaining marker)
                        lineage_uncertain = True
                elif in_hist_marker:
                    if rcp.result_revision >= manifest_rev:
                        return RecoveryConsistency.CORRUPT
                    if rcp.intended_manifest_sha256 == manifest_sha256:
                        return RecoveryConsistency.CORRUPT
                else:
                    # Committed receipt not present in current or historical manifest markers
                    if rcp.result_revision == manifest_rev:
                        if last_tx_id is not None and last_tx_id != rcp.tx_id:
                            return RecoveryConsistency.CORRUPT
                        if rcp.intended_manifest_sha256 != manifest_sha256:
                            return RecoveryConsistency.CORRUPT
                        lineage_uncertain = True
                    else:
                        if rcp.intended_manifest_sha256 == manifest_sha256:
                            return RecoveryConsistency.CORRUPT
                        lineage_uncertain = True

        if journal.active_tx is None:
            return RecoveryConsistency.UNCERTAIN if lineage_uncertain else RecoveryConsistency.CONSISTENT_IDLE

        tx = journal.active_tx
        if type(tx.expected_revision) is not int or type(tx.target_revision) is not int:
            return RecoveryConsistency.CORRUPT
        if tx.expected_revision < 1 or tx.target_revision <= tx.expected_revision:
            return RecoveryConsistency.CORRUPT
        if not tx.intended_manifest_sha256:
            return RecoveryConsistency.CORRUPT

        manifest_matches_intended = manifest_sha256 == tx.intended_manifest_sha256

        if tx.op == "delete_album":
            in_delete_marker = is_deleted is True and delete_tx_id == tx.tx_id
            in_other_markers = (last_tx_id == tx.tx_id) or (tx.tx_id in hist_list)
            if in_delete_marker:
                if tx.state in (JournalTxState.PREPARED, JournalTxState.ABORTED):
                    return RecoveryConsistency.CORRUPT
                if manifest_rev < tx.target_revision:
                    return RecoveryConsistency.CORRUPT
                if manifest_rev > tx.target_revision:
                    return RecoveryConsistency.UNCERTAIN
                if not manifest_matches_intended:
                    return RecoveryConsistency.CORRUPT
                ref_issue = cls._verify_v2_refs_against_manifest(album_data, tx.new_refs)
                if ref_issue == RecoveryConsistency.CORRUPT:
                    return RecoveryConsistency.CORRUPT
                if ref_issue == RecoveryConsistency.UNCERTAIN or lineage_uncertain:
                    return RecoveryConsistency.UNCERTAIN
                return RecoveryConsistency.PROVISIONAL_COMMIT_CANDIDATE
            if in_other_markers:
                return RecoveryConsistency.CORRUPT
            if tx.state in (JournalTxState.PREPARED, JournalTxState.ABORTED):
                if manifest_rev == tx.expected_revision and not manifest_matches_intended:
                    return RecoveryConsistency.UNCERTAIN if lineage_uncertain else RecoveryConsistency.PROVISIONAL_ABORT_CANDIDATE
                return RecoveryConsistency.CORRUPT
            if tx.state in (JournalTxState.PUBLISH_INTENT, JournalTxState.UNCERTAIN):
                return (
                    RecoveryConsistency.UNCERTAIN
                    if manifest_rev == tx.expected_revision and not manifest_matches_intended
                    else RecoveryConsistency.CORRUPT
                )
            return RecoveryConsistency.CORRUPT

        if delete_tx_id == tx.tx_id:
            return RecoveryConsistency.CORRUPT

        if last_tx_id == tx.tx_id:
            if tx.state in (JournalTxState.PREPARED, JournalTxState.ABORTED):
                return RecoveryConsistency.CORRUPT
            if manifest_rev < tx.target_revision:
                return RecoveryConsistency.CORRUPT
            if manifest_rev > tx.target_revision:
                # Advanced-revision candidate retaining active_tx marker
                return RecoveryConsistency.UNCERTAIN
            if not manifest_matches_intended:
                return RecoveryConsistency.CORRUPT
            ref_issue = cls._verify_v2_refs_against_manifest(album_data, tx.new_refs)
            if ref_issue == RecoveryConsistency.CORRUPT:
                return RecoveryConsistency.CORRUPT
            if ref_issue == RecoveryConsistency.UNCERTAIN or lineage_uncertain:
                return RecoveryConsistency.UNCERTAIN
            return RecoveryConsistency.PROVISIONAL_COMMIT_CANDIDATE

        if tx.tx_id in hist_list:
            if tx.state in (JournalTxState.PREPARED, JournalTxState.ABORTED):
                return RecoveryConsistency.CORRUPT
            idx = hist_list.index(tx.tx_id)
            trailing_journal_ops = len(hist_list) - 1 - idx
            if trailing_journal_ops < 1:
                return RecoveryConsistency.CORRUPT
            if manifest_rev >= tx.target_revision + trailing_journal_ops:
                return RecoveryConsistency.UNCERTAIN if lineage_uncertain else RecoveryConsistency.PROVISIONAL_COMMIT_CANDIDATE
            return RecoveryConsistency.CORRUPT

        if manifest_matches_intended:
            return RecoveryConsistency.CORRUPT
        if tx.state in (JournalTxState.PREPARED, JournalTxState.ABORTED):
            if manifest_rev == tx.expected_revision:
                return RecoveryConsistency.UNCERTAIN if lineage_uncertain else RecoveryConsistency.PROVISIONAL_ABORT_CANDIDATE
            return RecoveryConsistency.CORRUPT
        if tx.state in (JournalTxState.PUBLISH_INTENT, JournalTxState.UNCERTAIN):
            return (
                RecoveryConsistency.UNCERTAIN
                if manifest_rev == tx.expected_revision
                else RecoveryConsistency.CORRUPT
            )
        return RecoveryConsistency.CORRUPT

    @classmethod
    def _classify_album_consistency(
        cls,
        album_data: Optional[Dict[str, Any]],
        journal: Optional[AlbumJournal],
    ) -> RecoveryConsistency:
        """Pure provisional consistency classifier over manifest & journal data (read-only, zero cleanup)."""
        if not isinstance(album_data, dict):
            return RecoveryConsistency.CORRUPT

        album_id = album_data.get("id")
        if not isinstance(album_id, str) or not is_valid_project_id(album_id, expected_prefix="alb"):
            return RecoveryConsistency.CORRUPT

        # Enforce strict raw revision type before Pydantic coercion
        manifest_rev = album_data.get("revision")
        if type(manifest_rev) is not int or manifest_rev < 1:
            return RecoveryConsistency.CORRUPT

        last_tx_id = album_data.get("last_tx_id")
        if last_tx_id is not None:
            if not isinstance(last_tx_id, str) or not last_tx_id or last_tx_id != last_tx_id.strip():
                return RecoveryConsistency.CORRUPT
            if not all(c.isalnum() or c in "_.-" for c in last_tx_id) or last_tx_id in (".", ".."):
                return RecoveryConsistency.CORRUPT

        tx_history = album_data.get("tx_history")
        if tx_history is not None:
            if not isinstance(tx_history, list):
                return RecoveryConsistency.CORRUPT
            seen_hist: set[str] = set()
            for item in tx_history:
                if not isinstance(item, str) or not item or item != item.strip():
                    return RecoveryConsistency.CORRUPT
                if not all(c.isalnum() or c in "_.-" for c in item) or item in (".", ".."):
                    return RecoveryConsistency.CORRUPT
                if item in seen_hist:
                    return RecoveryConsistency.CORRUPT
                seen_hist.add(item)
            if tx_history:
                if last_tx_id is None or tx_history[-1] != last_tx_id:
                    return RecoveryConsistency.CORRUPT

        is_deleted = album_data.get("is_deleted")
        if is_deleted is not None and type(is_deleted) is not bool:
            return RecoveryConsistency.CORRUPT

        delete_tx_id = album_data.get("delete_tx_id")
        if delete_tx_id is not None:
            if not isinstance(delete_tx_id, str) or not delete_tx_id or delete_tx_id != delete_tx_id.strip():
                return RecoveryConsistency.CORRUPT
            if not all(c.isalnum() or c in "_.-" for c in delete_tx_id) or delete_tx_id in (".", ".."):
                return RecoveryConsistency.CORRUPT

        if is_deleted is True:
            if not delete_tx_id:
                return RecoveryConsistency.CORRUPT
            if last_tx_id is not None and delete_tx_id != last_tx_id:
                return RecoveryConsistency.CORRUPT
        elif delete_tx_id is not None:
            return RecoveryConsistency.CORRUPT

        # Validate raw manifest structure before normalization, then run full legacy-compatible AlbumMetadata validation
        try:
            cls._inspect_raw_album_manifest(album_data, album_id, protect_finalized=False)
            norm_data = dict(album_data)
            norm_tracks = cls._normalize_album_entries_shared(album_id, norm_data)
            norm_data["tracks"] = norm_tracks
            norm_data["track_ids"] = [t["track_id"] for t in norm_tracks if t.get("track_id")]
            AlbumMetadata(**norm_data)
        except Exception:
            return RecoveryConsistency.CORRUPT

        if journal is None:
            return RecoveryConsistency.CONSISTENT_IDLE

        if journal.album_id != album_id:
            return RecoveryConsistency.CORRUPT

        if journal.protocol == JournalProtocol.ENFORCED_V2:
            return cls._classify_v2_album_consistency(
                album_data=album_data,
                journal=journal,
                manifest_rev=manifest_rev,
                last_tx_id=last_tx_id,
                tx_history=tx_history,
                is_deleted=is_deleted,
                delete_tx_id=delete_tx_id,
            )

        if journal.active_tx is None:
            return RecoveryConsistency.CONSISTENT_IDLE

        tx = journal.active_tx
        if type(tx.expected_revision) is not int or type(tx.target_revision) is not int:
            return RecoveryConsistency.CORRUPT
        if tx.expected_revision < 1 or tx.target_revision <= tx.expected_revision:
            return RecoveryConsistency.CORRUPT

        hist_list = tx_history if isinstance(tx_history, list) else []

        if tx.op == "delete_album":
            in_delete_marker = (is_deleted is True and delete_tx_id == tx.tx_id)
            in_other_markers = (last_tx_id == tx.tx_id) or (tx.tx_id in hist_list)
            if in_delete_marker:
                if tx.state in (JournalTxState.PREPARED, JournalTxState.ABORTED):
                    return RecoveryConsistency.CORRUPT
                if manifest_rev == tx.target_revision:
                    return RecoveryConsistency.PROVISIONAL_COMMIT_CANDIDATE
                return RecoveryConsistency.CORRUPT
            if in_other_markers:
                return RecoveryConsistency.CORRUPT
            if tx.state in (JournalTxState.PREPARED, JournalTxState.ABORTED):
                return (
                    RecoveryConsistency.PROVISIONAL_ABORT_CANDIDATE
                    if manifest_rev == tx.expected_revision
                    else RecoveryConsistency.CORRUPT
                )
            if tx.state in (JournalTxState.PUBLISH_INTENT, JournalTxState.UNCERTAIN):
                return (
                    RecoveryConsistency.UNCERTAIN
                    if manifest_rev == tx.expected_revision
                    else RecoveryConsistency.CORRUPT
                )
            return RecoveryConsistency.CORRUPT

        # Non-deletion transaction: appearing as delete_tx_id is always contradictory
        if delete_tx_id == tx.tx_id:
            return RecoveryConsistency.CORRUPT

        if last_tx_id == tx.tx_id:
            if tx.state in (JournalTxState.PREPARED, JournalTxState.ABORTED):
                return RecoveryConsistency.CORRUPT
            if manifest_rev >= tx.target_revision:
                return RecoveryConsistency.PROVISIONAL_COMMIT_CANDIDATE
            return RecoveryConsistency.CORRUPT

        if tx.tx_id in hist_list:
            if tx.state in (JournalTxState.PREPARED, JournalTxState.ABORTED):
                return RecoveryConsistency.CORRUPT
            idx = hist_list.index(tx.tx_id)
            trailing_journal_ops = len(hist_list) - 1 - idx
            if trailing_journal_ops < 1:
                return RecoveryConsistency.CORRUPT
            if manifest_rev >= tx.target_revision + trailing_journal_ops:
                return RecoveryConsistency.PROVISIONAL_COMMIT_CANDIDATE
            return RecoveryConsistency.CORRUPT

        # tx.tx_id is absent from manifest markers
        if tx.state in (JournalTxState.PREPARED, JournalTxState.ABORTED):
            return (
                RecoveryConsistency.PROVISIONAL_ABORT_CANDIDATE
                if manifest_rev == tx.expected_revision
                else RecoveryConsistency.CORRUPT
            )
        if tx.state in (JournalTxState.PUBLISH_INTENT, JournalTxState.UNCERTAIN):
            return (
                RecoveryConsistency.UNCERTAIN
                if manifest_rev == tx.expected_revision
                else RecoveryConsistency.CORRUPT
            )
        if tx.state == JournalTxState.COMMITTED:
            return RecoveryConsistency.CORRUPT

        return RecoveryConsistency.CORRUPT

    @classmethod
    def _read_json_file_verified_in_dir(
        cls,
        dir_fd: int,
        filename: str,
        *,
        sync_file: bool = False,
        required: bool = False,
        expected_identity: Optional[Tuple[int, int]] = None,
        expected_sha256: Optional[str] = None,
    ) -> Optional[Tuple[Dict[str, Any], Tuple[int, int], str]]:
        """Read a JSON object via O_NOFOLLOW with stable (st_dev, st_ino) and optional digest/identity binding."""
        import errno
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = -1
        try:
            try:
                fd = os.open(filename, flags, dir_fd=dir_fd)
            except FileNotFoundError as exc:
                if required:
                    raise AlbumDurabilityUncertainError(
                        f"Required album file '{filename}' disappeared before verification."
                    ) from exc
                return None

            try:
                pre_st = os.fstat(fd)
            except OSError as exc:
                raise AlbumDurabilityUncertainError(f"I/O failure stating '{filename}': {exc}") from exc
            if not stat.S_ISREG(pre_st.st_mode):
                raise AlbumManifestCorruptError(f"Album file '{filename}' is not a regular file.")

            chunks: List[bytes] = []
            try:
                while True:
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                post_st = os.fstat(fd)
                lst = os.lstat(filename, dir_fd=dir_fd)
            except FileNotFoundError as exc:
                raise AlbumDurabilityUncertainError(
                    f"Album file '{filename}' disappeared during verification."
                ) from exc
            except PermissionError as exc:
                raise AlbumDurabilityUncertainError(f"Album file '{filename}' is inaccessible: {exc}") from exc
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR):
                    raise AlbumManifestCorruptError(f"Unsafe or non-regular album file '{filename}': {exc}") from exc
                raise AlbumDurabilityUncertainError(f"I/O failure reading '{filename}': {exc}") from exc

            file_id = (pre_st.st_dev, pre_st.st_ino)
            if (
                not stat.S_ISREG(lst.st_mode)
                or file_id != (post_st.st_dev, post_st.st_ino)
                or file_id != (lst.st_dev, lst.st_ino)
            ):
                raise AlbumManifestCorruptError(f"Album file '{filename}' changed identity during read.")
            if expected_identity is not None and file_id != expected_identity:
                raise AlbumManifestCorruptError(
                    f"Album file '{filename}' was replaced between validation and synchronization."
                )

            try:
                raw = json.loads(b"".join(chunks).decode("utf-8"))
                if not isinstance(raw, dict):
                    raise AlbumManifestCorruptError(f"Album file '{filename}' root must be a JSON object.")
                canonical_sha = cls._compute_canonical_manifest_sha256(raw)
            except (AlbumManifestCorruptError, AlbumDurabilityUncertainError):
                raise
            except Exception as exc:
                raise AlbumManifestCorruptError(f"Failed to parse '{filename}': {exc}") from exc

            if expected_sha256 is not None and canonical_sha != expected_sha256:
                raise AlbumManifestCorruptError(
                    f"Album file '{filename}' content changed between validation and synchronization."
                )

            if sync_file:
                try:
                    _fsync_file_fd(fd)
                except OSError as exc:
                    raise AlbumDurabilityUncertainError(
                        f"Failed to synchronize '{filename}' during recovery: {exc}"
                    ) from exc
                try:
                    re_lst = os.lstat(filename, dir_fd=dir_fd)
                except FileNotFoundError as exc:
                    raise AlbumDurabilityUncertainError(
                        f"Album file '{filename}' disappeared during synchronization."
                    ) from exc
                except OSError as exc:
                    raise AlbumDurabilityUncertainError(
                        f"I/O failure revalidating '{filename}' after sync: {exc}"
                    ) from exc
                if not stat.S_ISREG(re_lst.st_mode) or (re_lst.st_dev, re_lst.st_ino) != file_id:
                    raise AlbumManifestCorruptError(
                        f"Album file '{filename}' was replaced during synchronization."
                    )

            return raw, file_id, canonical_sha
        except (AlbumManifestCorruptError, AlbumDurabilityUncertainError):
            raise
        except PermissionError as exc:
            raise AlbumDurabilityUncertainError(f"Album file '{filename}' is inaccessible: {exc}") from exc
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR):
                raise AlbumManifestCorruptError(f"Unsafe or non-regular album file '{filename}': {exc}") from exc
            raise AlbumDurabilityUncertainError(f"I/O failure opening '{filename}': {exc}") from exc
        finally:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass

    @classmethod
    def _sync_terminal_journal_in_dir(
        cls,
        album_dir_fd: int,
        *,
        expected_journal_sha256: str,
        expected_identity: Optional[Tuple[int, int]] = None,
    ) -> Tuple[int, int]:
        """Verify journal.json matches expected validated evidence and synchronize both file and album_dir_fd."""
        res = cls._read_json_file_verified_in_dir(
            album_dir_fd,
            "journal.json",
            sync_file=True,
            required=True,
            expected_identity=expected_identity,
            expected_sha256=expected_journal_sha256,
        )
        assert res is not None
        _, file_id, _ = res
        try:
            _fsync_dir_fd(album_dir_fd)
        except OSError as exc:
            raise AlbumDurabilityUncertainError(f"Failed to synchronize album directory for terminal journal: {exc}") from exc
        try:
            lst = os.lstat("journal.json", dir_fd=album_dir_fd)
        except FileNotFoundError as exc:
            raise AlbumDurabilityUncertainError("Terminal journal 'journal.json' disappeared after directory sync.") from exc
        except OSError as exc:
            raise AlbumDurabilityUncertainError(f"Failed to revalidate 'journal.json' after directory sync: {exc}") from exc
        if not stat.S_ISREG(lst.st_mode) or (lst.st_dev, lst.st_ino) != file_id:
            raise AlbumManifestCorruptError("Terminal journal 'journal.json' was replaced during synchronization.")
        return file_id

    @staticmethod
    def _revalidate_live_artifact_binding_in_dir(
        album_dir_fd: int,
        ref: ArtifactRefRecord,
        expected_album_dir_id: Tuple[int, int],
        expected_dir_ids: List[Tuple[int, int]],
        expected_leaf_id: Tuple[int, int],
        *,
        open_leaf_fd: int = -1,
    ) -> None:
        """Revalidate ancestor directory attachment and leaf inode/size against retained evidence."""
        import errno
        parts = ref.rel_path.split("/")
        dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        re_opened_dirs: List[int] = []
        try:
            try:
                re_album_st = os.fstat(album_dir_fd)
            except OSError as exc:
                raise AlbumDurabilityUncertainError(
                    f"I/O failure revalidating album directory for '{ref.rel_path}': {exc}"
                ) from exc
            if (re_album_st.st_dev, re_album_st.st_ino) != expected_album_dir_id:
                raise AlbumManifestCorruptError(
                    f"Album directory changed identity during verification of '{ref.rel_path}'."
                )

            re_cur_fd = album_dir_fd
            for idx, seg in enumerate(parts[:-1]):
                try:
                    rnxt = os.open(seg, dir_flags, dir_fd=re_cur_fd)
                except FileNotFoundError as exc:
                    raise AlbumDurabilityUncertainError(
                        f"Ancestor directory '{seg}' disappeared after sync for '{ref.rel_path}'."
                    ) from exc
                except PermissionError as exc:
                    raise AlbumDurabilityUncertainError(
                        f"Ancestor directory '{seg}' became inaccessible after sync for '{ref.rel_path}': {exc}"
                    ) from exc
                except OSError as exc:
                    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                        raise AlbumManifestCorruptError(
                            f"Ancestor directory '{seg}' became unsafe after sync for '{ref.rel_path}': {exc}"
                        ) from exc
                    raise AlbumDurabilityUncertainError(
                        f"I/O failure revalidating '{seg}' after sync for '{ref.rel_path}': {exc}"
                    ) from exc
                re_opened_dirs.append(rnxt)
                try:
                    rdst = os.fstat(rnxt)
                except OSError as exc:
                    raise AlbumDurabilityUncertainError(
                        f"I/O failure stating ancestor directory '{seg}' after sync for '{ref.rel_path}': {exc}"
                    ) from exc
                if (rdst.st_dev, rdst.st_ino) != expected_dir_ids[idx]:
                    raise AlbumManifestCorruptError(
                        f"Ancestor directory '{seg}' was substituted during synchronization of '{ref.rel_path}'."
                    )
                re_cur_fd = rnxt

            leaf = parts[-1]
            try:
                re_lst = os.lstat(leaf, dir_fd=re_cur_fd)
                re_fst = os.fstat(open_leaf_fd) if open_leaf_fd >= 0 else None
            except FileNotFoundError as exc:
                raise AlbumDurabilityUncertainError(
                    f"Live artifact '{ref.rel_path}' disappeared after synchronization."
                ) from exc
            except PermissionError as exc:
                raise AlbumDurabilityUncertainError(
                    f"Live artifact '{ref.rel_path}' became inaccessible after synchronization: {exc}"
                ) from exc
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.EISDIR, errno.ENOTDIR):
                    raise AlbumManifestCorruptError(
                        f"Live artifact '{ref.rel_path}' became unsafe after synchronization: {exc}"
                    ) from exc
                raise AlbumDurabilityUncertainError(
                    f"I/O failure revalidating '{ref.rel_path}' after synchronization: {exc}"
                ) from exc

            if (
                not stat.S_ISREG(re_lst.st_mode)
                or (re_lst.st_dev, re_lst.st_ino) != expected_leaf_id
                or re_lst.st_size != ref.size_bytes
                or (
                    re_fst is not None
                    and ((re_fst.st_dev, re_fst.st_ino) != expected_leaf_id or re_fst.st_size != ref.size_bytes)
                )
            ):
                raise AlbumManifestCorruptError(
                    f"Live artifact '{ref.rel_path}' was substituted or altered during synchronization."
                )
        finally:
            for dfd in reversed(re_opened_dirs):
                try:
                    os.close(dfd)
                except OSError:
                    pass

    @classmethod
    def _verify_and_sync_live_artifact_in_dir(
        cls,
        album_dir_fd: int,
        ref: ArtifactRefRecord,
    ) -> Tuple[Tuple[int, int], List[Tuple[int, int]], Tuple[int, int]]:
        """Verify live artifact via O_NOFOLLOW traversal, stable inode, size, SHA-256, child-to-parent fsync, and return retained binding evidence."""
        import errno
        parts = ref.rel_path.split("/")
        dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        opened_dirs: List[int] = []
        dir_identities: List[Tuple[int, int]] = []
        cur_dir_fd = album_dir_fd
        ffd = -1
        try:
            try:
                album_st = os.fstat(album_dir_fd)
            except OSError as exc:
                raise AlbumDurabilityUncertainError(f"I/O failure stating album directory for '{ref.rel_path}': {exc}") from exc
            album_dir_id = (album_st.st_dev, album_st.st_ino)

            for seg in parts[:-1]:
                try:
                    nxt = os.open(seg, dir_flags, dir_fd=cur_dir_fd)
                except FileNotFoundError as exc:
                    raise AlbumDurabilityUncertainError(
                        f"Missing ancestor directory '{seg}' for artifact '{ref.rel_path}'."
                    ) from exc
                except PermissionError as exc:
                    raise AlbumDurabilityUncertainError(
                        f"Inaccessible ancestor directory '{seg}' for artifact '{ref.rel_path}': {exc}"
                    ) from exc
                except OSError as exc:
                    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                        raise AlbumManifestCorruptError(
                            f"Unsafe or non-directory ancestor '{seg}' for artifact '{ref.rel_path}': {exc}"
                        ) from exc
                    raise AlbumDurabilityUncertainError(
                        f"I/O failure traversing '{seg}' for artifact '{ref.rel_path}': {exc}"
                    ) from exc
                opened_dirs.append(nxt)
                try:
                    dst = os.fstat(nxt)
                except OSError as exc:
                    raise AlbumDurabilityUncertainError(
                        f"I/O failure stating ancestor directory '{seg}' for artifact '{ref.rel_path}': {exc}"
                    ) from exc
                dir_identities.append((dst.st_dev, dst.st_ino))
                cur_dir_fd = nxt

            leaf = parts[-1]
            try:
                ffd = os.open(leaf, file_flags, dir_fd=cur_dir_fd)
            except FileNotFoundError as exc:
                raise AlbumDurabilityUncertainError(f"Missing live artifact '{ref.rel_path}'.") from exc
            except PermissionError as exc:
                raise AlbumDurabilityUncertainError(f"Inaccessible live artifact '{ref.rel_path}': {exc}") from exc
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.EISDIR, errno.ENOTDIR):
                    raise AlbumManifestCorruptError(
                        f"Unsafe or non-regular live artifact '{ref.rel_path}': {exc}"
                    ) from exc
                raise AlbumDurabilityUncertainError(f"I/O failure opening '{ref.rel_path}': {exc}") from exc

            try:
                pre_st = os.fstat(ffd)
            except OSError as exc:
                raise AlbumDurabilityUncertainError(f"I/O failure stating '{ref.rel_path}': {exc}") from exc
            if not stat.S_ISREG(pre_st.st_mode):
                raise AlbumManifestCorruptError(f"Live artifact '{ref.rel_path}' is not a regular file.")

            h = hashlib.sha256()
            total_bytes = 0
            try:
                while True:
                    chunk = os.read(ffd, 65536)
                    if not chunk:
                        break
                    h.update(chunk)
                    total_bytes += len(chunk)
                post_st = os.fstat(ffd)
                lst = os.lstat(leaf, dir_fd=cur_dir_fd)
            except FileNotFoundError as exc:
                raise AlbumDurabilityUncertainError(f"Live artifact '{ref.rel_path}' disappeared during read.") from exc
            except PermissionError as exc:
                raise AlbumDurabilityUncertainError(f"Inaccessible live artifact '{ref.rel_path}': {exc}") from exc
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.EISDIR, errno.ENOTDIR):
                    raise AlbumManifestCorruptError(
                        f"Unsafe or non-regular live artifact '{ref.rel_path}': {exc}"
                    ) from exc
                raise AlbumDurabilityUncertainError(f"I/O failure verifying '{ref.rel_path}': {exc}") from exc

            leaf_id = (pre_st.st_dev, pre_st.st_ino)
            if (
                not stat.S_ISREG(lst.st_mode)
                or leaf_id != (post_st.st_dev, post_st.st_ino)
                or leaf_id != (lst.st_dev, lst.st_ino)
            ):
                raise AlbumManifestCorruptError(f"Live artifact '{ref.rel_path}' changed identity during verification.")

            if total_bytes != ref.size_bytes or h.hexdigest() != ref.sha256:
                raise AlbumManifestCorruptError(
                    f"Live artifact '{ref.rel_path}' size/SHA-256 mismatch (expected {ref.size_bytes}B/{ref.sha256}, observed {total_bytes}B/{h.hexdigest()})."
                )

            try:
                _fsync_file_fd(ffd)
            except OSError as exc:
                raise AlbumDurabilityUncertainError(
                    f"Failed to synchronize live artifact '{ref.rel_path}': {exc}"
                ) from exc

            for dfd in reversed(opened_dirs):
                try:
                    _fsync_dir_fd(dfd)
                except OSError as exc:
                    raise AlbumDurabilityUncertainError(
                        f"Failed to synchronize parent directory for '{ref.rel_path}': {exc}"
                    ) from exc
            try:
                _fsync_dir_fd(album_dir_fd)
            except OSError as exc:
                raise AlbumDurabilityUncertainError(
                    f"Failed to synchronize album directory for '{ref.rel_path}': {exc}"
                ) from exc

            cls._revalidate_live_artifact_binding_in_dir(
                album_dir_fd,
                ref,
                album_dir_id,
                dir_identities,
                leaf_id,
                open_leaf_fd=ffd,
            )
            return album_dir_id, dir_identities, leaf_id
        finally:
            if ffd >= 0:
                try:
                    os.close(ffd)
                except OSError:
                    pass
            for dfd in reversed(opened_dirs):
                try:
                    os.close(dfd)
                except OSError:
                    pass

    @staticmethod
    def _check_live_artifact_absent_in_dir(album_dir_fd: int, ref: ArtifactRefRecord) -> None:
        """Ensure a PREPARED transaction's target path does not already exist on disk (no overwrite/adoption)."""
        import errno
        parts = ref.rel_path.split("/")
        dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        opened_dirs: List[int] = []
        cur_dir_fd = album_dir_fd
        try:
            for seg in parts[:-1]:
                try:
                    nxt = os.open(seg, dir_flags, dir_fd=cur_dir_fd)
                except FileNotFoundError:
                    return
                except PermissionError as exc:
                    raise AlbumDurabilityUncertainError(
                        f"Cannot verify absence of '{ref.rel_path}' due to inaccessible directory '{seg}': {exc}"
                    ) from exc
                except OSError as exc:
                    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                        raise AlbumManifestCorruptError(
                            f"Unsafe ancestor directory '{seg}' checking '{ref.rel_path}': {exc}"
                        ) from exc
                    raise AlbumDurabilityUncertainError(f"I/O failure checking '{ref.rel_path}': {exc}") from exc
                opened_dirs.append(nxt)
                cur_dir_fd = nxt

            try:
                os.lstat(parts[-1], dir_fd=cur_dir_fd)
            except FileNotFoundError:
                return
            except PermissionError as exc:
                raise AlbumDurabilityUncertainError(
                    f"Cannot verify absence of '{ref.rel_path}': {exc}"
                ) from exc
            except OSError as exc:
                raise AlbumDurabilityUncertainError(
                    f"I/O failure checking absence of '{ref.rel_path}': {exc}"
                ) from exc
            raise AlbumDurabilityUncertainError(
                f"Destination '{ref.rel_path}' already exists during PREPARED transaction; refusing to abort or overwrite."
            )
        finally:
            for dfd in reversed(opened_dirs):
                try:
                    os.close(dfd)
                except OSError:
                    pass

    @staticmethod
    def _build_reconciled_journal(
        journal: AlbumJournal,
        receipt: TerminalTxReceipt,
        *,
        add_retained: bool = False,
    ) -> AlbumJournal:
        """Build updated v2 AlbumJournal with appended TerminalTxReceipt and cleared active_tx."""
        updated_idem: List[IdempotencyRecord] = []
        matched_idem = False
        for rec in journal.idempotency_records:
            if rec.tx_id == receipt.tx_id:
                matched_idem = True
                updated_idem.append(
                    IdempotencyRecord(
                        key=rec.key,
                        op=receipt.op,
                        expected_revision=receipt.expected_revision,
                        payload_sha256=receipt.payload_sha256,
                        result_revision=receipt.result_revision if receipt.outcome == JournalTxState.COMMITTED else None,
                        tx_id=receipt.tx_id,
                        outcome=receipt.outcome,
                        created_at=rec.created_at,
                    )
                )
            else:
                updated_idem.append(rec)
        if receipt.idempotency_key is not None and not matched_idem:
            updated_idem.append(
                IdempotencyRecord(
                    key=receipt.idempotency_key,
                    op=receipt.op,
                    expected_revision=receipt.expected_revision,
                    payload_sha256=receipt.payload_sha256,
                    result_revision=receipt.result_revision if receipt.outcome == JournalTxState.COMMITTED else None,
                    tx_id=receipt.tx_id,
                    outcome=receipt.outcome,
                )
            )

        updated_retained = list(journal.retained_artifacts)
        if add_retained and receipt.superseded_refs:
            existing_ret_paths = {r.rel_path for r in updated_retained}
            for sup in receipt.superseded_refs:
                if sup.rel_path not in existing_ret_paths:
                    updated_retained.append(
                        RetainedArtifactRecord(
                            rel_path=sup.rel_path,
                            sha256=sup.sha256,
                            kind=sup.role.value,
                            generation=sup.generation,
                            superseded_by_tx_id=receipt.tx_id,
                            superseded_at_rev=receipt.result_revision,
                        )
                    )
                    existing_ret_paths.add(sup.rel_path)

        return AlbumJournal(
            schema_version=2,
            protocol=JournalProtocol.ENFORCED_V2,
            album_id=journal.album_id,
            active_tx=None,
            tx_receipts=[*journal.tx_receipts, receipt],
            retained_artifacts=updated_retained,
            idempotency_records=updated_idem,
        )

    def _reconcile_album_durability_locked(
        self,
        user_id: str,
        album_id: str,
        album_dir_fd: int,
    ) -> Dict[str, Any]:
        """Authoritative storage-layer recovery coordinator (must be called under _album_lock)."""
        album_loaded = self._read_json_file_verified_in_dir(album_dir_fd, "album.json", sync_file=False, required=False)
        if album_loaded is None:
            raise AlbumManifestCorruptError(f"Album manifest 'album.json' is missing for '{album_id}'.")
        album_data, album_identity, album_sha256 = album_loaded

        journal_loaded = self._read_json_file_verified_in_dir(album_dir_fd, "journal.json", sync_file=False, required=False)
        journal: Optional[AlbumJournal] = None
        journal_identity: Optional[Tuple[int, int]] = None
        journal_sha256: Optional[str] = None
        if journal_loaded is not None:
            journal_raw, journal_identity, journal_sha256 = journal_loaded
            try:
                journal = AlbumJournal(**journal_raw)
                if journal.album_id != album_id:
                    raise AlbumManifestCorruptError(
                        f"Album journal identity mismatch (expected '{album_id}', found '{journal.album_id}')."
                    )
            except AlbumManifestCorruptError:
                raise
            except Exception as exc:
                raise AlbumManifestCorruptError(
                    f"Album journal failed validation; preserving all artifacts: {exc}"
                ) from exc

        consistency = self._classify_album_consistency(album_data, journal)
        if consistency == RecoveryConsistency.CORRUPT:
            raise AlbumManifestCorruptError(f"Album '{album_id}' failed recovery consistency verification.")

        manifest_rev = album_data["revision"]
        last_tx_id = album_data.get("last_tx_id")
        delete_tx_id = album_data.get("delete_tx_id")
        is_deleted = album_data.get("is_deleted") is True

        # Journal-less legacy Album: leave untouched
        if journal is None:
            return {
                "outcome": "consistent_idle",
                "protocol": None,
                "tx_id": None,
                "revision": manifest_rev,
            }

        assert journal_sha256 is not None

        # Legacy v1 journal: never auto-migrate or promote provisional classifications
        if journal.protocol == JournalProtocol.LEGACY_PROVISIONAL:
            if journal.active_tx is None and consistency == RecoveryConsistency.CONSISTENT_IDLE:
                self._sync_terminal_journal_in_dir(
                    album_dir_fd,
                    expected_journal_sha256=journal_sha256,
                    expected_identity=journal_identity,
                )
                return {
                    "outcome": "consistent_idle",
                    "protocol": journal.protocol.value,
                    "tx_id": None,
                    "revision": manifest_rev,
                }
            raise AlbumDurabilityUncertainError(
                f"Legacy v1 journal for album '{album_id}' has provisional state '{consistency.value}' and cannot be authoritatively reconciled."
            )

        # v2 enforced protocol
        if journal.active_tx is None:
            if consistency == RecoveryConsistency.UNCERTAIN:
                raise AlbumDurabilityUncertainError(
                    f"Album '{album_id}' journal has uncertain lineage/artifact evidence."
                )
            idle_bindings: List[Tuple[ArtifactRefRecord, Tuple[int, int], List[Tuple[int, int]], Tuple[int, int]]] = []
            for rcp in journal.tx_receipts:
                if (
                    rcp.outcome == JournalTxState.COMMITTED
                    and rcp.result_revision == manifest_rev
                    and (rcp.tx_id == last_tx_id or (is_deleted and rcp.tx_id == delete_tx_id))
                ):
                    for ref in rcp.new_refs:
                        album_dir_id, dir_ids, leaf_id = self._verify_and_sync_live_artifact_in_dir(album_dir_fd, ref)
                        idle_bindings.append((ref, album_dir_id, dir_ids, leaf_id))
            self._read_json_file_verified_in_dir(
                album_dir_fd,
                "album.json",
                sync_file=True,
                required=True,
                expected_identity=album_identity,
                expected_sha256=album_sha256,
            )
            for ref, album_dir_id, dir_ids, leaf_id in idle_bindings:
                self._revalidate_live_artifact_binding_in_dir(album_dir_fd, ref, album_dir_id, dir_ids, leaf_id)
            self._sync_terminal_journal_in_dir(
                album_dir_fd,
                expected_journal_sha256=journal_sha256,
                expected_identity=journal_identity,
            )
            return {
                "outcome": "consistent_idle",
                "protocol": journal.protocol.value,
                "tx_id": None,
                "revision": manifest_rev,
            }

        tx = journal.active_tx
        if tx.state in (JournalTxState.PREPARED, JournalTxState.ABORTED):
            if consistency == RecoveryConsistency.UNCERTAIN:
                raise AlbumDurabilityUncertainError(f"Album '{album_id}' PREPARED transaction '{tx.tx_id}' has uncertain evidence.")
            if consistency != RecoveryConsistency.PROVISIONAL_ABORT_CANDIDATE:
                raise AlbumManifestCorruptError(f"Album '{album_id}' PREPARED transaction '{tx.tx_id}' contradicts manifest.")
            for ref in tx.new_refs:
                self._check_live_artifact_absent_in_dir(album_dir_fd, ref)
            self._read_json_file_verified_in_dir(
                album_dir_fd,
                "album.json",
                sync_file=True,
                required=True,
                expected_identity=album_identity,
                expected_sha256=album_sha256,
            )
            try:
                _fsync_dir_fd(album_dir_fd)
            except OSError as exc:
                raise AlbumDurabilityUncertainError(f"Directory fsync failed during PREPARED abort for '{tx.tx_id}': {exc}") from exc

            receipt = TerminalTxReceipt(
                tx_id=tx.tx_id,
                op=tx.op,
                outcome=JournalTxState.ABORTED,
                expected_revision=tx.expected_revision,
                result_revision=tx.expected_revision,
                payload_sha256=tx.payload_sha256,
                intended_manifest_sha256=tx.intended_manifest_sha256,
                new_refs=tx.new_refs,
                superseded_refs=tx.superseded_refs,
                idempotency_key=tx.idempotency_key,
            )
            new_journal = self._build_reconciled_journal(journal, receipt, add_retained=False)
            new_journal_sha256 = self._compute_canonical_manifest_sha256(new_journal.model_dump(mode="json"))
            try:
                self._write_album_journal_atomic_in_dir(album_dir_fd, new_journal)
                self._sync_terminal_journal_in_dir(album_dir_fd, expected_journal_sha256=new_journal_sha256)
            except ManifestPublishError as exc:
                raise AlbumDurabilityUncertainError(f"Terminal ABORTED receipt publication uncertain for '{tx.tx_id}': {exc}") from exc
            return {
                "outcome": "aborted",
                "protocol": journal.protocol.value,
                "tx_id": tx.tx_id,
                "revision": tx.expected_revision,
            }

        # PUBLISH_INTENT / UNCERTAIN / COMMITTED
        if consistency == RecoveryConsistency.UNCERTAIN:
            raise AlbumDurabilityUncertainError(
                f"Album '{album_id}' transaction '{tx.tx_id}' lacks sufficient target-publication proof."
            )
        if consistency != RecoveryConsistency.PROVISIONAL_COMMIT_CANDIDATE:
            raise AlbumManifestCorruptError(f"Album '{album_id}' transaction '{tx.tx_id}' contradicts manifest.")

        # Independently verify exact-current publication proof (never promote historical candidates)
        current_marker_matches = (
            (is_deleted and delete_tx_id == tx.tx_id)
            if tx.op == "delete_album"
            else (not is_deleted and last_tx_id == tx.tx_id)
        )
        if manifest_rev != tx.target_revision or not current_marker_matches:
            raise AlbumDurabilityUncertainError(
                f"Album '{album_id}' transaction '{tx.tx_id}' is historical or superseded (manifest rev {manifest_rev}, target {tx.target_revision}); refusing authoritative COMMITTED promotion."
            )
        if album_sha256 != tx.intended_manifest_sha256:
            raise AlbumManifestCorruptError(
                f"Album '{album_id}' manifest digest does not match intended digest for '{tx.tx_id}'."
            )
        ref_binding = self._verify_v2_refs_against_manifest(album_data, tx.new_refs)
        if ref_binding == RecoveryConsistency.CORRUPT:
            raise AlbumManifestCorruptError(f"Album '{album_id}' artifact bindings contradict manifest for '{tx.tx_id}'.")
        if ref_binding == RecoveryConsistency.UNCERTAIN:
            raise AlbumDurabilityUncertainError(f"Album '{album_id}' artifact bindings are incomplete for '{tx.tx_id}'.")

        verified_bindings: List[Tuple[ArtifactRefRecord, Tuple[int, int], List[Tuple[int, int]], Tuple[int, int]]] = []
        for ref in tx.new_refs:
            album_dir_id, dir_ids, leaf_id = self._verify_and_sync_live_artifact_in_dir(album_dir_fd, ref)
            verified_bindings.append((ref, album_dir_id, dir_ids, leaf_id))

        # Revalidate and synchronize album.json against validated identity/digest
        self._read_json_file_verified_in_dir(
            album_dir_fd,
            "album.json",
            sync_file=True,
            required=True,
            expected_identity=album_identity,
            expected_sha256=album_sha256,
        )
        try:
            _fsync_dir_fd(album_dir_fd)
        except OSError as exc:
            raise AlbumDurabilityUncertainError(f"Directory fsync failed during COMMITTED recovery for '{tx.tx_id}': {exc}") from exc

        # Coordinated final verification of all required artifact & ancestor bindings after manifest/dir sync
        # and before terminal receipt publication. Concurrency boundary: protects against any substitution
        # observable prior to this final check; external writers after this point are excluded by _album_lock
        # and immutable-storage assumptions.
        for ref, album_dir_id, dir_ids, leaf_id in verified_bindings:
            self._revalidate_live_artifact_binding_in_dir(album_dir_fd, ref, album_dir_id, dir_ids, leaf_id)

        receipt = TerminalTxReceipt(
            tx_id=tx.tx_id,
            op=tx.op,
            outcome=JournalTxState.COMMITTED,
            expected_revision=tx.expected_revision,
            result_revision=tx.target_revision,
            payload_sha256=tx.payload_sha256,
            intended_manifest_sha256=tx.intended_manifest_sha256,
            new_refs=tx.new_refs,
            superseded_refs=tx.superseded_refs,
            idempotency_key=tx.idempotency_key,
        )
        new_journal = self._build_reconciled_journal(journal, receipt, add_retained=True)
        new_journal_sha256 = self._compute_canonical_manifest_sha256(new_journal.model_dump(mode="json"))
        try:
            self._write_album_journal_atomic_in_dir(album_dir_fd, new_journal)
            self._sync_terminal_journal_in_dir(album_dir_fd, expected_journal_sha256=new_journal_sha256)
        except ManifestPublishError as exc:
            raise AlbumDurabilityUncertainError(f"Terminal COMMITTED receipt publication uncertain for '{tx.tx_id}': {exc}") from exc
        return {
            "outcome": "committed",
            "protocol": journal.protocol.value,
            "tx_id": tx.tx_id,
            "revision": tx.target_revision,
        }

    def reconcile_album_durability(self, user_id: str, album_id: str) -> Optional[Dict[str, Any]]:
        """Explicitly reconcile and synchronize durability for an Album under _album_lock."""
        try:
            self._get_album_dir_path(user_id, album_id)
        except ValueError:
            return None

        with self._album_lock(user_id, album_id):
            album_dir_fd = self._open_album_dir_fd_nofollow(user_id, album_id)
            if album_dir_fd is None:
                return None
            try:
                return self._reconcile_album_durability_locked(user_id, album_id, album_dir_fd)
            finally:
                try:
                    os.close(album_dir_fd)
                except OSError:
                    pass

    @staticmethod
    def _cleanup_verified_staging_refs(
        album_dir_fd: int,
        staging_refs: List[StagedArtifactRef],
    ) -> List[str]:
        """Physical staging cleanup is intentionally deferred from Stage 1.1; preserve all staging evidence."""
        return []


# Global singleton instance
project_storage_manager = ProjectStorageManager()
