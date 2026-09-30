"""Persistent file and workspace storage manager for MP3Projects and MP3MetaManager.

Implements Option A: Self-contained Track & Album Workspaces with decoupled
cryptographic path hashing and strict POSIX 0700 filesystem isolation.
"""

import os
import time
import shutil
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

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
    is_valid_project_id,
    get_tenant_storage_dir_name,
    get_track_dir_name,
    get_take_dir_name,
    get_album_dir_name,
)
from backend.project_models import (
    WorkspaceStatus,
    StemRole,
    LyricsFormat,
    StemMetadata,
    TakeMetadata,
    TrackMetadata,
    AlbumTrackEntry,
    AlbumMetadata,
    StorageQuotaStats,
)
from backend.audio_formats import AUDIO_FORMATS

logger = logging.getLogger("mp3metafix.project_storage")


class ProjectStorageError(Exception):
    """Base exception for project storage operations."""
    pass


class ProjectStorageQuotaExceeded(ProjectStorageError):
    """Raised when an operation would exceed storage quota."""
    pass


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

    def update_track(
        self,
        user_id: str,
        track_id: str,
        updates: Dict[str, Any],
    ) -> Optional[TrackMetadata]:
        """Update fields in track workspace manifest."""
        track = self.get_track(user_id, track_id)
        if not track:
            return None

        track_dir = self._get_track_dir(user_id, track_id)
        manifest_path = track_dir / "track.json"

        data = track.model_dump()
        for k, v in updates.items():
            if v is not None and k in data and k not in ("id", "created_at", "takes_count"):
                data[k] = v
        data["updated_at"] = time.time()

        with file_lock(manifest_path):
            atomic_json(manifest_path, data)

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

        now = time.time()
        take_label = label.strip() if label else f"Take {track.takes_count + 1}"
        take_meta = TakeMetadata(
            id=take_id,
            label=take_label,
            filename=clean_filename,
            format=extension.lstrip(".").lower(),
            size_bytes=len(audio_bytes),
            duration_seconds=duration_seconds,
            is_master=is_master,
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

        # If marked as master or if this is the first take, update track's primary_take_id
        if is_master or track.primary_take_id is None:
            self.update_track(user_id, track_id, {"primary_take_id": take_id})

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
    ) -> Optional[TakeMetadata]:
        """Update take metadata (label, prompt, notes, is_master)."""
        take = self.get_take(user_id, track_id, take_id)
        if not take:
            return None

        take_dir = self._get_take_dir(user_id, track_id, take_id)
        manifest_path = take_dir / "take.json"

        data = take.model_dump()
        for k, v in updates.items():
            if v is not None and k in data and k not in ("id", "created_at", "filename", "format", "size_bytes"):
                data[k] = v
        data["updated_at"] = time.time()

        with file_lock(manifest_path):
            atomic_json(manifest_path, data)

        # If is_master was toggled True, update track's primary_take_id and unset on sibling takes
        if updates.get("is_master") is True:
            self.update_track(user_id, track_id, {"primary_take_id": take_id})
            # Clear is_master on sibling takes
            all_takes = self.list_takes(user_id, track_id)
            for other_take in all_takes:
                if other_take.id != take_id and other_take.is_master:
                    sibling_dir = self._get_take_dir(user_id, track_id, other_take.id)
                    s_manifest = sibling_dir / "take.json"
                    s_data = other_take.model_dump()
                    s_data["is_master"] = False
                    with file_lock(s_manifest):
                        atomic_json(s_manifest, s_data)

        return TakeMetadata(**data)

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
    ) -> TakeMetadata:
        """Update an existing take's audio file and metadata in place."""
        take = self.get_take(user_id, track_id, take_id)
        if not take:
            raise ValueError(f"Take {take_id} not found in track {track_id}")

        size_diff = len(audio_bytes) - take.size_bytes
        if size_diff > 0:
            self.ensure_quota_available(user_id, size_diff)

        take_dir = self._get_take_dir(user_id, track_id, take_id)
        fmt = extension.lstrip(".").lower()
        if fmt not in AUDIO_FORMATS:
            fmt = "mp3"

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
        duration = take.duration_seconds
        try:
            import mutagen
            audio = mutagen.File(str(audio_dest))
            if audio is not None and audio.info is not None:
                duration = round(float(getattr(audio.info, "length", 0.0)), 2)
        except Exception:
            pass

        data = take.model_dump()
        data["format"] = fmt
        data["size_bytes"] = len(audio_bytes)
        data["duration_seconds"] = duration
        if label:
            data["label"] = label
        if notes is not None:
            data["notes"] = notes
        if is_master is not None:
            data["is_master"] = is_master
        data["updated_at"] = time.time()

        manifest_path = take_dir / "take.json"
        with file_lock(manifest_path):
            atomic_json(manifest_path, data)

        if is_master:
            self.update_track(user_id, track_id, {"primary_take_id": take_id})

        return TakeMetadata(**data)

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
            shutil.rmtree(take_dir, ignore_errors=True)
            # If deleted take was the primary_take_id, reset or pick another take
            track = self.get_track(user_id, track_id)
            if track and track.primary_take_id == take_id:
                remaining = self.list_takes(user_id, track_id)
                new_primary = remaining[-1].id if remaining else None
                self.update_track(user_id, track_id, {"primary_take_id": new_primary})
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

    # --- Album Workspace Management ---

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
        album_id = generate_album_id()
        album_dir = self._get_album_dir(user_id, album_id)
        self._ensure_dir_0700(album_dir)

        now = time.time()
        album_meta = AlbumMetadata(
            id=album_id,
            title=title.strip(),
            album_artist=album_artist.strip() if album_artist else None,
            year=year,
            genre=genre.strip() if genre else None,
            credits=credits,
            status=status,
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
            import json
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Check if cover image exists
            data["has_cover"] = (album_dir / "cover.jpg").is_file() or (album_dir / "cover.png").is_file()
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
                        import json
                        with open(manifest_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        data["has_cover"] = (item / "cover.jpg").is_file() or (item / "cover.png").is_file()
                        meta = AlbumMetadata(**data)
                        if status is None or meta.status == status:
                            results.append(meta)
                    except Exception as e:
                        logger.warning(f"Skipping corrupted album {item.name}: {e}")

        results.sort(key=lambda a: a.updated_at, reverse=True)
        return results

    def update_album(
        self,
        user_id: str,
        album_id: str,
        updates: Dict[str, Any],
    ) -> Optional[AlbumMetadata]:
        """Update album info or sequenced tracklist."""
        album = self.get_album(user_id, album_id)
        if not album:
            return None

        album_dir = self._get_album_dir(user_id, album_id)
        manifest_path = album_dir / "album.json"

        data = album.model_dump()
        for k, v in updates.items():
            if v is not None and k not in ("id", "created_at", "has_cover"):
                data[k] = v

        # Normalize tracks if tracks or track_ids updated
        if "tracks" in updates and updates["tracks"] is not None:
            raw_tracks = updates["tracks"]
            norm_tracks = []
            for i, item in enumerate(raw_tracks):
                if isinstance(item, dict):
                    norm_tracks.append({
                        "track_number": item.get("track_number", i + 1),
                        "disc_number": item.get("disc_number", 1),
                        "track_id": item["track_id"],
                        "take_id": item.get("take_id"),
                        "custom_title": item.get("custom_title"),
                    })
                elif hasattr(item, "model_dump"):
                    norm_tracks.append(item.model_dump())
            data["tracks"] = norm_tracks
            data["track_ids"] = [t["track_id"] for t in norm_tracks]
        elif "track_ids" in updates and updates["track_ids"] is not None:
            data["track_ids"] = list(updates["track_ids"])
            data["tracks"] = [
                {"track_number": i + 1, "disc_number": 1, "track_id": tid, "take_id": None, "custom_title": None}
                for i, tid in enumerate(updates["track_ids"])
            ]

        data["updated_at"] = time.time()

        with file_lock(manifest_path):
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
