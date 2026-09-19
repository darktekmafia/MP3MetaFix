# MP3MetaFix 🎵

[![Version](https://img.shields.io/badge/version-0.1.1-blue.svg)](VERSION)
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
- 🛡️ **Hardened Security by Design**:
  - Magic bytes header validation (rejects masquerading binaries and malformed payloads).
  - Isolated UUID4 temporary sessions (zero risk of path traversal `../`).
  - Automated background TTL cleanup of temp files.
  - Strict Content Security Policy (CSP), HSTS, `X-Content-Type-Options: nosniff`, and trusted reverse proxy support.
- 🐧 **Smart Universal Linux Installer (`install.sh`)**:
  - Automatically detects your distro (`dnf`, `apt`, `pacman`).
  - Automatically detects **Desktop** (adds application launcher `.desktop` and menu icon) vs **Headless LXC/Server** (installs and enables `systemd` service).
  - Supports `--update`, `--status`, `--uninstall`, and custom ports.

---

## 🚀 Quickstart

### 1. Local Development (Fedora 44 / Workstation)
```bash
# Clone the repository (or navigate to workspace)
cd /run/media/psychlone/Projects/mp3metafix

# Start development server
./run.sh
```
Open your browser at `http://127.0.0.1:8844`.

---

### 2. Universal Linux Installation

Run the smart installer:
```bash
./install.sh
```

#### Installer Options:
| Flag | Description |
|------|-------------|
| `--install` | Default installation (auto-detects Desktop vs Headless) |
| `--update` | Pulls latest Git updates, updates python dependencies, and restarts service |
| `--status` | Checks if MP3MetaFix service or endpoint is active |
| `--uninstall` | Cleans up systemd service, desktop entries, and launchers |
| `--headless` | Force Headless / Server / LXC mode |
| `--desktop` | Force Desktop mode (creates app menu launcher & icon) |
| `--port <PORT>` | Custom port (default: `8844`) |
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
    subgraph Security Perimeter
        Val[Magic Byte & MIME Validator]
        San[Filename Sanitizer]
        UUID[UUID Temp Session Manager]
        TTL[Background TTL Cleanup Worker]
    end
    subgraph Audio Engine
        Mutagen[Mutagen ID3v2.4 Engine]
        Pillow[Pillow APIC Image Processor]
    end
    Backend --> Security Perimeter
    Backend --> Audio Engine
    Security Perimeter --> Storage[(Temporary Session Storage)]
```

For in-depth technical documentation, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## ⌨️ Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| <kbd>Ctrl</kbd> + <kbd>S</kbd> / <kbd>Cmd</kbd> + <kbd>S</kbd> | Save changes and download modified MP3 |

---

## 📄 License

MIT License © 2026 MP3MetaFix Contributors
