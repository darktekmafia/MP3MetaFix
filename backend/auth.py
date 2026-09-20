"""Authentication, user management, and security policy engine for MP3MetaFix."""

import os
import time
import json
import uuid
import secrets
import hashlib
import hmac
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

from backend.config import (
    DATA_DIR,
    SESSION_SECRET_KEY,
    SESSION_TTL_MINUTES,
    MAX_UPLOAD_SIZE_MB,
    MAX_GLOBAL_TEMP_STORAGE_MB,
)

logger = logging.getLogger("mp3metafix.auth")

AUTH_DIR = DATA_DIR / "auth"
USERS_FILE = AUTH_DIR / "users.json"
SETTINGS_FILE = AUTH_DIR / "settings.json"

AUTH_COOKIE_NAME = "mp3metafix_auth"
# Account session duration: 7 days default
AUTH_COOKIE_MAX_AGE = 7 * 24 * 60 * 60  


# --- Cryptographic Password Hashing (PBKDF2-HMAC-SHA256, 600,000 iterations) ---

def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    """Hash a password using PBKDF2-HMAC-SHA256 with a cryptographically random salt."""
    if not salt:
        salt = secrets.token_bytes(16)
    iterations = 600_000
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a password against a stored PBKDF2 hash using timing-safe comparison."""
    if not password or not stored_hash:
        return False
    try:
        parts = stored_hash.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
            return False
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        expected = bytes.fromhex(parts[3])
        derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return secrets.compare_digest(derived, expected)
    except Exception as e:
        logger.warning(f"Password verification error: {e}")
        return False


# --- Signed Account Session Tokens ---

def create_auth_token(user_id: str, secret_key: str = SESSION_SECRET_KEY) -> str:
    """Create a signed, timestamped authentication token for an account."""
    timestamp = int(time.time())
    payload = f"{user_id}.{timestamp}"
    signature = hmac.new(secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def verify_auth_token(
    token: Optional[str],
    max_age_seconds: int = AUTH_COOKIE_MAX_AGE,
    secret_key: str = SESSION_SECRET_KEY,
) -> Optional[str]:
    """Verify signature, freshness, and structure of an authentication token. Returns user_id if valid."""
    if not token or not isinstance(token, str):
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    user_id, timestamp_str, provided_sig = parts
    try:
        timestamp = int(timestamp_str)
    except ValueError:
        return None

    # Check expiration
    if time.time() - timestamp > max_age_seconds or timestamp > time.time() + 60:
        return None

    expected_payload = f"{user_id}.{timestamp_str}"
    expected_sig = hmac.new(secret_key.encode("utf-8"), expected_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not secrets.compare_digest(provided_sig, expected_sig):
        return None

    return user_id


# --- Sliding-Window Login Rate Limiter ---

class LoginRateLimiter:
    """Limits failed authentication attempts per client IP to prevent brute-force attacks."""

    def __init__(self, max_attempts: int = 5, window_seconds: int = 60, block_duration_seconds: int = 300):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.block_duration = block_duration_seconds
        self.failed_attempts: Dict[str, List[float]] = {}
        self.blocked_until: Dict[str, float] = {}

    def is_blocked(self, client_ip: str) -> bool:
        now = time.time()
        # Check if currently in cooldown
        if client_ip in self.blocked_until:
            if now < self.blocked_until[client_ip]:
                return True
            else:
                del self.blocked_until[client_ip]

        # Prune old attempts
        if client_ip in self.failed_attempts:
            cutoff = now - self.window_seconds
            self.failed_attempts[client_ip] = [t for t in self.failed_attempts[client_ip] if t > cutoff]
            if len(self.failed_attempts[client_ip]) >= self.max_attempts:
                self.blocked_until[client_ip] = now + self.block_duration
                return True
        return False

    def record_failure(self, client_ip: str):
        now = time.time()
        if client_ip not in self.failed_attempts:
            self.failed_attempts[client_ip] = []
        self.failed_attempts[client_ip].append(now)
        if len(self.failed_attempts[client_ip]) >= self.max_attempts:
            self.blocked_until[client_ip] = now + self.block_duration

    def record_success(self, client_ip: str):
        if client_ip in self.failed_attempts:
            del self.failed_attempts[client_ip]
        if client_ip in self.blocked_until:
            del self.blocked_until[client_ip]


login_rate_limiter = LoginRateLimiter()


# --- Pydantic Request Models ---

class SetupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class SettingsUpdateRequest(BaseModel):
    guest_mode_enabled: Optional[bool] = None
    session_ttl_minutes: Optional[int] = Field(default=None, ge=5, le=1440)
    max_upload_size_mb: Optional[int] = Field(default=None, ge=10, le=2048)
    max_global_storage_mb: Optional[int] = Field(default=None, ge=100, le=102400)


# --- Authentication & Settings Manager ---

class AuthManager:
    """Manages user persistence, credential verification, and system settings."""

    def __init__(self, auth_dir: Path = AUTH_DIR):
        self.auth_dir = auth_dir
        self.users_file = self.auth_dir / "users.json"
        self.settings_file = self.auth_dir / "settings.json"
        self._ensure_storage()

    def _ensure_storage(self):
        """Create auth directory with POSIX 0700 permissions."""
        self.auth_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.auth_dir, 0o700)
        except Exception:
            pass

        if not self.settings_file.exists():
            default_settings = {
                "guest_mode_enabled": False,
                "session_ttl_minutes": SESSION_TTL_MINUTES,
                "max_upload_size_mb": MAX_UPLOAD_SIZE_MB,
                "max_global_storage_mb": MAX_GLOBAL_TEMP_STORAGE_MB,
                "updated_at": int(time.time()),
            }
            self._save_settings(default_settings)

    def _load_users(self) -> List[Dict[str, Any]]:
        if not self.users_file.exists():
            return []
        try:
            with open(self.users_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("users", []) if isinstance(data, dict) else []
        except Exception as e:
            logger.error(f"Error reading users file: {e}")
            return []

    def _save_users(self, users: List[Dict[str, Any]]):
        temp_file = self.users_file.with_suffix(".tmp")
        payload = {"users": users, "updated_at": int(time.time())}
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        try:
            os.chmod(temp_file, 0o600)
        except Exception:
            pass
        temp_file.replace(self.users_file)

    def _load_settings(self) -> Dict[str, Any]:
        if not self.settings_file.exists():
            return {
                "guest_mode_enabled": False,
                "session_ttl_minutes": SESSION_TTL_MINUTES,
                "max_upload_size_mb": MAX_UPLOAD_SIZE_MB,
                "max_global_storage_mb": MAX_GLOBAL_TEMP_STORAGE_MB,
            }
        try:
            with open(self.settings_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error reading settings file: {e}")
            return {
                "guest_mode_enabled": False,
                "session_ttl_minutes": SESSION_TTL_MINUTES,
                "max_upload_size_mb": MAX_UPLOAD_SIZE_MB,
                "max_global_storage_mb": MAX_GLOBAL_TEMP_STORAGE_MB,
            }

    def _save_settings(self, settings: Dict[str, Any]):
        temp_file = self.settings_file.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        try:
            os.chmod(temp_file, 0o600)
        except Exception:
            pass
        temp_file.replace(self.settings_file)

    def is_setup_required(self) -> bool:
        """Returns True if no administrator accounts exist in the system."""
        users = self._load_users()
        return len(users) == 0

    def get_users_count(self) -> int:
        return len(self._load_users())

    def create_initial_admin(self, username: str, password: str) -> Dict[str, Any]:
        """Creates the primary admin user during first-time setup."""
        if not self.is_setup_required():
            raise ValueError("Initial administrator setup has already been completed.")

        clean_username = username.strip()
        if len(clean_username) < 3 or len(password) < 8:
            raise ValueError("Username must be >= 3 characters and password >= 8 characters.")

        user_id = str(uuid.uuid4())
        user_record = {
            "id": user_id,
            "username": clean_username,
            "password_hash": hash_password(password),
            "role": "admin",
            "created_at": int(time.time()),
            "updated_at": int(time.time()),
        }
        self._save_users([user_record])
        logger.info(f"Primary administrator account created: '{clean_username}' ({user_id})")
        return {
            "id": user_record["id"],
            "username": user_record["username"],
            "role": user_record["role"],
        }

    def authenticate(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """Verify username and password against stored users."""
        clean_username = username.strip().lower()
        users = self._load_users()
        for u in users:
            if u.get("username", "").lower() == clean_username:
                if verify_password(password, u.get("password_hash", "")):
                    return {
                        "id": u["id"],
                        "username": u["username"],
                        "role": u.get("role", "user"),
                    }
                return None
        return None

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Look up user record by ID."""
        if not user_id:
            return None
        users = self._load_users()
        for u in users:
            if u.get("id") == user_id:
                return {
                    "id": u["id"],
                    "username": u["username"],
                    "role": u.get("role", "user"),
                }
        return None

    def change_password(self, user_id: str, old_password: str, new_password: str) -> bool:
        """Update a user's password after verifying their current password."""
        if len(new_password) < 8:
            raise ValueError("New password must be at least 8 characters.")
        users = self._load_users()
        updated = False
        for u in users:
            if u.get("id") == user_id:
                if not verify_password(old_password, u.get("password_hash", "")):
                    raise ValueError("Current password is incorrect.")
                u["password_hash"] = hash_password(new_password)
                u["updated_at"] = int(time.time())
                updated = True
                break
        if updated:
            self._save_users(users)
            logger.info(f"Password updated for user ID {user_id}")
            return True
        return False

    def get_settings(self) -> Dict[str, Any]:
        """Return active system settings."""
        return self._load_settings()

    def update_settings(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Apply updates to system settings."""
        current = self._load_settings()
        for k, v in updates.items():
            if v is not None:
                current[k] = v
        current["updated_at"] = int(time.time())
        self._save_settings(current)
        logger.info(f"System settings updated: {updates}")
        return current

    def is_guest_mode_enabled(self) -> bool:
        """Check if unauthenticated guest access to /app is permitted."""
        settings = self._load_settings()
        return bool(settings.get("guest_mode_enabled", False))


auth_manager = AuthManager()
