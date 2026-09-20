"""Configuration settings for MP3MetaFix."""

import os
from pathlib import Path
from typing import Optional

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("MP3METAFIX_DATA_DIR", BASE_DIR / "data"))
TEMP_DIR = DATA_DIR / "temp"
STATIC_DIR = BASE_DIR / "frontend"
APP_DIR = STATIC_DIR / "app"
MANAGER_DIR = STATIC_DIR / "manager"
ADMIN_DIR = STATIC_DIR / "admin"
ASSETS_DIR = BASE_DIR / "assets"

# Ensure directories exist
TEMP_DIR.mkdir(parents=True, exist_ok=True)
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
APP_DIR.mkdir(parents=True, exist_ok=True)
MANAGER_DIR.mkdir(parents=True, exist_ok=True)
ADMIN_DIR.mkdir(parents=True, exist_ok=True)

# Server Config
HOST = os.getenv("MP3METAFIX_HOST", "127.0.0.1")
PORT = int(os.getenv("MP3METAFIX_PORT", "8844"))
DEBUG = os.getenv("MP3METAFIX_DEBUG", "false").lower() in ("true", "1", "yes")

# Storage & Lifecycle Config
SESSION_TTL_MINUTES = int(os.getenv("MP3METAFIX_SESSION_TTL_MINUTES", "60"))
MAX_UPLOAD_SIZE_MB = int(os.getenv("MP3METAFIX_MAX_UPLOAD_SIZE_MB", "150"))
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
MAX_GLOBAL_TEMP_STORAGE_MB = int(os.getenv("MP3METAFIX_MAX_GLOBAL_STORAGE_MB", "2048"))
MAX_GLOBAL_TEMP_STORAGE_BYTES = MAX_GLOBAL_TEMP_STORAGE_MB * 1024 * 1024

# Security / MIME Restrictions
ALLOWED_AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav"}
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_ARTWORK_SIZE_MB = int(os.getenv("MP3METAFIX_MAX_ARTWORK_SIZE_MB", "10"))
MAX_ARTWORK_SIZE_BYTES = MAX_ARTWORK_SIZE_MB * 1024 * 1024

# Reverse Proxy & Trust
TRUST_PROXIES = os.getenv("MP3METAFIX_TRUST_PROXIES", "false").lower() in ("true", "1", "yes")
TRUSTED_PROXIES_RAW = os.getenv("MP3METAFIX_TRUSTED_PROXIES", "127.0.0.1,::1")
TRUSTED_PROXIES = [p.strip() for p in TRUSTED_PROXIES_RAW.split(",") if p.strip()]
PROXY_HOST = os.getenv("MP3METAFIX_PROXY_HOST", "").strip()

# Cryptographic Session & Cookie Config
def get_secret_key() -> str:
    env_key = os.getenv("MP3METAFIX_SECRET_KEY")
    if env_key:
        return env_key
    key_file = DATA_DIR / ".secret_key"
    if key_file.exists():
        try:
            return key_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    import secrets
    new_key = secrets.token_hex(32)
    try:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(new_key, encoding="utf-8")
        try:
            os.chmod(key_file, 0o600)
        except Exception:
            pass
    except Exception:
        pass
    return new_key

SESSION_SECRET_KEY = get_secret_key()
SESSION_COOKIE_NAME = "mp3metafix_session"
SESSION_COOKIE_MAX_AGE = SESSION_TTL_MINUTES * 60

# GitHub & Repository Info
GITHUB_REPO = os.getenv("MP3METAFIX_GITHUB_REPO", "darktekmafia/MP3MetaFix")
GITHUB_REPO_URL = f"https://github.com/{GITHUB_REPO}"

# Version & Git Metadata
def get_version() -> str:
    version_file = BASE_DIR / "VERSION"
    if version_file.exists():
        return version_file.read_text().strip()
    return "0.5.0"

def get_git_commit() -> Optional[str]:
    """Retrieve current short Git commit hash if running in a git repository."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "-C", str(BASE_DIR), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None

def get_git_branch() -> Optional[str]:
    """Retrieve current Git branch name if available."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "-C", str(BASE_DIR), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None

VERSION = get_version()
