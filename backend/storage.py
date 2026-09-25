"""Session-based temporary file storage and lifecycle manager."""

import os
import time
import shutil
import uuid
import json
import logging
import asyncio
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
from backend.config import (
    TEMP_DIR,
    SESSION_TTL_MINUTES,
    MAX_GLOBAL_TEMP_STORAGE_BYTES,
    MAX_SESSIONS,
    get_runtime_session_ttl_seconds,
    get_runtime_global_storage_bytes,
    get_runtime_max_sessions,
)
from backend.security import sanitize_filename, get_storage_dir_name
from backend.audio_formats import AUDIO_FORMATS

logger = logging.getLogger("mp3metafix.storage")

class SessionManager:
    """Manages isolated session directories and automated TTL lifecycle cleanup."""

    def __init__(
        self,
        temp_dir: Path = TEMP_DIR,
        ttl_minutes: Optional[int] = None,
        max_storage_bytes: Optional[int] = None,
        max_sessions: Optional[int] = None,
    ):
        self.temp_dir = temp_dir
        self._override_ttl_seconds = (ttl_minutes * 60) if ttl_minutes is not None else None
        self._override_max_storage_bytes = max_storage_bytes
        self._override_max_sessions = max_sessions
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.temp_dir, 0o700)
        except Exception:
            pass

    @property
    def max_storage_bytes(self) -> int:
        if self._override_max_storage_bytes is not None:
            return self._override_max_storage_bytes
        return get_runtime_global_storage_bytes()

    @max_storage_bytes.setter
    def max_storage_bytes(self, value: int):
        self._override_max_storage_bytes = value

    @property
    def ttl_seconds(self) -> int:
        if self._override_ttl_seconds is not None:
            return self._override_ttl_seconds
        return get_runtime_session_ttl_seconds()

    @ttl_seconds.setter
    def ttl_seconds(self, value: int):
        self._override_ttl_seconds = value

    @property
    def max_sessions(self) -> int:
        if self._override_max_sessions is not None:
            return self._override_max_sessions
        return get_runtime_max_sessions()

    @max_sessions.setter
    def max_sessions(self, value: int):
        self._override_max_sessions = value

    def count_active_sessions(self) -> int:
        """Count active sessions with valid session metadata in data/temp."""
        count = 0
        if self.temp_dir.exists():
            for item in self.temp_dir.iterdir():
                if item.is_dir() and (item / "session.json").is_file():
                    count += 1
        return count

    def get_total_temp_size_bytes(self) -> int:
        """Calculate total disk space consumed by all sessions in data/temp."""
        total = 0
        if not self.temp_dir.exists():
            return 0
        try:
            for dirpath, _, filenames in os.walk(self.temp_dir):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    try:
                        total += os.path.getsize(fp)
                    except OSError:
                        pass
        except Exception as e:
            logger.warning(f"Could not calculate temp storage size: {e}")
        return total

    def get_session_stats(self) -> Dict[str, Any]:
        """Return aggregated session storage metrics for system diagnostics."""
        total_bytes = self.get_total_temp_size_bytes()
        count = self.count_active_sessions()
        max_storage = self.max_storage_bytes
        return {
            "temp_storage_bytes": total_bytes,
            "temp_storage_mb": round(total_bytes / (1024 * 1024), 2),
            "max_temp_storage_bytes": max_storage,
            "max_temp_storage_mb": round(max_storage / (1024 * 1024), 2),
            "temp_storage_used_percent": round((total_bytes / max_storage * 100), 1) if max_storage > 0 else 0.0,
            "active_sessions_count": count,
            "max_sessions": self.max_sessions,
        }

    def ensure_storage_available(self, required_bytes: int = 0) -> bool:
        """Verify storage quota and session count limits; trigger LRU eviction of oldest sessions if needed."""
        current_size = self.get_total_temp_size_bytes()
        current_count = self.count_active_sessions()
        max_bytes = self.max_storage_bytes
        max_sess = self.max_sessions

        # Check if space is available and count has room for at least 1 new session
        if (current_size + required_bytes <= max_bytes) and (current_count < max_sess):
            return True

        logger.warning(
            f"Temp storage or session limit reached ({current_size / (1024*1024):.1f}MB/{max_bytes / (1024*1024):.1f}MB, "
            f"{current_count}/{max_sess} sessions). Running LRU eviction..."
        )
        # Gather all session dirs with their last_accessed_at timestamp
        sessions = []
        if self.temp_dir.exists():
            for item in self.temp_dir.iterdir():
                if item.is_dir():
                    meta_file = item / "session.json"
                    last_active = item.stat().st_mtime
                    if meta_file.is_file():
                        try:
                            with open(meta_file, "r", encoding="utf-8") as f:
                                meta = json.load(f)
                                last_active = meta.get("last_accessed_at", last_active)
                        except Exception:
                            pass
                    sessions.append((last_active, item))

        # Sort oldest first
        sessions.sort(key=lambda x: x[0])
        for _, sdir in sessions:
            try:
                shutil.rmtree(sdir, ignore_errors=True)
                current_size = self.get_total_temp_size_bytes()
                current_count = self.count_active_sessions()
                if (current_size + required_bytes <= max_bytes) and (current_count < max_sess):
                    logger.info("Storage quota and session count capacity restored after LRU eviction.")
                    return True
            except Exception as e:
                logger.error(f"Failed to evict old session {sdir}: {e}")

        return (current_size + required_bytes <= max_bytes) and (current_count < max_sess)

    def create_session(self, original_filename: str, extension: str = ".mp3") -> Tuple[str, Path]:
        """Create a new unique session directory for an uploaded file using a decoupled hash."""
        if extension not in AUDIO_FORMATS:
            raise ValueError("Unsupported audio format.")
        session_id = str(uuid.uuid4())
        dir_name = get_storage_dir_name(session_id)
        session_dir = self.temp_dir / dir_name
        session_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(session_dir, 0o700)
        except Exception:
            pass

        clean_name = sanitize_filename(original_filename, extension=extension)
        audio_path = session_dir / f"audio{extension}"

        # Write session metadata
        meta = {
            "session_id": session_id,
            "extension": extension,
            "original_filename": clean_name,
            "created_at": time.time(),
            "last_accessed_at": time.time(),
        }
        with open(session_dir / "session.json", "w", encoding="utf-8") as f:
            json.dump(meta, f)

        return session_id, audio_path

    def get_session_dir(self, session_id: str) -> Optional[Path]:
        """Validate session ID format and return hashed session directory if it exists."""
        try:
            # Validate UUID format to prevent path traversal
            uuid_obj = uuid.UUID(session_id, version=4)
            if str(uuid_obj) != session_id:
                return None
        except (ValueError, TypeError, AttributeError):
            return None

        dir_name = get_storage_dir_name(session_id)
        session_dir = self.temp_dir / dir_name
        if session_dir.is_dir():
            # Update last accessed time
            self._touch_session(session_dir)
            return session_dir
        return None

    def get_audio_path(self, session_id: str) -> Optional[Path]:
        """Return the fixed audio path for a session."""
        sdir = self.get_session_dir(session_id)
        if sdir:
            # Only fixed allowlisted paths; old sessions retain audio.mp3.
            for extension in AUDIO_FORMATS:
                audio_file = sdir / f"audio{extension}"
                if audio_file.is_file() and not audio_file.is_symlink():
                    return audio_file
        return None

    def get_session_info(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Return session metadata dictionary."""
        sdir = self.get_session_dir(session_id)
        if sdir:
            meta_file = sdir / "session.json"
            if meta_file.is_file():
                try:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass
        return None

    def update_session_info(self, session_id: str, updates: Dict[str, Any]) -> None:
        """Update session metadata fields."""
        sdir = self.get_session_dir(session_id)
        if sdir:
            meta_file = sdir / "session.json"
            data = {}
            if meta_file.is_file():
                try:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    pass
            data.update(updates)
            data["last_accessed_at"] = time.time()
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(data, f)

    def cleanup_session(self, session_id: str) -> bool:
        """Explicitly remove a session directory."""
        sdir = self.get_session_dir(session_id)
        if sdir and sdir.exists():
            try:
                shutil.rmtree(sdir, ignore_errors=True)
                return True
            except Exception as e:
                logger.error(f"Error cleaning up session {session_id}: {e}")
        return False

    def prune_expired_sessions(self) -> int:
        """Scan temp dir and remove sessions older than TTL."""
        pruned = 0
        now = time.time()
        if not self.temp_dir.exists():
            return 0

        for item in self.temp_dir.iterdir():
            if item.is_dir():
                meta_file = item / "session.json"
                should_delete = False
                if meta_file.is_file():
                    try:
                        with open(meta_file, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            last_active = data.get("last_accessed_at", data.get("created_at", 0))
                            if now - last_active > self.ttl_seconds:
                                should_delete = True
                    except Exception:
                        should_delete = True
                else:
                    # Fallback to directory mtime
                    if now - item.stat().st_mtime > self.ttl_seconds:
                        should_delete = True

                if should_delete:
                    try:
                        shutil.rmtree(item, ignore_errors=True)
                        pruned += 1
                    except Exception as e:
                        logger.error(f"Failed to prune session dir {item}: {e}")

        if pruned > 0:
            logger.info(f"Pruned {pruned} expired sessions from temporary storage.")
        return pruned

    def _touch_session(self, session_dir: Path) -> None:
        """Update last accessed timestamp in session metadata."""
        meta_file = session_dir / "session.json"
        if meta_file.is_file():
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data["last_accessed_at"] = time.time()
                with open(meta_file, "w", encoding="utf-8") as f:
                    json.dump(data, f)
            except Exception:
                pass


# Global singleton instance
storage_manager = SessionManager()


async def start_periodic_cleanup_loop(interval_seconds: int = 300):
    """Background asyncio task to periodically purge expired sessions."""
    logger.info("Starting background session TTL cleanup worker...")
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            storage_manager.prune_expired_sessions()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in cleanup worker loop: {e}")
