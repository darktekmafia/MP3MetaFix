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
from backend.locking import file_lock, atomic_json
from functools import wraps

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
    if not provided_sig.isascii():
        return None
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


DEFAULT_PINNED_SETTINGS = ["guest_mode_enabled", "max_sessions", "max_global_storage_mb"]
ALLOWED_PINNABLE_SETTINGS = {
    "guest_mode_enabled",
    "max_sessions",
    "max_global_storage_mb",
    "session_ttl_minutes",
    "max_upload_size_mb",
    "software_updates",
}


class SettingsUpdateRequest(BaseModel):
    guest_mode_enabled: Optional[bool] = None
    guest_mode: Optional[bool] = None
    session_ttl_minutes: Optional[int] = Field(default=None, ge=5, le=1440)
    max_upload_size_mb: Optional[int] = Field(default=None, ge=10, le=2048)
    max_global_storage_mb: Optional[int] = Field(default=None, ge=100, le=102400)
    max_temp_storage_mb: Optional[int] = Field(default=None, ge=100, le=102400)
    max_sessions: Optional[int] = Field(default=None, ge=1, le=1000)
    quick_settings_pinned: Optional[List[str]] = None


# --- Authentication & Settings Manager ---

class AuthStoreError(RuntimeError):
    """Account storage is unavailable; never interpret corruption as enrollment."""


def locked_users(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with file_lock(self.auth_dir / '.accounts.lock'):
            return method(self, *args, **kwargs)
    return wrapped


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
                "quick_settings_pinned": list(DEFAULT_PINNED_SETTINGS),
                "updated_at": int(time.time()),
            }
            self._save_settings(default_settings)

    def _load_users(self) -> List[Dict[str, Any]]:
        if not self.users_file.exists():
            if (self.auth_dir / '.initialized').exists():
                raise AuthStoreError('Account storage unavailable.')
            return []
        try:
            data = json.loads(self.users_file.read_text(encoding='utf-8'))
            users = data['users']
            if not isinstance(users, list) or not users:
                raise ValueError('Invalid account records')
            ids = set()
            names = set()
            for user in users:
                if not isinstance(user, dict) or not all(isinstance(user.get(k), str) and user[k] for k in ('id', 'username', 'password_hash', 'role')):
                    raise ValueError('Invalid account record')
                if user['role'] not in ('admin', 'user') or user['id'] in ids or user['username'].lower() in names:
                    raise ValueError('Invalid account identity')
                uuid.UUID(user['id'])
                ids.add(user['id'])
                names.add(user['username'].lower())
            if not any(u['role'] == 'admin' for u in users):
                raise ValueError('Missing administrator')
            # Mark existing installations as initialized without changing credentials.
            (self.auth_dir / '.initialized').touch(mode=0o600, exist_ok=True)
            return users
        except Exception as exc:
            logger.error('Account storage unavailable: %s', type(exc).__name__)
            raise AuthStoreError('Account storage unavailable.') from None

    def _save_users(self, users: List[Dict[str, Any]]):
        atomic_json(self.users_file, {'users': users, 'updated_at': int(time.time())})
        (self.auth_dir / '.initialized').touch(mode=0o600, exist_ok=True)

    def _token_key(self, user):
        material = 'account-session:' + user['password_hash'] + ':' + user.get('session_epoch', '')
        return hmac.new(SESSION_SECRET_KEY.encode(), material.encode(), hashlib.sha256).hexdigest()

    def issue_token(self, user_id):
        for user in self._load_users():
            if user['id'] == user_id:
                return create_auth_token(user_id, secret_key=self._token_key(user))
        raise AuthStoreError('Account unavailable.')

    def verify_token(self, token):
        if not isinstance(token, str) or len(token) > 512:
            return None
        user_id = token.split('.')[0]
        for user in self._load_users():
            if user['id'] == user_id:
                return verify_auth_token(token, secret_key=self._token_key(user))
        return None

    @locked_users
    def revoke_sessions(self, user_id):
        users = self._load_users()
        for user in users:
            if user['id'] == user_id:
                user['session_epoch'] = secrets.token_hex(32)
                self._save_users(users)
                return

    def _load_settings(self) -> Dict[str, Any]:
        default_res = {
            "guest_mode_enabled": False,
            "session_ttl_minutes": SESSION_TTL_MINUTES,
            "max_upload_size_mb": MAX_UPLOAD_SIZE_MB,
            "max_global_storage_mb": MAX_GLOBAL_TEMP_STORAGE_MB,
            "quick_settings_pinned": list(DEFAULT_PINNED_SETTINGS),
        }
        if not self.settings_file.exists():
            return default_res
        try:
            with open(self.settings_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data.get("quick_settings_pinned"), list):
                    data["quick_settings_pinned"] = list(DEFAULT_PINNED_SETTINGS)
                return data
        except Exception as e:
            logger.error(f"Error reading settings file: {e}")
            return default_res

    def _save_settings(self, settings: Dict[str, Any]):
        atomic_json(self.settings_file, settings)

    def is_setup_required(self) -> bool:
        """Returns True if no administrator accounts exist in the system."""
        users = self._load_users()
        return len(users) == 0

    def get_users_count(self) -> int:
        return len(self._load_users())

    @locked_users
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
        logger.info("Primary administrator account created")
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

    @locked_users
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
                u["session_epoch"] = secrets.token_hex(32)
                u["updated_at"] = int(time.time())
                updated = True
                break
        if updated:
            self._save_users(users)
            logger.info("Account password updated")
            return True
        return False

    def get_settings(self) -> Dict[str, Any]:
        """Return active system settings."""
        return self._load_settings()

    @locked_users
    def update_settings(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Apply updates to system settings."""
        current = self._load_settings()
        # Normalize aliases
        if "guest_mode" in updates and updates["guest_mode"] is not None:
            updates["guest_mode_enabled"] = updates["guest_mode"]
        if "max_temp_storage_mb" in updates and updates["max_temp_storage_mb"] is not None:
            updates["max_global_storage_mb"] = updates["max_temp_storage_mb"]
        if "quick_settings_pinned" in updates and updates["quick_settings_pinned"] is not None:
            sanitized_pins = []
            for item in updates["quick_settings_pinned"]:
                if isinstance(item, str) and item in ALLOWED_PINNABLE_SETTINGS and item not in sanitized_pins:
                    sanitized_pins.append(item)
            updates["quick_settings_pinned"] = sanitized_pins

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
        return bool(settings.get("guest_mode_enabled", settings.get("guest_mode", False)))


auth_manager = AuthManager()
