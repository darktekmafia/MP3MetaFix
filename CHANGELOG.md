# Changelog

All notable changes to **MP3MetaFix** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.1] - 2026-09-19

### Added
- **Cryptographic Session Cookies & Zero-Knowledge Storage Architecture**:
  - Implemented HMAC-SHA256 signed `HttpOnly`, `SameSite=Lax` session cookies (`mp3metafix_session`) to eliminate exposed UUIDs and raw identifiers from URLs and browser JavaScript.
  - Decoupled server filesystem storage: session directories on disk are now named using one-way deterministic SHA-256 hashes (`SHA-256(secret:uuid)[:32]`).
  - Refactored API routes to clean, session-bound endpoints (`/api/artwork`, `/api/save`, `/api/stream`, `/api/download/{filename}`, `/api/session`).
  - Added strict HMAC signature verification, rejecting tampered or forged session tokens with HTTP 401 Unauthorized.
- **File System Access API & Save Picker**:
  - Implemented the modern Web File System Access API (`window.showSaveFilePicker`), prompting users to choose the exact destination directory and filename upon saving.
  - Added seamless fallback to browser downloads when File System Access API is not supported or declined.
- **Named Download Route & Enhanced Content-Disposition**:
  - Added `/api/download/{filename:path}` endpoint to guarantee target filenames are directly reflected in the URL path for all browsers.
  - Formatted `Content-Disposition` with RFC 5987 UTF-8 encoding (`filename*=UTF-8''...`) along with sanitized ASCII fallback.
  - Exposed `Content-Disposition` header in CORS/Access-Control settings.
- **Universal Installer & Systemd Service**:
  - Configured `install.sh` to install and enable `mp3metafix.service` by default across both desktop and server environments so the application starts automatically on boot.
  - Added desktop wrapper auto-recovery to interact with the systemd service.
  - Added `--no-service` flag for standalone installations.
- **Documentation & Quickstart Refactoring**:
  - Restructured `README.md` and `docs/DEPLOYMENT.md` to prioritize background service installation and management (`systemctl status/restart/stop`) over manual `run.sh` foreground execution.

---

## [0.1.0] - 2026-09-19

### Added
- **Core Backend Engine**:
  - FastAPI server with async file streaming and upload validation.
  - Mutagen ID3 metadata processing supporting ID3v2.4 and ID3v2.3 tags.
  - Complete APIC album art extraction, replacement, normalization, and removal via Pillow.
  - Audio stream properties inspection (duration, bitrate, sample rate, channels).
- **Security & Storage**:
  - Magic byte and MIME type validation for MP3 and image files.
  - Path traversal protection with client filename sanitization.
  - Isolated UUID4 temporary session management.
  - Automated background TTL cleanup worker for expired upload sessions.
  - Security headers middleware (CSP, HSTS, X-Content-Type-Options, X-Frame-Options).
  - Reverse proxy header forwarding compatibility (`ProxyHeadersMiddleware`).
- **Web User Interface**:
  - Glassmorphism dark mode UI using modern vanilla CSS.
  - Drag-and-drop MP3 uploader with live upload progress.
  - Dual-panel metadata editor with categorized tabs.
  - Interactive cover art manager (drag-and-drop cover replacement, download, remove).
  - Built-in HTML5 audio preview player with scrubber, seek, volume, and time tracking.
  - Live dynamic filename generator supporting patterns (`%artist% - %title%.mp3`, `%track%`, etc.).
  - Keyboard shortcuts (<kbd>Ctrl</kbd> + <kbd>S</kbd> to save and download).
- **Installer & Deployment**:
  - Universal smart Linux installer & updater `install.sh` supporting Fedora, Ubuntu, Debian, Arch, openSUSE.
  - Automatic environment detection for Desktop (application launcher and icons) vs Headless LXC (systemd service).
  - Production reverse proxy templates for Nginx and Caddy.
  - Systemd service template with security hardening directives.
  - Complete documentation suite (`README.md`, `ARCHITECTURE.md`, `DEPLOYMENT.md`).
