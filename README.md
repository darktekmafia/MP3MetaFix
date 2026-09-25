# MP3MetaFix 🎵

[![Version](https://img.shields.io/badge/version-0.5.3-blue.svg)](VERSION)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](#license)
[![Platform](https://img.shields.io/badge/platform-Linux%20(Fedora%20%7C%20Ubuntu%20%7C%20Debian%20%7C%20LXC)-purple.svg)](#)

**MP3MetaFix** is a high-performance, security-focused audio metadata and production suite. It provides a touch-friendly mobile editor, a desktop power-user workspace, and deep container tagging for **MP3, M4A (AAC/ALAC/Opus), and WAV** files — all processed natively in-place with **zero audio re-encoding and zero quality loss**.

Designed for seamless local desktop usage on Linux workstations (GNOME/KDE/XFCE) and secure self-hosted deployment inside **Proxmox LXC containers** or standalone servers behind reverse proxies.

---

## 🎛️ The Workspaces & Interface Overview

MP3MetaFix organizes music editing into tailored workspace interfaces:

| Workspace | Route | Status | Description & Target Device |
|---|---|---|---|
| 🏠 **Gateway Hub** | `/` | **Active** | Central dispatcher and workspace selector with status indicators and maintenance notices. |
| 📱 **MP3MetaFix Editor** | `/app` | **Active** | Mobile-first single-track editor with live retina waveforms, APIC cover art studio, Suno AI metadata extraction, and comment presets. Optimized for touchscreens ($\ge 320\text{px}$) and desktop browsers. |
| 🗂️ **MP3MetaManager** | `/manager` | *Under Development (Shell)* | Desktop-first power-user workspace for high-density spreadsheet batch editing, folder tree ingestion, deep ID3 frame inspection (`TXXX`, `COMM`, `APIC`), stems, and LRC karaoke sync. |
| 🎛️ **MP3Projects Studio** | `/projects` | *Under Development (Planned)* | Multi-track album sequencing, stem pack bundling, persistent project sessions, and structured release ZIP packaging. |
| 🛡️ **Admin Control Center** | `/admin` | **Active** | Administrator dashboard for system diagnostics (CPU, RAM, disk, quotas), guest access controls, Quick Settings pinning, and authorized update management. |
| 📖 **Documentation Portal** | `/docs` | **Active** | Full interactive documentation reader and in-app context help with architecture, guides, and API reference. |


---

## 🎧 Supported Formats & Native Engine

All files are inspected and updated natively without re-encoding audio streams:

- **MP3 (`.mp3`)**: Full ID3v2.3 and ID3v2.4 support (Track, Title, Artist, Album, Genre, Year, BPM, Composer, Comments, Lyrics, and embedded `APIC` cover artwork).
- **M4A / MP4 (`.m4a`)**: Native MP4 atoms for AAC, ALAC, and Opus audio codecs (`©nam`, `©ART`, `©alb`, `©day`, `trkn`, `disk`, `tmpo`, `covr`).
- **WAV (`.wav`)**: Embedded ID3 chunks synchronized with RIFF `INFO` text tags (`INAM`, `IART`, `IPRD`, `ICMT`) and embedded artwork.

---

## 🚀 Installation Scenarios

Choose the setup scenario that matches your environment:

### Scenario 1: Linux Desktop Workstation (Recommended for Personal Use)
*Installs background systemd service and registers desktop launcher icon in your application menu (GNOME/KDE/XFCE).*

```bash
# 1. Clone the repository
git clone https://github.com/darktekmafia/MP3MetaFix.git
cd MP3MetaFix

# 2. Run the installer (sets up systemd service & desktop integration)
sudo ./install.sh
```
- Open in browser: `http://127.0.0.1:8844`
- Or launch **MP3MetaFix** directly from your desktop application launcher.

---

### Scenario 2: Headless Server / Proxmox LXC (Behind Reverse Proxy)
*Installs lightweight server mode without GUI desktop launchers, configured for LAN or reverse proxy reachability.*

```bash
# 1. Clone to /opt
git clone https://github.com/darktekmafia/MP3MetaFix.git /opt/mp3metafix
cd /opt/mp3metafix

# 2. Install headless server service
sudo ./install.sh --headless --port 8844

# 3. Configure network access (LAN or Reverse Proxy)
sudo ./install.sh --lan              # Listen on 0.0.0.0 for local network access
sudo ./install.sh --proxy            # Trust forwarded headers from reverse proxies
./install.sh --access                # Inspect active URLs and proxy trust status
```
*See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for ready-to-use Nginx and Caddy reverse proxy templates.*

---

### Scenario 3: Ad-hoc / Local Development
*Runs a temporary foreground server directly in your terminal without installing system services.*

```bash
./run.sh
```
Press <kbd>Ctrl</kbd> + <kbd>C</kbd> in your terminal to stop the server.

---

### Scenario 4: Upgrading an Existing Installation
*Safely pulls code updates, updates Python dependencies, and restarts the service while preserving existing configurations.*

```bash
cd /opt/mp3metafix  # or your installation directory
sudo ./install.sh --update
```

---

## 🛠️ Service Management & CLI Tools

```bash
# Check service health and status
sudo systemctl status mp3metafix.service

# View live application logs
sudo journalctl -u mp3metafix.service -f

# Restart or stop the service
sudo systemctl restart mp3metafix.service
sudo systemctl stop mp3metafix.service

# Inspect network endpoints & reverse proxy settings
./install.sh --access
```

---

## 📚 Documentation & User Guides

Explore dedicated guides in the [`docs/`](docs/) directory:

- 📱 **[MP3MetaFix Editor Guide](docs/app/README.md)** — Single-track tagging, waveform scrubber, cover art studio, and Suno AI selective merge.
- 🗂️ **[MP3MetaManager Guide](docs/manager/README.md)** — Desktop batch editor, spreadsheet controls, and raw ID3 frame manager *(Under Development)*.
- 🎛️ **[MP3Projects Studio Guide](docs/projects/README.md)** — Multi-track album sequencing and stem bundle packaging *(Under Development)*.
- 🛡️ **[Administrator Guide](docs/admin/README.md)** — Access controls, Quick Settings pinning, diagnostics, and update management.
- 🌐 **[Production Deployment Guide](docs/DEPLOYMENT.md)** — Proxmox LXC setup, systemd hardening, reverse proxies, and CLI options.
- 🔒 **[Account Migration & Hardening](docs/ACCOUNT_MIGRATION.md)** — Dedicated unprivileged `mp3metafix` service account migration.
- 🏗️ **[System Architecture](docs/ARCHITECTURE.md)** & **[Security Hardening](docs/SECURITY_HARDENING.md)** — POSIX 0700 storage isolation, cryptographic cookies, and threat model.

---

## ⌨️ Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| <kbd>Ctrl</kbd> + <kbd>S</kbd> / <kbd>Cmd</kbd> + <kbd>S</kbd> | Save changes and download modified audio file |
| <kbd>Space</kbd> | Play / Pause audio preview in waveform visualizer |

---

## 🗺️ Master Roadmap & Governance

- 🧭 **[Project Vision & Architectural Direction](VISION.md)** — Foundational commitments, API-first architecture, and vision evolution.
- 🏛️ **[Project Governance & Transparency](GOVERNANCE.md)** — Founder stewardship, AI-assisted development transparency, contributor equality, and proposal docket lifecycle.
- 📋 **[Master Product Roadmap](ROADMAP.md)** — Track active milestones, sub-projects, and engineering backlog items.

---

## 📄 License

Declared project license: MIT © 2026 MP3MetaFix Contributors.
