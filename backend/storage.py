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
from backend.config import TEMP_DIR, SESSION_TTL_MINUTES
from backend.security import sanitize_filename

logger = logging.getLogger("mp3metafix.storage")

class SessionManager:
    """Manages isolated session directories and automated TTL lifecycle cleanup."""

    def __init__(self, temp_dir: Path = TEMP_DIR, ttl_minutes: int = SESSION_TTL_MINUTES):
        self.temp_dir = temp_dir
        self.ttl_seconds = ttl_minutes * 60
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def create_session(self, original_filename: str) -> Tuple[str, Path]:
        """Create a new unique session directory for an uploaded file."""
        session_id = str(uuid.uuid4())
        session_dir = self.temp_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        clean_name = sanitize_filename(original_filename)
        audio_path = session_dir / "audio.mp3"

        # Write session metadata
        meta = {
            "session_id": session_id,
            "original_filename": clean_name,
            "created_at": time.time(),
            "last_accessed_at": time.time(),
        }
        with open(session_dir / "session.json", "w", encoding="utf-8") as f:
            json.dump(meta, f)

        return session_id, audio_path

    def get_session_dir(self, session_id: str) -> Optional[Path]:
        """Validate session ID format and return session directory if it exists."""
        try:
            # Validate UUID format to prevent path traversal
            uuid_obj = uuid.UUID(session_id, version=4)
        except (ValueError, TypeError, AttributeError):
            return None

        session_dir = self.temp_dir / str(uuid_obj)
        if session_dir.is_dir():
            # Update last accessed time
            self._touch_session(session_dir)
            return session_dir
        return None

    def get_audio_path(self, session_id: str) -> Optional[Path]:
        """Return the path to the MP3 file for a session."""
        sdir = self.get_session_dir(session_id)
        if sdir:
            audio_file = sdir / "audio.mp3"
            if audio_file.is_file():
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
