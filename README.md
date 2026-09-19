# MP3MetaFix 🎵

[![Version](https://img.shields.io/badge/version-0.2.0-blue.svg)](VERSION)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux%20(Fedora%20%7C%20Ubuntu%20%7C%20Debian%20%7C%20LXC)-purple.svg)](#)

**MP3MetaFix** is a high-performance, security-focused web application and server-side MP3 metadata editor. Designed for seamless local desktop usage on Fedora / Ubuntu and headless server deployment inside **Proxmox LXC containers** behind reverse proxies.

---

## ✨ Features

- 🎧 **Complete ID3v2.4 & ID3v2.3 Tagging**:
  - Track Title, Artist, Album, Album Artist, Genre, Year / Date
  - Track Number / Total Tracks, Disc Number / Total Discs, BPM, Composer
  - Comments & Unsynchronized Lyrics
- 🖼️ **Album Artwork Manager**:
  - Extract and inspect embedded APIC cover art.
  - Drag-and-drop cover art replacement (JPEG, PNG, WebP converted & normalized via Pillow).
  - Extract/download original cover art or remove it.
- 🔊 **Built-in HTML5 Audio Preview**:
  - Listen to uploaded MP3s directly in your browser before and after editing.
  - HTTP 206 Partial Content Range streaming support.
- 💾 **Native Save & File System Access**:
  - Native browser save folder picker via the modern File System Access API (`showSaveFilePicker`).
  - Seamless fallback to direct named downloads with RFC 5987 UTF-8 Content-Disposition headers.
- ⚡ **Dynamic Filename Formatter**:
  - Automatically rename downloaded MP3s using patterns like `%artist% - %title%.mp3` or `%track% - %title%.mp3`.
- 🛡️ **14-Point Security Hardening & Threat Defense**:
  - **Cryptographic Session Cookies**: Timestamped HMAC-SHA256 signed `HttpOnly`, `SameSite=Lax` session cookies.
  - **Decoupled Hashed Storage**: Session directories isolated via one-way SHA-256 hashes (`SHA-256(secret:uuid)[:32]`).
  - **POSIX 0700 Isolation**: Multi-user permissions hardening on temporary storage.
  - **Global Storage Quota**: Automatic LRU session eviction when storage reaches disk limits.
  - **DDoS & Flood Protection**: Sliding-window rate limiter with proxy IP anti-spoofing.
  - **CSRF & XSS Defense**: Strict `Sec-Fetch-Site` validation and dynamic DOM node sanitization.
  - **Image Decompression Bomb Defense**: Pillow pixel limit bounds (10 MP max).
  - **Exception Masking**: Clean client error responses preventing filesystem disclosures.
  - **Process Sandboxing**: Systemd cgroup constraints (`MemoryMax=512M`, `TasksMax=64`, `CPUQuota=80%`).
- 🐧 **Smart Universal Linux Installer (`install.sh`)**:
  - Automatically detects your distro (`dnf`, `apt`, `pacman`).
  - Installs and enables a hardened **systemd background service** (`mp3metafix.service`) to start automatically on system boot.
  - Automatically registers **Desktop application launcher** (`.desktop`) and high-res icon for GUI environments.
  - Supports `--update`, `--status`, `--uninstall`, `--no-service`, and custom ports.

---

## 🚀 Installation & Service Setup

### 1. Install as a Systemd Service (Recommended)

To install MP3MetaFix so it runs continuously in the background and **starts automatically on system boot**:

```bash
# Clone the repository (or navigate to workspace)
cd /run/media/psychlone/Projects/mp3metafix

# Run the installer (installs systemd service & desktop integration)
sudo ./install.sh
```

Once installed, MP3MetaFix runs as a native systemd background service:
- **Web Interface**: Open `http://127.0.0.1:8844` (or your server's IP)
- **Desktop Launcher**: Available in your Application Menu (GNOME/KDE/XFCE)

#### Service Management Commands
```bash
# Check service status
sudo systemctl status mp3metafix.service

# View live application logs
sudo journalctl -u mp3metafix.service -f

# Restart or stop the service
sudo systemctl restart mp3metafix.service
sudo systemctl stop mp3metafix.service
```

---

### 2. Manual / Ad-hoc Development Mode

If you are developing or testing changes and prefer running a temporary foreground server without installing a system service:

```bash
./run.sh
```
The server will run in your active terminal session and stop when you press <kbd>Ctrl</kbd> + <kbd>C</kbd>.

---

### 3. Installer Options & Maintenance

```bash
./install.sh [OPTIONS]
```

| Flag | Description |
|------|-------------|
| `--install` | Default: Installs dependencies, sets up systemd service, and adds desktop integration |
| `--update` | Pulls latest Git updates, updates python dependencies, and restarts the systemd service |
| `--status` | Checks systemd service status and HTTP endpoint health |
| `--uninstall` | Stops and removes systemd service, desktop entries, and launchers |
| `--no-service` | Skips systemd service registration (standalone mode) |
| `--headless` | Force Headless / Server / LXC mode (skips GUI desktop entries) |
| `--desktop` | Force Desktop mode (ensures app menu launcher & icon are created) |
| `--port <PORT>` | Custom port (default: `8844`) |
| `--user <USER>` | Specify user for systemd service (default: current user) |
| `--version`, `-v` | Prints current version |

---

## 🌐 Proxmox LXC & Reverse Proxy Deployment

For hosting inside a Proxmox LXC (Ubuntu/Debian) behind Nginx or Caddy:

1. In your LXC container:
   ```bash
   git clone https://github.com/your-username/mp3metafix.git /opt/mp3metafix
   cd /opt/mp3metafix
   sudo ./install.sh --headless --port 8844
   ```
2. Set up your reverse proxy using the provided templates in `deploy/`:
   - [Nginx Configuration](deploy/nginx.conf)
   - [Caddyfile](deploy/Caddyfile)

Detailed deployment instructions are documented in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

---

## 🏗️ Architecture

```mermaid
graph TD
    Client([Browser / Desktop Launcher]) <-->|HTTPS| RP[Reverse Proxy Nginx / Caddy]
    RP <-->|HTTP Stream| Backend[FastAPI Backend]
    subgraph Security Defense Stack
        CSRF[CSRF & Origin Inspector]
        RateLimit[Anti-Spoofing Rate Limiter]
        Val[Magic Byte & MIME Validator]
        San[Filename & Metadata Sanitizer]
        HMAC[Timestamped HMAC Authenticator]
        Quota[Storage Quota & LRU Eviction]
        TTL[Background TTL Worker]
    end
    subgraph Audio Engine
        Mutagen[Mutagen ID3v2.4 Engine]
        Pillow[Pillow APIC Decompression Defense]
    end
    Backend --> Security Defense Stack
    Backend --> Audio Engine
    Security Defense Stack --> Storage[(Decoupled Hashed Storage 0700)]
```

For in-depth technical documentation, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/SECURITY_HARDENING.md](docs/SECURITY_HARDENING.md).

---

## ⌨️ Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| <kbd>Ctrl</kbd> + <kbd>S</kbd> / <kbd>Cmd</kbd> + <kbd>S</kbd> | Save changes and download modified MP3 |

---

## 📄 License

MIT License © 2026 MP3MetaFix Contributors
