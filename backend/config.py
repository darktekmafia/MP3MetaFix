"""Configuration settings for MP3MetaFix."""

import os
from pathlib import Path

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("MP3METAFIX_DATA_DIR", BASE_DIR / "data"))
TEMP_DIR = DATA_DIR / "temp"
STATIC_DIR = BASE_DIR / "frontend"
ASSETS_DIR = BASE_DIR / "assets"

# Ensure directories exist
TEMP_DIR.mkdir(parents=True, exist_ok=True)
ASSETS_DIR.mkdir(parents=True, exist_ok=True)

# Server Config
HOST = os.getenv("MP3METAFIX_HOST", "0.0.0.0")
PORT = int(os.getenv("MP3METAFIX_PORT", "8844"))
DEBUG = os.getenv("MP3METAFIX_DEBUG", "false").lower() in ("true", "1", "yes")

# Storage & Lifecycle Config
SESSION_TTL_MINUTES = int(os.getenv("MP3METAFIX_SESSION_TTL_MINUTES", "60"))
MAX_UPLOAD_SIZE_MB = int(os.getenv("MP3METAFIX_MAX_UPLOAD_SIZE_MB", "150"))
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024

# Security / MIME Restrictions
ALLOWED_AUDIO_EXTENSIONS = {".mp3"}
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_ARTWORK_SIZE_MB = int(os.getenv("MP3METAFIX_MAX_ARTWORK_SIZE_MB", "10"))
MAX_ARTWORK_SIZE_BYTES = MAX_ARTWORK_SIZE_MB * 1024 * 1024

# Reverse Proxy & Trust
TRUST_PROXIES = os.getenv("MP3METAFIX_TRUST_PROXIES", "true").lower() in ("true", "1", "yes")

# Version
def get_version() -> str:
    version_file = BASE_DIR / "VERSION"
    if version_file.exists():
        return version_file.read_text().strip()
    return "0.1.0"

VERSION = get_version()
